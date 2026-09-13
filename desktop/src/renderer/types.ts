export type EventType =
  | 'message'
  | 'reasoning'
  | 'reasoning_delta'
  | 'function_call'
  | 'function_call_result'
  | 'file-history-snapshot'
  | 'assistant_delta'
  | 'permission_request'
  | 'permission_resolved'
  | 'permission_expired'
  | 'turn_budget_warning'
  | 'run_aborted'
  | 'run_started'
  | 'run_finished'
  | 'skill_loaded'
  | 'artifact_presented'
  | 'context_usage'
  | 'context_limit_exceeded'
  | 'error'

export interface SoulEvent {
  session_id: string
  sequence: number
  type: EventType
  data: any
  timestamp: number
}

export interface SessionRecord {
  id: string
  workspace_root: string
  cwd: string
  provider: string
  /** 显示名；null 时前端回退到 workspace_root 的目录名 */
  title?: string | null
  created_at: number
  updated_at: number
}

export interface ToolCallInfo {
  id: string
  name: string
  arguments: any
}

export interface PermissionRequest {
  call_id: string
  tool: string
  args: any
  reason?: string
  allow_remember?: boolean
  overwrite?: boolean
  existing_bytes?: number
  diff_preview?: string
}

export interface PermissionRule {
  id: string
  tool: string
  pattern: string
  kind: string
  created_at?: number
}

/** P5: a deliverable produced inside the workspace. */
export interface Artifact {
  path: string
  name: string
  extension: string
  icon: string
  category: string
  size: string
  exists: boolean
  is_primary: boolean
  is_url: boolean
}

/** P7: 上下文用量分类别统计 (旁路 token 估算 + 校准). */
export interface ContextUsage {
  system: number       // role prompt (不含 skills/memory/connectors)
  tools: number        // tools spec JSON
  messages: number     // 对话历史 + 本轮 user
  skills: number       // skills index + loaded (已含在 system 内)
  memory: number       // memory segment (已含在 system 内)
  connectors: number   // MCP 连接器描述 (预留)
  total: number
  window: number
  pct: number          // total / window * 100
  estimated: boolean   // true=估算值, false=官方 prompt_tokens 校准后
}
