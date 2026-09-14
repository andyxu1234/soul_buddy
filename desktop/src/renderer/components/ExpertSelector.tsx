import { useEffect, useRef, useState } from 'react'
import { api, type ExpertRow } from '../api'
import { Icon } from './Icon'

interface Props {
  current?: string | null
  onChange: (expertId: string | null) => void
}

/** 会话级专家选择器：仿 ModelSelector，None = 普通会话。 */
export function ExpertSelector({ current, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const [experts, setExperts] = useState<ExpertRow[]>([])
  const ref = useRef<HTMLDivElement>(null)
  const active = experts.find((e) => e.id === current)

  useEffect(() => {
    let cancelled = false
    api.listExperts()
      .then((res) => { if (!cancelled) setExperts(res.experts) })
      .catch(() => { /* 桥未就绪等场景静默，选择器退化成占位 */ })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [open])

  return (
    <div className="model-dropdown" ref={ref}>
      <button
        className="model-toggle"
        onClick={() => setOpen((v) => !v)}
        title={active ? `当前专家：${active.name}` : '绑定专家（可基于资料库问答/拷打/面试）'}
      >
        {active ? (
          <span className="model-dot" style={{ background: active.color }} />
        ) : (
          <Icon name="brain" size={12} />
        )}
        <span className="model-label">{active ? active.name : '专家'}</span>
        <Icon name="chevron-down" size={12} />
      </button>
      {open && (
        <div className="model-popover">
          <button
            className={`model-option ${!current ? 'active' : ''}`}
            onClick={() => { setOpen(false); onChange(null) }}
          >
            <span className="model-option-label">不使用专家</span>
            {!current && <Icon name="check" size={12} />}
          </button>
          {experts.map((e) => (
            <button
              key={e.id}
              className={`model-option ${e.id === current ? 'active' : ''} ${e.enabled ? '' : 'kb-opt-disabled'}`}
              disabled={!e.enabled}
              title={e.enabled ? e.role : '已禁用（在 Experts 页开启）'}
              onClick={() => { setOpen(false); if (e.id !== current) onChange(e.id) }}
            >
              <span className="model-dot" style={{ background: e.color }} />
              <span className="model-option-label">{e.name}</span>
              {e.id === current && <Icon name="check" size={12} />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
