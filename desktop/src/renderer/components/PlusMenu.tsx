import { useEffect, useMemo, useRef, useState } from 'react'
import { Icon } from './Icon'
import { api, type ExpertRow, type WorkspaceFileItem } from '../api'

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

export type AgentMode = 'default' | 'plan' | 'ask'

interface Props {
  /** 点击 skill 时，把 skill 引用插入到输入框 */
  onInsertSkill?: (title: string) => void
  /** 点击专家时，切换会话级专家 */
  onExpertPick?: (expertId: string | null) => void
  /** 引用项目文件时，把 @relativePath 插入输入框 */
  onInsertFile?: (relativePath: string) => void
  /** 添加本地文件时，挂起为文件 chip（不读内容，发送时才解析） */
  onPickFiles?: (files: File[]) => void
  /** 选择图片后，把图片加为聊天附件 */
  onPickImages?: (files: File[]) => void
  /** 切换 Agent 模式 */
  onModeChange?: (mode: AgentMode) => void
  /** toast 提示 */
  onToast?: (msg: string, tone?: 'ok' | 'err' | 'info') => void
  /** workspace 根路径，用于加载项目级 skills 和文件树 */
  workspaceRoot?: string
  /** 当前会话绑定的专家 id（用于子菜单高亮） */
  currentExpertId?: string | null
  /** 当前 Agent 模式 */
  currentMode?: AgentMode
}

type SubMenu = null | 'files' | 'experts' | 'skills' | 'mcp' | 'modes'

