"""P2 context layer: externalize bytes, compact token drop, summary degradation."""
import tempfile
from pathlib import Path

from soul_buddy.context.compact import (
    CompactController, needs_compact, _message_tokens, _message_text,
    build_summary_prompt, split_summary_response,
)
from soul_buddy.context.externalize import Externalizer, mark_missing_pointers
from soul_buddy.config import (
    EXTERNALIZE_THRESHOLD_BYTES, DEFAULT_CONTEXT_WINDOW, context_window,
)

_SUPERSEDED_NOTE = "[superseded by a later read of the same file]"


def _assistant(text, tool_use_id=None):
    if tool_use_id:
        return {"role": "assistant", "content": [
            {"type": "text", "text": text},
            {"type": "tool_use", "id": tool_use_id, "name": "read_file",
             "input": {"path": "a.txt"}},
        ]}
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def _tool_result(tool_use_id, content):
    return {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}]}


# --- A14 externalize ---------------------------------------------------------

def test_inline_under_threshold(tmp_path):
    ext = Externalizer(tmp_path / "tr")
    small = "x" * 100
    assert ext.externalize(small, "s1") == small


def test_exactly_threshold_inline(tmp_path):
    ext = Externalizer(tmp_path / "tr", threshold_bytes=100)
    assert ext.externalize("x" * 100, "s1") == "x" * 100  # strict >


def test_externalize_over_threshold(tmp_path):
    ext = Externalizer(tmp_path / "tr", threshold_bytes=100)
    big = "y" * 500
    out = ext.externalize(big, "s1")
    assert "externalized" in out
    files = list((tmp_path / "tr" / "s1").glob("*.txt"))
    assert files
    assert files[0].read_text(encoding="utf-8") == big


def test_externalize_under_50kib_inline():
    ex = Externalizer(base=Path(tempfile.mkdtemp()))
    small = "x" * (EXTERNALIZE_THRESHOLD_BYTES - 10)
    assert ex.externalize(small, "s1") == small


def test_externalize_over_50kib_pointer():
    ex = Externalizer(base=Path(tempfile.mkdtemp()))
    big = "y" * (EXTERNALIZE_THRESHOLD_BYTES + 100)
    out = ex.externalize(big, "s1")
    assert out.startswith("<externalized path=")
    assert "truncated, full output on disk" in out
    files = list((ex.base / "s1").iterdir())
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == big


def test_externalize_exactly_threshold_stays_inline():
    ex = Externalizer(base=Path(tempfile.mkdtemp()))
    exact = "z" * EXTERNALIZE_THRESHOLD_BYTES
    assert ex.externalize(exact, "s1") == exact


# --- A13 needs_compact -------------------------------------------------------

def test_needs_compact_respects_model_window():
    # DeepSeek V4 是 1M 窗口：48k 的 messages 离 0.75 触发线还远
    assert needs_compact(48_000, "deepseek-flash") is False
    # offline 窗口只有 8k：6k + 4096 预留已经越过触发线
    assert needs_compact(6_000, "offline") is True
    assert needs_compact(200_000, "offline") is True


def test_context_window_lookup_by_model_name():
    # 官方在售 model id
    assert context_window("deepseek-flash") == 1_000_000
    assert context_window("deepseek-v4-pro") == 1_000_000
    assert context_window("gpt-4o") == 128_000
    assert context_window("Qwen/Qwen3-8B") == 32_000
    # 旧名 / 非官方写法也要命中，否则会白白回落到 32k
    assert context_window("deepseek-v4-flash") == 1_000_000
    assert context_window("deepseek-v4.1-flash") == 1_000_000
    # 带日期后缀的 id 走前缀匹配
    assert context_window("deepseek-flash-20260910") == 1_000_000
    # 未知 / 空模型回落到默认值，不替它猜窗口
    assert context_window("some-brand-new-model") == DEFAULT_CONTEXT_WINDOW
    assert context_window("") == DEFAULT_CONTEXT_WINDOW


# --- compact: token drop + pair preservation ---------------------------------

def _build_convo(n_turns: int):
    msgs = [_assistant("Let's work.", None),
            _tool_result("seed", "initial task")]
    for i in range(n_turns):
        msgs.append(_assistant(f"turn {i}", f"tu_{i}"))
        msgs.append(_tool_result(f"tu_{i}", f"result number {i} " * 20))
    return msgs


