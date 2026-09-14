import React, { useEffect, useRef, useState } from 'react'
import type { SessionRecord, PermissionRequest, ContextUsage } from '../types'
import { MessageList } from './MessageList'
import { PermissionDialog } from './PermissionDialog'

import { Icon } from './Icon'
import { sessionTitle } from './SessionList'
import { PlusMenu } from './PlusMenu'
import { PermissionDropdown } from './PermissionDropdown'
import { ExpertSelector } from './ExpertSelector'
import { ModelSelector } from './ModelSelector'
import { native } from '../api'
import { EmotionBall } from './EmotionBall'

const SB_THEME = { body: '#3b82f6', eyes: '#0f172a' }

interface ProviderOption {
  id: string
  label: string
}

interface Props {
  session: SessionRecord | null
  events: any[]
  running: boolean
  runStart?: number   // run_started 时间戳(ms),用于显示运行耗时
  prompt: string
  perms: PermissionRequest[]
  streamText: string
  /** reasoning_delta 流式缓冲；持久化 reasoning 事件到达后由后端事件顺序自然交接 */
  streamReasoning?: string
  rightPanelOpen: boolean
  permMode: 'default' | 'allow_all'
  providers: ProviderOption[]
  contextUsage: ContextUsage | null
  onPromptChange: (v: string) => void
  onSend: () => void
  onAbort: () => void
  onResolvePerm: (req: PermissionRequest, choice: string) => void
  onToggleRightPanel: () => void
  onOpenArtifacts: () => void
  onOpenChanges: () => void
  onPermModeChange: (mode: 'default' | 'allow_all') => void
  onProviderChange: (provider: string) => void
  onExpertChange?: (expertId: string | null) => void
  onToast: (msg: string, tone?: 'ok' | 'err' | 'info') => void
  /** 空状态下用户从居中 composer 发起新会话 */
  onStartNewSession: (prompt: string, workspaceRoot: string) => void
  /** 导航到 Skills / MCP / Expert 面板 */
  onNavigate?: (view: 'skills' | 'mcp' | 'expert') => void
}

const SUGGESTIONS = [
  { title: '梳理这个仓库的结构', desc: '列出目录职责、入口文件与技术栈' },
  { title: '找出潜在的重复代码', desc: '给出可提取的公共函数建议' },
  { title: '为关键模块补测试', desc: '先列出优先级，再逐个实现' },
]

function autoGrow(el: HTMLTextAreaElement) {
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 180) + 'px'
}

