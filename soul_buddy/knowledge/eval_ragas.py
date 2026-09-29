"""eval_ragas — RAGAS 桥接(生成层指标:忠实度/相关性/上下文精确召回)。

ragas 是可选依赖(extra: ragas-eval),未安装时 eval_harness 只跑检索层,
报告里标注 skipped 原因 —— sidecar 运行本身不依赖本模块。

0.4 collections API 要点(已实测确认):
  - judge LLM: ragas.llms.llm_factory(model, client=openai.AsyncOpenAI(...)),
    client 指向任意 OpenAI 兼容端点(DeepSeek/SiliconFlow/小米 MiMo 均可)。
  - embeddings: ragas.embeddings.OpenAIEmbeddings(client=..., model=...)。
  - 指标: ragas.metrics.collections 的 Faithfulness / AnswerRelevancy /
    ContextRecall / ContextPrecision,逐样本 await metric.ascore(...) ->
    MetricResult。AnswerRelevancy 需要 embeddings(问题相似度),其余只要 LLM。

judge 模型解析顺序(与 rubric 的"固定裁判"思路一致):
  SOUL_RAGAS_JUDGE_PROVIDER -> SOUL_RUBRIC_JUDGE_PROVIDER -> 会话 provider
faithfulness 对 judge 能力有要求,不要用 8B 级小模型当裁判。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

log = logging.getLogger("soul_buddy.knowledge.eval_ragas")


class RagasUnavailable(RuntimeError):
    """ragas 未安装或 judge/embedding 未配置 —— 消息可直接进报告。"""


def ragas_available() -> bool:
    try:
        import ragas  # noqa: F401
        return True
    except Exception:
        return False


# 仅 OpenAI 兼容端点可作 judge(Anthropic 原生 API 不走 openai client)
_JUDGE_PROVIDER_ATTRS = {
    "deepseek": ("deepseek_api_key", "deepseek_base_url", "deepseek_model"),
    "siliconflow": ("siliconflow_api_key", "siliconflow_base_url",
                    "siliconflow_model"),
    "xiaomi": ("xiaomi_api_key", "xiaomi_base_url", "xiaomi_model"),
    "openai-chat": ("openai_api_key", "openai_base_url", "openai_chat_model"),
}


def resolve_judge(settings, override: str | None = None,
                  session_provider: str | None = None) -> dict | None:
    """解析 judge 凭据;无可用配置返回 None(调用方降级跳过 RAGAS)。"""
    from ..config import RUBRIC_JUDGE_PROVIDER
    chain = [override, settings.ragas_judge_provider,
             RUBRIC_JUDGE_PROVIDER, session_provider]
    for name in chain:
        name = (name or "").strip().lower()
        if not name or name in ("", "session", "self", "auto", "offline"):
            continue
        attrs = _JUDGE_PROVIDER_ATTRS.get(name)
        if attrs is None:
            continue
        api_key, base_url, model = (getattr(settings, a, "") for a in attrs)
        if api_key:
            return {"provider": name, "api_key": api_key,
                    "base_url": base_url, "model": model}
    return None


def _metric_value(res: Any) -> float | None:
    """MetricResult -> float(防御式:0.4.x 返回 .value 字段)。"""
    v = getattr(res, "value", None)
    if v is None and isinstance(res, (int, float)):
        v = res
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class EvalCancelled(RuntimeError):
    """用户请求取消评估 —— 各阶段在检查点抛出。"""


def run_ragas_eval(samples: list[dict], judge: dict,
                   embedding_cfg: dict | None = None,
                   timeout_s: float = 120.0,
                   progress=None,
                   cancel_check=None) -> dict:
    """对逐题样本跑 RAGAS 指标,返回 {"metrics": 聚合, "per_item": 逐题}。

    samples: [{"q", "answer", "contexts", "reference", "must_refuse"}]
    - faithfulness 全量(拒答题的"忠实拒答"也该得高分)
    - answer_relevancy 跳过 must_refuse 题(拒答天然"不相关",不计入)
    - context_recall / context_precision 只跑有 reference 的题
    逐题异常不中断整体,记 None 并带 error 字符串。
    progress(done, total):逐题进度回调,异常不外抛。
    cancel_check():每题开头检查,返回 True 则抛 EvalCancelled。
    """
    try:
        from openai import AsyncOpenAI
        from ragas.llms import llm_factory
        from ragas.metrics.collections import (AnswerRelevancy, ContextPrecision,
                                               ContextRecall, Faithfulness)
    except Exception as exc:  # ragas 未安装 / langchain 栈不兼容
        raise RagasUnavailable(
            f"ragas 不可用: {exc}。安装: uv sync --extra ragas-eval") from exc

    client = AsyncOpenAI(api_key=judge["api_key"], base_url=judge["base_url"])
    judge_llm = llm_factory(judge["model"], client=client)
    # judge 结构化输出默认 max_tokens=1024 会被截断("output is incomplete"),
    # 提到 4096;temperature 压到 0.01 保证评分稳定。
    # (InstructorLLM.model_args 构造后是普通 dict,每轮调用时动态读取。)
    judge_llm.model_args["max_tokens"] = 4096
    judge_llm.model_args["temperature"] = 0.01

    emb_llm = None
    if embedding_cfg and embedding_cfg.get("api_key"):
        emb_client = AsyncOpenAI(api_key=embedding_cfg["api_key"],
                                 base_url=embedding_cfg["base_url"])
        emb_llm = _ragas_embeddings(embedding_cfg, emb_client)

    metrics: dict[str, Any] = {"faithfulness": Faithfulness(llm=judge_llm)}
    if emb_llm is not None:
        metrics["answer_relevancy"] = AnswerRelevancy(llm=judge_llm,
                                                      embeddings=emb_llm)
    metrics["context_recall"] = ContextRecall(llm=judge_llm)
    metrics["context_precision"] = ContextPrecision(llm=judge_llm)

    async def _score_all() -> list[dict]:
        out: list[dict] = []
        for i, s in enumerate(samples):
            if progress:
                try:
                    progress(i, len(samples))
                except Exception:
                    pass
            if cancel_check is not None and cancel_check():
                raise EvalCancelled("用户取消了评估")
            row: dict[str, Any] = {"id": s.get("id"), "scores": {},
                                   "attempted": []}
            ctx = [c for c in (s.get("contexts") or []) if c]
            calls: dict[str, Any] = {
                "faithfulness": lambda: metrics["faithfulness"].ascore(
                    user_input=s["q"], response=s["answer"],
                    retrieved_contexts=ctx),
                "context_recall": lambda: metrics["context_recall"].ascore(
                    user_input=s["q"], retrieved_contexts=ctx,
                    reference=s["reference"]),
                "context_precision": lambda: metrics["context_precision"].ascore(
                    user_input=s["q"], reference=s["reference"],
                    retrieved_contexts=ctx),
            }
            if "answer_relevancy" in metrics and not s.get("must_refuse"):
                calls["answer_relevancy"] = (lambda: metrics["answer_relevancy"]
                                             .ascore(user_input=s["q"],
                                                     response=s["answer"]))
            # 拒答题跳过 faithfulness:没有可核验的 claims,判 0 会污染均值
            if s.get("must_refuse"):
                calls.pop("faithfulness", None)
            for name, fn in calls.items():
                if name.startswith("context_") and not (s.get("reference")
                                                        and ctx):
                    continue   # 缺 reference/上下文:跳过,不算 error
                row["attempted"].append(name)
                try:
                    row["scores"][name] = _metric_value(
                        await asyncio.wait_for(fn(), timeout=timeout_s))
                except Exception as exc:
                    row["scores"][name] = None
                    row.setdefault("errors", {})[name] = str(exc)[:200]
            out.append(row)
        if progress:
            try:
                progress(len(samples), len(samples))
            except Exception:
                pass
        return out

    t0 = time.perf_counter()
    per_item = asyncio.run(_score_all())
    elapsed = time.perf_counter() - t0

    agg: dict[str, Any] = {}
    for name in metrics:
        vals = [it["scores"].get(name) for it in per_item
                if name in it.get("attempted", [])
                and it["scores"].get(name) is not None]
        errs = sum(1 for it in per_item
                   if name in it.get("attempted", [])
                   and it["scores"].get(name) is None)
        skipped = sum(1 for it in per_item
                      if name not in it.get("attempted", []))
        agg[name] = {
            "mean": round(sum(vals) / len(vals), 4) if vals else None,
            "n": len(vals), "errors": errs, "skipped": skipped,
        }
    return {"metrics": agg, "per_item": per_item,
            "judge_model": judge["model"], "judge_provider": judge["provider"],
            "elapsed_s": round(elapsed, 1)}


def _ragas_embeddings(embedding_cfg: dict, client):
    """EMBEDDING_* 配置 -> ragas BaseRagasEmbedding;未配置返回 None。"""
    if not (embedding_cfg.get("api_key") and embedding_cfg.get("model")):
        return None
    from ragas.embeddings import OpenAIEmbeddings as RagasOpenAIEmbeddings
    return RagasOpenAIEmbeddings(client=client,
                                 model=embedding_cfg["model"])
