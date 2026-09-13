import { useEffect, useMemo, useState } from 'react'
import type { Artifact, SoulEvent } from '../types'
import { Icon, type IconName } from './Icon'
import { Markdown } from './Markdown'
import { native, api } from '../api'

interface Props {
  session: { workspace_root: string; provider: string; id: string }
  artifacts: Artifact[]
  events: SoulEvent[]
  onClose: () => void
  onToast: (msg: string, tone?: 'ok' | 'err' | 'info') => void
  /** External request to open a specific artifact (e.g. from present_files) */
  activePath?: string | null
}

type Tab = 'artifacts' | 'changes' | 'overview'

const EXT_ICON: Record<string, IconName> = {
  py: 'code', js: 'code', ts: 'code', tsx: 'code', jsx: 'code', go: 'code',
  rs: 'code', sh: 'terminal', bash: 'terminal',
  md: 'file', txt: 'file', json: 'file', yaml: 'file', yml: 'file',
  html: 'code', css: 'code', scss: 'code', svg: 'file',
  png: 'file', jpg: 'file', gif: 'file', exe: 'terminal',
}
function gicon(ext: string): IconName { return EXT_ICON[ext.toLowerCase()] || 'file' }

/** 会产生变更的工具 —— 用于「变更」页签，从事件流里真实推导。 */
const MUTATING = new Set(['write_file', 'edit_file', 'delete_file', 'move_file', 'create_file'])