export function PlusMenu({ onInsertSkill, onExpertPick, onInsertFile, onPickFiles, onPickImages, onModeChange, onToast, workspaceRoot, currentExpertId, currentMode = 'default' }: Props) {
  const [open, setOpen] = useState(false)
  const [sub, setSub] = useState<SubMenu>(null)
  const [skills, setSkills] = useState<SkillItem[]>([])
  const [mcps, setMcps] = useState<McpItem[]>([])
  const [experts, setExperts] = useState<ExpertRow[]>([])
  const [files, setFiles] = useState<WorkspaceFileItem[]>([])
  const [fileSearch, setFileSearch] = useState('')
  const [loading, setLoading] = useState(false)
  const localFileRef = useRef<HTMLInputElement>(null)
  const imageFileRef = useRef<HTMLInputElement>(null)
  const ref = useRef<HTMLDivElement>(null)

  const handlePickLocalFile = () => {
    localFileRef.current?.click()
  }

  const handlePickImages = () => {
    imageFileRef.current?.click()
  }

  const onImageFilesChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(e.target.files || [])
    if (picked.length) onPickImages?.(picked)
    // reset 以便再次选同一个文件也能触发 change
    e.target.value = ''
    setOpen(false)
    setSub(null)
  }

  const onLocalFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(e.target.files || [])
    if (picked.length) onPickFiles?.(picked)
    // reset 以便再次选同一个文件也能触发 change
    e.target.value = ''
    setOpen(false)
    setSub(null)
  }

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
      } else if (key === 'files') {
        if (!workspaceRoot) { onToast?.('当前会话没有工作区', 'err'); setLoading(false); return }
        const res = await api.listWorkspaceFiles(workspaceRoot, 4, fileSearch || undefined)
        setFiles(res.items || [])
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

  // 文件树：扁平列表 → 嵌套 children
  const fileTree = useMemo(() => {
    const root: Record<string, WorkspaceFileItem & { children: any[] }> = {}
    const byId: Record<string, any> = {}
    for (const f of files) {
      byId[f.id] = { ...f, children: [] }
    }
    for (const f of files) {
      if (f.parent && byId[f.parent]) {
        byId[f.parent].children.push(byId[f.id])
      } else {
        const key = '__root__'
        if (!root[key]) root[key] = { id: key, name: '', parent: null, is_dir: true, size: null, relative_path: '', absolute_path: '', children: [] }
        root[key].children.push(byId[f.id])
      }
    }
    // 目录排序在前,按名称
    const sortTree = (arr: any[]) => arr.sort((a, b) => (b.is_dir ? 1 : 0) - (a.is_dir ? 1 : 0) || a.name.localeCompare(b.name)).map((n) => {
      if (n.is_dir) n.children = sortTree(n.children)
      return n
    })
    return sortTree(root.__root__?.children || [])
  }, [files])

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
          <SubItem icon="folder" label="引用项目文件" subKey="files" activeSub={sub} onEnter={enterSub} />
          <TopItem icon="file" label="添加文件" onClick={handlePickLocalFile} />
          <input ref={localFileRef} type="file" multiple style={{ display: 'none' }} onChange={onLocalFileChange} />
          <TopItem icon="image" label="图片" onClick={handlePickImages} />
          <input
            ref={imageFileRef}
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif"
            multiple
            style={{ display: 'none' }}
            onChange={onImageFilesChange}
          />
          <SubItem icon="zap" label="模式" subKey="modes" activeSub={sub} onEnter={enterSub} />
          <SubItem icon="user" label="专家" subKey="experts" activeSub={sub} onEnter={enterSub} />
          <SubItem icon="sparkles" label="Skills" subKey="skills" activeSub={sub} onEnter={enterSub} />
          <SubItem icon="plug" label="MCP" subKey="mcp" activeSub={sub} onEnter={enterSub} />

          {sub === 'files' && (
            <div className="plus-sub">
              <div className="plus-sub-head">
                <button className="plus-sub-back" onClick={() => setSub(null)}>
                  <Icon name="chevron-left" size={13} />
                </button>
                <span>选择文件</span>
                <button
                  className="plus-sub-refresh"
                  onClick={() => enterSub('files')}
                  title="刷新"
                >
                  <Icon name="refresh" size={12} />
                </button>
              </div>
              <div className="plus-sub-search">
                <Icon name="search" size={12} />
                <input
                  type="text"
                  placeholder="搜索文件名..."
                  value={fileSearch}
                  onChange={(e) => setFileSearch(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') enterSub('files') }}
                />
              </div>
              <div className="plus-sub-list file-tree-list">
                {loading && <div className="plus-sub-empty">加载中...</div>}
                {!loading && !workspaceRoot && <div className="plus-sub-empty">当前会话没有工作区</div>}
                {!loading && workspaceRoot && fileTree.length === 0 && <div className="plus-sub-empty">没有文件</div>}
                {!loading && workspaceRoot && fileTree.length > 0 && (
                  <FileTree nodes={fileTree} onPick={(p) => { onInsertFile?.(p); setOpen(false); setSub(null) }} depth={0} />
                )}
              </div>
              <div className="plus-sub-foot">
                <span className="psf-hint">点击文件引用到输入框</span>
              </div>
            </div>
          )}

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

          {sub === 'modes' && (
            <div className="plus-sub">
              <div className="plus-sub-head">
                <button className="plus-sub-back" onClick={() => setSub(null)}>
                  <Icon name="chevron-left" size={13} />
                </button>
                <span>模式</span>
              </div>
              <div className="plus-sub-desc">
                {modeDescription(currentMode)}
              </div>
              <div className="plus-sub-list">
                <div className="plus-sub-row">
                  <span className="psr-name">计划 Plan</span>
                  <Toggle
                    checked={currentMode === 'plan'}
                    onChange={() => {
                      const next: AgentMode = currentMode === 'plan' ? 'default' : 'plan'
                      onModeChange?.(next)
                      onToast?.(`模式：${modeLabel(next)}`, 'ok')
                    }}
                  />
                </div>
                <div className="plus-sub-row">
                  <span className="psr-name">仅问答 Ask</span>
                  <Toggle
                    checked={currentMode === 'ask'}
                    onChange={() => {
                      const next: AgentMode = currentMode === 'ask' ? 'default' : 'ask'
                      onModeChange?.(next)
                      onToast?.(`模式：${modeLabel(next)}`, 'ok')
                    }}
                  />
                </div>
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

/** 递归文件树。目录默认展开第一层,点击文件触发 onPick。 */
function FileTree({ nodes, onPick, depth }: {
  nodes: Array<WorkspaceFileItem & { children: any[] }>
  onPick: (relativePath: string) => void
  depth: number
}) {
  const [openDirs, setOpenDirs] = useState<Set<string>>(() => new Set(
    nodes.filter((n) => n.is_dir).map((n) => n.id)
  ))
  const toggle = (id: string) => {
    setOpenDirs((prev) => {
      const s = new Set(prev)
      if (s.has(id)) s.delete(id); else s.add(id)
      return s
    })
  }
  return (
    <div>
      {nodes.map((n) => (
        <div key={n.id}>
          <button
            className={`plus-sub-row ft-row ${n.is_dir ? 'ft-dir' : 'ft-file'}`}
            style={{ paddingLeft: 10 + depth * 14 }}
            onClick={() => n.is_dir ? toggle(n.id) : onPick(n.relative_path)}
            title={n.relative_path}
          >
            {n.is_dir && (
              <span className="ft-toggle">
                <Icon name={openDirs.has(n.id) ? 'chevron-down' : 'chevron-right'} size={10} />
              </span>
            )}
            {!n.is_dir && <span className="ft-toggle" style={{ width: 10 }} />}
            <Icon name={n.is_dir ? 'folder' : 'file'} size={13} />
            <span className="ft-name">{n.name}</span>
            {!n.is_dir && n.size != null && (
              <span className="ft-size">{formatSize(n.size)}</span>
            )}
          </button>
          {n.is_dir && openDirs.has(n.id) && n.children.length > 0 && (
            <FileTree nodes={n.children} onPick={onPick} depth={depth + 1} />
          )}
        </div>
      ))}
    </div>
  )
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function modeLabel(mode: AgentMode): string {
  switch (mode) {
    case 'plan': return '计划 Plan'
    case 'ask': return '仅问答 Ask'
    default: return '默认'
  }
}

function modeDescription(mode: AgentMode): string {
  switch (mode) {
    case 'plan':
      return '当前为计划模式，Agent 会先制定计划再逐步执行。'
    case 'ask':
      return '当前为仅问答模式，Agent 只回答问题，不修改文件或运行命令。'
    default:
      return '当前为默认模式，可高效执行并完成任务。'
  }
}

