import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  api,
  type RubricDimension,
  type RubricReportRow,
  type RubricSummary,
} from '../api'
import { Icon } from './Icon'

interface Props {
  onToast: (msg: string, tone?: 'ok' | 'err' | 'info') => void
}

/** 质量维度满分档（Q 维 0-3） */
const MAX_QUALITY = 3

function pct(v: number | null | undefined, digits = 0): string {
  return v == null ? '—' : `${(v * 100).toFixed(digits)}%`
}

function fmtTime(ts: number): string {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  return d.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

/** 质量分 → 语义色。3 优秀 / 2 良好 / 1 及格 / 0 待改进 */
function scoreTone(id: string, score: number | null): 'ok' | 'warn' | 'danger' | 'neutral' {
  if (score == null) return 'neutral'
  if (id.startsWith('G')) return score >= 1 ? 'ok' : 'danger'
  if (score >= 3) return 'ok'
  if (score === 2) return 'warn'
  if (score === 1) return 'warn'
  return 'danger'
}

function scoreLabel(id: string, score: number | null, applicable: boolean): string {
  if (!applicable || score == null) return '—'
  if (id.startsWith('G')) return score >= 1 ? '通过' : '未通过'
  return `${score}/${MAX_QUALITY}`
}

/** 数值点阵：把 0-3 分渲染成 3 个点，一眼看出档位 */
function ScoreDots({ score }: { score: number | null }) {
  if (score == null) return <span className="rb-dots muted"><i /><i /><i /></span>
  return (
    <span className={`rb-dots s${score}`}>
      {[0, 1, 2].map((i) => <i key={i} className={i < score ? 'on' : ''} />)}
    </span>
  )
}

export function RubricPanel({ onToast }: Props) {
  const [summary, setSummary] = useState<RubricSummary | null>(null)
  const [reports, setReports] = useState<RubricReportRow[]>([])
  const [loading, setLoading] = useState(true)
  const [detail, setDetail] = useState<RubricReportRow | null>(null)
  const [modeFilter, setModeFilter] = useState<string>('')   // '' = 全部

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [s, r] = await Promise.all([
        api.getRubricSummary(),
        api.listRubricReports(300, 0, modeFilter || undefined),
      ])
      setSummary(s)
      setReports(r.reports || [])
    } catch (e: any) {
      onToast(`加载 Rubric 报告失败：${e?.detail || e?.message || e}`, 'err')
    } finally {
      setLoading(false)
    }
  }, [modeFilter, onToast])

  useEffect(() => { load() }, [load])

  // 维度元信息（来自 summary，缺失时用内置兜底）
  const qualityMeta = summary?.dimension_meta?.quality ?? []
  const gatingMeta = summary?.dimension_meta?.gating ?? []

  /** 当前筛选下的通过率与平均分（用于顶部卡片，随筛选联动） */
  const filtered = useMemo(() => {
    const list = reports.map((r) => r.report)
    const n = list.length
    const passed = list.filter((r) => r.passed).length
    const scored = list.filter((r) => typeof r.total === 'number')
    const avg = scored.length
      ? scored.reduce((s, r) => s + (r.total || 0), 0) / scored.length
      : null
    const safety = list.filter((r) => r.safety_violation).length
    const retries = list.reduce((s, r) => s + (r.retries_used || 0), 0)
    return { n, passed, passRate: n ? passed / n : null, avg, safety, retries }
  }, [reports])

  if (loading && !summary) {
    return (
      <div className="plugin-panel">
        <div className="plugin-topbar">
          <div className="plugin-title">
            <div className="plugin-title-icon" style={{ background: 'var(--tool-soft)', color: 'var(--tool)' }}>
              <Icon name="shield" size={16} />
            </div>
            <div>
              <h2>Rubric</h2>
              <p>运行时交付验收 · 能力画像</p>
            </div>
          </div>
        </div>
        <div className="plugin-body">
          <div className="plugin-empty">
            <Icon name="refresh" size={28} />
            <p>加载中...</p>
          </div>
        </div>
      </div>
    )
  }

  const noData = !summary || summary.runs === 0

  return (
    <div className="plugin-panel">
      <div className="plugin-topbar">
        <div className="plugin-title">
          <div className="plugin-title-icon" style={{ background: 'var(--tool-soft)', color: 'var(--tool)' }}>
            <Icon name="shield" size={16} />
          </div>
          <div>
            <h2>Rubric</h2>
            <p>运行时交付验收 · 每一次收尾都会打分</p>
          </div>
        </div>

        <div className="plugin-stats">
          <div className="plugin-stat">
            <span className="ps-value">{summary?.runs ?? 0}</span>
            <span className="ps-label">报告数</span>
          </div>
          <div className="plugin-stat-divider" />
          <div className="plugin-stat">
            <span className="ps-value">{summary?.passed ?? 0}</span>
            <span className="ps-label">通过</span>
          </div>
          <div className="plugin-stat-divider" />
          <div className="plugin-stat">
            <span className="ps-value">
              {summary?.average_total != null ? Math.round(summary.average_total) : '—'}
            </span>
            <span className="ps-label">平均分</span>
          </div>
        </div>
      </div>

      <div className="plugin-body scroll">
        {noData ? (
          <div className="plugin-empty">
            <Icon name="shield" size={28} />
            <p>还没有 Rubric 报告</p>
            <span>
              设置 <code>SOUL_RUBRIC_MODE=advisory</code>（或 <code>enforce</code>）后
              跑一次会话即可产出报告
            </span>
          </div>
        ) : (
          <>
            {/* ── 1. 能力总览：§7.3 指标 + 通过率 ─────────────────────── */}
            <section className="rb-section">
              <div className="rb-section-head">
                <h3>能力总览</h3>
                <span className="rb-section-sub">
                  基于 {summary!.runs} 次运行 · 模式 {summary!.config_mode}
                </span>
              </div>
              <div className="rb-cards">
                <MetricCard
                  label="任务完成率"
                  value={pct(summary!.task_completion_rate)}
                  hint="G2 通过率（实际落盘）"
                  target={`阈值 ≥ ${pct(summary!.thresholds.completion)}`}
                  ok={summary!.task_completion_rate != null
                    && summary!.task_completion_rate >= summary!.thresholds.completion}
                />
                <MetricCard
                  label="工具选择正确率"
                  value={pct(summary!.tool_selection_accuracy)}
                  hint="Q1 ≥ 2 的占比"
                  target={`阈值 ≥ ${pct(summary!.thresholds.tool_selection)}`}
                  ok={summary!.tool_selection_accuracy != null
                    && summary!.tool_selection_accuracy >= summary!.thresholds.tool_selection}
                />
                <MetricCard
                  label="注入防护"
                  value={`${summary!.safety_violations + summary!.g1_failures}`}
                  hint="G1 违反 + 安全违规（必须为 0）"
                  target="阈值 = 0"
                  ok={summary!.safety_violations + summary!.g1_failures === 0}
                />
                <MetricCard
                  label="平均总分"
                  value={summary!.average_total != null ? summary!.average_total.toFixed(1) : '—'}
                  hint="所有报告的加权总分均值"
                  target="/ 100"
                  ok={(summary!.average_total ?? 0) >= 70}
                />
              </div>
            </section>

            {/* ── 2. 硬门槛：G1/G2/G3 通过率 ───────────────────────────── */}
            <section className="rb-section">
              <div className="rb-section-head">
                <h3>硬门槛（Gating）</h3>
                <span className="rb-section-sub">任一维度为 0 即判定不通过</span>
              </div>
              <div className="rb-gates">
                {gatingMeta.map((g) => {
                  const avg = summary!.dimension_averages[g.id]
                  const hit = summary!.dimension_counts[g.id] ?? 0
                  const rate = avg == null ? null : avg
                  return (
                    <div key={g.id} className={`rb-gate ${rate == null ? 'na' : rate >= 0.999 ? 'ok' : 'bad'}`}>
                      <div className="rb-gate-top">
                        <span className="rb-gate-id">{g.id}</span>
                        <span className="rb-gate-rate">{pct(rate)}</span>
                      </div>
                      <div className="rb-gate-name">{g.name}</div>
                      <div className="rb-gate-bar">
                        <i style={{ width: `${(rate ?? 0) * 100}%` }} />
                      </div>
                      <div className="rb-gate-foot">{g.desc} · 样本 {hit}/{summary!.runs}</div>
                    </div>
                  )
                })}
              </div>
            </section>

            {/* ── 3. 质量维度雷达式条形 ────────────────────────────────── */}
            <section className="rb-section">
              <div className="rb-section-head">
                <h3>质量维度</h3>
                <span className="rb-section-sub">归一化到满分档（0–3）</span>
              </div>
              <div className="rb-bars">
                {qualityMeta.map((q) => {
                  const avg = summary!.dimension_averages[q.id]
                  const hit = summary!.dimension_counts[q.id] ?? 0
                  const ratio = avg == null ? 0 : avg / MAX_QUALITY
                  const tone = avg == null ? 'neutral'
                    : avg >= 2.5 ? 'ok' : avg >= 1.5 ? 'warn' : 'danger'
                  return (
                    <div key={q.id} className="rb-bar-row">
                      <div className="rb-bar-label">
                        <span className="rb-bar-id">{q.id}</span>
                        <span className="rb-bar-name">{q.name}</span>
                        {q.weight != null && (
                          <span className="rb-bar-weight">权重 {Math.round(q.weight * 100)}%</span>
                        )}
                      </div>
                      <div className={`rb-bar-track ${tone}`}>
                        <i style={{ width: `${ratio * 100}%` }} />
                      </div>
                      <div className="rb-bar-val">
                        {avg == null ? '—' : avg.toFixed(2)}
                        <span className="rb-bar-hit">{hit}/{summary!.runs}</span>
                      </div>
                    </div>
                  )
                })}
              </div>
              <p className="rb-note">
                未被采集到的维度（如无错误时的 Q3、judge 降级时的 Q5/Q6）不计入分母，
                “样本”列即为命中数 / 报告总数。
              </p>
            </section>

            {/* ── 4. 运行明细 ──────────────────────────────────────────── */}
            <section className="rb-section">
              <div className="rb-section-head">
                <h3>运行明细</h3>
                <div className="rb-filter">
                  {['', 'advisory', 'enforce'].map((m) => (
                    <button
                      key={m || 'all'}
                      className={`rb-chip ${modeFilter === m ? 'active' : ''}`}
                      onClick={() => setModeFilter(m)}
                    >
                      {m === '' ? '全部' : m}
                    </button>
                  ))}
                </div>
              </div>

              {filtered.n === 0 ? (
                <div className="rb-empty-line">当前筛选下没有报告</div>
              ) : (
                <>
                  <div className="rb-run-strip">
                    <span>筛选结果 <b>{filtered.n}</b> 条</span>
                    <span>通过率 <b>{pct(filtered.passRate)}</b></span>
                    <span>平均分 <b>{filtered.avg != null ? filtered.avg.toFixed(1) : '—'}</b></span>
                    {filtered.safety > 0 && (
                      <span className="danger">安全违规 <b>{filtered.safety}</b></span>
                    )}
                    <span>重试 <b>{filtered.retries}</b> 次</span>
                  </div>

                  <div className="rb-table">
                    <div className="rb-thead">
                      <span>时间</span>
                      <span>结论</span>
                      <span>总分</span>
                      <span>G1</span>
                      <span>G2</span>
                      <span>G3</span>
                      <span>模式</span>
                      <span />
                    </div>
                    {reports.map((row) => {
                      const r = row.report
                      const g = (id: string) =>
                        (r.gating || []).find((d) => d.id === id)
                      const gcell = (id: string) => {
                        const d = g(id)
                        const tone = scoreTone(id, d?.score ?? null)
                        return <span className={`rb-cell-badge ${tone}`}>{d?.score ?? '—'}</span>
                      }
                      return (
                        <div key={row.path} className="rb-trow" onClick={() => setDetail(row)}>
                          <span className="rb-t-time">{fmtTime(row.mtime)}</span>
                          <span className={`rb-verdict ${r.passed ? 'ok' : 'bad'}`}>
                            <Icon name={r.passed ? 'check' : 'x'} size={12} />
                            {r.passed ? '通过' : '未通过'}
                          </span>
                          <span className="rb-t-total">{r.total}</span>
                          {gcell('G1')}{gcell('G2')}{gcell('G3')}
                          <span className="rb-t-mode">
                            {r.mode}
                            {r.degraded && <span className="rb-tag warn" title="LLM judge 未执行">降级</span>}
                            {r.safety_violation && <span className="rb-tag danger">安全</span>}
                            {r.retries_used > 0 && <span className="rb-tag">重试{r.retries_used}</span>}
                          </span>
                          <span className="rb-t-chev"><Icon name="chevron-right" size={14} /></span>
                        </div>
                      )
                    })}
                  </div>
                </>
              )}
            </section>
          </>
        )}
      </div>

      <div className="plugin-footer">
        <button className="primary" onClick={load}>
          <Icon name="refresh" size={14} />
          刷新
        </button>
      </div>

      {detail && <RubricDetail row={detail} onClose={() => setDetail(null)} />}
    </div>
  )
}

