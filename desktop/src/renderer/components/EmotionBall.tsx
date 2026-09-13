import { useEffect, useRef, useImperativeHandle, forwardRef } from 'react'
import { EmotionBall as EB } from '../emotion-ball'

export interface EmotionBallHandle {
  setEmotion: (id: string) => void
  spin: () => void
  bounce: () => void
}

export interface EmotionBallProps {
  /** 初始表情 ID，默认 '02'（平静） */
  emotionId?: string
  /** 是否启用 idle 待机策略 */
  idle?: boolean
  /** 身体形状 */
  shape?: 'blob' | 'wedge' | 'gem'
  /** 球主题色（覆盖默认色） */
  theme?: { body: string; eyes: string }
  /** 是否显示线稿模式 */
  sketch?: boolean
  /** 宽度像素 */
  width?: number
  /** 额外 className */
  className?: string
  /** 样式 */
  style?: React.CSSProperties
}

/**
 * SoulBuddy 的 EmotionBall 表情球组件
 * 基于 Emotion Ball 引擎（sam70361/aora-bot）
 *
 * 32 种表情 ID 速查：
 *   生命周期: 00 睡眠 · 01 苏醒 · 02 平静 · 03 思考 · 04 困倦 · 05 打哈欠 · 06 惊喜 · 07 疲惫
 *   情绪反应: 10 开心 · 11 大笑 · 12 坏笑 · 13 好奇 · 14 聆听 · 15 专注 · 16 生气 · 17 委屈
 *            18 难过 · 19 害羞 · 20 得意 · 21 兴奋 · 22 欣赏 · 23 惊讶 · 24 恐惧 · 25 嫌弃 · 26 羞怯
 *   代理状态: 30 待机 · 31 思考中 · 32 搜索中 · 33 执行中 · 34 运行中 · 35 等待中 · 36 完成 · 37 出错
 *            38 加载中 · 39 流式输出
 */
export const EmotionBall = forwardRef<EmotionBallHandle, EmotionBallProps>(
  function EmotionBall(
    {
      emotionId = '02',
      idle = true,
      shape = 'blob',
      theme,
      sketch = false,
      width = 100,
      className,
      style,
    },
    ref,
  ) {
    const containerRef = useRef<HTMLDivElement>(null)
    const engineRef = useRef<any>(null)

    useImperativeHandle(ref, () => ({
      setEmotion: (id: string) => engineRef.current?.setEmotion(id),
      spin: () => engineRef.current?.spin(),
      bounce: () => engineRef.current?.bounce(),
    }))

    useEffect(() => {
      const el = containerRef.current
      if (!el || !EB) return

      // 清空以便重建
      el.innerHTML = ''
      const engine = EB.create(el, {
        emotion: emotionId,
        idle,
        shape,
        theme,
        sketch,
        autostart: true,
      })
      engineRef.current = engine

      return () => {
        engine.destroy()
        engineRef.current = null
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    // 表情切换
    useEffect(() => {
      if (engineRef.current && emotionId) {
        engineRef.current.setEmotion(emotionId)
      }
    }, [emotionId])

    // 形状切换
    useEffect(() => {
      if (engineRef.current) {
        engineRef.current.setStyle({ shape })
      }
    }, [shape])

    useEffect(() => {
      if (engineRef.current) {
        engineRef.current.setStyle({ sketch: sketch ? 1 : 0 })
      }
    }, [sketch])

    return (
      <div
        ref={containerRef}
        className={className}
        style={{
          width,
          height: width,
          lineHeight: 0,
          ...style,
        }}
      />
    )
  },
)

export default EmotionBall
