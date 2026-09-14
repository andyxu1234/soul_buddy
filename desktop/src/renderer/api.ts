interface SoulApi {
  getBase: () => string
  listSessions: () => Promise<unknown>
  createSession: (workspace_root: string, cwd?: string, title?: string) => Promise<unknown>
  updateSession: (sessionId: string, fields: { title?: string; provider?: string; expert_id?: string | null }) => Promise<unknown>
  deleteSession: (sessionId: string) => Promise<unknown>
  getHistory: (sessionId: string) => Promise<unknown>
  startRun: (sessionId: string, prompt: string) => Promise<unknown>
  abortRun: (sessionId: string) => Promise<unknown>
  resolvePermission: (sessionId: string, callId: string, choice: string) => Promise<unknown>
  listRules: () => Promise<unknown>
  revokeRule: (ruleId: string) => Promise<unknown>
  getArtifacts: (sessionId: string) => Promise<unknown>
  listConnectors: () => Promise<unknown>
  trustConnector: (name: string) => Promise<unknown>
  connectConnector: (name: string) => Promise<unknown>
  disconnectConnector: (name: string) => Promise<unknown>
  getFileContent: (sessionId: string, path: string) => Promise<unknown>
  listSkills: (workspaceRoot?: string) => Promise<unknown>
  getPrompt: () => Promise<unknown>
  savePrompt: (text: string) => Promise<unknown>
  resetPrompt: () => Promise<unknown>
  listMemoryItems: (layer: string, workspaceRoot?: string) => Promise<unknown>
  getMemoryFiles: () => Promise<unknown>
  deleteMemoryItem: (layer: string, key: string, workspaceRoot?: string) => Promise<unknown>
  listExperts: () => Promise<unknown>
  createExpert: (fields: Record<string, unknown>) => Promise<unknown>
  updateExpert: (expertId: string, fields: Record<string, unknown>) => Promise<unknown>
  deleteExpert: (expertId: string) => Promise<unknown>
  listKnowledgeBases: () => Promise<unknown>
  createKnowledgeBase: (name: string, description: string) => Promise<unknown>
  deleteKnowledgeBase: (kbId: string) => Promise<unknown>
  listKbDocuments: (kbId: string) => Promise<unknown>
  deleteKbDocument: (kbId: string, docId: string) => Promise<unknown>
  reindexKbDocument: (kbId: string, docId: string) => Promise<unknown>
  kbSearch: (query: string, kbIds?: string[], topK?: number) => Promise<unknown>
}

/** Native helpers bridged from the main process (no Node in the renderer). */
interface SoulNative {
  pickDirectory: () => Promise<string | null>
  revealPath: (p: string) => Promise<boolean>
  openPath: (p: string) => Promise<string>
}

function getNative(): SoulNative {
  const w = window as any
  if (!w.soul || !w.soul.native) {
    throw new Error('soul native bridge 未就绪（请稍候或重启）')
  }
  return w.soul.native as SoulNative
}

function getSoul(): SoulApi {
  const w = window as any
  if (!w.soul || !w.soul.api) {
    throw new Error('soul bridge 未就绪（请稍候或重启）')
  }
  return w.soul.api as SoulApi
}

export const native = {
  pickDirectory: () => getNative().pickDirectory(),
  revealPath: (p: string) => getNative().revealPath(p),
  openPath: (p: string) => getNative().openPath(p),
}

