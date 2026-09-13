import { app, BrowserWindow, session, dialog, ipcMain, shell } from 'electron'
import path from 'node:path'
import fs from 'node:fs'
import crypto from 'node:crypto'
import { getFreePort, startSidecar, runtimeJsonPath, type SidecarHandle } from './sidecar'

let sidecar: SidecarHandle | null = null
let mainWindow: BrowserWindow | null = null
let apiBase = ''
let watchdog: NodeJS.Timeout | null = null

// Preload proactively asks for the sidecar base URL; reply with whatever we
// have (even empty before bootstrap completes — it re-asks on load anyway).
ipcMain.on('soul:request-config', (event) => {
  event.reply('soul:config', { base: apiBase })
})

function resolveStaticDir(): string {
  const env = process.env.SOUL_STATIC_DIR || ''
  if (env && fs.existsSync(env)) return env
  // Packaged: renderer shipped as resources next to the exe.
  if (app.isPackaged) {
    const p = path.join(process.resourcesPath, 'renderer')
    return fs.existsSync(p) ? p : ''
  }
  // Dev: use the pre-built out/renderer from `npm run build`.
  const repoRoot = path.resolve(__dirname, '..', '..')
  const devP = path.join(repoRoot, 'out', 'renderer')
  return fs.existsSync(devP) ? devP : ''
}

async function consumeBootstrap(port: number, token: string): Promise<void> {
  apiBase = `http://127.0.0.1:${port}`
  const res = await fetch(`${apiBase}/bootstrap?token=${token}`, { method: 'GET' })
  if (!res.ok) {
    throw new Error(`bootstrap failed: HTTP ${res.status}`)
  }
  // Capture the httpOnly session cookie and inject it into the renderer's
  // Electron session so all subsequent same-origin fetches are authenticated
  // without ever exposing the token to renderer JS (A09).
  const setCookie = res.headers.get('set-cookie')
  if (setCookie) {
    const [pair] = setCookie.split(';')
    const idx = pair.indexOf('=')
    const name = pair.slice(0, idx).trim()
    const value = pair.slice(idx + 1).trim()
    await session.defaultSession.cookies.set({
      url: apiBase,
      name,
      value,
      httpOnly: true,
      sameSite: 'strict',
      path: '/',
    })
  }
}

// --- native helpers (renderer may never touch Node APIs) ------------------
ipcMain.handle('soul:pick-directory', async () => {
  if (!mainWindow) return null
  const res = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory', 'createDirectory'],
    title: '选择工作区目录',
  })
  if (res.canceled || res.filePaths.length === 0) return null
  return res.filePaths[0]
})

// Reveal a file in Explorer/Finder without executing it.
ipcMain.handle('soul:reveal-path', async (_e, target: string) => {
  if (!target || typeof target !== 'string') return false
  shell.showItemInFolder(target)
  return true
})

// Open a file with the OS default handler (used by artifact cards).
ipcMain.handle('soul:open-path', async (_e, target: string) => {
  if (!target || typeof target !== 'string') return 'invalid'
  return shell.openPath(target)   // '' on success, else an error message
})

function createWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 900,
    minHeight: 620,
    backgroundColor: '#ffffff',
    show: false,
    icon: path.join(__dirname, '../renderer/SoulBuddy.png'),
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      webSecurity: true,
    },
  })

  win.once('ready-to-show', () => win.show())
  win.webContents.on('did-finish-load', () => {
    win.webContents.send('soul:config', { base: apiBase })
  })
  return win
}

function startWatchdog(): void {
  watchdog = setInterval(async () => {
    if (!sidecar) return
    try {
      const r = await fetch(`${apiBase}/api/v1/health`)
      if (!r.ok) throw new Error('bad status')
    } catch {
      dialog.showErrorBox('后端已退出', 'Python 后端进程无响应，请重启应用。')
      if (watchdog) clearInterval(watchdog)
    }
  }, 5000)
}

async function bootstrap(): Promise<void> {
  // Dev mode: renderer is served by Vite (localhost:5173). Allow that origin on
  // the sidecar via CORS so the renderer's same cookie can be used cross-origin.
  if (process.env.SOUL_DEV === '1' && !process.env.SOUL_DEV_CORS) {
    process.env.SOUL_DEV_CORS = 'http://localhost:5173'
  }

  const port = await getFreePort()
  const token = crypto.randomBytes(32).toString('hex')
  const staticDir = resolveStaticDir()

  sidecar = await startSidecar({ port, token, staticDir })
  await consumeBootstrap(port, token)

  fs.writeFileSync(
    runtimeJsonPath(),
    JSON.stringify({ pid: process.pid, port, ts: Date.now() }),
  )

  mainWindow = createWindow()

  if (!app.isPackaged && process.env.SOUL_DEV === '1') {
    // Dev: renderer served by Vite; sidecar allows the dev origin via SOUL_DEV_CORS.
    await mainWindow.loadURL('http://localhost:5173')
  } else {
    // Prod: same-origin React app served by the sidecar itself.
    await mainWindow.loadURL(`${apiBase}/`)
  }

  startWatchdog()
}

app.whenReady().then(bootstrap).catch((e) => {
  dialog.showErrorBox('启动失败', String(e?.stack || e))
  app.quit()
})

// A18: graceful shutdown — ask the sidecar to terminate, then kill it.
app.on('before-quit', () => {
  if (sidecar) {
    fetch(`${apiBase}/api/v1/shutdown`, { method: 'POST' }).catch(() => {})
    sidecar.stop()
    sidecar = null
  }
  if (watchdog) clearInterval(watchdog)
  try { fs.rmSync(runtimeJsonPath(), { force: true }) } catch { /* ignore */ }
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