def test_compact_reduces_tokens():
    msgs = _build_convo(40)
    before = _message_tokens(msgs)
    ctrl = CompactController(keep_recent_turns=6)
    ctrl.compact_if_needed(msgs, "offline")  # offline window tiny -> always compact
    after = _message_tokens(msgs)
    assert ctrl.last_compacted is True
    assert after < before


def test_compact_keeps_pairs():
    """A tool_use must always have its tool_result after compaction (A12)."""
    msgs = _build_convo(50)
    ctrl = CompactController(keep_recent_turns=6)
    ctrl.compact_if_needed(msgs, "offline")
    use_ids, result_ids = set(), set()
    n_results = 0
    for m in msgs:
        for b in (m["content"] if isinstance(m["content"], list) else []):
            if isinstance(b, dict):
                if b.get("type") == "tool_use":
                    use_ids.add(b["id"])
                if b.get("type") == "tool_result":
                    result_ids.add(b["tool_use_id"])
                    n_results += 1
    # every tool_use keeps its result (the fixture's "seed" result is a
    # pre-existing orphan; compaction must not create NEW orphans)
    assert use_ids <= result_ids, "orphaned tool_use after compaction"
    assert n_results == 51, "compaction dropped tool_result blocks"


def test_compact_no_compact_when_under_budget():
    msgs = _build_convo(3)
    ctrl = CompactController(keep_recent_turns=6)
    ctrl.compact_if_needed(msgs, "deepseek-chat")  # 1M window, 3 turns tiny
    assert ctrl.last_compacted is False


# --- A12 summary failure degrades to prune -----------------------------------

def test_summary_failure_degrades_to_prune():
    events = []
    msgs = _build_convo(50)

    def boom(_text: str) -> str:
        raise RuntimeError("LLM summary unavailable")

    ctrl = CompactController(summary_provider=boom,
                             on_event=lambda n, d: events.append((n, d)),
                             keep_recent_turns=6)
    # target deliberately tiny so the summary branch runs even after pruning
    ctrl._compact(msgs, target=100)
    assert any("summary_failed" == n for n, _ in events)
    use_ids, result_ids = set(), set()
    for m in msgs:
        for b in (m["content"] if isinstance(m["content"], list) else []):
            if isinstance(b, dict):
                if b.get("type") == "tool_use":
                    use_ids.add(b["id"])
                if b.get("type") == "tool_result":
                    result_ids.add(b["tool_use_id"])
    assert use_ids == result_ids
    assert len(msgs) < 100


def test_compact_internal_error_does_not_raise():
    ctrl = CompactController()
    msgs = [{"role": "assistant", "content": None}]  # nasty input
    ctrl.compact_if_needed(msgs, "offline")  # must not raise
    assert msgs == [{"role": "assistant", "content": None}]


# --- A15 externalize quota / LRU --------------------------------------------

def test_externalize_session_quota_lru_eviction():
    base = Path(tempfile.mkdtemp())
    # threshold lowered so the writes actually hit disk (default is 50 KiB)
    ex = Externalizer(base=base, threshold_bytes=100, session_max_files=3,
                      session_max_bytes=10**9, on_event=lambda n, d: None)
    for _ in range(5):
        ex.externalize("data-" * 100, "sess")
    files = list((base / "sess").iterdir())
    assert len(files) == 3  # LRU keeps only the 3 newest


def test_externalize_global_cleanup():
    base = Path(tempfile.mkdtemp())
    ex = Externalizer(base=base, threshold_bytes=100, session_max_files=10**6,
                      session_max_bytes=10**9,
                      global_max_bytes=2000, on_event=lambda n, d: None)
    for i in range(10):
        ex.externalize("x" * 500, f"s{i}")
    removed = ex.cleanup_global()
    assert removed > 0


# --- P0-3: fixed overhead in the trigger --------------------------------------

def test_needs_compact_counts_fixed_overhead():
    # gpt-4o window 128k -> trigger 96k: 1k messages alone is far below it...
    assert needs_compact(1_000, "gpt-4o", 0) is False
    # ...but a large system+tools overhead pushes the real request over it.
    assert needs_compact(1_000, "gpt-4o", 96_000) is True


# --- P1-5: early stop / P0-2: layered staging ---------------------------------

