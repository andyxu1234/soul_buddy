import { useCallback, useEffect, useState } from 'react'
import { api, type TracingStatus } from '../api'
import { Icon } from './Icon'

interface Props {
  onToast: (msg: string, tone?: 'ok' | 'err' | 'info') => void
}

/** LangSmith 入口面板 — 追踪状态 + 连接信息 + 跳转控制台。 */
export function LangSmithPanel({ onToast }: Props) {
  const [status, setStatus] = useState<TracingStatus | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setStatus(await api.getTracingStatus())
    } catch (e: any) {
      onToast(`加载 LangSmith 状态失败：${e?.detail || e?.message || e}`, 'err')
    } finally {
      setLoading(false)
    }
  }, [onToast])

  useEffect(() => { load() }, [load])

  const state = !status
    ? { label: '未知', tone: 'neutral' as const }
    : !status.sdk_available
      ? { label: '未安装', tone: 'danger' as const }
      : !status.tracing_flag
        ? { label: '已关闭', tone: 'neutral' as const }
        : !status.api_key_set
          ? { label: '缺少 Key', tone: 'warn' as const }
          : { label: '追踪中', tone: 'ok' as const }

  const openConsole = () => {
    const url = status?.project_url || status?.console_url
    if (!url) return
    window.open(url, '_blank', 'noopener,noreferrer')
  }

  return (
    <div className="plugin-panel">
      <div className="plugin-topbar">
        <div className="plugin-title">
          <div className="plugin-title-icon" style={{ background: 'var(--brand-soft)', color: 'var(--brand-fg)' }}>
            <Icon name="activity" size={16} />
          </div>
          <div>
            <h2>LangSmith</h2>
            <p>LLM 调用追踪与评测 · trace 嵌套视图</p>
          </div>
        </div>
        <div className="attu-topbar-actions">
          <span className={`ls-status-pill ${state.tone}`}>
            <span className="ls-status-dot" />
            {state.label}
          </span>
          <button className="btn-primary attu-open-btn" onClick={openConsole} disabled={loading}>
            <Icon name="external" size={14} />
            打开控制台
          </button>
        </div>
      </div>

      <div className="plugin-body attu-info-body">
        {!loading && status && !status.ready && status.reason && (
          <div className="ls-warn-card">
            <Icon name="alert" size={16} />
            <span>{status.reason}</span>
          </div>
        )}

        <div className="attu-info-card">
          <h3>连接信息</h3>
          <div className="attu-info-grid">
            <div className="attu-info-row">
              <span className="attu-info-label">项目</span>
              <code>{status?.project || '—'}</code>
            </div>
            <div className="attu-info-row">
              <span className="attu-info-label">采集端点</span>
              <code>{status?.endpoint || '—'}</code>
            </div>
            <div className="attu-info-row">
              <span className="attu-info-label">控制台</span>
              <code>{status?.console_url || '—'}</code>
            </div>
            <div className="attu-info-row">
              <span className="attu-info-label">API Key</span>
              <code>{status ? (status.api_key_set ? '已配置' : '未配置') : '—'}</code>
            </div>
          </div>
        </div>

        <div className="attu-info-card">
          <h3>追踪范围</h3>
          <div className="ls-scope">
            <div className="ls-scope-row">
              <Icon name="shield" size={15} />
              <div>
                <code>SoulAgent.run</code>
                <span>每次运行的根 chain，串联该次请求的全部步骤</span>
              </div>
            </div>
            <div className="ls-scope-row">
              <Icon name="brain" size={15} />
              <div>
                <code>LLM 调用</code>
                <span>DeepSeek / OpenAI / Anthropic 的 messages.create 自动埋点</span>
              </div>
            </div>
            <div className="ls-scope-row">
              <Icon name="wrench" size={15} />
              <div>
                <code>工具与压缩</code>
                <span>子调用作为子 run 挂在根 trace 下，形成嵌套树</span>
              </div>
            </div>
          </div>
        </div>

        <div className="attu-info-card">
          <h3>如何开启</h3>
          <ol className="attu-steps">
            <li>在 <code>.env</code>（或 <code>~/.soul_buddy/.env</code>）设置 <code>LANGSMITH_TRACING=true</code></li>
            <li>填写 <code>LANGSMITH_API_KEY</code>（在 LangSmith 设置页生成）</li>
            <li>可选：<code>LANGSMITH_PROJECT</code> 指定项目名，默认 <code>soul-buddy</code></li>
            <li>重启应用后本页状态变为「追踪中」，运行一次任务即可看到 trace</li>
          </ol>
        </div>

        <div className="attu-info-card attu-tip">
          <Icon name="info" size={16} />
          <span>
            追踪默认关闭（<code>LANGSMITH_TRACING=false</code>），未开启时
            <code>wrap_openai</code> / <code>wrap_anthropic</code> 均为零开销透传，不影响正常调用。
            本页仅展示状态，不读取、不回显 API Key。
          </span>
        </div>
      </div>

      <div className="plugin-footer">
        <button className="primary" onClick={load}>
          <Icon name="refresh" size={14} />
          刷新
        </button>
      </div>
    </div>
  )
}
