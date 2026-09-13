import type { Artifact } from '../types'

/**
 * 旧版 ArtifactCard — 保留用于向后兼容
 * 新版使用 RightPanel 中的列表式展示
 */
export function ArtifactCard({ artifact: a }: { artifact: Artifact }) {
  return (
    <div className="artifact-item" title={a.path}>
      <div className="artifact-icon-box">{a.icon}</div>
      <div className="artifact-info">
        <div className="artifact-name">{a.name}</div>
        <div className="artifact-sub">{a.exists ? a.size : '已删除'} · {a.category}</div>
      </div>
      {a.is_primary && <span className="artifact-primary-badge">主要</span>}
    </div>
  )
}

export function ArtifactList({ artifacts }: { artifacts: Artifact[] }) {
  if (!artifacts.length) return null
  return (
    <div style={{ margin: '8px 24px' }}>
      <div style={{
        fontSize: 11,
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: '0.06em',
        color: 'var(--text-muted)',
        padding: '10px 8px 6px',
      }}>产出物 ({artifacts.length})</div>
      {artifacts.map((a) => (
        <ArtifactCard key={a.path} artifact={a} />
      ))}
    </div>
  )
}
