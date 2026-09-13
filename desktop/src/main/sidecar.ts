import { spawn, type ChildProcess } from 'node:child_process'
import net from 'node:net'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { app } from 'electron'

/** Path to the sidecar log file — matches the Python side's SIDECAR_LOG. */
function sidecarLogPath(): string {
  const p = path.join(os.homedir(), '.soul_buddy', 'logs', 'sidecar.log')
  fs.mkdirSync(path.dirname(p), { recursive: true })
  return p
}

/** Append a chunk of raw sidecar output to sidecar.log with a [raw] prefix. */
function appendSidecarLog(stream: 'stdout' | 'stderr', data: Buffer): void {
  const line = `[${new Date().toISOString()}] [sidecar:${stream}] ${data.toString()}`
  try { fs.appendFileSync(sidecarLogPath(), line) } catch { /* best-effort */ }
}

/** Find a free TCP port on the loopback interface. */
export function getFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = net.createServer()
    srv.unref()
    srv.on('error', reject)
    srv.listen(0, '127.0.0.1', () => {
      const addr = srv.address() as net.AddressInfo
      const port = addr.port
      srv.close(() => resolve(port))
    })
  })
}

/**
 * Walk upward from `start` looking for the repo root that contains
 * `soul_buddy/api/__main__.py` so we can `python -m soul_buddy.api`.
 */
export function findProjectRoot(start: string): string {
  let dir = start
  for (let i = 0; i < 8; i++) {
    if (fs.existsSync(path.join(dir, 'soul_buddy', 'api', '__main__.py'))) {
      return dir
    }
    const parent = path.dirname(dir)
    if (parent === dir) break
    dir = parent
  }
  return process.cwd()
}

export interface SidecarHandle {
  proc: ChildProcess
  port: number
  token: string
  stop(): void
}

/**
 * Spawn the FastAPI sidecar and wait for the `SOULBUDDY_READY` line on stdout
 * (B11). The token is passed via argv, never printed (A09).
 */
export function startSidecar(opts: {
  port: number
  token: string
  staticDir: string
}): Promise<SidecarHandle> {
  // Packaged mode: launch the PyInstaller-built single exe shipped under
  // resources/sidecar (electron-builder extraResources). Dev mode: spawn the
  // Python module directly so a venv/editable install is used.
  let command: string
  let args: string[]
  let cwd: string
  if (app.isPackaged) {
    command = path.join(process.resourcesPath, 'sidecar', 'soul_sidecar.exe')
    args = [
      '--port', String(opts.port),
      '--token', opts.token,
    ]
    cwd = process.resourcesPath
  } else {
    // Prefer project .venv for dev (it has all dependencies installed).
    // Falls back to SOUL_PYTHON env var, then bare `python` on PATH.
    const projectRoot = findProjectRoot(__dirname)
    const venvPython = path.join(projectRoot, '.venv', 'Scripts', 'python.exe')
    const python = fs.existsSync(venvPython)
      ? venvPython
      : (process.env.SOUL_PYTHON || 'python')
    command = python
    args = [
      '-m', 'soul_buddy.api',
      '--port', String(opts.port),
      '--token', opts.token,
    ]
    cwd = projectRoot
  }
  if (opts.staticDir) args.push('--static-dir', opts.staticDir)

  // Strip proxy env vars — httpx on Windows picks up system proxy (often
  // SOCKS5 from Clash/v2ray via TUN-mode registry) and crashes with
  // "Missing dependencies for SOCKS support". The sidecar is pure localhost,
  // so no proxy is needed. Also set NO_PROXY to override anything httpx may
  // read from registry.
  const cleanEnv: NodeJS.ProcessEnv = { ...process.env }
  for (const k of Object.keys(cleanEnv)) {
    if (/proxy/i.test(k)) delete cleanEnv[k]
  }
  cleanEnv.NO_PROXY = '*'
  cleanEnv.no_proxy = '*'

  const proc = spawn(command, args, {
    cwd,
    env: cleanEnv,
    stdio: ['ignore', 'pipe', 'pipe'],
  })

  // Persistent raw capture — every stdout/stderr chunk from the sidecar (e.g.
  // Python crash traces, uvicorn banner before our logging kicks in, SOULBUDDY_READY)
  // goes to sidecar.log. Lifespan = proc lifetime.
  proc.stdout?.on('data', (d: Buffer) => {
    appendSidecarLog('stdout', d)
  })
  proc.stderr?.on('data', (d: Buffer) => {
    appendSidecarLog('stderr', d)
  })

  const ready = new Promise<void>((resolve, reject) => {
    let buf = ''
    const onReadyData = (d: Buffer) => {
      buf += d.toString()
      if (buf.includes('SOULBUDDY_READY')) {
        cleanup()
        resolve()
      }
    }
    const onExit = (code: number | null) => {
      cleanup()
      if (code !== null && code !== 0) {
        reject(new Error(`sidecar exited early (code=${code})`))
      }
    }
    const timer = setTimeout(() => {
      cleanup()
      reject(new Error('sidecar READY timeout (15s)'))
    }, 15000)
    function cleanup() {
      clearTimeout(timer)
      proc.stdout?.off('data', onReadyData)
      proc.off('exit', onExit)
    }
    proc.stdout?.on('data', onReadyData)
    proc.on('exit', onExit)
    proc.on('error', (e) => { cleanup(); reject(e) })
  })

  return ready.then(() => ({
    proc,
    port: opts.port,
    token: opts.token,
    stop() {
      try { proc.kill('SIGTERM') } catch { /* ignore */ }
    },
  }))
}

export const runtimeJsonPath = () => path.join(app.getPath('userData'), 'runtime.json')