export const api = {
  getBase: () => getSoul().getBase(),
  listSessions: () => getSoul().listSessions() as Promise<any[]>,
  createSession: (workspace_root: string, cwd?: string, title?: string) =>
    getSoul().createSession(workspace_root, cwd, title) as Promise<any>,
  updateSession: (sid: string, fields: { title?: string; provider?: string; expert_id?: string | null }) =>
    getSoul().updateSession(sid, fields) as Promise<any>,
  deleteSession: (sid: string) =>
    getSoul().deleteSession(sid) as Promise<any>,
  getHistory: (sid: string) => getSoul().getHistory(sid) as Promise<any[]>,
  startRun: (sid: string, prompt: string) =>
    getSoul().startRun(sid, prompt) as Promise<any>,
  abortRun: (sid: string) => getSoul().abortRun(sid) as Promise<any>,
  resolvePermission: (sid: string, callId: string, choice: string) =>
    getSoul().resolvePermission(sid, callId, choice) as Promise<any>,
  listRules: () => getSoul().listRules() as Promise<any[]>,
  revokeRule: (ruleId: string) => getSoul().revokeRule(ruleId) as Promise<any>,
  getArtifacts: (sid: string) => getSoul().getArtifacts(sid) as Promise<any>,
  listConnectors: () => getSoul().listConnectors() as Promise<{ connectors: Array<{
    name: string; status: string; trusted: boolean; tools: string[]
  }> }>,
  trustConnector: (name: string) => getSoul().trustConnector(name) as Promise<{ status: string; connector: string }>,
  connectConnector: (name: string) => getSoul().connectConnector(name) as Promise<{ status: string; connector: string; tools: string[] }>,
  disconnectConnector: (name: string) => getSoul().disconnectConnector(name) as Promise<{ status: string; connector: string }>,
  getFileContent: (sid: string, path: string) =>
    getSoul().getFileContent(sid, path) as Promise<{ path: string; name: string; content: string; size: number; is_text: boolean }>,
  listSkills: (workspaceRoot?: string) =>
    getSoul().listSkills(workspaceRoot) as Promise<{ skills: Array<{
      title: string
      summary: string
      read_when: string[]
      source: 'user' | 'project'
      permissions: { tools: string[]; network: boolean; read_paths: string[]; write_paths: string[] }
    }> }>,
  getPrompt: () => getSoul().getPrompt() as Promise<{ text: string; source: string; is_custom: boolean }>,
  savePrompt: (text: string) => getSoul().savePrompt(text) as Promise<{ text: string; source: string; is_custom: boolean; action: string; path?: string }>,
  resetPrompt: () => getSoul().resetPrompt() as Promise<{ text: string; source: string; is_custom: boolean; action: string; removed: boolean }>,
  listMemoryItems: (layer: string, workspaceRoot?: string) =>
    getSoul().listMemoryItems(layer, workspaceRoot) as Promise<{ items: MemoryItemRow[]; count: number }>,
  getMemoryFiles: () => getSoul().getMemoryFiles() as Promise<{
    dir: string
    files: Array<{ name: string; path: string; content: string }>
  }>,
  deleteMemoryItem: (layer: string, key: string, workspaceRoot?: string) =>
    getSoul().deleteMemoryItem(layer, key, workspaceRoot) as Promise<{ status: string; layer: string; key: string }>,
  listExperts: () =>
    getSoul().listExperts() as Promise<{ experts: ExpertRow[] }>,
  createExpert: (fields: {
    name: string; role?: string; systemPrompt?: string; color?: string;
    kbIds?: string[]; enabled?: boolean
  }) => getSoul().createExpert(fields) as Promise<ExpertRow>,
  updateExpert: (expertId: string, fields: {
    name?: string; role?: string; systemPrompt?: string; color?: string;
    kbIds?: string[]; enabled?: boolean
  }) => getSoul().updateExpert(expertId, fields) as Promise<ExpertRow>,
  deleteExpert: (expertId: string) =>
    getSoul().deleteExpert(expertId) as Promise<{ status: string; expert_id: string }>,
  listKnowledgeBases: () =>
    getSoul().listKnowledgeBases() as Promise<{ kbs: KbRow[] }>,
  createKnowledgeBase: (name: string, description: string) =>
    getSoul().createKnowledgeBase(name, description) as Promise<KbRow>,
  deleteKnowledgeBase: (kbId: string) =>
    getSoul().deleteKnowledgeBase(kbId) as Promise<{ status: string; kb_id: string }>,
  listKbDocuments: (kbId: string) =>
    getSoul().listKbDocuments(kbId) as Promise<{ documents: KbDocumentRow[] }>,
  deleteKbDocument: (kbId: string, docId: string) =>
    getSoul().deleteKbDocument(kbId, docId) as Promise<{ status: string; doc_id: string }>,
  reindexKbDocument: (kbId: string, docId: string) =>
    getSoul().reindexKbDocument(kbId, docId) as Promise<KbDocumentRow>,
  /** multipart 上传：renderer 直连 sidecar（cookie 鉴权同 EventSource 先例） */
  uploadKbDocuments: async (kbId: string, files: File[]) => {
    const fd = new FormData()
    for (const f of files) fd.append('files', f, f.name)
    const res = await fetch(`${getSoul().getBase()}/api/v1/kb/${encodeURIComponent(kbId)}/documents`,
      { method: 'POST', credentials: 'include', body: fd })
    const text = await res.text()
    if (!res.ok) {
      let detail: unknown = text
      try { detail = JSON.parse(text).detail } catch { /* raw */ }
      throw { status: res.status, detail } as ApiErrorShape
    }
    return JSON.parse(text) as { documents: KbDocumentRow[]; rejected: Array<{ filename: string; reason: string }> }
  },
  kbSearch: (query: string, kbIds?: string[], topK?: number) =>
    getSoul().kbSearch(query, kbIds, topK) as Promise<{
      results: Array<{ doc_name: string; heading_path: string; text: string; score: number }>
      count: number
    }>,
}

