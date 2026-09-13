import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import type { SessionRecord } from '../types'

import { Icon } from './Icon'
import { EmotionBall } from './EmotionBall'

const SB_THEME = { body: '#3b82f6', eyes: '#0f172a' }
import { api, native } from '../api'



/** 空 workspace_root 视为默认工作区 */
export const DEFAULT_WORKSPACE = ''
export const DEFAULT_WORKSPACE_NAME = '默认工作区'

export interface SessionListProps {
  sessions: SessionRecord[]
  selectedId: string | null
  runningIds: Set<string>
  collapsed: boolean
  onSelect: (id: string) => void
  onNew: (workspaceRoot?: string) => void
  onOpenSettings: () => void
  onToggleCollapse: () => void
  onRename: (s: SessionRecord) => void
  onDelete: (s: SessionRecord) => void
  onDeleteWorkspaceSessions?: (workspace: string, name: string, count: number) => void
}

export function basename(p: string): string {
  if (!p) return DEFAULT_WORKSPACE_NAME
  const parts = p.replace(/[\\/]+$/, '').split(/[\\/]/)
  return parts[parts.length - 1] || DEFAULT_WORKSPACE_NAME
}

export function sessionTitle(s: SessionRecord): string {
  return s.title || basename(s.workspace_root)
}

export function formatTime(ts: number): string {
  const now = Date.now() / 1000
  const diff = now - ts
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  if (diff < 7 * 86400) return `${Math.floor(diff / 86400)} 天前`
  return new Date(ts * 1000).toLocaleDateString()
}

/** 按 workspace_root 分组 sessions，每组按 updated_at 排序 */
function groupByWorkspace(sessions: SessionRecord[]): Array<{
  workspace: string
  name: string
  sessions: SessionRecord[]
}> {
  const map = new Map<string, SessionRecord[]>()
  for (const s of sessions) {
    const arr = map.get(s.workspace_root) || []
    arr.push(s)
    map.set(s.workspace_root, arr)
  }
  const groups: Array<{ workspace: string; name: string; sessions: SessionRecord[] }> = []
  for (const [workspace, list] of map) {
    list.sort((a, b) => b.updated_at - a.updated_at)
    groups.push({ workspace, name: basename(workspace), sessions: list })
  }
  // 组间也按最近更新时间排序
  groups.sort((a, b) => {
    const ta = a.sessions[0]?.updated_at ?? 0
    const tb = b.sessions[0]?.updated_at ?? 0
    return tb - ta
  })
  return groups
}

/** 尚未实现的导航项：禁用 + tooltip「敬请期待」 */
const NAV_SOON = [
  { icon: 'folder', label: '项目' },
  { icon: 'zap', label: '自动化' },
  { icon: 'book', label: '资料库' },
] as const

export type NavView = 'chat' | 'skills' | 'expert' | 'mcp'

