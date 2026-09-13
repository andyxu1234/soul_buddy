/* Emotion Ball 引擎统一入口
 * 原始 4 个文件通过 window 共享，这里顺序 import 后统一导出
 */
import './rings.js'
import './emotions.js'
import './ball.js'
import './engine.js'

declare global {
  interface Window {
    EmotionBall: any
    EMOTION_SEED: any[]
    EMOTION_GROUPS: any[]
    EB_RINGS: any
  }
}

export const EmotionBall = window.EmotionBall
export const EMOTION_SEED = window.EMOTION_SEED
export const EMOTION_GROUPS = window.EMOTION_GROUPS
export const EB_RINGS = window.EB_RINGS

export default EmotionBall
