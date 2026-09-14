import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type KbDocumentRow, type KbRow } from '../api'
import { Icon } from './Icon'

interface Props {
  onToast: (msg: string, tone?: 'ok' | 'err' | 'info') => void
}

const STATUS_LABEL: Record<KbDocumentRow['status'], string> = {
  pending: '排队中',
  parsing: '解析中',
  chunking: '分块中',
  embedding: '向量化中',
  indexing: '入库中',
  ready: '已就绪',
  failed: '失败',
}

function errText(e: unknown): string {
  const anyErr = e as { detail?: unknown }
  if (anyErr && typeof anyErr === 'object' && 'detail' in anyErr) {
    const d = anyErr.detail
    if (typeof d === 'string') return d
    if (d != null) return JSON.stringify(d)
  }
  return String(e)
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function KnowledgePanel({ onToast }: Props) {
  const [kbs, setKbs] = useState<KbRow[]>([])
  const [activeKbId, setActiveKbId] = useState<string>('default')
  const [docs, setDocs] = useState<KbDocumentRow[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchHits, setSearchHits] = useState<Array<{
    doc_name: string; heading_path: string; text: string; score: number
  }> | null>(null)
  const [searching, setSearching] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const pollRef = useRef<number | null>(null)

  const reloadKbs = useCallback(async (preferId?: string) => {
    try {
      const res = await api.listKnowledgeBases()
      setKbs(res.kbs)
      setActiveKbId((cur) => {
        if (preferId && res.kbs.some((k) => k.id === preferId)) return preferId
        if (res.kbs.some((k) => k.id === cur)) return cur
        return res.kbs[0]?.id ?? 'default'
      })
    } catch (e) {
      onToast(`加载资料库失败：${errText(e)}`, 'err')
    } finally {
      setLoading(false)
    }
  }, [onToast])

  const reloadDocs = useCallback(async (kbId: string) => {
    if (!kbId) return
    try {
      const res = await api.listKbDocuments(kbId)
      setDocs(res.documents)
    } catch (e) {
      onToast(`加载文档失败：${errText(e)}`, 'err')
    }
  }, [onToast])

  useEffect(() => { void reloadKbs() }, [reloadKbs])
  useEffect(() => { void reloadDocs(activeKbId) }, [activeKbId, reloadDocs])

  // 有未就绪文档时轮询刷新（索引是后台异步的）
  useEffect(() => {
    const busy = docs.some((d) => d.status !== 'ready' && d.status !== 'failed')
    if (!busy) {
      if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null }
      return
    }
    pollRef.current = window.setInterval(() => void reloadDocs(activeKbId), 2000)
    return () => {
      if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null }
    }
  }, [docs, activeKbId, reloadDocs])

  const handleUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setUploading(true)
    try {
      const res = await api.uploadKbDocuments(activeKbId, Array.from(files))
      const okCount = res.documents.length
      for (const r of res.rejected) onToast(`跳过 ${r.filename}：${r.reason}`, 'err')
      if (okCount > 0) {
        onToast(`已上传 ${okCount} 个文档，后台索引中…`, 'ok')
        await reloadKbs(activeKbId)
        await reloadDocs(activeKbId)
      }
    } catch (e) {
      onToast(`上传失败：${errText(e)}`, 'err')
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const handleDeleteDoc = async (doc: KbDocumentRow) => {
    try {
      await api.deleteKbDocument(doc.kb_id, doc.id)
      onToast(`已删除：${doc.filename}`, 'info')
      await reloadDocs(doc.kb_id)
      await reloadKbs(doc.kb_id)
    } catch (e) {
      onToast(`删除失败：${errText(e)}`, 'err')
    }
  }

  const handleReindex = async (doc: KbDocumentRow) => {
    try {
      await api.reindexKbDocument(doc.kb_id, doc.id)
      onToast(`重新索引：${doc.filename}`, 'info')
      await reloadDocs(doc.kb_id)
    } catch (e) {
      onToast(`重新索引失败：${errText(e)}`, 'err')
    }
  }

  const handleCreateKb = async () => {
    const name = window.prompt('新资料库名称', '')
    if (!name || !name.trim()) return
    try {
      const kb = await api.createKnowledgeBase(name.trim(), '')
      onToast(`已创建：${kb.name}`, 'ok')
      await reloadKbs(kb.id)
    } catch (e) {
      onToast(`创建失败：${errText(e)}`, 'err')
    }
  }

  const handleDeleteKb = async (kb: KbRow) => {
    if (kb.id === 'default') {
      onToast('内置默认资料库不可删除', 'err')
      return
    }
    if (!window.confirm(`删除资料库「${kb.name}」及其全部 ${kb.document_count} 个文档？`)) return
    try {
      await api.deleteKnowledgeBase(kb.id)
      onToast(`已删除资料库：${kb.name}`, 'info')
      await reloadKbs('default')
    } catch (e) {
      onToast(`删除失败：${errText(e)}`, 'err')
    }
  }

  const handleSearch = async () => {
    const q = searchQuery.trim()
    if (!q) return
    setSearching(true)
    try {
      const res = await api.kbSearch(q, [activeKbId], 5)
      setSearchHits(res.results)
    } catch (e) {
      setSearchHits(null)
      onToast(`检索失败：${errText(e)}`, 'err')
    } finally {
      setSearching(false)
    }
  }

  const activeKb = kbs.find((k) => k.id === activeKbId)

  return (
    <div className="plugin-panel">
      <div className="plugin-topbar">
        <div className="plugin-title">
          <div className="plugin-title-icon" style={{ background: 'var(--brand-soft)', color: 'var(--brand-fg)' }}>
            <Icon name="book" size={16} />
          </div>
          <div>
            <h2>资料库</h2>
            <p>上传本地文档，绑定专家后即可基于文档问答、出题、拷打</p>
          </div>
        </div>

        <div className="kb-topbar-actions">
          <select
            className="field-input kb-kbselect"
            value={activeKbId}
            onChange={(e) => setActiveKbId(e.target.value)}
            title="切换资料库"
          >
            {kbs.map((k) => (
              <option key={k.id} value={k.id}>{k.name}（{k.document_count}）</option>
            ))}
          </select>
          <button className="ibtn" title="新建资料库" onClick={() => void handleCreateKb()}>
            <Icon name="plus" size={14} />
          </button>
          {activeKb && activeKb.id !== 'default' && (
            <button className="ibtn" title="删除当前资料库" onClick={() => void handleDeleteKb(activeKb)}>
              <Icon name="trash" size={14} />
            </button>
          )}
          <button className="primary" disabled={uploading} onClick={() => fileRef.current?.click()}>
            <Icon name={uploading ? 'clock' : 'plus'} size={14} />
            {uploading ? '上传中…' : '上传文档'}
          </button>
          <input
            ref={fileRef}
            type="file"
            multiple
            accept=".md,.markdown,.txt,.pdf,.docx"
            style={{ display: 'none' }}
            onChange={(e) => void handleUpload(e.target.files)}
          />
        </div>
      </div>

      <div className="plugin-body scroll">
          <div className="kb-searchbar">
            <input
              type="search"
              className="field-input"
              placeholder="在当前资料库中检索（回车）"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') void handleSearch() }}
            />
            <button onClick={() => void handleSearch()} disabled={searching || !searchQuery.trim()}>
              {searching ? '检索中…' : '检索'}
            </button>
          </div>

          {searchHits !== null && (
            <div className="kb-search-results">
              {searchHits.length === 0 && <div className="kb-search-empty">没有检索到相关内容</div>}
              {searchHits.map((h, i) => (
                <div key={i} className="kb-hit">
                  <div className="kb-hit-src">
                    [{i + 1}] {h.doc_name}{h.heading_path ? ` > ${h.heading_path}` : ''}
                    <span className="kb-hit-score">{h.score.toFixed(3)}</span>
                  </div>
                  <div className="kb-hit-text">{h.text}</div>
                </div>
              ))}
            </div>
          )}

          <div className="plugin-list">
            {docs.map((doc) => (
              <div key={doc.id} className={`plugin-row ${doc.status === 'failed' ? 'disabled' : ''}`}>
                <div className="plugin-row-avatar kb-doc-avatar">
                  {doc.ext.replace('.', '').toUpperCase().slice(0, 4) || 'DOC'}
                </div>
                <div className="plugin-row-info">
                  <div className="plugin-row-name">
                    {doc.filename}
                    <span className={`plugin-badge kb-status-${doc.status}`}>
                      {STATUS_LABEL[doc.status] ?? doc.status}
                    </span>
                    {doc.status === 'ready' && (
                      <span className="plugin-row-sub">{doc.chunk_count} 分块</span>
                    )}
                  </div>
                  <div className="plugin-row-sub">{fmtSize(doc.size_bytes)}</div>
                  {doc.status === 'failed' && doc.error && (
                    <div className="plugin-row-prompt kb-err" title={doc.error}>{doc.error}</div>
                  )}
                </div>
                <div className="plugin-row-actions">
                  {doc.status !== 'ready' && doc.status !== 'failed' && <span className="spinner" />}
                  {doc.status === 'failed' && (
                    <button className="ibtn" title="重新索引" onClick={() => void handleReindex(doc)}>
                      <Icon name="sparkles" size={14} />
                    </button>
                  )}
                  <button className="ibtn" title="删除" onClick={() => void handleDeleteDoc(doc)}>
                    <Icon name="trash" size={14} />
                  </button>
                </div>
              </div>
            ))}
          </div>

          {!loading && docs.length === 0 && (
            <div className="plugin-empty">
              <Icon name="book" size={28} />
              <p>这个资料库还是空的</p>
              <span>支持 .md / .txt / .pdf / .docx，上传后自动分块并向量化</span>
            </div>
          )}
          {loading && <div className="plugin-empty"><p>加载中…</p></div>}
        </div>
    </div>
  )
}
