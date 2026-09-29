import { useEffect, useMemo, useRef, useState } from 'react'
import { api, type KbRow } from '../api'
import { Icon } from './Icon'

interface Props {
  /** 当前会话挂载的知识库 id 列表；空数组/undefined = 未挂载 */
  current?: string[] | null
  /** 勾选变化，回传完整 id 列表（保持选择顺序） */
  onChange: (kbIds: string[]) => void
  /** 跳转知识库管理页 */
  onManage?: () => void
}

/** 会话级知识库挂载选择器：仿截图中的弹出面板，搜索 + 开关列表 + 管理入口。 */
export function KnowledgeSelector({ current, onChange, onManage }: Props) {
  const [open, setOpen] = useState(false)
  const [kbs, setKbs] = useState<KbRow[]>([])
  const [query, setQuery] = useState('')
  const ref = useRef<HTMLDivElement>(null)

  const selected = useMemo(() => current ?? [], [current])

  useEffect(() => {
    let cancelled = false
    api.listKnowledgeBases()
      .then((res) => { if (!cancelled) setKbs(res.kbs) })
      .catch(() => { /* 桥未就绪等场景静默，选择器退化成占位 */ })
    return () => { cancelled = true }
  }, [open])

  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [open])

  const toggle = (id: string) => {
    onChange(selected.includes(id)
      ? selected.filter((k) => k !== id)
      : [...selected, id])
  }

  const kw = query.trim().toLowerCase()
  const visible = kw ? kbs.filter((k) => k.name.toLowerCase().includes(kw)) : kbs

  return (
    <div className="model-dropdown kb-dropdown" ref={ref}>
      <button
        className={`model-toggle ${selected.length ? 'kb-on' : ''}`}
        onClick={() => { setOpen((v) => !v); setQuery('') }}
        title={selected.length
          ? `已挂载 ${selected.length} 个知识库`
          : '挂载知识库（基于上传文档问答）'}
      >
        <Icon name="book" size={12} />
        <span className="model-label">
          {selected.length ? `知识库 · ${selected.length}` : '知识库'}
        </span>
        <Icon name="chevron-down" size={12} />
      </button>
      {open && (
        <div className="model-popover kb-popover">
          <div className="kb-pop-search">
            <input
              type="text"
              placeholder="搜索知识库"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <Icon name="search" size={14} />
          </div>
          <div className="kb-pop-list">
            {visible.length === 0 && (
              <div className="kb-pop-empty">
                {kbs.length === 0 ? '还没有知识库' : '没有匹配的知识库'}
              </div>
            )}
            {visible.map((k) => {
              const on = selected.includes(k.id)
              return (
                <div key={k.id} className="kb-pop-row" onClick={() => toggle(k.id)}
                     title={k.description || k.name}>
                  <span className="kb-pop-icon"><Icon name="book" size={14} /></span>
                  <span className="kb-pop-info">
                    <span className="kb-pop-name">{k.name}</span>
                    <span className="kb-pop-sub">{k.document_count} 个文档</span>
                  </span>
                  <button
                    className={`toggle-switch ${on ? 'on' : 'off'}`}
                    onClick={(e) => { e.stopPropagation(); toggle(k.id) }}
                    aria-pressed={on}
                    title={on ? '已挂载，点击取消' : '未挂载，点击挂载'}
                  >
                    <span className="toggle-knob" />
                  </button>
                </div>
              )
            })}
          </div>
          {onManage && (
            <button className="kb-pop-manage" onClick={() => { setOpen(false); onManage() }}>
              <Icon name="settings" size={13} />
              管理知识库
            </button>
          )}
        </div>
      )}
    </div>
  )
}