def _build_big_convo(n_turns: int, result_chars: int = 6000,
                     distinct_paths: bool = True):
    """Turns big enough that L1/L2 alone cannot reach the compaction target."""
    msgs = []
    for i in range(n_turns):
        path = f"f{i}.txt" if distinct_paths else "a.txt"
        msgs.append({"role": "assistant", "content": [
            {"type": "text", "text": f"turn {i}"},
            {"type": "tool_use", "id": f"tu_{i}", "name": "read_file",
             "input": {"path": path}},
        ]})
        msgs.append(_tool_result(f"tu_{i}", "x" * result_chars))
    return msgs


def test_early_stop_preserves_history_when_cheap_layers_suffice():
    # _build_convo turns all read a.txt -> the supersede layer collapses them
    # under target on its own, so the pruner must NOT drop whole turns.
    msgs = _build_convo(40)
    ctrl = CompactController(keep_recent_turns=6)
    ctrl.compact_if_needed(msgs, "offline")
    assert ctrl.last_compacted is True
    assistants = sum(1 for m in msgs if m["role"] == "assistant")
    assert assistants == 41, "history was pruned although L2 already met target"


def test_prune_runs_when_cheap_layers_not_enough():
    msgs = _build_big_convo(30, distinct_paths=True)
    ctrl = CompactController(keep_recent_turns=6)
    ctrl.compact_if_needed(msgs, "offline")
    assistants = sum(1 for m in msgs if m["role"] == "assistant")
    assert assistants == 6  # pruned to keep_recent_turns


# --- P1-6: superseded file reads ----------------------------------------------

def test_supersede_old_file_reads_keeps_pairs():
    msgs = _build_big_convo(8, result_chars=2400, distinct_paths=False)
    ctrl = CompactController(keep_recent_turns=6)
    ctrl.compact_if_needed(msgs, "offline")
    results = [b for m in msgs
               for b in (m["content"] if isinstance(m["content"], list) else [])
               if isinstance(b, dict) and b.get("type") == "tool_result"]
    superseded = [b for b in results if b["content"] == _SUPERSEDED_NOTE]
    assert len(superseded) == 7  # all but the latest read of a.txt
    assert all(b["tool_use_id"] != "tu_7" for b in superseded)
    use_ids = {b["id"] for m in msgs
               for b in (m["content"] if isinstance(m["content"], list) else [])
               if isinstance(b, dict) and b.get("type") == "tool_use"}
    result_ids = {b["tool_use_id"] for b in results}
    assert use_ids == result_ids, "supersede must blank content, not drop blocks"


# --- P0-1/P0-2: L4 summary + durable facts extraction --------------------------

def test_summary_and_durable_block_extracted():
    def summarizer(_prompt: str) -> str:
        return ("---SUMMARY---\nUser wants the WAL migration done.\n"
                "---DURABLE---\n- Decision: use SQLite WAL\n"
                "- Pending: migrate schema")

    msgs = _build_big_convo(20)
    ctrl = CompactController(summary_provider=summarizer, keep_recent_turns=6)
    ctrl.compact_if_needed(msgs, "offline")
    assert "SQLite WAL" in ctrl.durable_block
    assert "migrate schema" in ctrl.durable_block
    summary_msgs = [m for m in msgs
                    if "Summary of earlier turns" in _message_text(m)]
    assert summary_msgs, "summary message missing after compaction"
    assert "WAL migration" in _message_text(summary_msgs[0])
    # kept recent turns still pair up
    use_ids = {b["id"] for m in msgs
               for b in (m["content"] if isinstance(m["content"], list) else [])
               if isinstance(b, dict) and b.get("type") == "tool_use"}
    result_ids = {b["tool_use_id"] for m in msgs
                  for b in (m["content"] if isinstance(m["content"], list) else [])
                  if isinstance(b, dict) and b.get("type") == "tool_result"}
    assert use_ids == result_ids


def test_summary_prompt_carries_durable_forward():
    prompt = build_summary_prompt("history text", "- Decision: use SQLite WAL")
    assert "- Decision: use SQLite WAL" in prompt


def test_split_summary_response_variants():
    s, d = split_summary_response("---SUMMARY---\nabc\n---DURABLE---\n- fact x")
    assert s == "abc" and d == "- fact x"
    s2, d2 = split_summary_response("plain summary only")
    assert s2 == "plain summary only" and d2 == ""


def test_summary_without_markers_treated_as_plain_summary():
    def summarizer(_prompt: str) -> str:
        return "Just a plain summary."

    msgs = _build_big_convo(20)
    ctrl = CompactController(summary_provider=summarizer, keep_recent_turns=6)
    ctrl.compact_if_needed(msgs, "offline")
    assert ctrl.durable_block == ""
    assert any("Just a plain summary." in _message_text(m) for m in msgs)


