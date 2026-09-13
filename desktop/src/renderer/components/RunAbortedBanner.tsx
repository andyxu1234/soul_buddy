import { Icon } from './Icon'

interface Props {
  reason: string
  modifiedFiles: string[]
  turns: number
}

export function RunAbortedBanner({ reason, modifiedFiles, turns }: Props) {
  return (
    <div className="aborted-banner">
      <div className="h">
        <Icon name="alert" size={16} /> 运行已中止
      </div>
      <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 8 }}>
        原因：{reason}
      </div>
      <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
        已执行 {turns} 轮 · 以下文件已被本次运行修改（未自动回滚）：
      </div>
      {modifiedFiles.length > 0 ? (
        <ul>
          {modifiedFiles.map((f) => (
            <li key={f}>{f}</li>
          ))}
        </ul>
      ) : (
        <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
          无文件变更。
        </div>
      )}
    </div>
  )
}
