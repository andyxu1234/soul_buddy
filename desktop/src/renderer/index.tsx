import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'
import { applyTheme, loadTheme } from './theme'

// 首帧前就定好主题，避免深色偏好下闪一帧白屏
applyTheme(loadTheme())

const el = document.getElementById('root')
if (el) {
  createRoot(el).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  )
}