def test_empty_summary_degrades_to_prune():
    events = []

    def summarizer(_prompt: str) -> str:
        return "   "

    msgs = _build_big_convo(20)
    ctrl = CompactController(summary_provider=summarizer,
                             on_event=lambda n, d: events.append((n, d)),
                             keep_recent_turns=6)
    ctrl.compact_if_needed(msgs, "offline")
    assert any(n == "summary_failed" for n, _ in events)
    assistants = sum(1 for m in msgs if m["role"] == "assistant")
    assert assistants == 3  # degraded prune keeps keep_min turns


# --- P0-4: hard-limit preflight ------------------------------------------------

def test_hard_limit_preflight_and_force_reduce():
    msgs = _build_big_convo(30)
    ctrl = CompactController(keep_recent_turns=6)
    over = ctrl.check_hard_limit(msgs, "offline")
    assert over is not None
    assert over["estimated_tokens"] > over["window"]
    assert set(over) == {"estimated_tokens", "window", "fixed_overhead"}, \
        "over-limit info must stay audit-safe (no message bodies)"
    ctrl.force_reduce(msgs)
    over2 = ctrl.check_hard_limit(msgs, "offline")
    assert over2 is None
    assistants = sum(1 for m in msgs if m["role"] == "assistant")
    assert assistants == 1  # system + leading + last group only


# --- P1-7: durable block renders into the system prompt ------------------------

def test_durable_block_renders_into_system_prompt():
    from soul_buddy.context import build_context_layer
    ctx = build_context_layer(register_base_segments=False)
    ctx.compact.durable_block = "- Decision: use SQLite WAL"
    text, meta = ctx.assemble_system_prompt(None)
    assert "use SQLite WAL" in text
    assert "durable" in meta["segments"]


# --- P1-8: externalized pointer verification ------------------------------------

def test_mark_missing_pointers(tmp_path):
    ex = Externalizer(tmp_path / "tr", threshold_bytes=100)
    pointer = ex.externalize("y" * 500, "s1")
    assert mark_missing_pointers(pointer) == pointer  # file exists -> untouched
    next((tmp_path / "tr" / "s1").glob("*.txt")).unlink()
    marked = mark_missing_pointers(pointer)
    assert 'status="missing"' in marked
    assert mark_missing_pointers("no tags here") == "no tags here"


def test_externalize_head_tail_preview(tmp_path):
    ex = Externalizer(tmp_path / "tr", threshold_bytes=100, preview_bytes=100)
    head_only = ex.externalize("HEAD" + "x" * 500 + "TAIL", "s1")
    assert "HEAD" in head_only and "TAIL" not in head_only
    both = ex.externalize("HEAD" + "y" * 500 + "TAIL", "s2",
                          preview_mode="head_tail")
    assert "HEAD" in both and "TAIL" in both


# --- P1-10: bash output bounding -------------------------------------------------

def test_bash_output_bounded_and_externalized(tmp_path):
    from soul_buddy.tools.bash import _bound_output

    class Ctx:
        session_id = "s1"
        externalizer = Externalizer(tmp_path / "tr")  # default 50 KiB threshold

    # huge -> externalized pointer, readable back from disk
    out = _bound_output("e" * 60_000, Ctx())
    assert "<externalized" in out
    pointers = list((tmp_path / "tr" / "s1").glob("*.txt"))
    assert pointers and pointers[0].stat().st_size == 60_000
    # medium (over inline cap, under threshold) -> head+tail trim
    mid = "M" * 14_000 + "x" * 11_000 + "Z" * 5_000
    out2 = _bound_output(mid, Ctx())
    assert "chars elided" in out2
    assert out2.startswith("M") and out2.rstrip().endswith("Z")
    # small -> unchanged
    assert _bound_output("ok", Ctx()) == "ok"


# --- images count toward the context budget -----------------------------------
# 图片在 buffer 里只是 {"type": "image", "path": ...} 的轻引用，base64 的展开
# 发生在 wire 阶段，所以纯文本估算会把整张图算成 0 token。视觉模型是按
# **分辨率**计费的，必须有单独的口径。

