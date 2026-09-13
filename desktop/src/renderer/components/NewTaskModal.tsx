import { useState } from 'react'
import { Icon } from './Icon'
import { native } from '../api'
import { basename } from './SessionList'

interface Props {
  onClose: () => void
  onCreate: (workspaceRoot: string, title: string) => void
  /** 预填的工作区目录（从分组的「+」按钮点新建时传入） */
  initialDir?: string
}

/**
 * 取代 window.prompt —— 原生 prompt 在 Electron 里既没有目录选择器、
 * 样式也无法控制，还会阻塞渲染进程。
 */
export function NewTaskModal({ onClose, onCreate, initialDir }: Props) {
  const [dir, setDir] = useState(initialDir || '')
  const [title, setTitle] = useState(initialDir ? basename(initialDir) : '')
  const [busy, setBusy] = useState(false)

  const effectiveTitle = title.trim() || (dir ? basename(dir) : '')

  const pick = async () => {
    setBusy(true)
    try {
      const p = await native.pickDirectory()
      if (p) {
        setDir(p)
        if (!title.trim()) setTitle(basename(p))
      }
    } finally {
      setBusy(false)
    }
  }

  const submit = () => {
    onCreate(dir.trim(), effectiveTitle || '未命名任务')
  }

  return (
    <div className="backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="mh-icon" style={{ background: 'var(--brand-soft)', color: 'var(--brand-fg)' }}>
            <Icon name="plus" size={16} />
          </span>
          <span className="mh-title">新建任务</span>
          <button className="ibtn" onClick={onClose} title="关闭">
            <Icon name="x" size={15} />
          </button>
        </div>

        <div className="modal-body">
          <div className="field">
            <label className="field-label">工作区目录 <span style={{ color: 'var(--fg-muted)', fontSize: 11, fontWeight: 400 }}>（可选）</span></label>
            <div className="field-row">
              <input
                className="field-input"
                type="text"
                value={dir}
                placeholder="留空则使用默认工作区"
                onChange={(e) => setDir(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') submit() }}
              />
              <button onClick={pick} disabled={busy} type="button">
                <Icon name="folder" size={14} /> 选择
              </button>
            </div>
            <div className="field-hint">
              Agent 的所有读写都会被限制在这个目录内（沙箱边界）。
            </div>
          </div>

          <div className="field">
            <label className="field-label">任务名称</label>
            <input
              className="field-input"
              type="text"
              value={title}
              placeholder={dir ? basename(dir) : '留空则使用目录名'}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') submit() }}
            />
          </div>
        </div>

        <div className="modal-foot">
          <span className="mf-note">Enter 确认 · Esc 取消</span>
          <button onClick={onClose}>取消</button>
          <button className="primary" onClick={submit}>
            创建任务
          </button>
        </div>
      </div>
    </div>
  )
}

interface RenameProps {
  current: string
  onClose: () => void
  onConfirm: (title: string) => void
}

export function RenameModal({ current, onClose, onConfirm }: RenameProps) {
  const [value, setValue] = useState(current)

  return (
    <div className="backdrop" onClick={onClose}>
      <div className="modal" style={{ width: 400 }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="mh-icon" style={{ background: 'var(--bg-sunken)', color: 'var(--fg-secondary)' }}>
            <Icon name="pencil" size={16} />
          </span>
          <span className="mh-title">重命名任务</span>
          <button className="ibtn" onClick={onClose} title="关闭">
            <Icon name="x" size={15} />
          </button>
        </div>
        <div className="modal-body">
          <div className="field">
            <label className="field-label">显示名称</label>
            <input
              className="field-input"
              type="text"
              value={value}
              autoFocus
              maxLength={80}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && value.trim()) onConfirm(value.trim())
                if (e.key === 'Escape') onClose()
              }}
            />
            <div className="field-hint">只改显示名，工作区路径不受影响。</div>
          </div>
        </div>
        <div className="modal-foot">
          <button onClick={onClose}>取消</button>
          <button className="primary" onClick={() => onConfirm(value.trim())} disabled={!value.trim()}>
            保存
          </button>
        </div>
      </div>
    </div>
  )
}

interface ConfirmProps {
  title: string
  message: string
  confirmLabel?: string
  onClose: () => void
  onConfirm: () => void
}

export function ConfirmModal({
  title, message, confirmLabel = '确认', onClose, onConfirm,
}: ConfirmProps) {
  return (
    <div className="backdrop" onClick={onClose}>
      <div className="modal" style={{ width: 400 }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="mh-icon" style={{ background: 'var(--danger-soft)', color: 'var(--danger)' }}>
            <Icon name="alert" size={16} />
          </span>
          <span className="mh-title">{title}</span>
          <button className="ibtn" onClick={onClose} title="关闭">
            <Icon name="x" size={15} />
          </button>
        </div>
        <div className="modal-body">
          <div style={{ fontSize: 13, lineHeight: 1.65, color: 'var(--fg-secondary)' }}>
            {message}
          </div>
        </div>
        <div className="modal-foot">
          <button onClick={onClose}>取消</button>
          <button className="danger" onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  )
}
