import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, streamEvents } from './api'
import type { SoulEvent, SessionRecord, PermissionRequest, Artifact, ContextUsage, ImageAttachment, FileAttachment } from './types'
import { SessionList, sessionTitle, type NavView } from './components/SessionList'
import { ChatPanel } from './components/ChatPanel'
import type { AgentMode } from './components/PlusMenu'
import { RightPanel } from './components/RightPanel'
import { SettingsModal } from './components/SettingsModal'
import { SkillsPanel } from './components/SkillsPanel'
import { ExpertPanel } from './components/ExpertPanel'
import { KnowledgePanel } from './components/KnowledgePanel'
import { AttuPanel } from './components/AttuPanel'
import { McpPanel } from './components/McpPanel'
import { RubricPanel } from './components/RubricPanel'
import { LangSmithPanel } from './components/LangSmithPanel'
import { NewTaskModal, RenameModal, ConfirmModal } from './components/NewTaskModal'
import { Icon } from './components/Icon'
import { applyTheme, loadTheme, saveTheme, watchSystemTheme, type ThemeMode } from './theme'

type Toast = { id: number; msg: string; tone: 'ok' | 'err' | 'info' }

function formatError(err: unknown): string {
  if (err == null) return '未知错误'
  if (typeof err === 'string') return err
  if (err instanceof Error) return err.message || String(err)
  if (typeof err === 'object') {
    const obj = err as Record<string, unknown>
    const detail = obj.detail
    if (typeof detail === 'string') return detail
    if (typeof detail === 'number') return String(detail)
    if (Array.isArray(detail)) {
      const msgs = detail.map((d: any) => d?.msg ?? String(d)).filter(Boolean)
      if (msgs.length) return msgs.join('; ')
    }
    if (detail !== undefined && typeof detail === 'object') {
      const d = detail as Record<string, unknown>
      if (typeof d.detail === 'string') return d.detail
      if (typeof d.status === 'string') return d.status
      try { return JSON.stringify(detail) } catch { /* fallthrough */ }
    }
    if (typeof (obj as any).message === 'string') return (obj as any).message
    try { return JSON.stringify(err) } catch { return String(err) }
  }
  return String(err)
}

let toastSeq = 0

/** 发送时才读取文件内容为 base64（UI 阶段从不解析，同 ZCode/WorkBuddy）。 */
function fileToBase64(f: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result).split(',')[1] ?? '')
    r.onerror = () => reject(r.error)
    r.readAsDataURL(f)
  })
}