export function RightPanel({ session, artifacts, events, onClose, onToast, activePath }: Props) {
  const [tab, setTab] = useState<Tab>('artifacts')
  // Currently previewed artifact + its content
  const [preview, setPreview] = useState<{ artifact: Artifact; content: string; loading: boolean; error?: string } | null>(null)

  const changes = useMemo(() => {
    const seen = new Map<string, { tool: string; path: string }>()
    for (const ev of events) {
      if (ev.type !== 'function_call') continue
      const tool = ev.data?.tool || ev.data?.name
      if (!MUTATING.has(tool)) continue
      const p = ev.data?.arguments?.path
      if (p && !seen.has(p)) seen.set(p, { tool, path: p })
    }
    // run_aborted 会带上真实的 modified_files，优先级更高
    for (const ev of events) {
      if (ev.type !== 'run_aborted') continue
      for (const p of ev.data?.modified_files || []) {
        if (!seen.has(p)) seen.set(p, { tool: 'modified', path: p })
      }
    }
    return [...seen.values()]
  }, [events])

  const stats = useMemo(() => ({
    user: events.filter((e) => e.type === 'message' && e.data?.role === 'user').length,
    assistant: events.filter((e) => e.type === 'message' && e.data?.role === 'assistant').length,
    tool: events.filter((e) => e.type === 'function_call').length,
  }), [events])

  const loadArtifact = async (a: Artifact) => {
    if (a.is_url) {
      onToast('远程产物暂不支持直接打开', 'info')
      return
    }
    setPreview({ artifact: a, content: '', loading: true })
    setTab('artifacts')
    try {
      const r = await api.getFileContent(session.id, a.path)
      setPreview({ artifact: a, content: r.content, loading: false })
    } catch (e: any) {
      setPreview({ artifact: a, content: '', loading: false, error: '无法读取文件内容' })
      onToast('无法读取文件', 'err')
    }
  }

  const openArtifact = (a: Artifact) => {
    if (a.is_url) {
      onToast('远程产物暂不支持直接打开', 'info')
      return
    }
    // Single-click now previews inline; double-click still opens in OS.
    loadArtifact(a)
  }

  const openInOS = (a: Artifact) => {
    native.revealPath(a.path).then(
      (ok) => { if (!ok) onToast('无法定位该文件', 'err') },
      () => onToast('无法定位该文件', 'err'),
    )
  }

  // Auto-open the externally-requested artifact (from present_files)
  useEffect(() => {
    if (!activePath) return
    const a = artifacts.find((x) => x.path === activePath)
    if (a && (!preview || preview.artifact.path !== activePath)) {
      loadArtifact(a)
    }
  }, [activePath, artifacts])

  const copyPath = (p: string) => {
    navigator.clipboard?.writeText(p).then(
      () => onToast('路径已复制', 'ok'),
      () => onToast('复制失败', 'err'),
    )
  }

  return (
    <div className="right-panel">
      <div className="right-head">
        <span className="rh-title">任务详情</span>
        <button className="ibtn" onClick={onClose} title="隐藏面板">
          <Icon name="chevron-right" size={15} />
        </button>
      </div>

      <div className="tabs">
        <button
          className={`tab ${tab === 'artifacts' ? 'active' : ''}`}
          onClick={() => setTab('artifacts')}
        >
          产物
          <span className="t-count">{artifacts.length}</span>
        </button>
        <button
          className={`tab ${tab === 'changes' ? 'active' : ''}`}
          onClick={() => setTab('changes')}
        >
          变更
          <span className="t-count">{changes.length}</span>
        </button>
        <button
          className={`tab ${tab === 'overview' ? 'active' : ''}`}
          onClick={() => setTab('overview')}
        >
          概览
        </button>
      </div>

      <div className="right-body scroll">
        {tab === 'artifacts' && (
          <div className="artifacts-layout">
            {/* Left: file list */}
            <div className="artifacts-list scroll">
              {artifacts.length === 0 ? (
                <div className="sidebar-empty" style={{ paddingTop: 40 }}>
                  <Icon name="folder" size={28} />
                  <div style={{ marginTop: 10 }}>还没有产出物</div>
                  <div>Agent 产出文件后会在这里列出</div>
                </div>
              ) : (
                <>
                  {['主要产出', '其他产物'].map((group) => {
                    const list = artifacts.filter((a) =>
                      group === '主要产出' ? a.is_primary : !a.is_primary)
                    if (list.length === 0) return null
                    return (
                      <div key={group}>
                        <div className="section-label"><span>{group}</span></div>
                        {list.map((a) => (
                          <div
                            key={a.path}
                            className={`artifact-item ${a.is_primary ? 'primary' : ''} ${preview?.artifact.path === a.path ? 'active' : ''}`}
                            onClick={() => loadArtifact(a)}
                            onDoubleClick={() => openInOS(a)}
                            title={`${a.path}（双击在系统中打开）`}
                          >
                            <span className="ai-icon"><Icon name={gicon(a.extension)} size={15} /></span>
                            <span className="ai-info">
                              <span className="ai-name">{a.name}</span>
                              <span className="ai-sub">
                                {a.size}{a.category ? ` · ${a.category}` : ''}
                              </span>
                            </span>
                            <span
                              className="ai-open"
                              onClick={(e) => { e.stopPropagation(); copyPath(a.path) }}
                              title="复制路径"
                            >
                              <Icon name="copy" size={13} />
                            </span>
                          </div>
                        ))}
                      </div>
                    )
                  })}
                </>
              )}
            </div>

            {/* Right: preview area */}
            <div className="artifacts-preview">
              {!preview ? (
                <div className="preview-empty">
                  <Icon name="file" size={32} />
                  <div style={{ marginTop: 12, color: 'var(--fg-tertiary)' }}>
                    选择左侧文件以预览
                  </div>
                  <div style={{ fontSize: 12, marginTop: 4, color: 'var(--fg-tertiary)' }}>
                    HTML / 图片 / 文本可在此直接查看
                  </div>
                </div>
              ) : preview.loading ? (
                <div className="preview-empty">
                  <div className="spin" />
                  <div style={{ marginTop: 12 }}>加载中…</div>
                </div>
              ) : preview.error ? (
                <div className="preview-empty">
                  <Icon name="alert" size={32} />
                  <div style={{ marginTop: 12 }}>{preview.error}</div>
                </div>
              ) : (
                <ArtifactPreview artifact={preview.artifact} content={preview.content} onOpenInOS={() => openInOS(preview.artifact)} />
              )}
            </div>
          </div>
        )}

        {tab === 'changes' && (
          <>
            {changes.length === 0 ? (
              <div className="sidebar-empty" style={{ paddingTop: 40 }}>
                <Icon name="pencil" size={26} />
                <div style={{ marginTop: 10 }}>还没有文件变更</div>
              </div>
            ) : (
              <>
                <div className="section-label"><span>已改动文件</span><span className="count">{changes.length}</span></div>
                {changes.map((c) => (
                  <div
                    key={c.path}
                    className="artifact-item"
                    onClick={() => native.revealPath(c.path)}
                    title={c.path}
                  >
                    <span className="ai-icon"><Icon name="pencil" size={14} /></span>
                    <span className="ai-info">
                      <span className="ai-name">
                        {c.path.replace(/\\/g, '/').split('/').pop()}
                      </span>
                      <span className="ai-sub" style={{ fontFamily: 'var(--mono)', fontSize: 10.5 }}>
                        {c.tool}
                      </span>
                    </span>
                    <span className="ai-open"><Icon name="external" size={13} /></span>
                  </div>
                ))}
              </>
            )}
          </>
        )}

        {tab === 'overview' && (
          <>
            <div className="section-label"><span>会话统计</span></div>
            <Stat label="用户消息" value={String(stats.user)} />
            <Stat label="AI 回复" value={String(stats.assistant)} />
            <Stat label="工具调用" value={String(stats.tool)} />
            <Stat label="产物文件" value={String(artifacts.length)} />

            <div className="section-label"><span>会话信息</span></div>
            <Stat label="Provider" value={session.provider} />
            <Stat label="工作区" value={session.workspace_root} mono />
            <Stat label="会话 ID" value={session.id.slice(0, 12)} mono />
          </>
        )}
      </div>
    </div>
  )
}