export function ChatPanel({
  session, events, running, runStart, prompt, perms, streamText, streamReasoning,
  rightPanelOpen, permMode, providers, contextUsage,
  onPromptChange, onSend, onAbort, onResolvePerm,
  onToggleRightPanel, onOpenArtifacts, onOpenChanges,
  onPermModeChange, onProviderChange, onExpertChange, onToast,
  onStartNewSession, onNavigate,
}: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const messagesRef = useRef<HTMLDivElement>(null)
  const [pinnedToBottom, setPinnedToBottom] = useState(true)
  const [usageOpen, setUsageOpen] = useState(false)
  const usageBtnRef = useRef<HTMLButtonElement>(null)

  // 运行耗时显示: 每秒刷新一次
  const [elapsed, setElapsed] = useState<string>('')
  useEffect(() => {
    if (!running || !runStart) { setElapsed(''); return }
    const update = () => {
      const s = Math.floor((Date.now() - runStart) / 1000)
      if (s < 60) setElapsed(`${s}s`)
      else {
        const m = Math.floor(s / 60)
        const rs = s % 60
        setElapsed(`${m}m${rs.toString().padStart(2, '0')}s`)
      }
    }
    update()
    const id = setInterval(update, 1000)
    return () => clearInterval(id)
  }, [running, runStart])

  // 空状态（无 session）的本地状态：独立 composer，不污染主 composer
  const [emptyPrompt, setEmptyPrompt] = useState('')
  const [emptyWorkspace, setEmptyWorkspace] = useState<string>('')
  const [startingNew, setStartingNew] = useState(false)

  useEffect(() => {
    if (textareaRef.current) {
      autoGrow(textareaRef.current)
      textareaRef.current.focus()
    }
  }, [prompt])

  useEffect(() => {
    if (pinnedToBottom) {
      messagesRef.current?.scrollTo({
        top: messagesRef.current.scrollHeight,
        behavior: 'smooth',
      })
    }
  }, [events.length, streamText, streamReasoning, pinnedToBottom])

  const onScroll = () => {
    const el = messagesRef.current
    if (!el) return
    setPinnedToBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 80)
  }

  const hasMessages = events.length > 0

  const agentName = useAgentName(session)

  const handleInsertSkill = (title: string) => {
    // 优先用主对话 textarea，fallback 到空状态 textarea
    const ta = textareaRef.current || emptyTextareaRef.current
    if (!ta) {
      // 两个都没有（理论上不会），往 props prompt 里追加
      onPromptChange(prompt + ` /use skill:${title}`)
      return
    }
    const isEmpty = ta === emptyTextareaRef.current
    const currentText = isEmpty ? emptyPrompt : prompt
    const setText = isEmpty ? setEmptyPrompt : onPromptChange

    const start = ta.selectionStart ?? 0
    const end = ta.selectionEnd ?? 0
    const before = currentText.slice(0, start)
    const after = currentText.slice(end)
    const insertion = before.endsWith(' ') || before.length === 0 ? `` : ` `
    const newText = `${before}${insertion}/use skill:${title}${after}`
    setText(newText)
    requestAnimationFrame(() => {
      ta.focus()
      const pos = start + insertion.length + `/use skill:${title}`.length
      ta.setSelectionRange(pos, pos)
    })
  }

  const handlePickWorkspace = async () => {
    try {
      const dir = await native.pickDirectory()
      if (dir) {
        setEmptyWorkspace(dir)
        onToast(`工作区：${dir}`, 'info')
      }
    } catch (e) {
      onToast(`选择失败：${e}`, 'err')
    }
  }

  const handleEmptySend = () => {
    if (!emptyPrompt.trim() || startingNew) return
    setStartingNew(true)
    try {
      onStartNewSession(emptyPrompt.trim(), emptyWorkspace)
      setEmptyPrompt('')
    } finally {
      // 真正的 reset 等切换到 session 后自然消失
      setTimeout(() => setStartingNew(false), 800)
    }
  }

  const emptyTextareaRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    if (emptyTextareaRef.current) autoGrow(emptyTextareaRef.current)
  }, [emptyPrompt])

  return (
    <div className="main">
      <div className="topbar">
        <div className="topbar-title">
          {session ? sessionTitle(session) : 'SoulBuddy'}
        </div>
        {session && running && (
          <div className="topbar-meta">
            <span className="pill brand"><span className="pulse" />运行中 {elapsed}</span>
          </div>
        )}
        <span className="topbar-spacer" />
        <div className="topbar-tools">
          <button
            className="ibtn"
            onClick={onToggleRightPanel}
            title={rightPanelOpen ? '隐藏详情面板' : '显示详情面板'}
          >
            <Icon name="panel-right" size={16} />
          </button>
        </div>
      </div>

      <div className={`messages scroll ${!session ? 'empty-bg' : ''}`} ref={messagesRef} onScroll={onScroll}>
        {!session ? (
          <div className="empty-center">
            <div className="empty-hero">
              <div className="em-logo-lg">
                <img src="/SoulBuddy.png" alt="SoulBuddy" width={96} height={96} style={{borderRadius:'50%'}} />
              </div>
              <div className="empty-title-lg">SoulBuddy</div>
              <div className="empty-sub-lg">描述你想做什么，我来帮你写代码、改文件、跑命令</div>
            </div>

            <div className="empty-composer">
              <div className="composer-box">
                <textarea
                  ref={emptyTextareaRef}
                  className="composer-textarea"
                  value={emptyPrompt}
                  placeholder="描述一个任务…  @ 引用文件，/ 调用 Skills"
                  onChange={(e) => setEmptyPrompt(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey && !startingNew) {
                      e.preventDefault()
                      handleEmptySend()
                    }
                  }}
                  rows={1}
                  autoFocus
                />
                <div className="composer-bar">
                  <div className="cb-left">
                    <PlusMenu onInsertSkill={handleInsertSkill} onToast={onToast} workspaceRoot={session?.workspace_root} onExpertPick={onExpertChange} currentExpertId={session?.expert_id} />
                    <PermissionDropdown mode={permMode} onChange={onPermModeChange} />
                  </div>
                  <div className="cb-right">
                    {startingNew ? (
                      <div className="empty-send-loading">
                        <span className="spinner" /> 正在创建...
                      </div>
                    ) : (
                      <>
                        <ExpertSelector
                          current={null}
                          onChange={(id) => onExpertChange?.(id)}
                        />
                        <ModelSelector
                          current={session?.provider}
                          providers={providers}
                          onChange={onProviderChange}
                        />
                        <button
                          className="send-btn"
                          onClick={handleEmptySend}
                          disabled={!emptyPrompt.trim()}
                          title="发送 (Enter)"
                        >
                          <Icon name="send" size={34} strokeWidth={2.4} />
                        </button>
                      </>
                    )}
                  </div>
                </div>
              </div>

              <div className="empty-footer">
                <button
                  className={`chip ew-chip ${emptyWorkspace ? 'selected' : ''}`}
                  onClick={handlePickWorkspace}
                  title="选择工作空间（不选则使用默认工作区）"
                >
                  <Icon name="folder" size={14} />
                  {emptyWorkspace ? (
                    <span className="ew-path" title={emptyWorkspace}>{basename(emptyWorkspace)}</span>
                  ) : (
                    <span className="ew-hint">工作空间（可选）</span>
                  )}
                </button>
              </div>

              <div className="empty-suggests">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s.title}
                    className="empty-suggest-chip"
                    onClick={() => setEmptyPrompt(s.title)}
                  >
                    <Icon name="sparkles" size={12} />
                    {s.title}
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : !hasMessages ? (
          <div className="empty">
            <div className="em-logo">
              <img src="/SoulBuddy.png" alt="SoulBuddy" width={34} height={34} style={{borderRadius:'50%', objectFit:'cover'}} />
            </div>
            <div className="em-title">今天帮你做些什么？</div>
            <div className="em-sub">描述一个目标，Agent 会自己读代码、改文件、跑命令</div>
            <div className="empty-grid">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s.title}
                  className="empty-card"
                  onClick={() => onPromptChange(s.title)}
                >
                  <span className="ec-title">{s.title}</span>
                  <span className="ec-desc">{s.desc}</span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <>
            <MessageList
              events={events}
              onOpenArtifacts={onOpenArtifacts}
              onOpenChanges={onOpenChanges}
              onEditUser={onPromptChange}
            />
            {streamReasoning ? (
              <div className="msg">
                <div className="msg-inner">
                  <div className="reasoning-card live">
                    <div className="reasoning-head">
                      <span className="reasoning-icon"><Icon name="brain" size={14} /></span>
                      <span className="reasoning-name">深度思考中</span>
                      <span className="pill brand"><span className="pulse" />思考</span>
                    </div>
                    <div className="reasoning-body">
                      {streamReasoning}
                      <span className="streaming-caret" />
                    </div>
                  </div>
                </div>
              </div>
            ) : null}
            {streamText ? (
              <div className="msg assistant">
                <div className="msg-inner">
                  <div className="row">
                    <div className="avatar">
                      <img src="/SoulBuddy.png" alt="SoulBuddy" width={32} height={32} style={{borderRadius:'50%'}} />
                    </div>
                    <div className="body-wrap">
                      <div className="role-name">{agentName}</div>
                      <div className="md">
                        {streamText}
                        <span className="streaming-caret" />
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            ) : null}
          </>
        )}
      </div>

      {session && (
        <div className="composer">
        <div className="composer-box">
          <textarea
            ref={textareaRef}
            className="composer-textarea"
            value={prompt}
            placeholder={
              !session ? '先选择一个任务'
                : running ? 'Agent 正在运行，可点击右侧停止按钮中断'
                  : '描述一个任务…  @ 引用文件，/ 调用 Skills'
            }
            disabled={!session}
            onChange={(e) => onPromptChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !running) {
                e.preventDefault()
                onSend()
              }
            }}
            rows={1}
          />
          <div className="composer-bar">
            <div className="cb-left">
              <PlusMenu onInsertSkill={handleInsertSkill} onToast={onToast} workspaceRoot={session?.workspace_root} onExpertPick={onExpertChange} currentExpertId={session?.expert_id} />
              <PermissionDropdown mode={permMode} onChange={onPermModeChange} />
            </div>
            <div className="cb-right">
              {contextUsage && (
                <button
                  ref={usageBtnRef}
                  className="usage-ring-btn"
                  onClick={() => setUsageOpen((v) => !v)}
                  title="上下文用量"
                  aria-label="上下文用量"
                >
                  <UsageRing usage={contextUsage} />
                </button>
              )}
              <ExpertSelector
                current={session?.expert_id}
                onChange={(id) => onExpertChange?.(id)}
              />
              <ModelSelector
                current={session?.provider}
                providers={providers}
                onChange={onProviderChange}
              />
              {running ? (
                <button className="send-btn stop" onClick={onAbort} title="停止这次运行">
                  <Icon name="stop" size={14} strokeWidth={2.2} />
                </button>
              ) : (
                <button
                  className="send-btn"
                  onClick={onSend}
                  disabled={!session || !prompt.trim()}
                  title="发送 (Enter)"
                >
                  <Icon name="send" size={34} strokeWidth={2.4} />
                </button>
              )}
            </div>
          </div>
        </div>
        </div>
      )}

      {perms.map((req) => (
        <PermissionDialog
          key={req.call_id}
          req={req}
          onResolve={(choice) => onResolvePerm(req, choice)}
        />
      ))}

      {usageOpen && contextUsage && (
        <UsagePopover
          usage={contextUsage}
          anchorRef={usageBtnRef}
          onClose={() => setUsageOpen(false)}
        />
      )}
    </div>
  )
}