def _png_header(w: int, h: int) -> bytes:
    """够用的 PNG 头：签名 + IHDR（宽高在 offset 16/20）。"""
    return (b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
            + w.to_bytes(4, "big") + h.to_bytes(4, "big")
            + b"\x08\x06\x00\x00\x00")


def test_estimate_image_tokens_uses_pixel_dimensions(tmp_path):
    from soul_buddy.context.tokens import estimate_image_tokens

    big = tmp_path / "shot.png"
    big.write_bytes(_png_header(1920, 1080) + b"\x00" * 16)
    assert estimate_image_tokens({"type": "image", "path": str(big)}) == \
        round(1920 * 1080 / 750)                       # 2765
    # 极小图走下限，绝不返回 0
    tiny = tmp_path / "tiny.png"
    tiny.write_bytes(_png_header(1, 1) + b"\x00" * 16)
    assert estimate_image_tokens({"type": "image", "path": str(tiny)}) == 85


def test_estimate_image_tokens_fallback_and_explicit_size(tmp_path):
    from soul_buddy.context.tokens import (estimate_image_tokens,
                                           IMAGE_TOKEN_FALLBACK)

    # 读不到文件 -> 兜底常量，绝不抛
    assert estimate_image_tokens(
        {"type": "image", "path": str(tmp_path / "gone.png")}
    ) == IMAGE_TOKEN_FALLBACK
    assert estimate_image_tokens({"type": "image"}) == IMAGE_TOKEN_FALLBACK
    # ref 里已带 width/height 时直接采用，不碰磁盘
    assert estimate_image_tokens(
        {"type": "image", "path": "Z:/nope.png", "width": 800, "height": 600}
    ) == round(800 * 600 / 750)


def test_image_dimension_probes(tmp_path):
    from soul_buddy.context.tokens import _image_dimensions

    gif = tmp_path / "a.gif"
    gif.write_bytes(b"GIF89a" + (1920).to_bytes(2, "little")
                    + (1080).to_bytes(2, "little") + b"\x00" * 8)
    assert _image_dimensions(str(gif)) == (1920, 1080)

    bmp = tmp_path / "a.bmp"
    bmp.write_bytes(b"BM" + b"\x00" * 16 + (1920).to_bytes(4, "little")
                    + (1080).to_bytes(4, "little"))
    assert _image_dimensions(str(bmp)) == (1920, 1080)

    # JPEG：SOI + APP0 + SOF0（尺寸在 SOF0 段里）
    jpg = tmp_path / "a.jpg"
    jpg.write_bytes(
        b"\xff\xd8"
        + b"\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\x00"
        + b"\x01\x01\x00" + (72).to_bytes(2, "big")
        + (72).to_bytes(2, "big") + b"\x00\x00"
        + b"\xff\xc0" + (17).to_bytes(2, "big") + b"\x08"
        + (1080).to_bytes(2, "big") + (1920).to_bytes(2, "big")
        + b"\x03\x01\x11\x00\x02\x11\x01\x03\x11\x01")
    assert _image_dimensions(str(jpg)) == (1920, 1080)

    # 非图片 / 目录：不炸，返回 (0, 0)
    junk = tmp_path / "x.bin"
    junk.write_bytes(b"not an image at all")
    assert _image_dimensions(str(junk)) == (0, 0)
    assert _image_dimensions(str(tmp_path)) == (0, 0)
    assert _image_dimensions(str(tmp_path / "missing.jpg")) == (0, 0)


def test_estimate_images_tokens_sums_all_refs(tmp_path):
    from soul_buddy.context.tokens import estimate_images_tokens

    p = tmp_path / "s.png"
    p.write_bytes(_png_header(1024, 1024) + b"\x00" * 16)
    ref = {"type": "image", "path": str(p), "media_type": "image/png"}
    msgs = [{"role": "user", "content": [{"type": "text", "text": "看图"},
                                         ref, dict(ref)]},
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}]
    assert estimate_images_tokens(msgs) == 2 * round(1024 * 1024 / 750)
    # 纯文本消息不计图片
    assert estimate_images_tokens([{"role": "user", "content": "纯文本"}]) == 0
    assert estimate_images_tokens([]) == 0


# --- P0-1: compaction offload (history file) + embedded path ------------------

