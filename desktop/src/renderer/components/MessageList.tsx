import { useMemo, useState } from 'react'
import type { SoulEvent, Artifact } from '../types'
import { ToolCallCard } from './ToolCallCard'
import { EmotionBall } from './EmotionBall'
import { Markdown } from './Markdown'
import { Icon } from './Icon'

const SB_THEME = { body: '#3b82f6', eyes: '#0f172a' }

function assistantEmotion(finished: boolean | undefined): string {
  return finished ? '36' : '34'
}

function formatTime(ts: number): string {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const hh = d.getHours().toString().padStart(2, '0')
  const mm = d.getMinutes().toString().padStart(2, '0')
  return `${hh}:${mm}`
}

interface Props {
  events: SoulEvent[]
  onOpenArtifacts: () => void
  onOpenChanges: () => void
  onEditUser?: (text: string) => void
}

type Item =
  | { id: string; kind: 'user'; text: string; timestamp: number }
  | { id: string; kind: 'assistant'; text: string; finished?: boolean; isLast?: boolean }
  | { id: string; kind: 'reasoning'; text: string; provider?: string }
  | { id: string; kind: 'tool'; name: string; callId?: string; args?: any; result?: string; isError?: boolean }
  | { id: string; kind: 'note'; tone?: 'warn' | 'err' | 'ok'; text: string }
  | { id: string; kind: 'aborted'; reason: string; modifiedFiles: string[]; turns: number }
  | { id: string; kind: 'delivery'; cards: Artifact[] }

function copyText(text: string) {
  navigator.clipboard?.writeText(text).catch(() => {})
}

const ABORT_REASON: Record<string, string> = {
  max_turns: '已达轮次上限，已自动停止',
  user_abort: '你中断了这次运行',
}

const PROVIDER_LABEL: Record<string, string> = {
  deepseek: 'DeepSeek',
  anthropic: 'Claude',
  'openai-chat': 'GPT',
  offline: '离线 Agent',
}

/** 持久化的 reasoning 事件 — 默认折叠,点击展开完整思考过程 */
function ReasoningCard({ text, provider }: { text: string; provider?: string }) {
  const [open, setOpen] = useState(false)
  const label = (provider && PROVIDER_LABEL[provider]) || provider
  return (
    <div className="reasoning-card">
      <button className="reasoning-head" onClick={() => setOpen((o) => !o)}
        title={open ? '收起思考过程' : '展开思考过程'}>
        <span className="reasoning-icon"><Icon name="brain" size={14} /></span>
        <span className="reasoning-name">思考过程</span>
        {label && <span className="reasoning-provider">{label}</span>}
        <span className={`reasoning-chev ${open ? 'open' : ''}`}>
          <Icon name="chevron-down" size={14} />
        </span>
      </button>
      {open && <div className="reasoning-body scroll">{text}</div>}
    </div>
  )
}