export default function App() {
  const [sessions, setSessions] = useState<SessionRecord[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [events, setEvents] = useState<SoulEvent[]>([])
  const [prompt, setPrompt] = useState('')
  // 主 composer 的待发送图片附件（发送失败时要原样还原给用户）
  const [pendingImages, setPendingImages] = useState<ImageAttachment[]>([])
  // 主 composer 的待发送文件附件（只持句柄，发送时才读内容）
  const [pendingFiles, setPendingFiles] = useState<FileAttachment[]>([])
  const [perms, setPerms] = useState<PermissionRequest[]>([])
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [streamText, setStreamText] = useState('')
  // reasoning 流式缓冲(reasoning_delta, bus-only)。持久化的 reasoning 事件
  // 到达后由 MessageList 渲染,这里清空,完成 stream→落盘 的交接。
  const [streamReasoning, setStreamReasoning] = useState('')
  const [toasts, setToasts] = useState<Toast[]>([])

  const [settingsOpen, setSettingsOpen] = useState(false)
  const [rightPanelOpen, setRightPanelOpen] = useState(true)
  const [activeArtifactPath, setActiveArtifactPath] = useState<string | null>(null)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [theme, setTheme] = useState<ThemeMode>(loadTheme)
  const [activeView, setActiveView] = useState<NavView>('chat')
  const [sidebarW, setSidebarW] = useState(() => parseInt(localStorage.getItem('sb-sidebar-w') || '244', 10))
  const [rightW, setRightW] = useState(() => parseInt(localStorage.getItem('sb-right-w') || '320', 10))

  const [newTask, setNewTask] = useState<{ open: boolean; initialDir?: string }>({ open: false })
  const [renameTarget, setRenameTarget] = useState<SessionRecord | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<SessionRecord | null>(null)
  const [deleteWs, setDeleteWs] = useState<{ workspace: string; name: string; count: number } | null>(null)

  // 上下文用量 (旁路 token 估算 + 校准)
  const [contextUsage, setContextUsage] = useState<ContextUsage | null>(null)

  // 运行中的会话集合：按会话隔离，避免切会话时 running 状态串扰
  const [runningIds, setRunningIds] = useState<Set<string>>(new Set())
  const running = selectedId ? runningIds.has(selectedId) : false

  // 每个会话的 run_started 时间戳(ms),用于显示运行耗时
  const [runStartTimes, setRunStartTimes] = useState<Record<string, number>>({})
  // 每秒 tick,驱动计时器刷新
  const [, setTick] = useState(0)
  useEffect(() => {
    if (!running) return
    const id = setInterval(() => setTick((t) => t + 1), 1000)
    return () => clearInterval(id)
  }, [running])

  // 权限模式：default 逐条询问，allow_all 自动通过
  const [permMode, setPermMode] = useState<'default' | 'allow_all'>('default')
  const permModeRef = useRef(permMode)
  useEffect(() => { permModeRef.current = permMode }, [permMode])

  // Agent 模式（前端 mock，后端暂不处理）
  const [sessionMode, setSessionMode] = useState<Record<string, AgentMode>>({})

  const pushToast = useCallback((msg: string, tone: Toast['tone'] = 'info') => {
    const id = ++toastSeq
    setToasts((p) => [...p, { id, msg, tone }])
    setTimeout(() => setToasts((p) => p.filter((t) => t.id !== id)), 4200)
  }, [])

  useEffect(() => { applyTheme(theme) }, [theme])
  useEffect(() => watchSystemTheme(theme, () => applyTheme(theme)), [theme])

  const changeTheme = (t: ThemeMode) => { setTheme(t); saveTheme(t) }

  const loadArtifacts = useCallback((sid: string) => {
    api.getArtifacts(sid)
      .then((r) => setArtifacts(((r as any)?.artifacts ?? []) as Artifact[]))
      .catch(() => setArtifacts([]))
  }, [])

  const loadSessions = useCallback(() => {
    api.listSessions()
      .then((s) => setSessions((s || []) as SessionRecord[]))
      .catch((e) => pushToast(formatError(e), 'err'))
  }, [pushToast])

  useEffect(loadSessions, [loadSessions])

  // 切到「允许完全访问」时，自动通过当前 pending 的权限请求
  useEffect(() => {
    if (permMode !== 'allow_all' || !selectedId || perms.length === 0) return
    perms.forEach((req) => {
      api.resolvePermission(selectedId, req.call_id, 'allow_once').catch(() => {})
    })
    setPerms([])
  }, [permMode, selectedId, perms])

  // 选中会话 -> 拉历史 + 订阅 SSE
  useEffect(() => {
    if (!selectedId) return
    const sid = selectedId
    setEvents([]); setPerms([]); setStreamText(''); setArtifacts([])
    setStreamReasoning('')
    setContextUsage(null)

    api.getHistory(sid).then((h) => {
      const hist = (h || []) as SoulEvent[]
      setEvents(hist)
      // 从历史事件重建 running 状态:最后一个 run_started 是否被
      // run_finished / run_aborted 覆盖。后端没有单独的"当前是否运行"
      // 接口,所以这是唯一可靠的恢复手段。
      const lastStart = [...hist].reverse().find((e) => e.type === 'run_started')
      const lastEnd = [...hist].reverse().find(
        (e) => e.type === 'run_finished' || e.type === 'run_aborted',
      )
      const isRunning = !!lastStart && (!lastEnd || lastStart.sequence > lastEnd.sequence)
      setRunningIds((p) => {
        const n = new Set(p)
        if (isRunning) n.add(sid); else n.delete(sid)
        return n
      })
      // 重建计时起点
      if (isRunning && lastStart?.data?.started_at) {
        setRunStartTimes((p) => ({ ...p, [sid]: lastStart.data.started_at * 1000 }))
      } else if (!isRunning) {
        setRunStartTimes((p) => { const n = { ...p }; delete n[sid]; return n })
      }
    }).catch((e) => pushToast(formatError(e), 'err'))
    loadArtifacts(sid)

    const close = streamEvents(
      sid,
      (ev: SoulEvent) => {
        if (ev.type === 'permission_request') {
          const req = ev.data as PermissionRequest
          if (permModeRef.current === 'allow_all') {
            api.resolvePermission(sid, req.call_id, 'allow_once').catch(() => {})
          } else {
            setPerms((prev) =>
              prev.some((p) => p.call_id === req.call_id) ? prev : [...prev, req])
          }
          return
        }
        if (ev.type === 'permission_resolved' || ev.type === 'permission_expired') {
          const cid = ev.data?.call_id
          if (cid) setPerms((prev) => prev.filter((p) => p.call_id !== cid))
          return
        }
        if (ev.type === 'assistant_delta') {
          setStreamText((prev) => prev + (ev.data.text ?? ''))
          return
        }
        if (ev.type === 'reasoning_delta') {
          setStreamReasoning((prev) => prev + (ev.data.text ?? ''))
          return
        }
        if (ev.type === 'reasoning') {
          // 完整 reasoning 已持久化(会进 MessageList),清空流式缓冲
          setStreamReasoning('')
          // 不 return —— reasoning 事件本身也要进 events 供 MessageList 渲染
        }
        if (ev.type === 'run_started') {
          setRunningIds((p) => new Set(p).add(sid))
          // 用后端 started_at (秒) 或本地时间 作为计时起点
          const startedAt = ev.data?.started_at
            ? ev.data.started_at * 1000
            : Date.now()
          setRunStartTimes((p) => ({ ...p, [sid]: startedAt }))
          setStreamText('')
          setStreamReasoning('')
        }
        if (ev.type === 'message' && ev.data?.role === 'assistant') setStreamText('')
        if (ev.type === 'run_finished' || ev.type === 'run_aborted') {
          setRunningIds((p) => { const n = new Set(p); n.delete(sid); return n })
          setRunStartTimes((p) => { const n = { ...p }; delete n[sid]; return n })
          setStreamText('')
          setStreamReasoning('')
          setPerms([])
          loadArtifacts(sid)
          loadSessions()
        }
        // present_files delivery — auto-open the right panel and preview the
        // primary artifact without requiring the user to click anything.
        if (ev.type === 'artifact_presented') {
          const cards = ev.data?.cards ?? []
          if (cards.length) {
            const primary = cards.find((c: any) => c.is_primary) ?? cards[0]
            setActiveArtifactPath(primary.path)
            setRightPanelOpen(true)
          }
        }
        if (ev.type === 'context_usage') {
          setContextUsage(ev.data as ContextUsage)
          return
        }
        setEvents((prev) =>
          prev.some((e) => e.sequence === ev.sequence) ? prev : [...prev, ev])
      },
      () => { /* EventSource 自动重连 */ },
    )
    return () => close()
  }, [selectedId, loadArtifacts, loadSessions, pushToast])

  const handleSend = async (images: ImageAttachment[] = [], files: FileAttachment[] = []) => {
    if (!selectedId || running) return
    if (!prompt.trim() && images.length === 0 && files.length === 0) return
    const text = prompt.trim()
    const sid = selectedId
    setPrompt('')
    setPendingImages([])
    setPendingFiles([])
    setRunningIds((p) => new Set(p).add(sid))
    const restore = () => {
      // 失败时把内容还给用户，别丢
      setPrompt(text)
      setPendingImages(images)
      setPendingFiles(files)
    }
    let call: Promise<unknown>
    try {
      // 发送时才把文件读成 base64 原始字节；文本解析在后端组装 LLM 请求时进行
      const filePayload = await Promise.all(files.map(async (f) => ({
        filename: f.name, mime: f.mime, data: await fileToBase64(f.file),
      })))
      call = (images.length || filePayload.length)
        ? api.startRunWithAttachments(
            sid, text,
            images.map((i) => ({ filename: i.name, mime: i.mime, data: i.base64 })),
            filePayload)
        : api.startRun(sid, text)
    } catch (e) {
      // 同步异常（如桥接陈旧）：绝不能让 running 卡死
      setRunningIds((p) => { const n = new Set(p); n.delete(sid); return n })
      restore()
      pushToast(formatError(e), 'err')
      return
    }
    call.catch((e) => {
      setRunningIds((p) => { const n = new Set(p); n.delete(sid); return n })
      restore()
      pushToast(formatError(e), 'err')
    })
  }

  const handleAbort = () => {
    if (!selectedId) return
    // 乐观更新:立即清 running,避免用户卡在"运行中"假状态。
    // 后端是唯一真相源 —— 如果 abort 失败(如 409 NO_ACTIVE_RUN),
    // 前端会在校准分支里静默处理,不会造成状态回跳(因为此时本来就没在跑)。
    const sid = selectedId
    setRunningIds((p) => { const n = new Set(p); n.delete(sid); return n })
    api.abortRun(sid)
      .then(() => pushToast('已请求中断', 'info'))
      .catch((e) => {
        const msg = formatError(e)
        if (msg === 'NO_ACTIVE_RUN') {
          // 后端说没在跑,说明之前是前端状态假阳性 — 已经被上面的
          // 乐观更新清掉了,这里静默即可,不需要再弹 toast。
          return
        }
        // 真实错误:恢复 running 状态,让用户看到系统仍在跑
        setRunningIds((p) => { const n = new Set(p); n.add(sid); return n })
        pushToast(msg, 'err')
      })
  }

  const handleResolve = (req: PermissionRequest, choice: string) => {
    if (!selectedId) return
    setPerms((prev) => prev.filter((p) => p.call_id !== req.call_id))
    api.resolvePermission(selectedId, req.call_id, choice).catch((e) => {
      const msg = formatError(e)
      if (!msg.includes('not pending') && !msg.includes('expired')) pushToast(msg, 'err')
    })
  }

  const handleCreate = (workspaceRoot: string, title: string) => {
    setNewTask({ open: false })
    api.createSession(workspaceRoot, undefined, title)
      .then((s) => {
        const rec = s as SessionRecord
        setSessions((prev) => [rec, ...prev])
        setSelectedId(rec.id)
      })
      .catch((e) => pushToast(formatError(e), 'err'))
  }

  /** 空状态：从居中 composer 发起新会话 —— 自动 create + startRun + select */
  const handleStartNewSession = async (prompt: string, workspaceRoot: string,
                                       images: ImageAttachment[] = [],
                                       files: FileAttachment[] = []) => {
    if (!prompt.trim() && images.length === 0 && files.length === 0) return
    api.createSession(workspaceRoot, undefined, prompt.slice(0, 30) || '附件任务')
      .then((s) => {
        const rec = s as SessionRecord
        setSessions((prev) => [rec, ...prev])
        setSelectedId(rec.id)
        setActiveView('chat')
        // 切到新 session 后自动启动运行
        try {
          const call = (async () => {
            // 发送时才把文件读成 base64（UI 阶段从不解析内容）
            const filePayload = await Promise.all(files.map(async (f) => ({
              filename: f.name, mime: f.mime, data: await fileToBase64(f.file),
            })))
            if (images.length || filePayload.length) {
              return api.startRunWithAttachments(
                rec.id, prompt,
                images.map((i) => ({ filename: i.name, mime: i.mime, data: i.base64 })),
                filePayload)
            }
            return api.startRun(rec.id, prompt)
          })()
          call.catch((e) => pushToast(formatError(e), 'err'))
        } catch (e) {
          pushToast(formatError(e), 'err')
        }
      })
      .catch((e) => pushToast(formatError(e), 'err'))
  }

  const handleRename = (title: string) => {
    const target = renameTarget
    setRenameTarget(null)
    if (!target) return
    api.updateSession(target.id, { title })
      .then((s) => {
        const rec = s as SessionRecord
        setSessions((prev) => prev.map((x) => (x.id === rec.id ? rec : x)))
      })
      .catch((e) => pushToast(formatError(e), 'err'))
  }

  const handleProviderChange = (provider: string) => {
    if (!selectedId) return
    api.updateSession(selectedId, { provider })
      .then((s) => {
        const rec = s as SessionRecord
        setSessions((prev) => prev.map((x) => (x.id === rec.id ? rec : x)))
      })
      .catch((e) => pushToast(formatError(e), 'err'))
  }

  const handleExpertChange = (expertId: string | null) => {
    if (!selectedId) return
    api.updateSession(selectedId, { expert_id: expertId })
      .then((s) => {
        const rec = s as SessionRecord
        setSessions((prev) => prev.map((x) => (x.id === rec.id ? rec : x)))
        pushToast(expertId ? '已绑定专家，下一轮生效' : '已解绑专家', 'ok')
      })
      .catch((e) => pushToast(formatError(e), 'err'))
  }

  const handleModeChange = (mode: AgentMode) => {
    if (!selectedId) return
    setSessionMode((prev) => ({ ...prev, [selectedId]: mode }))
    // TODO: 后端实现后改为调用 api.updateSession(selectedId, { mode })
  }

  const handleDelete = () => {
    const target = deleteTarget
    setDeleteTarget(null)
    if (!target) return
    api.deleteSession(target.id)
      .then(() => {
        setSessions((prev) => prev.filter((x) => x.id !== target.id))
        setRunningIds((prev) => { const n = new Set(prev); n.delete(target.id); return n })
        if (selectedId === target.id) setSelectedId(null)
        pushToast('任务已删除', 'ok')
      })
      .catch((e) => pushToast(formatError(e), 'err'))
  }

  const handleDeleteWorkspace = async () => {
    const ws = deleteWs
    setDeleteWs(null)
    if (!ws) return
    const targets = sessions.filter((s) => s.workspace_root === ws.workspace)
    let ok = 0, fail = 0
    for (const s of targets) {
      try {
        await api.deleteSession(s.id)
        ok++
      } catch {
        fail++
      }
    }
    setSessions((prev) => prev.filter((x) => x.workspace_root !== ws.workspace))
    setRunningIds((prev) => {
      const n = new Set(prev)
      for (const s of targets) n.delete(s.id)
      return n
    })
    if (selectedId && targets.some((s) => s.id === selectedId)) setSelectedId(null)
    if (fail === 0) pushToast(`已删除 ${ok} 个任务`, 'ok')
    else pushToast(`删除完成：成功 ${ok}，失败 ${fail}`, fail > 0 ? 'err' : 'ok')
  }

  const selected = useMemo(
    () => sessions.find((s) => s.id === selectedId) || null,
    [sessions, selectedId],
  )

  const openArtifacts = () => setRightPanelOpen(true)

  // Persist widths
  useEffect(() => { localStorage.setItem('sb-sidebar-w', String(sidebarW)) }, [sidebarW])
  useEffect(() => { localStorage.setItem('sb-right-w', String(rightW)) }, [rightW])

  // Resize drag handler
  const startResize = useCallback((side: 'sidebar' | 'right') => (e: React.MouseEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const startW = side === 'sidebar' ? sidebarW : rightW
    const setW = side === 'sidebar' ? setSidebarW : setRightW
    const min = side === 'sidebar' ? 180 : 240
    const max = side === 'sidebar' ? 500 : 600
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    const onMove = (ev: MouseEvent) => {
      const delta = ev.clientX - startX
      const sign = side === 'right' ? -1 : 1
      const newW = Math.min(max, Math.max(min, startW + delta * sign))
      setW(newW)
    }
    const onUp = () => {
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [sidebarW, rightW])

  return (
    <div className="app" style={{ '--w-sidebar': `${sidebarW}px`, '--w-right': `${rightW}px` } as React.CSSProperties}>
      <SessionList
        sessions={sessions}
        selectedId={selectedId}
        runningIds={runningIds}
        collapsed={sidebarCollapsed}
        activeView={activeView}
        onViewChange={setActiveView}
        onSelect={(id) => { setSelectedId(id); setActiveView('chat') }}
        onNew={(workspaceRoot) => setNewTask({ open: true, initialDir: workspaceRoot })}
        onOpenSettings={() => setSettingsOpen(true)}
        onToggleCollapse={() => setSidebarCollapsed((c) => !c)}
        onRename={setRenameTarget}
        onDelete={setDeleteTarget}
        onDeleteWorkspaceSessions={(workspace, name, count) => setDeleteWs({ workspace, name, count })}
      />

      {!sidebarCollapsed && <div className="resizer" onMouseDown={startResize('sidebar')} />}

      {activeView === 'skills' ? (
        <SkillsPanel onToast={pushToast} workspaceRoot={selected?.workspace_root} />
      ) : activeView === 'expert' ? (
        <ExpertPanel onToast={pushToast} />
      ) : activeView === 'knowledge' ? (
        <KnowledgePanel onToast={pushToast} />
      ) : activeView === 'attu' ? (
        <AttuPanel />
      ) : activeView === 'mcp' ? (
        <McpPanel onToast={pushToast} />
      ) : activeView === 'rubric' ? (
        <RubricPanel onToast={pushToast} />
      ) : activeView === 'langsmith' ? (
        <LangSmithPanel onToast={pushToast} />
      ) : (
        <>
          <ChatPanel
            session={selected}
            events={events}
            running={running}
            runStart={selectedId ? runStartTimes[selectedId] : undefined}
            prompt={prompt}
            perms={perms}
            streamText={streamText}
            streamReasoning={streamReasoning}
            rightPanelOpen={rightPanelOpen}
            permMode={permMode}
            providers={AVAILABLE_PROVIDERS}
            contextUsage={contextUsage}
            onPromptChange={setPrompt}
            onSend={handleSend}
            onAbort={handleAbort}
            onResolvePerm={handleResolve}
            onToggleRightPanel={() => setRightPanelOpen((o) => !o)}
            onOpenArtifacts={openArtifacts}
            onOpenChanges={() => setRightPanelOpen(true)}
            onPermModeChange={setPermMode}
            onProviderChange={handleProviderChange}
            onExpertChange={handleExpertChange}
            onToast={pushToast}
            onStartNewSession={handleStartNewSession}
            onNavigate={(v) => setActiveView(v)}
            pendingImages={pendingImages}
            onPendingImagesChange={setPendingImages}
            pendingFiles={pendingFiles}
            onPendingFilesChange={setPendingFiles}
            mode={selectedId ? sessionMode[selectedId] : undefined}
            onModeChange={handleModeChange}
          />
          {selected && rightPanelOpen && (
            <>
              <div className="resizer" onMouseDown={startResize('right')} />
              <RightPanel
                session={selected}
                artifacts={artifacts}
                events={events}
                onClose={() => setRightPanelOpen(false)}
                onToast={pushToast}
                activePath={activeArtifactPath}
              />
            </>
          )}
        </>
      )}

      {settingsOpen && (
        <SettingsModal
          theme={theme}
          onThemeChange={changeTheme}
          onClose={() => setSettingsOpen(false)}
          onToast={pushToast}
        />
      )}

      {newTask.open && (
        <NewTaskModal
          onClose={() => setNewTask({ open: false })}
          onCreate={handleCreate}
          initialDir={newTask.initialDir}
        />
      )}
      {renameTarget && (
        <RenameModal
          current={sessionTitle(renameTarget)}
          onClose={() => setRenameTarget(null)}
          onConfirm={handleRename}
        />
      )}
      {deleteTarget && (
        <ConfirmModal
          title="删除任务"
          message={`确定删除「${sessionTitle(deleteTarget)}」？该任务的完整对话记录与产物索引会被一并移除，磁盘上的工作区文件不受影响。此操作不可撤销。`}
          confirmLabel="删除"
          onClose={() => setDeleteTarget(null)}
          onConfirm={handleDelete}
        />
      )}

      {deleteWs && (
        <ConfirmModal
          title="删除工作区全部对话"
          message={`确定删除「${deleteWs.name}」下全部 ${deleteWs.count} 个任务？对话记录与产物索引会被一并移除，磁盘上的工作区文件不受影响。此操作不可撤销。`}
          confirmLabel="全部删除"
          onClose={() => setDeleteWs(null)}
          onConfirm={handleDeleteWorkspace}
        />
      )}

      {toasts.length > 0 && (
        <div className="toast-wrap">
          {toasts.map((t) => (
            <div
              key={t.id}
              className={`toast ${t.tone}`}
              onClick={() => setToasts((p) => p.filter((x) => x.id !== t.id))}
              title="点击关闭"
            >
              <Icon name={t.tone === 'err' ? 'alert' : t.tone === 'ok' ? 'check' : 'info'} size={14} />
              {t.msg}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const AVAILABLE_PROVIDERS = [
  { id: 'deepseek', label: 'Deepseek-V4.1-Flash' },
  { id: 'siliconflow', label: 'Qwen/Qwen3-8B' },
]

function providerLabel(provider?: string | null): string {
  const p = AVAILABLE_PROVIDERS.find((x) => x.id === provider)
  return p?.label || provider || 'Deepseek-V4.1-Flash'
}
