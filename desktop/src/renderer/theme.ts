export type ThemeMode = 'light' | 'dark' | 'system'

const KEY = 'soulbuddy.theme'

function prefersDark(): boolean {
  return typeof window !== 'undefined' &&
    !!window.matchMedia?.('(prefers-color-scheme: dark)').matches
}

export function resolveTheme(mode: ThemeMode): 'light' | 'dark' {
  return mode === 'system' ? (prefersDark() ? 'dark' : 'light') : mode
}

export function loadTheme(): ThemeMode {
  try {
    const v = localStorage.getItem(KEY)
    if (v === 'light' || v === 'dark' || v === 'system') return v
  } catch { /* localStorage 不可用时回退 */ }
  return 'system'
}

export function saveTheme(mode: ThemeMode): void {
  try { localStorage.setItem(KEY, mode) } catch { /* ignore */ }
}

/** 把主题写到 <html data-theme>，并同步 body 背景避免加载期闪白/闪黑。 */
export function applyTheme(mode: ThemeMode): void {
  const resolved = resolveTheme(mode)
  const root = document.documentElement
  root.setAttribute('data-theme', resolved)
  root.style.background = resolved === 'dark' ? '#17181c' : '#ffffff'
  document.body.style.background = resolved === 'dark' ? '#17181c' : '#ffffff'
}

/** 跟随系统模式时监听系统主题变化。返回取消监听函数。 */
export function watchSystemTheme(mode: ThemeMode, onChange: () => void): () => void {
  if (mode !== 'system' || typeof window === 'undefined') return () => {}
  const mq = window.matchMedia('(prefers-color-scheme: dark)')
  const handler = () => onChange()
  mq.addEventListener?.('change', handler)
  return () => mq.removeEventListener?.('change', handler)
}
