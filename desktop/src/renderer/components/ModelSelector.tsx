import { useEffect, useRef, useState } from 'react'
import { Icon } from './Icon'

interface Provider {
  id: string
  label: string
}

interface Props {
  current?: string | null
  providers: Provider[]
  onChange: (provider: string) => void
}

export function ModelSelector({ current, providers, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const active = providers.find((p) => p.id === current) || providers[0]

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
        title="切换模型"
      >
        <span className="model-dot" />
        <span className="model-label">{active?.label || 'Auto'}</span>
        <Icon name="chevron-down" size={12} />
      </button>
      {open && (
        <div className="model-popover">
          {providers.map((p) => (
            <button
              key={p.id}
              className={`model-option ${p.id === current ? 'active' : ''}`}
              onClick={() => {
                setOpen(false)
                if (p.id !== current) onChange(p.id)
              }}
            >
              <span>{p.label}</span>
              {p.id === current && <Icon name="check" size={12} />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
