import { useEffect, useRef, useState } from 'react'
import { Icon } from './Icon'

interface Props {
  mode: 'default' | 'allow_all'
  onChange: (mode: 'default' | 'allow_all') => void
}

const OPTIONS: { id: 'default' | 'allow_all'; label: string; desc: string }[] = [
  { id: 'default', label: '默认权限', desc: '每次请求都需确认' },
  { id: 'allow_all', label: '允许完全访问', desc: '自动通过后续权限请求' },
]

export function PermissionDropdown({ mode, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const active = OPTIONS.find((o) => o.id === mode) || OPTIONS[0]

  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [open])

  return (
    <div className="perm-dropdown" ref={ref}>
      <button
        className={`perm-toggle ${mode === 'allow_all' ? 'warn' : ''}`}
        onClick={() => setOpen((v) => !v)}
        title={active.desc}
      >
        <Icon name={mode === 'allow_all' ? 'shield' : 'check'} size={12} />
        <span>{active.label}</span>
        <Icon name="chevron-down" size={12} />
      </button>
      {open && (
        <div className="perm-popover">
          {OPTIONS.map((o) => (
            <button
              key={o.id}
              className={`perm-option ${o.id === mode ? 'active' : ''}`}
              onClick={() => {
                setOpen(false)
                onChange(o.id)
              }}
            >
              <div className="perm-opt-title">{o.label}</div>
              <div className="perm-opt-desc">{o.desc}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