// --- Context Usage UI ----------------------------------------------------

function UsageRing({ usage }: { usage: ContextUsage }) {
  const size = 28
  const stroke = 3
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  const pct = Math.min(usage.pct, 100)
  const dash = (pct / 100) * c
  const color = usage.pct > 80 ? '#ef4444' : usage.pct > 60 ? '#f59e0b' : '#3b82f6'
  return (
    <svg width={size} height={size} className="usage-ring">
      <circle cx={size / 2} cy={size / 2} r={r} stroke="var(--bd-subtle, #e4e4ee)" strokeWidth={stroke} fill="none" />
      <circle
        cx={size / 2} cy={size / 2} r={r}
        stroke={color} strokeWidth={stroke} fill="none"
        strokeDasharray={`${dash} ${c - dash}`}
        strokeDashoffset={c * 0.25}
        strokeLinecap="round"
      />
      <text x="50%" y="54%" textAnchor="middle" fontSize="8" fill={color} fontWeight="600">
        {Math.round(pct)}%
      </text>
    </svg>
  )
}

function UsagePopover({
  usage, anchorRef, onClose,
}: {
  usage: ContextUsage
  anchorRef: React.RefObject<HTMLButtonElement>
  onClose: () => void
}) {
  const popoverRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null)

  useEffect(() => {
    const btn = anchorRef.current
    if (!btn) return
    const rect = btn.getBoundingClientRect()
    // 定位在按钮上方, 右对齐按钮
    setPos({
      top: rect.top - 8,
      right: window.innerWidth - rect.right,
    })
  }, [anchorRef])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (!popoverRef.current) return
      if (!popoverRef.current.contains(e.target as Node)) onClose()
    }
    // 延迟一帧再绑, 避免立即触发
    const t = setTimeout(() => document.addEventListener('mousedown', handler), 0)
    return () => {
      clearTimeout(t)
      document.removeEventListener('mousedown', handler)
    }
  }, [onClose])

  const fmt = (n: number) => {
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K'
    return String(n)
  }
  const items = [
    { key: 'system', label: '系统提示词', color: '#4cd07f' },
    { key: 'tools', label: '工具及子智能体', color: '#2d8eff' },
    { key: 'messages', label: '对话消息', color: '#ff9f43' },
    { key: 'memory', label: '记忆', color: '#a855f7' },
    { key: 'skills', label: 'Skills', color: '#ff6b9d' },
    { key: 'connectors', label: 'MCP', color: '#06b6d4' },
  ] as const

  if (!pos) return null

  return (
    <div
      ref={popoverRef}
      className="usage-popover"
      style={{
        position: 'fixed',
        top: pos.top,
        right: pos.right,
        transform: 'translateY(-100%)',
      }}
    >
      <div className="usage-popover-head">
        <span className="usage-popover-title">上下文用量</span>
        <button className="usage-popover-close" onClick={onClose}>×</button>
      </div>
      <div className="usage-popover-body">
        <div className="usage-pct-row">
          <span className="usage-pct-num">{usage.pct}%</span>
          <span className="usage-pct-sub">
            已使用 {fmt(usage.total)} / {fmt(usage.window)}
            {usage.estimated && <span className="usage-est-badge">估算中</span>}
          </span>
        </div>
        {/* 进度条: 按 window 计算宽度, 灰色底 = 未用部分 */}
        <div className="usage-bar">
          <div className="usage-bar-used">
            {items.map((it) => {
              const v = usage[it.key] || 0
              const w = usage.window > 0 ? (v / usage.window) * 100 : 0
              return <div key={it.key} style={{ width: `${w}%`, background: it.color }} />
            })}
          </div>
        </div>
        <div className="usage-legend">
          {items.map((it) => {
            const v = usage[it.key] || 0
            const pct = usage.window > 0 ? ((v / usage.window) * 100).toFixed(1) : '0.0'
            return (
              <div key={it.key} className="usage-legend-row">
                <span className="usage-legend-dot" style={{ background: it.color }} />
                <span className="usage-legend-label">{it.label}</span>
                <span className="usage-legend-val">{fmt(v)} · {pct}%</span>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

function useAgentName(session: SessionRecord | null): string {
  if (!session) return 'Agent'
  if (session.provider && session.provider !== 'auto' && session.provider !== 'offline') {
    return providerName(session.provider)
  }
  return 'Agent'
}

function providerName(id: string): string {
  const map: Record<string, string> = {
    deepseek: 'DeepSeek',
    anthropic: 'Claude',
    'openai-chat': 'GPT',
    offline: '离线 Agent',
  }
  return map[id] || id
}

function providerLabel(providers: ProviderOption[], current?: string | null): string {
  return providers.find((p) => p.id === current)?.label || 'Auto'
}

function basename(p: string): string {
  const parts = p.replace(/[\\/]+$/, '').split(/[\\/]/)
  return parts[parts.length - 1] || p
}
