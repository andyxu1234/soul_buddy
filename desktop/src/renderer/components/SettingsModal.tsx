import { useEffect, useState } from 'react'
import { Icon, type IconName } from './Icon'
import { type ThemeMode, applyTheme } from '../theme'
import { api, native } from '../api'
import type { MemoryItemRow } from '../api'

interface Props {
  theme: ThemeMode
  onThemeChange: (t: ThemeMode) => void
  onClose: () => void
  onToast?: (msg: string, tone?: 'ok' | 'err' | 'info') => void
}

interface TabDef {
  key: string
  icon: IconName
  label: string
}

const TABS: TabDef[] = [
  { key: 'general',  icon: 'settings', label: '通用' },
  { key: 'prompt',   icon: 'sparkles', label: '系统提示词' },
  { key: 'memory',   icon: 'folder',   label: '用户记忆' },
  { key: 'about',    icon: 'info',     label: '关于' },
]

const THEME_OPTS: Array<{ mode: ThemeMode; icon: IconName; label: string }> = [
  { mode: 'light', icon: 'sun', label: '浅色' },
  { mode: 'dark',  icon: 'moon', label: '深色' },
  { mode: 'auto',  icon: 'monitor', label: '跟随系统' },
]

interface MemFile {
  name: string
  path: string
  content: string
}