function Stat({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="stat-row" title={value}>
      <span className="sr-lbl">{label}</span>
      <span className="sr-val" style={{ fontFamily: mono ? 'var(--mono)' : undefined }}>{value}</span>
    </div>
  )
}

/** Inline preview of a deliverable file — HTML renders in iframe, images via
 *  file:// URL, markdown via the shared Markdown component, everything else
 *  as a read-only code block. */
function ArtifactPreview({ artifact, content, onOpenInOS }: {
  artifact: Artifact; content: string; onOpenInOS: () => void
}) {
  const ext = artifact.extension.toLowerCase()
  const isHtml = ext === '.html' || ext === '.htm'
  const isImg = ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp'].includes(ext)
  const isMd = ext === '.md' || ext === '.markdown'
  // Build a safe file:// URL for the renderer (Electron allows file:// from
  // the renderer when webSecurity permits; images typically work without issue).
  const fileUrl = `file://${artifact.path.replace(/\\/g, '/')}`

  return (
    <div className="artifact-preview-inner">
      <div className="ap-head">
        <span className="ap-name" title={artifact.path}>{artifact.name}</span>
        <span className="ap-size">{artifact.size}</span>
        <button className="ibtn sm" onClick={onOpenInOS} title="在系统文件管理器中打开">
          <Icon name="folder" size={13} />
        </button>
      </div>
      <div className="ap-body">
        {isHtml ? (
          <iframe
            className="ap-iframe"
            title={artifact.name}
            srcDoc={content}
            sandbox="allow-same-origin allow-scripts allow-forms allow-popups"
          />
        ) : isImg ? (
          <img className="ap-img" src={fileUrl} alt={artifact.name} />
        ) : isMd ? (
          <div className="ap-md">
            <Markdown text={content} />
          </div>
        ) : (
          <pre className="ap-code"><code>{content}</code></pre>
        )}
      </div>
    </div>
  )
}
