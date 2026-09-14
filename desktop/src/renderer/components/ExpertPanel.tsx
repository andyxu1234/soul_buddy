import { useCallback, useEffect, useState } from 'react'
import { api, type ExpertRow, type KbRow } from '../api'
import { Icon } from './Icon'

interface Props {
  onToast: (msg: string, tone?: 'ok' | 'err' | 'info') => void
}

function errText(e: unknown): string {
  const anyErr = e as { detail?: unknown; status?: number }
  if (anyErr && typeof anyErr === 'object' && 'detail' in anyErr) {
    const d = anyErr.detail
    if (typeof d === 'string') return d
    if (d != null) return JSON.stringify(d)
  }
  return String(e)
}

export function ExpertPanel({ onToast }: Props) {
  const [experts, setExperts] = useState<ExpertRow[]>([])
  const [editing, setEditing] = useState<ExpertRow | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [loading, setLoading] = useState(true)

  const reload = useCallback(async () => {
    try {
      const res = await api.listExperts()
      setExperts(res.experts)
    } catch (e) {
      onToast(`加载专家失败：${errText(e)}`, 'err')
    } finally {
      setLoading(false)
    }
  }, [onToast])

  useEffect(() => { void reload() }, [reload])

  const toggleExpert = async (expert: ExpertRow) => {
    try {
      const updated = await api.updateExpert(expert.id, { enabled: !expert.enabled })
      setExperts((prev) => prev.map((e) => (e.id === expert.id ? updated : e)))
      onToast(`${updated.enabled ? '已启用' : '已禁用'}：${updated.name}`, 'ok')
    } catch (e) {
      onToast(`操作失败：${errText(e)}`, 'err')
    }
  }

  const handleDelete = async (expert: ExpertRow) => {
    try {
      await api.deleteExpert(expert.id)
      onToast(`已删除：${expert.name}`, 'info')
      void reload()
    } catch (e) {
      onToast(`删除失败：${errText(e)}`, 'err')
    }
  }

  const handleSave = async (fields: {
    id: string | null
    name: string; role: string; systemPrompt: string; color: string
    kbIds: string[]
  }) => {
    try {
      if (fields.id) {
        const updated = await api.updateExpert(fields.id, {
          name: fields.name, role: fields.role,
          systemPrompt: fields.systemPrompt, color: fields.color,
          kbIds: fields.kbIds,
        })
        setExperts((prev) => prev.map((e) => (e.id === fields.id ? updated : e)))
      } else {
        const created = await api.createExpert({
          name: fields.name, role: fields.role,
          systemPrompt: fields.systemPrompt, color: fields.color,
          kbIds: fields.kbIds,
        })
        setExperts((prev) => [...prev, created])
      }
      setShowForm(false)
      setEditing(null)
      onToast(`已保存：${fields.name}`, 'ok')
    } catch (e) {
      onToast(`保存失败：${errText(e)}`, 'err')
    }
  }

  return (
    <div className="plugin-panel">
      <div className="plugin-topbar">
        <div className="plugin-title">
          <div className="plugin-title-icon" style={{ background: 'var(--brand-soft)', color: 'var(--brand-fg)' }}>
            <Icon name="brain" size={16} />
          </div>
          <div>
            <h2>Experts</h2>
            <p>预设的角色和系统提示词，快速切换专业视角</p>
          </div>
        </div>
        <button className="primary" onClick={() => { setEditing(null); setShowForm(true) }}>
          <Icon name="plus" size={14} />
          新建专家
        </button>
      </div>

      <div className="plugin-body scroll">
        <div className="plugin-list">
          {experts.map((expert) => (
            <div key={expert.id} className={`plugin-row ${expert.enabled ? '' : 'disabled'}`}>
              <div className="plugin-row-avatar" style={{ background: expert.color }}>
                {expert.name.charAt(0)}
              </div>
              <div className="plugin-row-info">
                <div className="plugin-row-name">
                  {expert.name}
                  {expert.isBuiltin && <span className="plugin-badge">内置</span>}
                  {!expert.enabled && <span className="plugin-badge disabled">已禁用</span>}
                </div>
                <div className="plugin-row-sub">{expert.role}</div>
                <div className="plugin-row-prompt" title={expert.systemPrompt}>
                  {expert.systemPrompt}
                </div>
              </div>
              <div className="plugin-row-actions">
                <button className="ibtn" title="编辑" onClick={() => { setEditing(expert); setShowForm(true) }}>
                  <Icon name="pencil" size={14} />
                </button>
                <label className="plugin-switch">
                  <input
                    type="checkbox"
                    checked={expert.enabled}
                    onChange={() => void toggleExpert(expert)}
                  />
                  <span className="plugin-switch-slider" />
                </label>
                {!expert.isBuiltin && (
                  <button className="ibtn" title="删除" onClick={() => void handleDelete(expert)}>
                    <Icon name="trash" size={14} />
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>

        {!loading && experts.length === 0 && (
          <div className="plugin-empty">
            <Icon name="brain" size={28} />
            <p>还没有专家</p>
            <span>点击右上角「新建专家」创建</span>
          </div>
        )}
        {loading && (
          <div className="plugin-empty">
            <p>加载中…</p>
          </div>
        )}
      </div>

      {showForm && (
        <ExpertForm
          expert={editing}
          onClose={() => { setShowForm(false); setEditing(null) }}
          onSave={handleSave}
        />
      )}
    </div>
  )
}

interface SaveFields {
  id: string | null
  name: string
  role: string
  systemPrompt: string
  color: string
  kbIds: string[]
}

function ExpertForm({
  expert,
  onClose,
  onSave,
}: {
  expert: ExpertRow | null
  onClose: () => void
  onSave: (f: SaveFields) => void
}) {
  const [name, setName] = useState(expert?.name ?? '')
  const [role, setRole] = useState(expert?.role ?? '')
  const [prompt, setPrompt] = useState(expert?.systemPrompt ?? '')
  const [color, setColor] = useState(expert?.color ?? '#7c3aed')
  const [kbIds, setKbIds] = useState<string[]>(expert?.kbIds ?? [])
  const [kbs, setKbs] = useState<KbRow[]>([])

  useEffect(() => {
    let cancelled = false
    api.listKnowledgeBases()
      .then((res) => { if (!cancelled) setKbs(res.kbs) })
      .catch(() => { /* 资料库不可用时隐藏绑定区 */ })
    return () => { cancelled = true }
  }, [])

  const toggleKb = (id: string) => {
    setKbIds((prev) => (prev.includes(id)
      ? prev.filter((x) => x !== id)
      : [...prev, id]))
  }

  const handleSubmit = () => {
    if (!name.trim()) return
    onSave({
      id: expert?.id ?? null,
      name: name.trim(),
      role: role.trim(),
      systemPrompt: prompt.trim(),
      color,
      kbIds,
    })
  }

  const PRESET_COLORS = ['#7c3aed', '#3b82f6', '#16a34a', '#c2740a', '#d33b3b', '#db2777']

  return (
    <div className="backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div className="mh-icon" style={{ background: color, color: '#fff' }}>
            {(name || '专').charAt(0)}
          </div>
          <div className="mh-title">{expert ? '编辑专家' : '新建专家'}</div>
          <button className="ibtn" onClick={onClose}>
            <Icon name="x" size={16} />
          </button>
        </div>
        <div className="modal-body">
          <div className="field">
            <label className="field-label">名称</label>
            <input
              type="text"
              className="field-input"
              value={name}
              placeholder="如：架构师"
              onChange={(e) => setName(e.target.value)}
              autoFocus
            />
          </div>
          <div className="field">
            <label className="field-label">角色描述</label>
            <input
              type="text"
              className="field-input"
              value={role}
              placeholder="简短描述这个专家擅长什么"
              onChange={(e) => setRole(e.target.value)}
            />
          </div>
          <div className="field">
            <label className="field-label">系统提示词</label>
            <textarea
              className="field-input"
              rows={5}
              value={prompt}
              placeholder="定义这个专家的行为和专长..."
              onChange={(e) => setPrompt(e.target.value)}
            />
            <div className="field-hint">这段 prompt 会作为 system message 发送给模型（绑定专家的会话生效）</div>
          </div>
          <div className="field">
            <label className="field-label">颜色标识</label>
            <div className="color-picker">
              {PRESET_COLORS.map((c) => (
                <button
                  key={c}
                  className={`color-dot ${color === c ? 'active' : ''}`}
                  style={{ background: c }}
                  onClick={() => setColor(c)}
                />
              ))}
            </div>
          </div>
          {kbs.length > 0 && (
            <div className="field">
              <label className="field-label">绑定资料库（绑定后可通过检索工具引用文档回答）</label>
              <div className="kb-bind-list">
                {kbs.map((k) => (
                  <label key={k.id} className="kb-bind-item">
                    <input
                      type="checkbox"
                      checked={kbIds.includes(k.id)}
                      onChange={() => toggleKb(k.id)}
                    />
                    <span>{k.name}</span>
                    <span className="plugin-row-sub">{k.document_count} 文档</span>
                  </label>
                ))}
              </div>
            </div>
          )}
        </div>
        <div className="modal-foot">
          <button onClick={onClose}>取消</button>
          <button className="primary" onClick={handleSubmit} disabled={!name.trim()}>
            {expert ? '保存' : '创建'}
          </button>
        </div>
      </div>
    </div>
  )
}
