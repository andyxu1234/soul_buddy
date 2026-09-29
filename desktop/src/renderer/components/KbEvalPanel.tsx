import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type KbEvalJob, type KbEvalReport, type KbEvalReportRow,
  type KbEvalSetInfo, type KbOnlineSummary, type KbRow } from '../api'
import { Icon } from './Icon'

interface Props {
  kbId: string
  kbName: string
  onToast: (msg: string, tone?: 'ok' | 'err' | 'info') => void
}

function errText(e: unknown): string {
  const anyErr = e as { detail?: unknown }
  if (anyErr && typeof anyErr === 'object' && 'detail' in anyErr) {
    const d = anyErr.detail
    if (typeof d === 'string') return d
    if (d != null) return JSON.stringify(d)
  }
  return String(e)
}

function fmtTime(ts: number): string {
  const d = new Date(ts * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}`
}

function fmt3(v: number | null | undefined): string {
  return typeof v === 'number' ? v.toFixed(3) : '—'
}

/** 单个指标卡:大数字 + 标签。 */
function MetricCard({ label, value, sub, tone }: {
  label: string; value: string; sub?: string; tone?: 'good' | 'warn' | 'bad'
}) {
  return (
    <div className={`kbe-card ${tone ? `tone-${tone}` : ''}`}>
      <div className="kbe-card-value">{value}</div>
      <div className="kbe-card-label">{label}</div>
      {sub && <div className="kbe-card-sub">{sub}</div>}
    </div>
  )
}

function toneOf(v: number | null | undefined): 'good' | 'warn' | 'bad' | undefined {
  if (typeof v !== 'number') return undefined
  if (v >= 0.8) return 'good'
  if (v >= 0.5) return 'warn'
  return 'bad'
}

/** 知识库「评估」页签:运行评估 + 指标总览 + 报告历史 + bad cases。 */
export function KbEvalPanel({ kbId, kbName, onToast }: Props) {
  const [evalSet, setEvalSet] = useState<KbEvalSetInfo | null>(null)
  const [job, setJob] = useState<KbEvalJob | null>(null)
  const [report, setReport] = useState<KbEvalReport | null>(null)
  const [reports, setReports] = useState<KbEvalReportRow[]>([])
  const [withRagas, setWithRagas] = useState(true)
  const [onlineKey, setOnlineKey] = useState(0)
  const [starting, setStarting] = useState(false)
  const pollRef = useRef<number | null>(null)

  const reloadReports = useCallback(async () => {
    try {
      const [list, latest] = await Promise.all([
        api.listKbEvalReports(kbId), api.getLatestKbEvalReport(kbId)])
      setReports(list.reports)
      setReport(latest.report)
      setJob((j) => {
        const lj = latest.job as KbEvalJob | undefined
        // 运行中以后端为准;空闲且本地记着 done/error 时保留本地状态细节
        if (lj && lj.status && lj.status !== 'idle') return lj
        return j && j.status !== 'running' ? j : (lj ?? j ?? null)
      })
    } catch (e) {
      onToast(`加载评估报告失败：${errText(e)}`, 'err')
    }
  }, [kbId, onToast])

  const reloadEvalSet = useCallback(async () => {
    try {
      setEvalSet(await api.getKbEvalSet(kbId))
    } catch {
      setEvalSet(null)
    }
  }, [kbId])

  useEffect(() => {
    setEvalSet(null); setJob(null); setReport(null); setReports([])
    void reloadEvalSet()
    void reloadReports()
  }, [kbId, reloadEvalSet, reloadReports])

  // 运行中每 2s 轮询状态,结束后拉一次报告
  useEffect(() => {
    if (job?.status !== 'running') {
      if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null }
      return
    }
    pollRef.current = window.setInterval(async () => {
      try {
        const s = await api.getKbEvalStatus(kbId)
        setJob(s)
        if (s.status === 'done') {
          onToast('评估完成', 'ok')
          void reloadReports()
          void reloadEvalSet()
          setOnlineKey((k) => k + 1)
        } else if (s.status === 'error') {
          onToast(`评估失败：${s.error ?? '未知错误'}`, 'err')
          void reloadReports()
        }
      } catch { /* 轮询失败静默,下轮再试 */ }
    }, 2000)
    return () => {
      if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null }
    }
  }, [job?.status, kbId, onToast, reloadReports, reloadEvalSet])

  const running = job?.status === 'running'

  const handleRun = async () => {
    if (running || starting) return
    setStarting(true)
    try {
      await api.startKbEval(kbId, { with_ragas: withRagas })
      setJob({ status: 'running' })
      onToast('评估已启动，后台运行中', 'ok')
    } catch (e) {
      onToast(`启动失败：${errText(e)}`, 'err')
    } finally {
      setStarting(false)
    }
  }

  const handleCancel = async () => {
    try {
      await api.cancelKbEval(kbId)
      onToast('已请求取消，当前题目结束后停止', 'info')
    } catch (e) {
      onToast(`取消失败：${errText(e)}`, 'err')
    }
  }

  const openReport = (row: KbEvalReportRow) => {
    // 历史行是轻量摘要:回填成报告形状(无 bad cases/逐题明细)
    setReport({
      meta: { ...row.meta, report_path: row.path },
      retrieval: row.retrieval as KbEvalReport['retrieval'],
      ragas: { enabled: !!row.ragas, metrics: row.ragas ?? undefined },
      mechanical: {
        refuse_ok: row.mechanical?.refuse_ok ?? null,
        refuse_total: row.mechanical?.refuse_total ?? 0,
        cite_ok: row.mechanical?.cite_ok ?? null,
        cite_total: row.mechanical?.cite_total ?? 0,
      },
    })
  }

  const r = report?.retrieval
  const ragas = report?.ragas
  const mec = report?.mechanical
  const badCases: Array<{ type: string; q: string; detail?: unknown }> = [
    ...((r as unknown as { bad_cases?: typeof badCases })?.bad_cases ?? []),
    ...((mec as unknown as { bad_cases?: typeof badCases })?.bad_cases ?? []),
  ]

  return (
    <div className="kbe">
      <div className="kbe-toolbar">
        <button className="primary" onClick={() => void handleRun()}
                disabled={running || starting || !evalSet?.exists}>
          <Icon name={running ? 'clock' : 'sparkles'} size={14} />
          {running ? '评估中…' : '运行评估'}
        </button>
        <label className="kbe-opt">
          <input type="checkbox" checked={withRagas}
                 onChange={(e) => setWithRagas(e.target.checked)}
                 disabled={running} />
          生成层评估（RAGAS）
        </label>
        <span className="kbe-meta">
          {evalSet?.exists
            ? `评测集 ${evalSet.count} 条 · ${evalSet.hash?.slice(0, 8)}`
            : '评测集为空 —— 先用 CLI 造题：python -m soul_buddy.knowledge.eval_run gen --kb ' + kbId}
        </span>
        {running && (
          <>
            <span className="pill brand">
              <span className="pulse" />
              后台运行中{job?.stage && typeof job?.done === 'number'
                ? ` · ${job.stage} ${job.done}/${job.total ?? '…'}`
                : ''}
            </span>
            <button className="ibtn" title="取消评估（当前题目结束后停止）"
                    onClick={() => void handleCancel()}>
              <Icon name="x" size={14} />
            </button>
          </>
        )}
        {job?.status === 'cancelled' && (
          <span className="kbe-meta">已取消（可重新运行）</span>
        )}
      </div>

      {job?.status === 'error' && (
        <div className="kbe-errbox">评估失败：{job.error}</div>
      )}

      {report && r ? (
        <>
          <div className="kbe-report-head">
            <span className="kbe-report-title">
              {fmtTime(report.meta.created_at)} · {report.meta.n_items} 题 ·
              top_k={report.meta.top_k} · 评测集 {report.meta.evalset_hash.slice(0, 8)}
            </span>
            <span className="kbe-report-models">
              被测 {report.meta.tested_provider}/{report.meta.tested_model}
              {ragas?.enabled && ragas?.judge_model &&
                ` · judge ${ragas.judge_provider ?? ''}/${ragas.judge_model}`}
            </span>
          </div>

          <div className="kbe-section-title">检索（决定上限）</div>
          <div className="kbe-grid">
            <MetricCard label={`Hit@${r.top_k}`} value={fmt3(r['hit@k'])}
                        tone={toneOf(r['hit@k'])} sub={`gold 题 ${r.n_gold}`} />
            <MetricCard label={`Recall@${r.top_k}`} value={fmt3(r['recall@k'])}
                        tone={toneOf(r['recall@k'])} />
            <MetricCard label="MRR" value={fmt3(r.mrr)} tone={toneOf(r.mrr)} />
            <MetricCard label={`nDCG@${r.top_k}`} value={fmt3(r['ndcg@k'])}
                        tone={toneOf(r['ndcg@k'])} />
            <MetricCard label="延迟 p50" value={`${Math.round(r.latency_ms_p50)}ms`} />
            <MetricCard label="延迟 p95" value={`${Math.round(r.latency_ms_p95)}ms`} />
          </div>

          {r.by_type && Object.keys(r.by_type).length > 0 && (
            <div className="kbe-bytype">
              {Object.entries(r.by_type).map(([t, m]) => (
                <span key={t} className="kbe-bytype-row" title={`n_gold=${m.n_gold}`}>
                  <span className="kbe-bytype-name">{t}</span>
                  <span>Hit {fmt3(m['hit@k'])}</span>
                  <span>Recall {fmt3(m['recall@k'])}</span>
                </span>
              ))}
            </div>
          )}

          <div className="kbe-section-title">生成（决定可信度）</div>
          {ragas?.enabled && ragas.metrics ? (
            <div className="kbe-grid">
              {['faithfulness', 'answer_relevancy', 'context_recall',
                'context_precision'].map((k) => {
                const m = ragas.metrics?.[k]
                return <MetricCard key={k} label={k} value={fmt3(m?.mean)}
                  tone={toneOf(m?.mean)}
                  sub={m ? `n=${m.n} 错=${m.errors} 跳=${m.skipped}` : undefined} />
              })}
            </div>
          ) : (
            <div className="kbe-skipbox">
              RAGAS 未运行{ragas?.skipped ? `：${ragas.skipped}` : ''}
            </div>
          )}

          {mec && (mec.cite_ok != null || mec.refuse_ok != null) && (
            <div className="kbe-grid">
              {mec.cite_ok != null && (
                <MetricCard label="引用可核验" value={fmt3(mec.cite_ok)}
                            tone={toneOf(mec.cite_ok)} sub={`n=${mec.cite_total}`} />
              )}
              {mec.refuse_ok != null && (
                <MetricCard label="拒答正确" value={fmt3(mec.refuse_ok)}
                            tone={toneOf(mec.refuse_ok)}
                            sub={`n=${mec.refuse_total}`} />
              )}
            </div>
          )}

          {badCases.length > 0 && (
            <>
              <div className="kbe-section-title">bad cases（{badCases.length}）</div>
              <div className="kbe-badlist">
                {badCases.map((bc, i) => (
                  <div key={i} className="kbe-bad">
                    <span className="kbe-bad-type">{bc.type}</span>
                    <span className="kbe-bad-q">{bc.q}</span>
                    <span className="kbe-bad-detail">
                      {typeof bc.detail === 'string'
                        ? bc.detail : JSON.stringify(bc.detail)}
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}
        </>
      ) : (
        !running && (
          <div className="plugin-empty">
            <Icon name="sparkles" size={28} />
            <p>还没有评估报告</p>
            <span>评测集就绪后点「运行评估」；造题用 CLI gen 或手写 evals/{kbId}.json</span>
          </div>
        )
      )}

      <OnlineSection kbId={kbId} refreshKey={onlineKey}
                     onChanged={() => { void reloadEvalSet(); setOnlineKey((k) => k + 1) }}
                     onToast={onToast} />

      {reports.length > 0 && (
        <>
          <div className="kbe-section-title">历史报告</div>
          <div className="kbe-reports">
            {reports.map((row) => (
              <button key={row.path} className={`kbe-report-row ${
                report?.meta.report_path === row.path ? 'active' : ''}`}
                onClick={() => void openReport(row)}
                title={row.path}>
                <span className="kbe-report-time">{fmtTime(row.mtime)}</span>
                <span>n={row.meta?.n_items}</span>
                <span>Hit {fmt3(row.retrieval?.['hit@k'])}</span>
                <span>Faith {fmt3(row.ragas?.faithfulness?.mean)}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}


/** 在线回流区:真实 chat 检索事件的聚合展示。 */
function OnlineSection({ kbId, refreshKey, onChanged, onToast }: {
  kbId: string
  refreshKey: number
  onChanged: () => void
  onToast: (msg: string, tone?: 'ok' | 'err' | 'info') => void
}) {
  const [sum, setSum] = useState<KbOnlineSummary | null>(null)
  const [adding, setAdding] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    api.getKbEvalOnline(kbId)
      .then((s) => { if (!cancelled) setSum(s) })
      .catch(() => { if (!cancelled) setSum(null) })
    return () => { cancelled = true }
  }, [kbId, refreshKey])

  if (!sum || sum.total === 0) {
    return (
      <>
        <div className="kbe-section-title">在线回流</div>
        <div className="kbe-skipbox">
          还没有在线检索数据 —— 在聊天中挂载知识库并使用 search_knowledge 后，
          真实查询会自动回流到这里，形成零命中榜与块被引用率。
        </div>
      </>
    )
  }

  const addToEvalset = async (z: { id: number; query: string }) => {
    setAdding(z.id)
    try {
      const r = await api.appendKbEvalItem(kbId, {
        q: z.user_query || z.rewritten || '', source: 'online' })
      onToast(r.status === 'added' ? '已加入评测集' : '该题已在评测集中', 'ok')
      onChanged()
    } catch (e) {
      onToast(`加入失败：${errText(e)}`, 'err')
    } finally {
      setAdding(null)
    }
  }

  return (
    <>
      <div className="kbe-section-title">在线回流（真实 chat 检索）</div>
      <div className="kbe-grid">
        <MetricCard label="累计检索" value={String(sum.total)} />
        <MetricCard label="零命中率" value={sum.zero_hit_rate != null
          ? `${(sum.zero_hit_rate * 100).toFixed(1)}%` : '—'}
          tone={sum.zero_hit_rate != null && sum.zero_hit_rate > 0.3 ? 'bad'
            : sum.zero_hit_rate != null && sum.zero_hit_rate > 0.1 ? 'warn' : 'good'}
          sub={`零命中 ${sum.zero_hit_count} 次`} />
        <MetricCard label="平均延迟" value={sum.avg_latency_ms != null
          ? `${Math.round(sum.avg_latency_ms)}ms` : '—'} />
        <MetricCard label="块被引用率" value={sum.citation_rate != null
          ? `${(sum.citation_rate * 100).toFixed(1)}%` : '—'}
          tone={toneOf(sum.citation_rate)}
          sub={`出现过的块 ${sum.blocks_seen}`} />
      </div>

      {sum.zero_hit_list.length > 0 && (
        <>
          <div className="kbe-sub-title">零命中查询榜（真实问题检索不到 —— 一键加入评测集）</div>
          <div className="kbe-badlist">
            {sum.zero_hit_list.slice(0, 10).map((z) => (
              <div key={z.id} className="kbe-bad"
                   title={z.rewritten ? `检索词：${z.rewritten}` : undefined}>
                <span className="kbe-bad-type">零命中</span>
                <span className="kbe-bad-q">{z.user_query || z.rewritten || '(空)'}</span>
                <button className="kbe-add-btn" disabled={adding === z.id}
                        onClick={() => void addToEvalset(z)}>
                  {adding === z.id ? '加入中…' : '+ 加入评测集'}
                </button>
              </div>
            ))}
          </div>
        </>
      )}

      {sum.dead_blocks.length > 0 && (
        <>
          <div className="kbe-sub-title">从未被引用的块（死块清理候选）</div>
          <div className="kbe-badlist">
            {sum.dead_blocks.slice(0, 8).map((d, i) => (
              <div key={i} className="kbe-bad">
                <span className="kbe-bad-type">死块</span>
                <span className="kbe-bad-q">
                  {d.doc_name}{d.heading_path ? ` > ${d.heading_path}` : ''}
                </span>
                <span className="kbe-bad-detail">出现 {d.seen} 次 · 引用 0 次</span>
              </div>
            ))}
          </div>
        </>
      )}

      {sum.recent.length > 0 && (
        <>
          <div className="kbe-sub-title">最近检索事件</div>
          <div className="kbe-reports">
            {sum.recent.slice(0, 10).map((r) => (
              <div key={r.id} className="kbe-report-row" style={{ cursor: 'default' }}>
                <span className="kbe-report-time">{fmtTime(r.created_at)}</span>
                <span className="kbe-bad-q"
                      title={r.rewritten ? `检索词：${r.rewritten}` : undefined}>
                  {r.user_query || r.rewritten || '(空)'}
                </span>
                <span>{r.hit_count} 命中</span>
                <span>{Math.round(r.latency_ms)}ms</span>
                <span>{r.answered ? `引用 ${r.cited_pos.length}` : '待回填'}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </>
  )
}

/** 独立评估页(左栏「评估」页签):知识库选择器 + KbEvalPanel。 */
export function KbEvalPage({ onToast }: {
  onToast: (msg: string, tone?: 'ok' | 'err' | 'info') => void
}) {
  const [kbs, setKbs] = useState<KbRow[]>([])
  const [kbId, setKbId] = useState<string>('')

  useEffect(() => {
    let cancelled = false
    api.listKnowledgeBases()
      .then((res) => {
        if (cancelled) return
        setKbs(res.kbs)
        setKbId((cur) => cur
          || res.kbs.find((k) => k.id === 'default')?.id
          || res.kbs[0]?.id || '')
      })
      .catch((e) => onToast(`加载知识库失败：${errText(e)}`, 'err'))
    return () => { cancelled = true }
  }, [onToast])

  const active = kbs.find((k) => k.id === kbId)

  return (
    <div className="plugin-panel">
      <div className="plugin-topbar">
        <div className="plugin-title">
          <div className="plugin-title-icon"
               style={{ background: 'var(--brand-soft)', color: 'var(--brand-fg)' }}>
            <Icon name="sparkles" size={16} />
          </div>
          <div>
            <h2>RAG评估</h2>
            <p>离线评测知识库的检索与生成质量：Hit/Recall/nDCG + RAGAS 忠实度；改配置前后对比防回归</p>
          </div>
        </div>
        <select
          className="field-input kb-kbselect"
          value={kbId}
          onChange={(e) => setKbId(e.target.value)}
          title="选择要评估的知识库"
        >
          {kbs.map((k) => (
            <option key={k.id} value={k.id}>{k.name}（{k.document_count}）</option>
          ))}
        </select>
      </div>
      <div className="plugin-body scroll">
        {active ? (
          <KbEvalPanel kbId={active.id} kbName={active.name} onToast={onToast} />
        ) : (
          <div className="plugin-empty">
            <Icon name="sparkles" size={28} />
            <p>还没有知识库</p>
            <span>先到「知识库」页创建并上传文档</span>
          </div>
        )}
      </div>
    </div>
  )
}
