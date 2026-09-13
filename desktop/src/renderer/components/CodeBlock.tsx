import { useMemo, useState } from 'react'
import { Icon } from './Icon'

/**
 * 轻量语法高亮。
 *
 * 刻意不引入 highlight.js / prism —— 那会往 renderer 里塞几百 KB，
 * 而 Agent 输出的代码块只需要"看得清结构"这一层着色。
 * 实现：一次 regex 扫描同时匹配注释 / 字符串 / 数字 / 关键字，
 * 命中的切片包成 <span>，其余原样输出，因此不会破坏原始空白。
 */

const KEYWORDS: Record<string, string[]> = {
  python: ['def', 'class', 'return', 'if', 'elif', 'else', 'for', 'while', 'import',
    'from', 'as', 'with', 'try', 'except', 'finally', 'raise', 'yield', 'lambda',
    'async', 'await', 'in', 'is', 'not', 'and', 'or', 'None', 'True', 'False',
    'self', 'pass', 'break', 'continue', 'global', 'nonlocal', 'assert', 'del'],
  javascript: ['const', 'let', 'var', 'function', 'return', 'if', 'else', 'for',
    'while', 'import', 'from', 'export', 'default', 'class', 'extends', 'new',
    'async', 'await', 'try', 'catch', 'finally', 'throw', 'typeof', 'instanceof',
    'this', 'null', 'undefined', 'true', 'false', 'switch', 'case', 'break',
    'continue', 'of', 'in', 'delete', 'void', 'yield'],
  typescript: ['const', 'let', 'var', 'function', 'return', 'if', 'else', 'for',
    'while', 'import', 'from', 'export', 'default', 'class', 'extends', 'new',
    'async', 'await', 'try', 'catch', 'finally', 'throw', 'typeof', 'instanceof',
    'this', 'null', 'undefined', 'true', 'false', 'switch', 'case', 'break',
    'continue', 'of', 'in', 'interface', 'type', 'enum', 'implements', 'public',
    'private', 'protected', 'readonly', 'as', 'satisfies'],
  json: ['true', 'false', 'null'],
  bash: ['if', 'then', 'else', 'elif', 'fi', 'for', 'in', 'do', 'done', 'while',
    'case', 'esac', 'function', 'return', 'export', 'local', 'echo', 'cd', 'set'],
  sql: ['select', 'from', 'where', 'insert', 'into', 'values', 'update', 'set',
    'delete', 'create', 'table', 'alter', 'drop', 'join', 'left', 'right',
    'inner', 'outer', 'on', 'group', 'by', 'order', 'limit', 'and', 'or', 'not'],
  go: ['func', 'package', 'import', 'return', 'if', 'else', 'for', 'range', 'var',
    'const', 'type', 'struct', 'interface', 'go', 'defer', 'chan', 'map', 'nil',
    'true', 'false', 'switch', 'case', 'default', 'break', 'continue'],
  rust: ['fn', 'let', 'mut', 'pub', 'use', 'mod', 'struct', 'enum', 'impl', 'trait',
    'match', 'if', 'else', 'for', 'while', 'loop', 'return', 'self', 'Some',
    'None', 'Ok', 'Err', 'true', 'false', 'crate', 'async', 'await'],
}

const ALIAS: Record<string, string> = {
  py: 'python', js: 'javascript', jsx: 'javascript', ts: 'typescript',
  tsx: 'typescript', sh: 'bash', shell: 'bash', zsh: 'bash', console: 'bash',
  yml: 'yaml', golang: 'go', rs: 'rust',
}

const KW_SET: Record<string, Set<string>> = {}
function keywordsFor(lang: string): Set<string> {
  const norm = ALIAS[lang] || lang
  if (!KW_SET[norm]) {
    KW_SET[norm] = new Set(KEYWORDS[norm] || [])
  }
  return KW_SET[norm]
}

// 一次扫描：注释 | 字符串 | 数字 | 标识符
const TOKEN_RE = new RegExp(
  [
    '(#|//)[^\\n]*',                       // 行注释
    '/\\*[\\s\\S]*?\\*/',                  // 块注释
    '"(?:[^"\\\\\\n]|\\\\.)*"',            // 双引号字符串
    "'(?:[^'\\\\\\n]|\\\\.)*'",            // 单引号字符串
    '`(?:[^`\\\\]|\\\\.)*`',               // 模板字符串
    '\\b\\d+(?:\\.\\d+)?\\b',              // 数字
    '[A-Za-z_$][\\w$]*',                   // 标识符
  ].join('|'),
  'g',
)

interface Piece {
  text: string
  cls?: string
}

function tokenize(code: string, lang: string): Piece[] {
  const kw = keywordsFor(lang)
  const out: Piece[] = []
  let last = 0
  let m: RegExpExecArray | null
  TOKEN_RE.lastIndex = 0
  while ((m = TOKEN_RE.exec(code)) !== null) {
    if (m.index > last) out.push({ text: code.slice(last, m.index) })
    const t = m[0]
    let cls: string | undefined
    if (t.startsWith('#') || t.startsWith('//') || t.startsWith('/*')) cls = 'tk-com'
    else if (t[0] === '"' || t[0] === "'" || t[0] === '`') cls = 'tk-str'
    else if (/^\d/.test(t)) cls = 'tk-num'
    else if (kw.has(t)) cls = 'tk-key'
    out.push({ text: t, cls })
    last = m.index + t.length
  }
  if (last < code.length) out.push({ text: code.slice(last) })
  return out
}

export interface CodeBlockProps {
  code: string
  lang?: string
}

export function CodeBlock({ code, lang }: CodeBlockProps) {
  const [copied, setCopied] = useState(false)
  const pieces = useMemo(() => tokenize(code, lang || ''), [code, lang])

  const copy = () => {
    navigator.clipboard?.writeText(code).then(
      () => {
        setCopied(true)
        setTimeout(() => setCopied(false), 1600)
      },
      () => { /* clipboard 不可用时静默 */ },
    )
  }

  return (
    <div className="code-block">
      <div className="code-head">
        <span className="code-lang">{lang || 'text'}</span>
        <span className="ch-spacer" />
        <button className="ibtn sm" onClick={copy} title={copied ? '已复制' : '复制代码'}>
          <Icon name={copied ? 'check' : 'copy'} size={14} />
        </button>
      </div>
      <pre className="code-body scroll">
        <code>
          {pieces.map((p, i) =>
            p.cls ? (
              <span key={i} className={p.cls}>{p.text}</span>
            ) : (
              <span key={i}>{p.text}</span>
            ),
          )}
        </code>
      </pre>
    </div>
  )
}