export function SessionList({
  sessions, selectedId, runningIds, collapsed,
  onSelect, onNew, onOpenSettings, onToggleCollapse,
  onRename, onDelete, onDeleteWorkspaceSessions,
  activeView = 'chat',
  onViewChange,
}: SessionListProps & { activeView?: NavView; onViewChange?: (v: NavView) => void }) {
  const [query, setQuery] = useState('')
  // ... 展开的工作区 popover（workspace 字符串，空=关闭）
  const [openPopover, setOpenPopover] = useState<string | null>(null)
  // 跟踪每个分组的折叠状态；默认展开
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set())
  // 右键菜单
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; workspace: string; name: string } | null>(null)

  // 导航 hover popover（Skills / MCP）
  const [hoverPopover, setHoverPopover] = useState<null | {
    type: 'skills' | 'mcp'
    x: number
    y: number
    data: any[]
    loading: boolean
  }>(null)
  const hoverTimer = useRef<number | null>(null)

  const handleNavHover = async (type: 'skills' | 'mcp', e: React.MouseEvent) => {
    if (hoverTimer.current) window.clearTimeout(hoverTimer.current)
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
    setHoverPopover({ type, x: rect.right + 8, y: rect.top, data: [], loading: true })
    try {
      if (type === 'skills') {
        const res = await api.listSkills()
        setHoverPopover((p) => p?.type === type ? { ...p, data: res.skills || [], loading: false } : p)
      } else {
        const res = await api.listConnectors()
        setHoverPopover((p) => p?.type === type ? { ...p, data: res.connectors || [], loading: false } : p)
      }
    } catch {
      setHoverPopover((p) => p ? { ...p, loading: false } : p)
    }
  }

  const handleNavLeave = () => {
    if (hoverTimer.current) window.clearTimeout(hoverTimer.current)
    hoverTimer.current = window.setTimeout(() => setHoverPopover(null), 150)
  }

  // 全局关闭右键菜单
  useEffect(() => {
    if (!ctxMenu) return
    const close = () => setCtxMenu(null)
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close() }
    window.addEventListener('click', close)
    window.addEventListener('scroll', close, true)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('click', close)
      window.removeEventListener('scroll', close, true)
      window.removeEventListener('keydown', onKey)
    }
  }, [ctxMenu])

  // 原生 DOM contextmenu 监听 —— 比 React onContextMenu 更可靠
  const sidebarRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = sidebarRef.current
    if (!el) return
    const handler = (e: MouseEvent) => {
      // 找到被右键点击的 sess-group（向上冒泡查找直到根 sidebar 为止）
      let target: HTMLElement | null = e.target as HTMLElement
      let group: HTMLElement | null = null
      while (target && target !== el) {
        if (target.classList && target.classList.contains('sess-group')) {
          group = target
          break
        }
        target = target.parentElement
      }
      if (!group) return
      // 从 group 的 key 属性（或 data-*）拿到 workspace
      // React 渲染的 key 不会进 DOM，用 dataset 存 workspace
      const ws = group.dataset.workspace ?? ''
      const name = group.dataset.name ?? (ws || DEFAULT_WORKSPACE_NAME)
      e.preventDefault()
      setCtxMenu({ x: e.clientX, y: e.clientY, workspace: ws, name })
    }
    el.addEventListener('contextmenu', handler)
    return () => el.removeEventListener('contextmenu', handler)
  }, [])

  const handleOpenInExplorer = async (workspace: string) => {
    setCtxMenu(null)
    if (!workspace) return
    try {
      const err = await native.openPath(workspace)
      if (err) console.warn('openPath error:', err)
    } catch (e) {
      console.warn('openPath failed:', e)
    }
  }

  const toggleGroup = useCallback((workspace: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev)
      if (next.has(workspace)) next.delete(workspace)
      else next.add(workspace)
      return next
    })
  }, [])

  const grouped = useMemo(() => {
    const q = query.trim().toLowerCase()
    const sorted = [...sessions].sort((a, b) => b.updated_at - a.updated_at)
    if (!q) return groupByWorkspace(sorted)
    const filtered = sorted.filter(
      (s) =>
        sessionTitle(s).toLowerCase().includes(q) ||
        s.workspace_root.toLowerCase().includes(q),
    )
    return groupByWorkspace(filtered)
  }, [sessions, query])

  return (
    <div ref={sidebarRef} className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
      <div className="sidebar-brand">
        <div className="sidebar-brand-logo">
          <img src="/SoulBuddy.png" alt="SoulBuddy" width={26} height={26} style={{borderRadius:'50%', objectFit:'cover'}} />
        </div>
        {!collapsed && (
          <div className="sidebar-brand-text">
            <div className="sidebar-brand-name">SoulBuddy</div>
            <div className="sidebar-brand-sub">本地 Agent · v0.1.0</div>
          </div>
        )}
      </div>

      <div className="sidebar-body">
        <button className="sidebar-cta" onClick={() => onNew()} title="新建任务">
          <Icon name="plus" size={15} />
          {!collapsed && <span>新建任务</span>}
        </button>

        <button
          className={`nav-item ${activeView === 'skills' ? 'active' : ''}`}
          onClick={() => onViewChange?.('skills')}
          onMouseEnter={(e) => handleNavHover('skills', e)}
          onMouseLeave={handleNavLeave}
          title="Skills"
        >
          <span className="ni-icon"><Icon name="sparkles" size={16} /></span>
          <span className="ni-label">Skills</span>
        </button>

        <button
          className={`nav-item ${activeView === 'expert' ? 'active' : ''}`}
          onClick={() => onViewChange?.('expert')}
          title="专家"
        >
          <span className="ni-icon"><Icon name="brain" size={16} /></span>
          <span className="ni-label">专家</span>
        </button>

        <button
          className={`nav-item ${activeView === 'mcp' ? 'active' : ''}`}
          onClick={() => onViewChange?.('mcp')}
          onMouseEnter={(e) => handleNavHover('mcp', e)}
          onMouseLeave={handleNavLeave}
          title="MCP"
        >
          <span className="ni-icon"><Icon name="plug" size={16} /></span>
          <span className="ni-label">MCP</span>
        </button>

        {NAV_SOON.map((n) => (
          <button key={n.label} className="nav-item" disabled title="敬请期待">
            <span className="ni-icon"><Icon name={n.icon as any} size={16} /></span>
            <span className="ni-label">{n.label}</span>
          </button>
        ))}

        <div className="section-label">
          {collapsed ? <Icon name="clock" size={13} /> : <span>对话</span>}
          {!collapsed && <span className="count">{sessions.length}</span>}
        </div>

        {!collapsed && (
          <div className="session-search">
            <span className="ss-icon"><Icon name="search" size={13} /></span>
            <input
              type="search"
              value={query}
              placeholder="搜索任务"
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
        )}

        <div className="session-list scroll">
          {grouped.length === 0 ? (
            <div className="sidebar-empty">
              {query ? '没有匹配的任务' : '还没有任务\n点击上方「新建任务」开始'}
            </div>
          ) : (
            grouped.map((g) => {
              const groupCollapsed = collapsedGroups.has(g.workspace)
              return (
                <div
                  key={g.workspace}
                  className={`sess-group ${groupCollapsed ? 'collapsed' : ''}`}
                  data-workspace={g.workspace}
                  data-name={g.name}
                >
                  {/* 分组标题 */}
                  <div
                    className="sg-header"
                    onClick={() => toggleGroup(g.workspace)}
                    title={g.workspace || DEFAULT_WORKSPACE_NAME}
                  >
                    <span className="sg-chevron">
                      <Icon name="chevron-down" size={13} />
                    </span>
                    <span className="sg-icon">
                      <Icon name="folder" size={14} />
                    </span>
                    <span className="sg-name">{g.name}</span>
                    {!collapsed && (
                      <>
                        <div className="sg-more-wrap" onClick={(e) => e.stopPropagation()}>
                          <button
                            className={`sg-more ${openPopover === g.workspace ? 'active' : ''}`}
                            title="更多"
                            onClick={(e) => {
                              e.stopPropagation()
                              setOpenPopover(openPopover === g.workspace ? null : g.workspace)
                            }}
                          >
                            <span className="sg-more-ic">⋯</span>
                          </button>
                          {openPopover === g.workspace && (
                            <div className="sg-popover" onClick={(e) => e.stopPropagation()}>
                              <button
                                className="sg-pop-item"
                                disabled={!g.workspace}
                                onClick={() => {
                                  setOpenPopover(null)
                                  handleOpenInExplorer(g.workspace)
                                }}
                              >
                                <Icon name="folder" size={14} />
                                在资源管理器打开
                              </button>
                              <button
                                className="sg-pop-item danger"
                                onClick={() => {
                                  setOpenPopover(null)
                                  onDeleteWorkspaceSessions?.(g.workspace, g.name, g.sessions.length)
                                }}
                              >
                                <Icon name="trash" size={14} />
                                删除全部对话（{g.sessions.length}）
                              </button>
                            </div>
                          )}
                        </div>
                        <button
                          className="sg-add"
                          title="在此项目下新建任务"
                          onClick={(e) => {
                            e.stopPropagation()
                            onNew(g.workspace)
                          }}
                        >
                          <Icon name="plus" size={13} />
                        </button>
                      </>
                    )}
                  </div>

                  {/* 组内 sessions */}
                  <div className="sg-body">
                    {g.sessions.map((s) => {
                      const cls = [
                        'session-item',
                        s.id === selectedId ? 'active' : '',
                        runningIds.has(s.id) ? 'running' : '',
                      ].filter(Boolean).join(' ')
                      return (
                        <div
                          key={s.id}
                          className={cls}
                          onClick={() => onSelect(s.id)}
                          title={`${s.workspace_root}\n${s.provider}\n${formatTime(s.updated_at)}`}
                        >
                          <span className="si-dot" />
                          {collapsed ? (
                            <span className="si-mini">
                              <Icon name="message" size={15} />
                            </span>
                          ) : (
                            <>
                              <span className="si-name">{sessionTitle(s)}</span>
                              <span className="si-actions">
                                <button
                                  className="ibtn sm"
                                  title="重命名"
                                  onClick={(e) => { e.stopPropagation(); onRename(s) }}
                                >
                                  <Icon name="pencil" size={13} />
                                </button>
                                <button
                                  className="ibtn sm"
                                  title="删除任务"
                                  onClick={(e) => { e.stopPropagation(); onDelete(s) }}
                                >
                                  <Icon name="trash" size={13} />
                                </button>
                              </span>
                            </>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </div>
              )
            })
          )}
        </div>
      </div>

      <div className="sidebar-footer">
        <div className="sf-avatar">
          <EmotionBall emotionId="13" width={26} idle={false} theme={SB_THEME} />
        </div>
        {!collapsed && (
          <div className="sf-info">
            <div className="sf-name">本地用户</div>
            <div className="sf-sub">{sessions.length} 个任务</div>
          </div>
        )}
        <button className="ibtn" onClick={onOpenSettings} title="设置">
          <Icon name="settings" size={15} />
        </button>
        {!collapsed && (
          <button className="ibtn" onClick={onToggleCollapse} title="收起侧栏">
            <Icon name="panel-left" size={15} />
          </button>
        )}
      </div>

      {collapsed && (
        <div className="sidebar-footer" style={{ borderTop: 'none', paddingTop: 0 }}>
          <button className="ibtn" onClick={onToggleCollapse} title="展开侧栏">
            <Icon name="panel-right" size={15} />
          </button>
        </div>
      )}

      {ctxMenu && (
        <div
          className="ctx-menu"
          style={{ left: ctxMenu.x, top: ctxMenu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="ctx-title">{ctxMenu.name}</div>
          <button
            className="ctx-item"
            disabled={!ctxMenu.workspace}
            onClick={() => handleOpenInExplorer(ctxMenu.workspace)}
          >
            <Icon name="folder" size={14} />
            在资源管理器打开该目录
          </button>
        </div>
      )}

      {hoverPopover && (
        <div
          className="nav-hover-popover"
          style={{ left: hoverPopover.x, top: hoverPopover.y }}
          onMouseEnter={() => { if (hoverTimer.current) window.clearTimeout(hoverTimer.current) }}
          onMouseLeave={handleNavLeave}
        >
          <div className="nph-title">
            {hoverPopover.type === 'skills' ? (
              <><Icon name="sparkles" size={12} /> Skills（{hoverPopover.data.length}）</>
            ) : (
              <><Icon name="plug" size={12} /> MCP（{hoverPopover.data.length}）</>
            )}
          </div>
          <div className="nph-list">
            {hoverPopover.loading && <div className="nph-empty">加载中...</div>}
            {!hoverPopover.loading && hoverPopover.data.length === 0 && (
              <div className="nph-empty">
                {hoverPopover.type === 'skills' ? '暂无已安装 Skills' : '暂无 MCP 连接器'}
              </div>
            )}
            {hoverPopover.data.map((item: any, i: number) => (
              <div key={i} className="nph-row">
                {hoverPopover.type === 'skills' ? (
                  <>
                    <span className="nph-dot" style={{ background: item.source === 'project' ? '#6366f1' : '#10b981' }} />
                    <span className="nph-name">{item.title}</span>
                    <span className="nph-src">{item.source === 'project' ? '项目' : '用户'}</span>
                  </>
                ) : (
                  <>
                    <span
                      className={`nph-status nph-${statusClass(item.status)}`}
                      title={item.status}
                    />
                    <span className="nph-name">{item.name}</span>
                    <span className="nph-src">{item.tools?.length || 0} tools</span>
                  </>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function statusClass(status: string): string {
  if (status === 'connected' || status === 'connecting') return 'ok'
  if (status.startsWith('error')) return 'err'
  return 'off'
}
