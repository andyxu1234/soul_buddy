import { useEffect, useState } from 'react'
import { api } from '../api'
import type { PermissionRule } from '../types'
import { Icon, type IconName } from './Icon'
import { type ThemeMode, applyTheme } from '../theme'

interface Props {
  theme: ThemeMode
  onThemeChange: (t: ThemeMode) => void
  onClose: () => void
  onToast: (msg: string, tone?: 'ok' | 'err' | 'info') => void
}

const THEME_OPTS: Array<{ mode: ThemeMode; icon: IconName; label: string }> = [
  { mode: 'light', icon: 'sun', label: '浅色' },
  { mode: 'dark', icon: 'moon', label: '深色' },
  { mode: 'system', icon: 'monitor', label: '跟随系统' },
]

export function SettingsPanel({ theme, onThemeChange, onClose, onToast }: Props) {
  const [rules, setRules] = useState<PermissionRule[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    setLoading(true)
    api
      .listRules()
      .then((r) => setRules((r || []) as PermissionRule[]))
      .catch((e) => setError(String(e?.detail ?? e)))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  const revoke = (id: string) => {
    api
      .revokeRule(id)
      .then(() => { setRules((p) => p.filter((r) => r.id !== id)); onToast('规则已撤销', 'ok') })
      .catch((e) => onToast(String(e?.detail ?? e), 'err'))
  }

  return (
    <div className="main">
      <div className="topbar">
        <button className="ibtn" onClick={onClose} title="返回对话">
          <Icon name="chevron-left" size={16} />
        </button>
        <div className="topbar-title">设置</div>
        <span className="topbar-spacer" />
      </div>

      <div className="settings scroll">
        <div className="settings-inner">
          <h2>设置</h2>
          <div className="st-sub">外观与权限记忆</div>

          <div className="settings-section">
            <h3>外观</h3>
            <div className="theme-picker">
              {THEME_OPTS.map((o) => (
                <button
                  key={o.mode}
                  className={`theme-opt ${theme === o.mode ? 'active' : ''}`}
                  onClick={() => { onThemeChange(o.mode); applyTheme(o.mode) }}
                >
                  <Icon name={o.icon} size={18} />
                  {o.label}
                </button>
              ))}
            </div>
          </div>

          <div className="settings-section">
            <h3>权限记忆规则</h3>
            {loading && <div className="note">加载中…</div>}
            {error && <div className="note err">{error}</div>}
            {!loading && rules.length === 0 && (
              <div className="note" style={{ justifyContent: 'center', padding: 20 }}>
                暂无记忆规则。在授权弹窗里选择「允许该目录」后会自动生成。
              </div>
            )}
            {rules.map((r) => (
              <div className="rule-row" key={r.id}>
                <span className="rr-tool">{r.tool}</span>
                <span className="rr-pat" title={r.pattern}>{r.pattern}</span>
                <span className="pill brand">{r.kind}</span>
                <button className="danger" onClick={() => revoke(r.id)}>撤销</button>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
