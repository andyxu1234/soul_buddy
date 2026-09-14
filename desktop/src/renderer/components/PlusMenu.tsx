import { useEffect, useRef, useState } from 'react'
import { Icon } from './Icon'
import { api, type ExpertRow } from '../api'

interface SkillItem {
  title: string
  summary: string
  source: 'user' | 'project'
}

interface McpItem {
  name: string
  status: string   // disconnected | trusted | connected | connecting | error:*
  trusted: boolean
  tools: string[]
}

interface Props {
  /** 点击 skill 时，把 skill 引用插入到输入框 */
  onInsertSkill?: (title: string) => void
  /** 点击专家时，切换会话级专家 */
  onExpertPick?: (expertId: string | null) => void
  /** toast 提示 */
  onToast?: (msg: string, tone?: 'ok' | 'err' | 'info') => void
  /** workspace 根路径，用于加载项目级 skills */
  workspaceRoot?: string
  /** 当前会话绑定的专家 id（用于子菜单高亮） */
  currentExpertId?: string | null
}

type SubMenu = null | 'experts' | 'skills' | 'mcp'

export function PlusMenu({ onInsertSkill, onExpertPick, onToast, workspaceRoot, currentExpertId }: Props) {
  const [open, setOpen] = useState(false)
  const [sub, setSub] = useState<SubMenu>(null)
  const [skills, setSkills] = useState<SkillItem[]>([])
  const [mcps, setMcps] = useState<McpItem[]>([])
  const [experts, setExperts] = useState<ExpertRow[]>([])
  const [loading, setLoading] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  // 外部关闭
  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) { setOpen(false); setSub(null) }
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [open])

  const enterSub = async (key: SubMenu) => {
    setSub(key)
    if (!key) return
    setLoading(true)
    try {
      if (key === 'skills') {
        const res = await api.listSkills(workspaceRoot)
        setSkills(res.skills || [])
      } else if (key === 'experts') {
        const res = await api.listExperts()
        setExperts(res.experts || [])
      } else {
        const res = await api.listConnectors()
        setMcps(res.connectors || [])
      }
    } catch (e: any) {
      onToast?.(`加载失败：${e?.detail || e?.message || e}`, 'err')
    } finally {
      setLoading(false)
    }
  }

  const handleToggleMcp = async (name: string, current: McpItem) => {
    const shouldEnable = !isEnabled(current)
    try {
      if (shouldEnable) {
        // trust + connect
        if (!current.trusted) await api.trustConnector(name)
        await api.connectConnector(name)
        onToast?.(`MCP ${name} 已连接`, 'ok')
      } else {
        await api.disconnectConnector(name)
        onToast?.(`MCP ${name} 已断开`, 'ok')
      }
      // 刷新列表
      const res = await api.listConnectors()
      setMcps(res.connectors || [])
    } catch (e: any) {
      onToast?.(`操作失败：${e?.detail || e?.message || e}`, 'err')
    }
  }

  const handleSkillClick = (title: string) => {
    onInsertSkill?.(title)
    setOpen(false)
    setSub(null)
  }

  return (
    <div className="plus-menu" ref={ref}>
      <button
        className="plus-btn"
        onClick={() => { setOpen((v) => !v); setSub(null) }}
        title="添加内容"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <Icon name="plus" size={16} strokeWidth={2.2} />
      </button>
      {open && (
        <div className="plus-popover" role="menu">
          <TopItem icon="folder" label="添加文件" onClick={() => { setOpen(false); onToast?.('文件引用即将支持', 'info') }} />
          <TopItem icon="message" label="引用对话中的文件" onClick={() => { setOpen(false); onToast?.('引用即将支持', 'info') }} />
          <TopItem icon="zap" label="模式" onClick={() => { setOpen(false); onToast?.('模式切换即将支持', 'info') }} />
          <SubItem icon="user" label="专家" subKey="experts" activeSub={sub} onEnter={enterSub} />
          <SubItem icon="sparkles" label="Skills" subKey="skills" activeSub={sub} onEnter={enterSub} />
          <SubItem icon="plug" label="MCP" subKey="mcp" activeSub={sub} onEnter={enterSub} />

          {sub === 'experts' && (
            <div className="plus-sub">
              <div className="plus-sub-head">
                <button className="plus-sub-back" onClick={() => setSub(null)}>
                  <Icon name="chevron-left" size={13} />
                </button>
                <span>选择专家</span>
                <span className="plus-sub-count">{experts.filter((e) => e.enabled).length}</span>
              </div>
              <div className="plus-sub-list">
                {loading && <div className="plus-sub-empty">加载中...</div>}
                {!loading && experts.length === 0 && <div className="plus-sub-empty">暂无专家</div>}
                {!loading && experts.length > 0 && (
                  <>
                    <button
                      className="plus-sub-row"
                      onClick={() => { onExpertPick?.(null); setOpen(false); setSub(null) }}
                    >
                      <span className="psr-dot" style={{ background: '#9ca3af' }} />
                      <span className="psr-name">不使用专家</span>
                      {!currentExpertId && <Icon name="check" size={12} />}
                    </button>
                    {experts.filter((e) => e.enabled).map((e) => (
                      <button
                        key={e.id}
                        className="plus-sub-row"
                        onClick={() => { onExpertPick?.(e.id); setOpen(false); setSub(null) }}
                        title={e.role}
                      >
                        <span className="psr-dot" style={{ background: e.color }} />
                        <span className="psr-name">{e.name}</span>
                        {e.isBuiltin && <span className="psr-src">内置</span>}
                        {!e.isBuiltin && <span className="psr-src">用户</span>}
                        {currentExpertId === e.id && <Icon name="check" size={12} />}
                      </button>
                    ))}
                  </>
                )}
              </div>
              <div className="plus-sub-foot">
                <span className="psf-hint">点击绑定到当前会话</span>
              </div>
            </div>
          )}

          {sub === 'skills' && (
            <div className="plus-sub">
              <div className="plus-sub-head">
                <button className="plus-sub-back" onClick={() => setSub(null)}>
                  <Icon name="chevron-left" size={13} />
                </button>
                <span>已安装 Skills</span>
                <span className="plus-sub-count">{skills.length}</span>
              </div>
              <div className="plus-sub-list">
                {loading && <div className="plus-sub-empty">加载中...</div>}
                {!loading && skills.length === 0 && <div className="plus-sub-empty">暂无 Skills</div>}
                {skills.map((s) => (
                  <button
                    key={s.title}
                    className="plus-sub-row"
                    onClick={() => handleSkillClick(s.title)}
                    title={s.summary}
                  >
                    <span className="psr-dot" style={{ background: s.source === 'project' ? '#6366f1' : '#10b981' }} />
                    <span className="psr-name">{s.title}</span>
                    <span className="psr-src">{s.source === 'project' ? '项目' : '用户'}</span>
                  </button>
                ))}
              </div>
              <div className="plus-sub-foot">
                <span className="psf-hint">点击插入到输入框</span>
              </div>
            </div>
          )}

          {sub === 'mcp' && (
            <div className="plus-sub">
              <div className="plus-sub-head">
                <button className="plus-sub-back" onClick={() => setSub(null)}>
                  <Icon name="chevron-left" size={13} />
                </button>
                <span>MCP 连接器</span>
                <span className="plus-sub-count">{mcps.length}</span>
              </div>
              <div className="plus-sub-list">
                {loading && <div className="plus-sub-empty">加载中...</div>}
                {!loading && mcps.length === 0 && <div className="plus-sub-empty">暂无 MCP 连接器</div>}
                {mcps.map((c) => (
                  <div key={c.name} className="plus-sub-row">
                    <span className={`psr-dot mcp-${statusClass(c.status)}`} title={c.status} />
                    <span className="psr-name">{c.name}</span>
                    <span className="psr-src">{c.tools.length} tools</span>
                    <Toggle checked={isEnabled(c)} onChange={() => handleToggleMcp(c.name, c)} />
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function TopItem({ icon, label, onClick }: { icon: string; label: string; onClick: () => void }) {
  return (
    <button className="plus-item" role="menuitem" onClick={onClick}>
      <Icon name={icon as any} size={14} />
      <span>{label}</span>
    </button>
  )
}

function SubItem({
  icon, label, subKey, activeSub, onEnter,
}: {
  icon: string; label: string; subKey: SubMenu; activeSub: SubMenu; onEnter: (k: SubMenu) => void
}) {
  return (
    <button
      className={`plus-item ${activeSub === subKey ? 'active' : ''}`}
      role="menuitem"
      onClick={() => onEnter(subKey)}
      onMouseEnter={() => onEnter(subKey)}
    >
      <Icon name={icon as any} size={14} />
      <span>{label}</span>
      <Icon name="chevron-right" size={12} />
    </button>
  )
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: () => void }) {
  return (
    <button
      className={`toggle-switch ${checked ? 'on' : 'off'}`}
      onClick={(e) => { e.stopPropagation(); onChange() }}
      aria-pressed={checked}
    >
      <span className="toggle-knob" />
    </button>
  )
}

function isEnabled(c: McpItem) {
  return c.trusted && c.status !== 'disconnected' && !c.status.startsWith('error')
}

function statusClass(status: string): string {
  if (status === 'connected' || status === 'connecting') return 'ok'
  if (status.startsWith('error')) return 'err'
  return 'off'
}