/** 总览卡片：数值 + 阈值判定 */
function MetricCard({
  label, value, hint, target, ok,
}: { label: string; value: string; hint: string; target: string; ok: boolean }) {
  return (
    <div className={`rb-card ${ok ? 'ok' : 'bad'}`}>
      <div className="rb-card-top">
        <span className="rb-card-label">{label}</span>
        <span className={`rb-card-dot ${ok ? 'ok' : 'bad'}`} />
      </div>
      <div className="rb-card-value">{value}</div>
      <div className="rb-card-hint">{hint}</div>
      <div className="rb-card-target">{target}</div>
    </div>
  )
}

/** 单次运行报告详情弹层 */
function RubricDetail({ row, onClose }: { row: RubricReportRow; onClose: () => void }) {
  const r = row.report
  const dim = (d: RubricDimension) => (
    <div key={d.id} className={`rb-dim ${scoreTone(d.id, d.score)}`}>
      <div className="rb-dim-left">
        <span className="rb-dim-id">{d.id}</span>
        <div className="rb-dim-info">
          <span className="rb-dim-name">
            {d.name}
            {!d.applicable && <span className="rb-tag neutral">不适用</span>}
            {d.judge === 'llm' && <span className="rb-tag info">LLM</span>}
          </span>
          <span className="rb-dim-reason">{d.reason}</span>
        </div>
      </div>
      <div className="rb-dim-right">
        <ScoreDots score={d.applicable ? d.score : null} />
        <span className="rb-dim-score">{scoreLabel(d.id, d.score, d.applicable)}</span>
      </div>
    </div>
  )

  return (
    <div className="rb-modal-mask" onClick={onClose}>
      <div className="rb-modal" onClick={(e) => e.stopPropagation()}>
        <div className="rb-modal-head">
          <div>
            <h3>
              运行报告
              <span className={`rb-verdict ${r.passed ? 'ok' : 'bad'}`}>
                <Icon name={r.passed ? 'check' : 'x'} size={12} />
                {r.passed ? '通过' : '未通过'}
              </span>
            </h3>
            <p>
              {fmtTime(row.mtime)} · session {row.session_id.slice(0, 8)} · mode {r.mode}
            </p>
          </div>
          <div className="rb-modal-total">
            <span className="rb-modal-score">{r.total}</span>
            <span className="rb-modal-thr">/ 阈值 {r.threshold}</span>
          </div>
        </div>

        <div className="rb-modal-body scroll">
          <div className="rb-modal-tags">
            {r.degraded && <span className="rb-tag warn">LLM judge 降级</span>}
            {r.safety_violation && <span className="rb-tag danger">安全违规</span>}
            {r.retries_used > 0 && <span className="rb-tag">重试 {r.retries_used} 次</span>}
            {r.failed_gating.length > 0 && (
              <span className="rb-tag danger">未通过门槛：{r.failed_gating.join(' / ')}</span>
            )}
            {!r.degraded && !r.safety_violation && r.failed_gating.length === 0 && (
              <span className="rb-tag ok">全部检查通过</span>
            )}
          </div>

          <div className="rb-modal-group">硬门槛</div>
          {(r.gating || []).map(dim)}

          <div className="rb-modal-group">质量维度</div>
          {(r.quality || []).map(dim)}

          {r.detail && (
            <>
              <div className="rb-modal-group">备注</div>
              <div className="rb-modal-detail">{r.detail}</div>
            </>
          )}

          <div className="rb-modal-path">{row.path}</div>
        </div>

        <div className="rb-modal-foot">
          <button className="primary" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  )
}
