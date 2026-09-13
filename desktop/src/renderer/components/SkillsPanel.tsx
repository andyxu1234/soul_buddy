import { useEffect, useState } from 'react'
import { Icon } from './Icon'
import { api } from '../api'

interface SkillItem {
  id: string
  name: string
  description: string
  enabled: boolean
  category: string
  icon: string
}

interface Props {
  onToast: (msg: string, tone?: 'ok' | 'err' | 'info') => void
  workspaceRoot?: string
}

/** 根据 skill title 推断图标，找不到时回退到 sparkles。 */
function guessIcon(title: string): string {
  const t = title.toLowerCase()
  if (/(code|review|审查|代码)/.test(t)) return 'code'
  if (/(test|测试|单测)/.test(t)) return 'terminal'
  if (/(doc|文档|readme|api)/.test(t)) return 'book'
  if (/(refactor|重构)/.test(t)) return 'sparkles'
  if (/(bug|debug|诊断|错误|日志)/.test(t)) return 'alert'
  if (/(git|commit|提交)/.test(t)) return 'folder'
  if (/(deploy|部署|发布)/.test(t)) return 'zap'
  if (/(db|sql|database|数据库)/.test(t)) return 'file'
  return 'sparkles'
}

export function SkillsPanel({ onToast, workspaceRoot }: Props) {
  const [skills, setSkills] = useState<SkillItem[]>([])
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState<'installed' | 'store'>('installed')
  const [query, setQuery] = useState('')

  const loadSkills = async () => {
    setLoading(true)
    try {
      const res = await api.listSkills(workspaceRoot)
      const list = (res.skills || []).map((s) => ({
        id: s.title,
        name: s.title,
        description: s.summary || '(无描述)',
        enabled: true,
        category: s.source === 'project' ? '项目级' : '用户级',
        icon: guessIcon(s.title),
      }))
      setSkills(list)
    } catch (e: any) {
      onToast(`加载 Skills 失败：${e?.detail || e?.message || e}`, 'err')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadSkills()
    // workspaceRoot 变化时重新加载
  }, [workspaceRoot])

  const toggleSkill = (id: string) => {
    setSkills((prev) => prev.map((s) => {
      if (s.id === id) {
        onToast(`${s.enabled ? '已禁用' : '已启用'}：${s.name}`, 'ok')
        return { ...s, enabled: !s.enabled }
      }
      return s
    }))
  }

  const filtered = skills.filter(
    (s) => !query || s.name.toLowerCase().includes(query.toLowerCase()) || s.description.toLowerCase().includes(query.toLowerCase()),
  )

  const categories = [...new Set(skills.map((s) => s.category))]

  return (
    <div className="plugin-panel">
      <div className="plugin-topbar">
        <div className="plugin-title">
          <div className="plugin-title-icon" style={{ background: 'var(--tool-soft)', color: 'var(--tool)' }}>
            <Icon name="sparkles" size={16} />
          </div>
          <div>
            <h2>Skills</h2>
            <p>自动化任务模板，一键复用最佳实践</p>
          </div>
        </div>
        <div className="plugin-search">
          <Icon name="search" size={14} />
          <input
            type="search"
            placeholder="搜索 Skill..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>

      <div className="plugin-tabs">
        <button
          className={`plugin-tab ${activeTab === 'installed' ? 'active' : ''}`}
          onClick={() => setActiveTab('installed')}
        >
          <Icon name="check" size={13} />
          已安装
          <span className="plugin-tab-count">{skills.filter((s) => s.enabled).length}</span>
        </button>
        <button
          className={`plugin-tab ${activeTab === 'store' ? 'active' : ''}`}
          onClick={() => setActiveTab('store')}
        >
          <Icon name="plus" size={13} />
          SkillHub
        </button>
      </div>

      <div className="plugin-body scroll">
        {activeTab === 'installed' ? (
          <>
            {loading ? (
              <div className="plugin-empty">
                <Icon name="refresh" size={28} />
                <p>加载中...</p>
              </div>
            ) : categories.length === 0 ? (
              <div className="plugin-empty">
                <Icon name="sparkles" size={28} />
                <p>还没有安装任何 Skill</p>
                <span>将 SKILL.md 放入 ~/.soul_buddy/skills/ 或项目 .soul_buddy/skills/ 目录</span>
              </div>
            ) : (
              categories.map((cat) => {
                const items = filtered.filter((s) => s.category === cat)
                if (items.length === 0) return null
                return (
                  <div key={cat} className="plugin-group">
                    <div className="plugin-group-title">{cat}</div>
                    <div className="plugin-grid">
                      {items.map((skill) => (
                        <div key={skill.id} className={`plugin-card ${skill.enabled ? '' : 'disabled'}`}>
                          <div className="plugin-card-head">
                            <div className="plugin-card-icon">
                              <Icon name={skill.icon as any} size={18} />
                            </div>
                            <label className="plugin-switch">
                              <input
                                type="checkbox"
                                checked={skill.enabled}
                                onChange={() => toggleSkill(skill.id)}
                              />
                              <span className="plugin-switch-slider" />
                            </label>
                          </div>
                          <div className="plugin-card-name">{skill.name}</div>
                          <div className="plugin-card-desc">{skill.description}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                )
              })
            )}
            {!loading && filtered.length === 0 && skills.length > 0 && (
              <div className="plugin-empty">
                <Icon name="search" size={28} />
                <p>没有找到匹配的 Skill</p>
              </div>
            )}
          </>
        ) : (
          <div className="plugin-store-placeholder">
            <div className="plugin-empty">
              <Icon name="sparkles" size={28} />
              <p>SkillHub 即将上线</p>
              <span>这里将展示可下载的社区 Skill 市场</span>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