export function SettingsModal({ theme, onThemeChange, onClose, onToast }: Props) {
  const [tab, setTab] = useState<string>('general')

  // --- System prompt state ---
  const [promptText, setPromptText] = useState<string>('')
  const [promptSource, setPromptSource] = useState<string>('')
  const [promptCustom, setPromptCustom] = useState<boolean>(false)
  const [promptSaving, setPromptSaving] = useState<boolean>(false)
  const [promptDirty, setPromptDirty] = useState<boolean>(false)

  // --- User memory state ---
  const [memItems, setMemItems] = useState<MemoryItemRow[]>([])
  const [memFiles, setMemFiles] = useState<MemFile[]>([])
  const [memDir, setMemDir] = useState<string>('')
  const [memLoading, setMemLoading] = useState<boolean>(false)
  const [memLoaded, setMemLoaded] = useState<boolean>(false)
  const [memOpen, setMemOpen] = useState<Record<string, boolean>>({})

  const loadPrompt = async () => {
    try {
      const res = await api.getPrompt()
      setPromptText(res.text)
      setPromptSource(res.source)
      setPromptCustom(res.is_custom)
      setPromptDirty(false)
    } catch (e: any) {
      onToast?.(`加载提示词失败：${e?.detail || e?.message || e}`, 'err')
    }
  }

  const loadMemory = async () => {
    setMemLoading(true)
    try {
      const [items, files] = await Promise.all([
        api.listMemoryItems('user'),
        api.getMemoryFiles(),
      ])
      setMemItems(items.items || [])
      setMemFiles(files.files || [])
      setMemDir(files.dir || '')
    } catch (e: any) {
      onToast?.(`加载用户记忆失败：${e?.detail || e?.message || e}`, 'err')
    } finally {
      setMemLoading(false)
      setMemLoaded(true)
    }
  }

  useEffect(() => {
    if (tab === 'prompt' && !promptText) loadPrompt()
    if (tab === 'memory' && !memLoaded) loadMemory()
  }, [tab])

  const handleSavePrompt = async () => {
    setPromptSaving(true)
    try {
      await api.savePrompt(promptText)
      onToast?.('系统提示词已保存，下一次对话生效', 'ok')
      await loadPrompt()
    } catch (e: any) {
      onToast?.(`保存失败：${e?.detail || e?.message || e}`, 'err')
    } finally {
      setPromptSaving(false)
    }
  }

  const handleResetPrompt = async () => {
    setPromptSaving(true)
    try {
      await api.resetPrompt()
      onToast?.('已恢复默认系统提示词', 'ok')
      await loadPrompt()
    } catch (e: any) {
      onToast?.(`重置失败：${e?.detail || e?.message || e}`, 'err')
    } finally {
      setPromptSaving(false)
    }
  }

  const handleDeleteMemory = async (key: string) => {
    try {
      await api.deleteMemoryItem('user', key)
      onToast?.('记忆已删除', 'ok')
      await loadMemory()
    } catch (e: any) {
      onToast?.(`删除失败：${e?.detail || e?.message || e}`, 'err')
    }
  }

  const fmtTime = (ts: number | null) => {
    if (!ts) return '-'
    try { return new Date(ts * 1000).toLocaleString() } catch { return '-' }
  }

  return (
    <div className="backdrop" onClick={onClose}>
      <div className="sm" onClick={(e) => e.stopPropagation()}>
        <div className="sm-head">
          <div className="sm-title">设置</div>
          <button className="sm-close" onClick={onClose} title="关闭 (Esc)">
            <Icon name="x" size={16} />
          </button>
        </div>

        <div className="sm-body">
          {/* 左侧 tab 导航 */}
          <nav className="sm-nav">
            {TABS.map((t) => (
              <button
                key={t.key}
                className={`sm-nav-item ${tab === t.key ? 'active' : ''}`}
                onClick={() => setTab(t.key)}
              >
                <Icon name={t.icon} size={15} />
                <span>{t.label}</span>
              </button>
            ))}
          </nav>

          {/* 右侧内容 */}
          <div className="sm-content">
            {tab === 'general' && (
              <section className="sm-section">
                <h3 className="sm-section-title">外观</h3>
                <div className="sm-card">
                  <div className="sm-row">
                    <div className="sm-row-left">
                      <div className="sm-row-label">主题</div>
                      <div className="sm-row-hint">选择一个你喜欢的界面风格</div>
                    </div>
                    <div className="theme-picker">
                      {THEME_OPTS.map((o) => (
                        <button
                          key={o.mode}
                          className={`theme-opt ${theme === o.mode ? 'active' : ''}`}
                          onClick={() => { onThemeChange(o.mode); applyTheme(o.mode) }}
                        >
                          <Icon name={o.icon} size={16} />
                          <span>{o.label}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              </section>
            )}

            {tab === 'prompt' && (
              <section className="sm-section">
                <h3 className="sm-section-title">系统提示词</h3>
                <div className="sm-card">
                  <div className="sm-row" style={{ alignItems: 'center', gap: '12px' }}>
                    <div className="sm-row-left" style={{ flex: 1 }}>
                      <div className="sm-row-label">当前来源</div>
                      <div className="sm-row-hint">{promptSource || '加载中...'}</div>
                    </div>
                    {promptCustom ? (
                      <span className="pill ok" style={{ fontSize: '11px' }}>自定义</span>
                    ) : (
                      <span className="pill neutral" style={{ fontSize: '11px' }}>默认</span>
                    )}
                  </div>
                </div>

                <div className="sm-prompt-editor">
                  <textarea
                    className="sm-prompt-textarea"
                    value={promptText}
                    onChange={(e) => { setPromptText(e.target.value); setPromptDirty(true) }}
                    placeholder="加载中..."
                    spellCheck={false}
                  />
                </div>

                <div className="sm-prompt-actions">
                  <button
                    className="primary"
                    onClick={handleSavePrompt}
                    disabled={promptSaving || !promptDirty}
                  >
                    <Icon name="check" size={13} />
                    {promptSaving ? '保存中...' : '保存修改'}
                  </button>
                  <button
                    className="ghost"
                    onClick={handleResetPrompt}
                    disabled={promptSaving}
                  >
                    <Icon name="refresh" size={13} />
                    恢复默认
                  </button>
                  <span className="sm-prompt-hint">
                    修改后下一次对话生效，MCP/Skills 等动态区块会自动追加在末尾
                  </span>
                </div>
              </section>
            )}

            {tab === 'memory' && (
              <section className="sm-section">
                <h3 className="sm-section-title">用户记忆</h3>
                <div className="sm-row-hint" style={{ marginBottom: 10 }}>
                  agent 学到的长期偏好保存在下面两个文件里（跨项目、跨会话生效），
                  每次对话自动注入。删除某条后立即生效。对话中说「记住我喜欢…」这类
                  长期偏好后，agent 会自动保存到这里。
                </div>

                {memLoading && <div className="sm-row-hint">加载中…</div>}
                {!memLoading && memItems.length === 0 && (
                  <div className="sm-card">
                    <div className="sm-row-hint" style={{ textAlign: 'center', padding: '16px 0' }}>
                      暂无用户记忆
                    </div>
                  </div>
                )}
                {memItems.map((m) => (
                  <div className="rule-row" key={m.key}>
                    <span className="rr-tool">{m.kind === 'profile' ? '画像' : '偏好'}</span>
                    <span className="rr-pat" title={m.value}>{m.key}: {m.value}</span>
                    <span className="pill brand" title={`修订 ${m.revision}，更新于 ${fmtTime(m.updated_at)}`}>
                      rev {m.revision}
                    </span>
                    <button className="danger" onClick={() => handleDeleteMemory(m.key)}>删除</button>
                  </div>
                ))}

                {memDir && (
                  <button
                    className="theme-opt"
                    style={{ margin: '10px 0' }}
                    onClick={() => native.revealPath(memDir).catch(() => onToast?.('无法打开目录', 'err'))}
                    title={memDir}
                  >
                    <Icon name="folder" size={14} />
                    打开记忆目录
                  </button>
                )}

                {memFiles.map((f) => {
                  const open = !!memOpen[f.name]
                  return (
                    <div key={f.name} style={{ marginTop: 10 }}>
                      <button
                        className="sm-nav-item"
                        style={{ background: 'none', border: 'none', padding: '2px 0', width: 'auto' }}
                        onClick={() => setMemOpen((p) => ({ ...p, [f.name]: !p[f.name] }))}
                        title={f.path}
                      >
                        <Icon name={open ? 'chevron-down' : 'chevron-right'} size={12} />
                        <span>{f.name}</span>
                      </button>
                      {open && (
                        <pre className="mem-file-view">{f.content || '（空）'}</pre>
                      )}
                    </div>
                  )
                })}
              </section>
            )}

            {tab === 'about' && (
              <section className="sm-section">
                <h3 className="sm-section-title">关于 SoulBuddy</h3>
                <div className="sm-card sm-about">
                  <img className="sm-about-logo" src="./SoulBuddy.png" alt="SoulBuddy" />
                  <div className="sm-about-name">SoulBuddy</div>
                  <div className="sm-about-ver">本地 Agent · v0.1.0</div>
                  <div className="sm-about-desc">一个把 Agent 嵌进编辑器的桌面应用</div>
                </div>
              </section>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