def test_compact_offloads_dropped_turns_to_history_file(tmp_path):
    def summarizer(_prompt: str) -> str:
        return "---SUMMARY---\nsummary text\n---DURABLE---\n- Decision: X"

    hist = tmp_path / "history" / "sess1.txt"
    msgs = _build_big_convo(20)
    ctrl = CompactController(summary_provider=summarizer,
                             keep_recent_turns=6,
                             history_path=hist,
                             durable_path=tmp_path / "durable.txt")
    ctrl.compact_if_needed(msgs, "offline")
    # history file exists and contains the dropped turns (with role markers)
    assert hist.is_file()
    body = hist.read_text(encoding="utf-8")
    assert "[assistant]" in body and "[user]" in body
    assert "turn 0" in body          # an early dropped turn is recoverable
    # the summary message embeds the history path for read_file recovery
    summary_msgs = [m for m in msgs if "Summary of earlier turns" in _message_text(m)]
    assert summary_msgs
    assert str(hist) in _message_text(summary_msgs[0])


def test_compact_offload_appends_across_multiple_compactions(tmp_path):
    def summarizer(_prompt: str) -> str:
        return "---SUMMARY---\nsummary\n---DURABLE---\n- Decision: X"

    hist = tmp_path / "history" / "sess1.txt"
    ctrl = CompactController(summary_provider=summarizer,
                             keep_recent_turns=6,
                             history_path=hist)
    msgs = _build_big_convo(20)
    ctrl.compact_if_needed(msgs, "offline")
    first = hist.read_text(encoding="utf-8")
    # second compaction on the grown buffer appends, does not overwrite
    msgs2 = msgs + _build_big_convo(20)
    ctrl.compact_if_needed(msgs2, "offline")
    second = hist.read_text(encoding="utf-8")
    assert second.startswith(first) and len(second) > len(first)


def test_offload_disabled_by_default():
    ctrl = CompactController()
    assert ctrl.history_path is None
    assert ctrl._offload_groups([[_assistant("x")]]) is None


def test_offload_failure_does_not_raise(tmp_path):
    def summarizer(_prompt: str) -> str:
        return "---SUMMARY---\nsummary\n---DURABLE---\n- Decision: X"

    # history path under a *file* (not dir) -> mkdir fails -> must not raise
    blocker = tmp_path / "blocker"
    blocker.write_text("occupied", encoding="utf-8")
    hist = blocker / "history" / "s.txt"
    events = []
    msgs = _build_big_convo(20)
    ctrl = CompactController(summary_provider=summarizer,
                             on_event=lambda n, d: events.append(n),
                             keep_recent_turns=6,
                             history_path=hist)
    ctrl.compact_if_needed(msgs, "offline")  # must not raise
    assert any(n == "history_offload_failed" for n in events)
    assert "Summary of earlier turns" in _message_text(msgs[0])


# --- P0-2: durable persistence across controller rebuilds --------------------

def test_durable_persisted_and_reloaded_on_rebuild(tmp_path):
    def summarizer(_prompt: str) -> str:
        return "---SUMMARY---\nsummary\n---DURABLE---\n- Decision: keep SQLite WAL"

    durable = tmp_path / "durable.txt"
    msgs = _build_big_convo(20)
    ctrl = CompactController(summary_provider=summarizer,
                             keep_recent_turns=6,
                             durable_path=durable)
    ctrl.compact_if_needed(msgs, "offline")
    assert "SQLite WAL" in ctrl.durable_block
    assert durable.is_file()
    assert "SQLite WAL" in durable.read_text(encoding="utf-8")
    # rebuild the controller (process restart) -> durable facts restored from disk
    ctrl2 = CompactController(summary_provider=summarizer,
                              keep_recent_turns=6,
                              durable_path=durable)
    assert "SQLite WAL" in ctrl2.durable_block


def test_durable_persist_failure_does_not_raise(tmp_path):
    def summarizer(_prompt: str) -> str:
        return "---SUMMARY---\nsummary\n---DURABLE---\n- Decision: X"

    blocker = tmp_path / "blocker"
    blocker.write_text("occupied", encoding="utf-8")
    events = []
    msgs = _build_big_convo(20)
    ctrl = CompactController(summary_provider=summarizer,
                             on_event=lambda n, d: events.append(n),
                             keep_recent_turns=6,
                             durable_path=blocker / "durable.txt")
    ctrl.compact_if_needed(msgs, "offline")  # must not raise
    assert any(n == "durable_persist_failed" for n in events)
    # durable block still held in memory despite the failed write
    assert "Decision: X" in ctrl.durable_block


# --- P0-3: provider-token dual channel in the trigger -------------------------

