import { useEffect, useState } from 'react'
import { Icon } from './Icon'
import { api } from '../api'

interface McpConnector {
  name: string
  status: string          // disconnected | trusted | connected | connecting | error:*
  trusted: boolean
  tools: string[]         // namespaced tool names, e.g. ["mcp__github__list_repos"]
}

interface Props {
  onToast: (msg: string, tone?: 'ok' | 'err' | 'info') => void
}

/** 返回 UI 友好的状态标签和颜色。 */
function statusMeta(status: string) {
  if (status === 'connected') return { label: '已连接', className: 'running' }
  if (status === 'connecting') return { label: '连接中', className: 'running' }
  if (status === 'trusted') return { label: '已信任', className: 'stopped' }
  if (status.startsWith('error')) return { label: '错误', className: 'error' }
  return { label: '未连接', className: 'stopped' }
}

/** 是否算"已启用"（toggle 开）—— trusted 或 connected 或 connecting 都算。 */
function isEnabled(status: string, trusted: boolean) {
  return trusted && status !== 'disconnected'
}

/** 从 tool name 推断图标。 */
function guessIcon(name: string): string {
  const t = name.toLowerCase()
  if (t.includes('github')) return 'zap'
  if (t.includes('git')) return 'folder'
  if (t.includes('file')) return 'file'
  if (t.includes('brave') || t.includes('web')) return 'link'
  return 'plug'
}

export function McpPanel({ onToast }: Props) {
  const [connectors, setConnectors] = useState<McpConnector[]>([])
  const [loading, setLoading] = useState(true)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [connecting, setConnecting] = useState<Record<string, boolean>>({})

  const loadConnectors = async () => {
    try {
      const res = await api.listConnectors()
      const list = (res.connectors || []).map((c) => ({
        name: c.name,
        status: c.status,
        trusted: c.trusted,
        tools: c.tools,
      }))
      setConnectors(list)
    } catch (e: any) {
      onToast(`加载 MCP 失败：${e?.detail || e?.message || e}`, 'err')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadConnectors()
  }, [])

  const toggleConnector = async (name: string) => {
    const conn = connectors.find((c) => c.name === name)
    if (!conn) return

    if (isEnabled(conn.status, conn.trusted)) {
      // 关闭：disconnect
      setConnecting((p) => ({ ...p, [name]: true }))
      try {
        await api.disconnectConnector(name)
        onToast(`已断开：${name}`, 'info')
      } catch (e: any) {
        onToast(`断开失败：${e?.detail || e?.message || e}`, 'err')
      } finally {
        setConnecting((p) => ({ ...p, [name]: false }))
        loadConnectors()
      }
    } else {
      // 开启：先 trust 再 connect
      setConnecting((p) => ({ ...p, [name]: true }))
      try {
        // 如果还没 trust，先 trust
        if (!conn.trusted) {
          await api.trustConnector(name)
        }
        await api.connectConnector(name)
        onToast(`已连接：${name}`, 'ok')
      } catch (e: any) {
        onToast(`连接失败：${e?.detail || e?.message || e}`, 'err')
      } finally {
        setConnecting((p) => ({ ...p, [name]: false }))
        loadConnectors()
      }
    }
  }

  const toggleExpand = (name: string) => {
    setExpandedId((prev) => (prev === name ? null : name))
  }

  const runningCount = connectors.filter((c) => c.status === 'connected').length
  const totalTools = connectors.reduce((sum, c) => sum + (c.status === 'connected' ? c.tools.length : 0), 0)

  return (
    <div className="plugin-panel">
      <div className="plugin-topbar">
        <div className="plugin-title">
          <div className="plugin-title-icon" style={{ background: 'var(--ok-soft)', color: 'var(--ok)' }}>
            <Icon name="plug" size={16} />
          </div>
          <div>
            <h2>MCP</h2>
            <p>Model Context Protocol — 连接外部工具和数据源</p>
          </div>
        </div>
        <div className="plugin-stats">
          <div className="plugin-stat">
            <span className="ps-value">{runningCount}</span>
            <span className="ps-label">运行中</span>
          </div>
          <div className="plugin-stat-divider" />
          <div className="plugin-stat">
            <span className="ps-value">{totalTools}</span>
            <span className="ps-label">可用工具</span>
          </div>
        </div>
      </div>

      <div className="plugin-body scroll">
        {loading ? (
          <div className="plugin-empty">
            <Icon name="refresh" size={28} />
            <p>加载中...</p>
          </div>
        ) : connectors.length === 0 ? (
          <div className="plugin-empty">
            <Icon name="plug" size={28} />
            <p>还没有 MCP 连接器</p>
            <span>在 ~/.soul_buddy/mcp.json 中配置 mcpServers</span>
          </div>
        ) : (
          <div className="plugin-list">
            {connectors.map((conn) => {
              const expanded = expandedId === conn.name
              const meta = statusMeta(conn.status)
              const enabled = isEnabled(conn.status, conn.trusted)
              const busy = !!connecting[conn.name]
              return (
                <div key={conn.name} className={`plugin-row mcp ${enabled ? '' : 'disabled'}`}>
                  <div
                    className="plugin-row-main"
                    onClick={() => toggleExpand(conn.name)}
                  >
                    <div className="plugin-row-chev">
                      <Icon name={expanded ? 'chevron-down' : 'chevron-right'} size={14} />
                    </div>
                    <div className={`mcp-status-dot ${meta.className}`} />
                    <div className="plugin-row-info">
                      <div className="plugin-row-name">
                        {conn.name}
                        <span className="plugin-badge">{conn.tools.length} tools</span>
                        {!enabled && <span className="plugin-badge disabled">未启用</span>}
                      </div>
                      <div className="plugin-row-sub">{meta.label}</div>
                    </div>
                    <div className="plugin-row-actions" onClick={(e) => e.stopPropagation()}>
                      <label className="plugin-switch">
                        <input
                          type="checkbox"
                          checked={enabled}
                          disabled={busy}
                          onChange={() => toggleConnector(conn.name)}
                        />
                        <span className="plugin-switch-slider" />
                      </label>
                    </div>
                  </div>

                  {expanded && (
                    <div className="mcp-details">
                      {conn.status.startsWith('error') && (
                        <div className="mcp-detail-row">
                          <span className="mcp-detail-label">错误信息</span>
                          <code className="mcp-detail-code">{conn.status}</code>
                        </div>
                      )}
                      <div className="mcp-detail-row">
                        <span className="mcp-detail-label">状态</span>
                        <span className={`pill ${meta.className === 'running' ? 'ok' : meta.className === 'error' ? 'danger' : 'neutral'}`}>
                          {meta.className === 'running' && <span className="pulse" />}
                          {meta.label}
                        </span>
                      </div>
                      {conn.status === 'connected' && conn.tools.length > 0 && (
                        <div className="mcp-detail-row">
                          <span className="mcp-detail-label">工具列表</span>
                          <div className="mcp-detail-tools">
                            {conn.tools.map((t) => (
                              <span key={t} className="mcp-tool-tag">{t}</span>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      <div className="plugin-footer">
        <button className="primary" onClick={() => loadConnectors()}>
          <Icon name="refresh" size={14} />
          刷新
        </button>
      </div>
    </div>
  )
}
