import { useState } from 'react'
import type { IconName } from './Icon'
import { Icon } from './Icon'

interface Props {
  name: string
  callId?: string
  args?: any
  result?: string
  isError?: boolean
}

function toolIcon(name: string): IconName {
  const n = (name || '').toLowerCase()
  if (n.includes('read') || n.includes('glob') || n.includes('file')) return 'file'
  if (n.includes('write') || n.includes('edit') || n.includes('replace')) return 'pencil'
  if (n.includes('bash') || n.includes('exec') || n.includes('run')) return 'terminal'
  if (n.includes('search') || n.includes('grep')) return 'search'
  if (n.includes('list') || n.includes('dir')) return 'folder'
  if (n.includes('delete') || n.includes('remove')) return 'trash'
  return 'code'
}

function render(v: any): string {
  if (v === undefined || v === null) return '(无参数)'
  if (typeof v === 'string') return v
  try {
    return JSON.stringify(v, null, 2)
  } catch {
    return String(v)
  }
}

export function ToolCallCard({ name, callId, args, result, isError }: Props) {
  const [open, setOpen] = useState(false)
  const hasDetail = args !== undefined || result !== undefined
  const pending = result === undefined

  return (
    <div className={`tool-card ${isError ? 'is-error' : ''}`}>
      <button
        className="tool-head"
        onClick={() => hasDetail && setOpen((o) => !o)}
        disabled={!hasDetail}
        title={hasDetail ? (open ? '收起' : '展开') : '无详情'}
      >
        <span className="tool-icon">
          <Icon name={toolIcon(name)} size={14} />
        </span>
        {callId && <span className="tool-call-id">#{callId.slice(0, 6)}</span>}
        <span className="tool-name">{name}</span>
        <span className={`pill ${pending ? 'brand' : isError ? 'danger' : 'ok'}`}>
          {pending && <span className="pulse" />}
          {pending ? '运行中' : isError ? '失败' : '完成'}
        </span>
        {hasDetail && (
          <span className={`tool-chev ${open ? 'open' : ''}`}>
            <Icon name="chevron-down" size={14} />
          </span>
        )}
      </button>

      {open && hasDetail && (
        <div className="tool-body scroll">
          {args !== undefined && (
            <>
              <div className="tb-label">参数</div>
              <div>{render(args)}</div>
            </>
          )}
          {result !== undefined && (
            <>
              <div className="tb-label">{isError ? '错误输出' : '执行结果'}</div>
              <div>{result}</div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
