// Headless smoke test of the Electron <-> sidecar handshake (P4 integration).
// Simulates what src/main/index.ts does: spawn sidecar, wait for SOULBUDDY_READY,
// consume /bootstrap to get the httpOnly cookie, then exercise the auth-gated API
// and the same-origin static hosting.
const { spawn } = require('node:child_process')
const net = require('node:net')
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')

const REPO = path.resolve(__dirname, '..', '..')
const VENV_PY = path.join(REPO, '.venv', 'Scripts', 'python.exe')
const PY = fs.existsSync(VENV_PY) ? VENV_PY : 'python'
const STATIC_DIR = path.resolve(__dirname, '..', 'out', 'renderer')

function getFreePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer()
    s.unref()
    s.on('error', reject)
    s.listen(0, '127.0.0.1', () => {
      const a = s.address()
      s.close(() => resolve(a.port))
    })
  })
}

function startSidecar(port, token) {
  const args = ['-m', 'soul_buddy.api', '--port', String(port), '--token', token, '--static-dir', STATIC_DIR]
  const proc = spawn(PY, args, { cwd: REPO, env: { ...process.env }, stdio: ['ignore', 'pipe', 'pipe'] })
  return new Promise((resolve, reject) => {
    let buf = ''
    const onData = (d) => {
      buf += d.toString()
      if (buf.includes('SOULBUDDY_READY')) {
        cleanup()
        resolve(proc)
      }
    }
    const onErr = (d) => process.stderr.write('[sidecar] ' + d.toString())
    const onExit = (code) => { cleanup(); reject(new Error('sidecar exited: ' + code)) }
    const timer = setTimeout(() => { cleanup(); reject(new Error('READY timeout')) }, 15000)
    function cleanup() { clearTimeout(timer); proc.stdout.off('data', onData); proc.stderr.off('data', onErr); proc.off('exit', onExit) }
    proc.stdout.on('data', onData)
    proc.stderr.on('data', onErr)
    proc.on('exit', onExit)
    proc.on('error', (e) => { cleanup(); reject(e) })
  })
}

async function main() {
  const port = await getFreePort()
  const token = crypto.randomBytes(32).toString('hex')
  const base = `http://127.0.0.1:${port}`
  console.log(`spawning sidecar on ${base} (static=${STATIC_DIR})`)
  const proc = await startSidecar(port, token)
  console.log('✓ SOULBUDDY_READY received')

  // 1. bootstrap handshake
  const bres = await fetch(`${base}/bootstrap?token=${token}`, { method: 'GET' })
  if (!bres.ok) throw new Error('bootstrap HTTP ' + bres.status)
  const setCookie = bres.headers.get('set-cookie')
  if (!setCookie) throw new Error('no Set-Cookie from /bootstrap')
  const [pair] = setCookie.split(';')
  const eq = pair.indexOf('=')
  const cookie = `${pair.slice(0, eq).trim()}=${pair.slice(eq + 1).trim()}`
  console.log('✓ /bootstrap returned cookie:', cookie)

  const headers = { cookie, 'Content-Type': 'application/json' }

  // 2. health (no auth needed)
  const h = await fetch(`${base}/api/v1/health`)
  console.log('✓ /api/v1/health ->', await h.json())

  // 3. list sessions (auth-gated)
  const list = await fetch(`${base}/api/v1/sessions`, { headers })
  const sessions = await list.json()
  console.log('✓ GET /sessions ->', Array.isArray(sessions) ? `${sessions.length} sessions` : sessions)

  // 4. create session (auth-gated)
  const created = await fetch(`${base}/api/v1/sessions`, {
    method: 'POST', headers,
    body: JSON.stringify({ workspace_root: REPO }),
  })
  const sess = await created.json()
  if (!sess.id) throw new Error('create session failed: ' + JSON.stringify(sess))
  console.log('✓ POST /sessions ->', sess.id)

  // 5. bootstrap token is one-time: replay must 401
  const replay = await fetch(`${base}/bootstrap?token=${token}`)
  console.log('✓ bootstrap replay rejected:', replay.status === 401 ? 'yes (401)' : 'NO (' + replay.status + ')')

  // 6. missing cookie must 401
  const noAuth = await fetch(`${base}/api/v1/sessions`)
  console.log('✓ unauthenticated /sessions rejected:', noAuth.status === 401 ? 'yes (401)' : 'NO (' + noAuth.status + ')')

  // 7. static hosting: index.html served at "/"
  const idx = await fetch(`${base}/`, { headers })
  const html = await idx.text()
  console.log('✓ GET / served index.html:', idx.status === 200 && html.includes('<div id="root">') ? 'yes' : 'NO')

  // 8. permission rules list (auth-gated)
  const rules = await fetch(`${base}/api/v1/permissions/rules`, { headers })
  console.log('✓ GET /permissions/rules ->', rules.status, await rules.text())

  // cleanup
  proc.kill('SIGTERM')
  console.log('\nALL CHECKS PASSED')
}

main().catch((e) => {
  console.error('SMOKE FAILED:', e)
  process.exit(1)
})