def test_needs_compact_provider_channel_triggers():
    # gpt-4o window 128k -> threshold 96k. 1k estimate alone is far below it...
    assert needs_compact(1_000, "gpt-4o", 0) is False
    # ...but the provider's official prompt_tokens already exceed it -> trigger
    assert needs_compact(1_000, "gpt-4o", 0, provider_tokens=100_000) is True
    # provider channel does not require fixed_overhead (official count has it)
    assert needs_compact(0, "gpt-4o", 0, provider_tokens=100_000) is True
    # provider_tokens 0 / None -> fall back to estimate channel
    assert needs_compact(1_000, "gpt-4o", 0, provider_tokens=0) is False
    assert needs_compact(1_000, "gpt-4o", 0, provider_tokens=None) is False


def test_compact_if_needed_provider_channel_triggers():
    ctrl = CompactController()
    msgs = _build_convo(3)  # tiny, estimate way below threshold
    ctrl.compact_if_needed(msgs, "gpt-4o", 0, provider_tokens=120_000)
    assert ctrl.last_compacted is True
    # without provider channel, the same buffer would not compact
    ctrl2 = CompactController()
    msgs2 = _build_convo(3)
    ctrl2.compact_if_needed(msgs2, "gpt-4o", 0)
    assert ctrl2.last_compacted is False


# --- P0-4: context-overflow detection + force_reduce offload ------------------

def test_is_context_overflow_error_matches_provider_messages():
    from soul_buddy.providers.base import is_context_overflow_error

    class FakeErr(Exception):
        pass

    assert is_context_overflow_error(
        RuntimeError("This model's maximum context length is 128000 tokens")) is True
    assert is_context_overflow_error(
        Exception("Request too large")) is True
    assert is_context_overflow_error(
        Exception("bad gateway")) is False
    e = FakeErr("prompt is too long")
    e.code = "context_length_exceeded"
    assert is_context_overflow_error(e) is True


def test_force_reduce_offloads_dropped_turns(tmp_path):
    hist = tmp_path / "history" / "sess1.txt"
    msgs = _build_big_convo(30)
    ctrl = CompactController(keep_recent_turns=6, history_path=hist)
    ctrl.force_reduce(msgs)
    assert hist.is_file()
    body = hist.read_text(encoding="utf-8")
    assert "turn 0" in body  # an early dropped turn recoverable after force reduce
    # force_reduce keeps system + leading + most recent group only
    assistants = sum(1 for m in msgs if m["role"] == "assistant")
    assert assistants == 1


# --- P1-6: tool schema token cache --------------------------------------------

def test_tool_schema_token_cache_reuses_estimate():
    from soul_buddy.context.usage import (
        ContextUsageCalculator, _TOOL_SCHEMA_CACHE,
    )

    class Spec:
        def __init__(self, name, description, parameters):
            self.name = name
            self.description = description
            self.parameters = parameters

    spec = Spec("read_file", "read a file", {"path": {"type": "string"}})
    calc = ContextUsageCalculator("offline")
    t1 = calc._safe_estimate_tools([spec])
    # same tool schema -> cache hit, identical number
    t2 = calc._safe_estimate_tools([spec])
    assert t1 == t2
    # different schema still estimable, does not poison the cache key
    spec2 = Spec("write_file", "write a file", {"content": {"type": "string"}})
    t3 = calc._safe_estimate_tools([spec2])
    assert t3 > 0
    assert t1 != t3
    # cache did grow (only the new key was added)
    assert len(_TOOL_SCHEMA_CACHE) >= 2


def test_context_usage_calc_tool_total_matches_shared_estimator():
    from soul_buddy.context.usage import ContextUsageCalculator

    calc = ContextUsageCalculator("offline")
    assert calc.calc("sys", {}, [], [], 0).total > 0
    assert calc.calc("sys", {}, [{"role": "user", "content": "hi"}], [], 0).messages > 0


# --- P1-7: unified estimated_total in the trigger -----------------------------

def test_needs_compact_uses_estimated_total_when_provided():
    # offline window 8k -> threshold 6k. 1k text estimate is below it...
    assert needs_compact(1_000, "offline", 0) is False
    # ...but the display-side estimated_total already counts it as over -> trigger
    assert needs_compact(1_000, "offline", 0, estimated_total=8_000) is True
    # provider channel still independent of estimated_total
    assert needs_compact(1_000, "offline", 0, provider_tokens=7_000,
                         estimated_total=0) is True
