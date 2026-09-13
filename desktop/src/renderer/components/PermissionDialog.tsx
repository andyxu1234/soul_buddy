import { useEffect, useState } from 'react'
import type { PermissionRequest } from '../types'
import { Icon, type IconName } from './Icon'

interface Props {
  req: PermissionRequest
  onResolve: (choice: string) => void
}

/** 写类操作才需要警告配色，只读操作用中性提示。 */
const WRITE_TOOLS = /write|edit|delete|remove|move|bash|exec|run/i

function toolIcon(name: string): IconName {
  const n = (name || '').toLowerCase()
  if (n.includes('bash') || n.includes('exec') || n.includes('run')) return 'terminal'
  if (n.includes('write') || n.includes('edit')) return 'pencil'
  if (n.includes('delete') || n.includes('remove')) return 'trash'
  if (n.includes('read')) return 'file'
  return 'shield'
}

function renderArgs(args: any): string {
  if (args === undefined || args === null) return '(无参数)'
  if (typeof args === 'string') return args
  try {
    return JSON.stringify(args, null, 2)
  } catch {
    return String(args)
  }
}

export function PermissionDialog({ req, onResolve }: Props) {
  const [left, setLeft] = useState(300)
  const [busy, setBusy] = useState(false)
  const risky = WRITE_TOOLS.test(req.tool || '')

  useEffect(() => {
    if (left <= 0) return
    const t = setInterval(() => setLeft((l) => Math.max(0, l - 1)), 1000)
    return () => clearInterval(t)
  }, [left])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') resolve('deny')
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const resolve = (choice: string) => {
    if (busy) return
    setBusy(true)
    // 乐观关闭：后端可能已超时/已处理，无论成败都先移除
    onResolve(choice)
  }

  return (
    <div className="backdrop" onClick={() => resolve('deny')}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span
            className="mh-icon"
            style={{
              background: risky ? 'var(--warn-soft)' : 'var(--brand-soft)',
              color: risky ? 'var(--warn)' : 'var(--brand-fg)',
            }}
          >
            <Icon name={toolIcon(req.tool)} size={16} />
          </span>
          <span className="mh-title">需要授权 · {req.tool}</span>
          <span className={`pill ${left <= 0 ? 'danger' : left < 60 ? 'warn' : 'neutral'}`}>
            <Icon name="clock" size={12} />
            {left <= 0 ? '已超时' : `${left}s`}
          </span>
        </div>

        <div className="modal-body scroll">
          <div className="cmd">{renderArgs(req.args)}</div>

          {req.reason && (
            <div className="notice warn">
              <Icon name="info" size={14} />
              <span>
                <strong>代理说明：</strong>
                {req.reason}
              </span>
            </div>
          )}

          {req.overwrite && (
            <div className="notice danger">
              <Icon name="alert" size={14} />
              <span>
                该操作将覆盖已有文件
                {req.existing_bytes ? `（当前 ${req.existing_bytes} 字节）` : ''}。
              </span>
            </div>
          )}

          {req.diff_preview && (
            <>
              <div className="field-label" style={{ marginTop: 14 }}>
                覆盖前差异预览
              </div>
              <div className="diff">{req.diff_preview}</div>
            </>
          )}
        </div>

        <div className="modal-actions-grid">
          <button
            className="primary"
            disabled={busy}
            onClick={() => resolve('allow_once')}
            title="仅允许这一次调用"
          >
            允许本次
          </button>
          <button
            disabled={busy}
            onClick={() => resolve('allow_dir')}
            title="记住该目录下的同类操作，后续自动放行"
          >
            允许该目录
          </button>
          <button
            className="danger"
            disabled={busy}
            onClick={() => resolve('deny')}
            title="拒绝这次调用，Agent 会换一种方式继续"
          >
            拒绝本次
          </button>
          <button
            className="danger"
            disabled={busy}
            onClick={() => resolve('deny_rest')}
            title="拒绝并终止本次运行后续的所有工具调用"
          >
            拒绝并终止
          </button>
        </div>

        <div className="modal-foot" style={{ paddingTop: 0, borderTop: 'none' }}>
          <span className="mf-note" style={{ margin: '0 auto' }}>
            按 Esc 或点击遮罩 = 拒绝本次
          </span>
        </div>
      </div>
    </div>
  )
}
