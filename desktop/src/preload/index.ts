import { contextBridge, ipcRenderer } from 'electron'

// Work around the race between renderer mount and the main process pushing
// the sidecar base URL: the preload proactively asks for it instead of only
// relying on the `soul:config` event from `did-finish-load`.
ipcRenderer.send('soul:request-config')

/**
 * Secure bridge between the renderer and the FastAPI sidecar.
 *
 * - contextIsolated: true  ->  no Node/Electron globals leak to the page.
 * - The sidecar base URL is delivered by the main process over IPC (never
 *   hardcoded, never visible in DevTools as a secret).
 * - Every request sends credentials so the httpOnly session cookie authenticates.
 */

let API_BASE = ''

ipcRenderer.on('soul:config', (_e, cfg: { base: string }) => {
  API_BASE = cfg.base
})

interface ApiError {
  status: number
  detail: unknown
}

async function request(method: string, p: string, body?: unknown): Promise<unknown> {
  if (!API_BASE) throw new Error('soul bridge not ready')
  const opts: RequestInit = {
    method,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
  }
  if (body !== undefined) opts.body = JSON.stringify(body)
  const res = await fetch(API_BASE + p, opts)
  const text = await res.text()
  if (!res.ok) {
    let detail: unknown = text
    try {
      detail = JSON.parse(text).detail
    } catch {
      /* keep raw text */
    }
    const err: ApiError = { status: res.status, detail }
    throw err
  }
  return text ? JSON.parse(text) : null
}

const api = {
  getBase: () => API_BASE,
  listSessions: () => request('GET', '/api/v1/sessions'),
  createSession: (workspace_root: string, cwd?: string, title?: string) =>
    request('POST', '/api/v1/sessions', { workspace_root, cwd, title }),
  updateSession: (sessionId: string, fields: { title?: string; provider?: string }) =>
    request('PATCH', `/api/v1/sessions/${sessionId}`, fields),
  deleteSession: (sessionId: string) =>
    request('DELETE', `/api/v1/sessions/${sessionId}`),
  abortRun: (sessionId: string) =>
    request('POST', `/api/v1/runs/${sessionId}/abort`),
  getHistory: (sessionId: string) =>
    request('GET', `/api/v1/sessions/${sessionId}/history`),
  startRun: (sessionId: string, prompt: string) =>
    request('POST', '/api/v1/runs', { session_id: sessionId, prompt }),
  resolvePermission: (sessionId: string, callId: string, choice: string) =>
    request('POST', `/api/v1/sessions/${sessionId}/permissions/${callId}`, { choice }),
  listRules: () => request('GET', '/api/v1/permissions/rules'),
  revokeRule: (ruleId: string) =>
    request('DELETE', `/api/v1/permissions/rules/${ruleId}`),
  // P5: deliverable cards + MCP connector list
  getArtifacts: (sessionId: string) =>
    request('GET', `/api/v1/sessions/${sessionId}/artifacts`),
  listConnectors: () => request('GET', '/api/v1/mcp/connectors'),
  trustConnector: (name: string) => request('POST', `/api/v1/mcp/connectors/${name}/trust`),
  connectConnector: (name: string) => request('POST', `/api/v1/mcp/connectors/${name}/connect`),
  disconnectConnector: (name: string) => request('POST', `/api/v1/mcp/connectors/${name}/disconnect`),
  // Preview: read a workspace file's content for inline rendering
  getFileContent: (sessionId: string, path: string) =>
    request('GET', `/api/v1/sessions/${sessionId}/file-content?path=${encodeURIComponent(path)}`),
  // Skills: list installed skills (user-level + project-level)
  listSkills: (workspaceRoot?: string) =>
    request('GET', workspaceRoot
      ? `/api/v1/skills?workspace_root=${encodeURIComponent(workspaceRoot)}`
      : '/api/v1/skills'),
  // System prompt: read / write / reset user-editable override
  getPrompt: () => request('GET', '/api/v1/prompt'),
  savePrompt: (text: string) => request('PUT', '/api/v1/prompt', { text }),
  resetPrompt: () => request('PUT', '/api/v1/prompt', { action: 'reset' }),
  // User memory: list items, view user.md / user_memory.md projections, delete
  listMemoryItems: (layer: string, workspaceRoot?: string) =>
    request('GET', `/api/v1/memory/items?layer=${encodeURIComponent(layer)}` +
      (workspaceRoot ? `&workspace_root=${encodeURIComponent(workspaceRoot)}` : '')),
  getMemoryFiles: () => request('GET', '/api/v1/memory/files'),
  deleteMemoryItem: (layer: string, key: string, workspaceRoot?: string) =>
    request('DELETE', `/api/v1/memory/items/${encodeURIComponent(layer)}/${encodeURIComponent(key)}` +
      (workspaceRoot ? `?workspace_root=${encodeURIComponent(workspaceRoot)}` : '')),
  // Experts (s18): preset role packages, builtin < user two layers
  listExperts: () => request('GET', '/api/v1/experts'),
  createExpert: (fields: Record<string, unknown>) =>
    request('POST', '/api/v1/experts', fields),
  updateExpert: (expertId: string, fields: Record<string, unknown>) =>
    request('PATCH', `/api/v1/experts/${encodeURIComponent(expertId)}`, fields),
  deleteExpert: (expertId: string) =>
    request('DELETE', `/api/v1/experts/${encodeURIComponent(expertId)}`),
  // Knowledge base (资料库): metadata + documents (upload 走 renderer FormData)
  listKnowledgeBases: () => request('GET', '/api/v1/kb'),
  createKnowledgeBase: (name: string, description: string) =>
    request('POST', '/api/v1/kb', { name, description }),
  deleteKnowledgeBase: (kbId: string) =>
    request('DELETE', `/api/v1/kb/${encodeURIComponent(kbId)}`),
  listKbDocuments: (kbId: string) =>
    request('GET', `/api/v1/kb/${encodeURIComponent(kbId)}/documents`),
  deleteKbDocument: (kbId: string, docId: string) =>
    request('DELETE', `/api/v1/kb/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(docId)}`),
  reindexKbDocument: (kbId: string, docId: string) =>
    request('POST', `/api/v1/kb/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(docId)}/reindex`),
  kbSearch: (query: string, kbIds?: string[], topK?: number) =>
    request('POST', '/api/v1/kb/search', {
      query, ...(kbIds ? { kb_ids: kbIds } : {}), ...(topK ? { top_k: topK } : {}),
    }),
}

// Native (main-process) helpers — no Node access from the renderer.
const native = {
  pickDirectory: (): Promise<string | null> =>
    ipcRenderer.invoke('soul:pick-directory'),
  revealPath: (p: string): Promise<boolean> =>
    ipcRenderer.invoke('soul:reveal-path', p),
  openPath: (p: string): Promise<string> =>
    ipcRenderer.invoke('soul:open-path', p),
}

contextBridge.exposeInMainWorld('soul', { api, native })