interface ApiErrorShape { status: number; detail: unknown }

export interface KbRow {
  id: string
  name: string
  description: string
  document_count: number
  created_at: number
}

export interface KbDocumentRow {
  id: string
  kb_id: string
  filename: string
  ext: string
  size_bytes: number
  status: 'pending' | 'parsing' | 'chunking' | 'embedding' | 'indexing' | 'ready' | 'failed'
  error: string | null
  chunk_count: number
  created_at: number
}

export interface ExpertRow {
  id: string
  name: string
  role: string
  systemPrompt: string
  enabled: boolean
  color: string
  kbIds: string[]
  isBuiltin: boolean
  createdAt: number
  updatedAt: number
}

export interface MemoryItemRow {
  id: number
  layer: string
  key: string
  value: string
  kind: string
  importance: number
  revision: number
  updated_at: number
  expires_at: number | null
  source_session_id: string | null
  active: boolean
  workspace_root?: string | null
}

/** Open an authenticated SSE stream for a session. Returns a close fn. */
export function streamEvents(
  sessionId: string,
  onEvent: (ev: any) => void,
  onError?: (e: any) => void,
): () => void {
  const base = getSoul().getBase()
  const url = `${base}/api/v1/sessions/${sessionId}/events`
  const es = new EventSource(url, { withCredentials: true })

  es.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data))
    } catch (err) {
      onError?.(err)
    }
  }
  // Typed events (the backend emits `event: <type>`).
  const types = [
    'message', 'reasoning', 'reasoning_delta', 'function_call',
    'function_call_result',
    'file-history-snapshot', 'assistant_delta',
    'permission_request', 'permission_resolved', 'permission_expired',
    'turn_budget_warning', 'run_aborted', 'run_started', 'run_finished',
    'skill_loaded', 'artifact_presented', 'context_usage',
    'context_limit_exceeded', 'error',
  ]
  const handlers: Array<(e: MessageEvent) => void> = []
  for (const t of types) {
    const h = (e: MessageEvent) => {
      try {
        onEvent(JSON.parse(e.data))
      } catch (err) {
        onError?.(err)
      }
    }
    es.addEventListener(t, h as EventListener)
    handlers.push(h)
  }
  es.onerror = (e) => {
    // EventSource auto-reconnects; only surface to caller.
    onError?.(e)
  }

  return () => {
    es.close()
  }
}
