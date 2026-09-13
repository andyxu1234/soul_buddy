import { memo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CodeBlock } from './CodeBlock'

/**
 * Agent 输出的 Markdown 渲染器。
 *
 * 唯一职责：把 ``` 围栏交给 CodeBlock（语言标签 + 复制 + 高亮），
 * 其余元素沿用 styles.css 里的 .md 排版。
 * 行内 `code` 走 .md code 样式，不进 CodeBlock。
 */
export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ className, children, ...props }: any) {
            const match = /language-(\w+)/.exec(className || '')
            const value = String(children ?? '').replace(/\n$/, '')
            // 围栏块：react-markdown 给 pre>code，带 language-xxx
            if (match || (value.includes('\n') && className)) {
              return <CodeBlock code={value} lang={match?.[1]} />
            }
            return (
              <code className={className} {...props}>
                {children}
              </code>
            )
          },
          pre({ children }: any) {
            // CodeBlock 自带 pre，这里不能再包一层
            return <>{children}</>
          },
          a({ children, href }: any) {
            return (
              <a href={href} target="_blank" rel="noreferrer noopener">
                {children}
              </a>
            )
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
})
