import { useState } from 'react'
import { Icon } from './Icon'

interface ExpertItem {
  id: string
  name: string
  role: string
  systemPrompt: string
  enabled: boolean
  color: string
}

const DEFAULT_EXPERTS: ExpertItem[] = [
  {
    id: 'architect',
    name: '架构师',
    role: '系统设计与技术选型',
    systemPrompt: '你是一位资深软件架构师，擅长系统设计、技术选型和性能优化。在给出建议时，请考虑可扩展性、可维护性和团队协作成本。',
    enabled: true,
    color: '#7c3aed',
  },
  {
    id: 'frontend',
    name: '前端专家',
    role: 'UI 实现与交互优化',
    systemPrompt: '你是一位精通 React/TypeScript 的前端专家，关注组件化设计、性能优化和用户体验。输出代码时优先考虑 TypeScript 类型安全。',
    enabled: true,
    color: '#3b82f6',
  },
  {
    id: 'backend',
    name: '后端工程师',
    role: 'API 设计与数据建模',
    systemPrompt: '你是一位后端工程师，熟悉 Python/FastAPI/PostgreSQL。设计 API 时遵循 RESTful 规范，关注数据一致性和接口安全性。',
    enabled: true,
    color: '#16a34a',
  },
  {
    id: 'reviewer',
    name: '代码审查员',
    role: 'Code Review 与质量把控',
    systemPrompt: '你是一位严格的代码审查员，关注代码可读性、边界条件处理、错误处理和测试覆盖。审查时按严重程度分类问题。',
    enabled: false,
    color: '#c2740a',
  },
]

interface Props {
  onToast: (msg: string, tone?: 'ok' | 'err' | 'info') => void
}

export function ExpertPanel({ onToast }: Props) {
  const [experts, setExperts] = useState<ExpertItem[]>(DEFAULT_EXPERTS)
  const [editing, setEditing] = useState<ExpertItem | null>(null)
  const [showForm, setShowForm] = useState(false)

  const toggleExpert = (id: string) => {
    setExperts((prev) => prev.map((e) => {
      if (e.id === id) {
        onToast(`${e.enabled ? '已禁用' : '已启用'}：${e.name}`, 'ok')
        return { ...e, enabled: !e.enabled }
      }
      return e
    }))
  }

  const handleDelete = (id: string) => {
    const expert = experts.find((e) => e.id === id)
    setExperts((prev) => prev.filter((e) => e.id !== id))
    onToast(`已删除：${expert?.name ?? '专家'}`, 'info')
  }

  const handleSave = (expert: ExpertItem) => {
    setExperts((prev) => {
      const exists = prev.some((e) => e.id === expert.id)
      if (exists) return prev.map((e) => (e.id === expert.id ? expert : e))
      return [...prev, expert]
    })
    setShowForm(false)
    setEditing(null)
    onToast(`已保存：${expert.name}`, 'ok')
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
                    onChange={() => toggleExpert(expert.id)}
                  />
                  <span className="plugin-switch-slider" />
                </label>
                <button className="ibtn" title="删除" onClick={() => handleDelete(expert.id)}>
                  <Icon name="trash" size={14} />
                </button>
              </div>
            </div>
          ))}
        </div>

        {experts.length === 0 && (
          <div className="plugin-empty">
            <Icon name="brain" size={28} />
            <p>还没有专家</p>
            <span>点击右上角「新建专家」创建</span>
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

function ExpertForm({
  expert,
  onClose,
  onSave,
}: {
  expert: ExpertItem | null
  onClose: () => void
  onSave: (e: ExpertItem) => void
}) {
  const [name, setName] = useState(expert?.name ?? '')
  const [role, setRole] = useState(expert?.role ?? '')
  const [prompt, setPrompt] = useState(expert?.systemPrompt ?? '')
  const [color, setColor] = useState(expert?.color ?? '#7c3aed')

  const handleSubmit = () => {
    if (!name.trim()) return
    onSave({
      id: expert?.id ?? `expert-${Date.now()}`,
      name: name.trim(),
      role: role.trim() || '自定义角色',
      systemPrompt: prompt.trim(),
      enabled: expert?.enabled ?? true,
      color,
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
            <div className="field-hint">这段 prompt 会作为 system message 发送给模型</div>
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