export function MessageList({ events, onOpenArtifacts, onOpenChanges, onEditUser }: Props) {
  const [vote, setVote] = useState<Record<string, 'up' | 'down'>>({})
  const [copiedId, setCopiedId] = useState<string | null>(null)

  const toggleVote = (id: string, dir: 'up' | 'down') =>
    setVote((p) => {
      const next = { ...p }
      if (next[id] === dir) delete next[id]
      else next[id] = dir
      return next
    })

  const items = useMemo<Item[]>(() => {
    const out: Item[] = []
    // 整轮运行是否结束（run_finished / run_aborted 出现过）
    let runFinished = false
    for (const ev of events) {
      if (ev.type === 'run_finished' || ev.type === 'run_aborted') {
        runFinished = true
        break
      }
    }

    for (const ev of events) {
      const d = ev.data || {}
      switch (ev.type) {
        case 'run_finished':
          out.push({ id: `rf-${ev.sequence}`, kind: 'note', tone: 'ok', text: `运行结束 · ${d.turns} 轮` })
          break
        case 'run_aborted':
          out.push({
            id: `ra-${ev.sequence}`, kind: 'aborted',
            reason: d.reason, modifiedFiles: d.modified_files || [], turns: d.turns,
          })
          break
        case 'message':
          if (d.role === 'user') {
            out.push({ id: `u-${ev.sequence}`, kind: 'user', text: d.text ?? '', timestamp: ev.timestamp })
          } else if (d.role === 'assistant') {
            if (d.text) {
              out.push({
                id: `a-${ev.sequence}`,
                kind: 'assistant',
                text: d.text,
                finished: runFinished,
                isLast: false,  // 下方回填
              })
            }
          }
          break
        case 'reasoning':
          if (d.text) {
            out.push({ id: `r-${ev.sequence}`, kind: 'reasoning', text: d.text, provider: d.provider })
          }
          break
        case 'function_call':
          out.push({
            id: `tc-${d.call_id}`, kind: 'tool',
            name: d.tool || d.name,
            callId: d.call_id, args: d.arguments, result: undefined, isError: false,
          })
          break
        case 'function_call_result': {
          const id = `tc-${d.call_id}`
          const existing = out.find((o) => o.id === id) as Extract<Item, { kind: 'tool' }> | undefined
          if (existing) {
            existing.result = d.content
            existing.isError = !!d.is_error
          } else {
            out.push({
              id, kind: 'tool', name: d.tool, callId: d.call_id,
              args: undefined, result: d.content, isError: !!d.is_error,
            })
          }
          break
        }
        case 'permission_request':
          out.push({ id: `pr-${ev.sequence}`, kind: 'note', tone: 'warn', text: `请求授权：${d.tool}` })
          break
        case 'permission_resolved':
          out.push({ id: `pj-${ev.sequence}`, kind: 'note', text: `授权结果：${d.action}` })
          break
        case 'permission_expired':
          out.push({ id: `pe-${ev.sequence}`, kind: 'note', tone: 'err', text: '授权超时（已按拒绝处理）' })
          break
        case 'turn_budget_warning':
          out.push({
            id: `tw-${ev.sequence}`, kind: 'note', tone: 'warn',
            text: `接近轮次上限（${d.turn}/${d.max}）`,
          })
          break
        case 'error':
          out.push({ id: `er-${ev.sequence}`, kind: 'note', tone: 'err', text: d.detail || '发生错误' })
          break
        case 'skill_loaded':
          out.push({
            id: `sk-${ev.sequence}`, kind: 'note',
            text: `已加载技能：${d.title}${d.auto ? '（自动匹配）' : ''}`,
          })
          break
        case 'artifact_presented':
          if (d?.cards?.length) {
            out.push({
              id: `ap-${ev.sequence}`, kind: 'delivery',
              cards: d.cards as Artifact[],
            })
          }
          break
      }
    }
    // 标记最后一条 assistant 消息：只有它显示 run-status / 点赞等操作
    const assistants = out.filter((o): o is Extract<Item, { kind: 'assistant' }> =>
      o.kind === 'assistant')
    if (assistants.length > 0) {
      assistants[assistants.length - 1].isLast = true
    }
    return out
  }, [events])

  const copy = (id: string, text: string) => {
    copyText(text)
    setCopiedId(id)
    setTimeout(() => setCopiedId((c) => (c === id ? null : c)), 1600)
  }

  // 只渲染"有内容"的条目：单纯的运行开始/结束噪音不进消息流，
  // 状态由顶栏徽标和 composer 承担。
  return (
    <>
      {items.map((it) => {
        if (it.kind === 'user') {
          return (
            <div className="msg user" key={it.id}>
              <div className="msg-inner">
                <div className="row user">
                  <div className="body-wrap">
                    <div className="bubble">{it.text}</div>
                    <div className="user-meta">
                      <span className="um-time">{formatTime(it.timestamp)}</span>
                      <span className="um-divider" />
                      <button
                        className="ibtn sm um-action"
                        onClick={() => copy(it.id, it.text)}
                        title={copiedId === it.id ? '已复制' : '复制'}
                      >
                        <Icon name={copiedId === it.id ? 'check' : 'copy'} size={13} />
                      </button>
                      <button
                        className="ibtn sm um-action"
                        onClick={() => onEditUser?.(it.text)}
                        title="编辑并重新发送"
                      >
                        <Icon name="pencil" size={13} />
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )
        }

        if (it.kind === 'assistant') {
          const v = vote[it.id]
          const showActions = it.isLast
          return (
            <div className="msg assistant" key={it.id}>
              <div className="msg-inner">
                <div className="row">
                  <div className="avatar">
                    <img src="/SoulBuddy.png" alt="SoulBuddy" width={32} height={32} style={{borderRadius:'50%'}} />
                  </div>
                  <div className="body-wrap">
                    <div className="role-name">SoulBuddy</div>
                    <Markdown text={it.text} />

                    {showActions && (
                      <div className="run-status">
                        <span className="rs-label">
                          {it.finished ? '已完成' : '运行中'}
                        </span>
                        <span className="rs-sep" />
                        <button className="rs-link" onClick={onOpenArtifacts}>查看所有产物</button>
                        <span className="rs-sep" />
                        <button className="rs-link" onClick={onOpenChanges}>查看所有变更</button>
                      </div>
                    )}

                    {showActions && (
                      <div className="msg-actions">
                        <button
                          className="ibtn sm"
                          onClick={() => copy(it.id, it.text)}
                          title={copiedId === it.id ? '已复制' : '复制'}
                        >
                          <Icon name={copiedId === it.id ? 'check' : 'copy'} size={14} />
                        </button>
                        <button
                          className={`ibtn sm ${v === 'up' ? 'on' : ''}`}
                          onClick={() => toggleVote(it.id, 'up')}
                          title="有帮助"
                        >
                          <Icon name="thumbs-up" size={14} />
                        </button>
                        <button
                          className={`ibtn sm ${v === 'down' ? 'on' : ''}`}
                          onClick={() => toggleVote(it.id, 'down')}
                          title="没帮助"
                        >
                          <Icon name="thumbs-down" size={14} />
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          )
        }

        if (it.kind === 'reasoning') {
          return (
            <div className="msg" key={it.id}>
              <div className="msg-inner">
                <ReasoningCard text={it.text} provider={it.provider} />
              </div>
            </div>
          )
        }

        if (it.kind === 'tool') {
          return (
            <div className="msg" key={it.id}>
              <div className="msg-inner">
                <ToolCallCard
                  name={it.name} callId={it.callId}
                  args={it.args} result={it.result} isError={it.isError}
                />
              </div>
            </div>
          )
        }

        if (it.kind === 'note') {
          const icon: any = it.tone === 'err' || it.tone === 'warn' ? 'alert' : 'info'
          return (
            <div className="msg" key={it.id}>
              <div className="msg-inner">
                <div className={`note ${it.tone || ''}`}>
                  <span className="n-icon"><Icon name={icon} size={13} /></span>
                  <span>{it.text}</span>
                </div>
              </div>
            </div>
          )
        }

        if (it.kind === 'aborted') {
          return (
            <div className="msg" key={it.id}>
              <div className="msg-inner">
                <div className="aborted-banner">
                  <div className="ab-head">
                    <Icon name="alert" size={14} />
                    {ABORT_REASON[it.reason] || `运行已中断（${it.reason}）`}
                  </div>
                  <div className="ab-body">
                    已执行 {it.turns} 轮
                    {it.modifiedFiles.length > 0
                      ? `，以下文件已被修改（未回滚）：`
                      : '，没有文件被改动。'}
                  </div>
                  {it.modifiedFiles.length > 0 && (
                    <ul>
                      {it.modifiedFiles.map((f) => <li key={f}>{f}</li>)}
                    </ul>
                  )}
                </div>
               </div>
             </div>
           )
        }

        if (it.kind === 'delivery') {
          const primary = it.cards.find((c) => c.is_primary) ?? it.cards[0]
          return (
            <div className="msg" key={it.id}>
              <div className="msg-inner">
                <div className="delivery-card">
                  <div className="del-head">
                    <Icon name="folder" size={14} />
                    <span>已交付 {it.cards.length} 个文件</span>
                    <span className="del-hint">点击可在右侧预览</span>
                  </div>
                  <div className="del-list">
                    {it.cards.map((c) => (
                      <button
                        key={c.path}
                        className={`del-item ${c.is_primary ? 'primary' : ''}`}
                        onClick={() => {
                          // Auto-open right panel + set active artifact
                          onOpenArtifacts()
                          // Dispatch a custom event so App/RightPanel knows which to open
                          window.dispatchEvent(new CustomEvent('sb:open-artifact', { detail: c.path }))
                        }}
                      >
                        <span className="del-icon">{c.icon}</span>
                        <span className="del-name">{c.name}</span>
                        <span className="del-ext">{c.extension}</span>
                        <span className="del-size">{c.size}</span>
                        {c.is_primary && <span className="del-primary">主要</span>}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )
        }

        return null
      })}
    </>
  )
}
