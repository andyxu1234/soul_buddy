/* Smoke-test the PyInstaller-bundled sidecar exe (no GUI, no python -m).
 *
 * Verifies the highest-risk packaging concern: that the onefile bundle
 * includes every lazily-imported module (providers, mcp, skills, sqlalchemy)
 * and still prints SOULBUDDY_READY + serves the API + same-origin UI.
 *
 *   node scripts/smoke_exe.cjs
 */
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const net = require('node:net')
const http = require('node:http')
const crypto = require('node:crypto')
const path = require('node:path')

const ROOT = path.resolve(__dirname, '..', '..')
const EXE = path.join(ROOT, 'dist', 'soul_sidecar.exe')
const RENDERER = path.join(__dirname, '..', 'out', 'renderer')

function getFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer()
    srv.unref()
    srv.on('error', reject)
    srv.listen(0, '127.0.0.1', () => {
      const port = srv.address().port
      srv.close(() => resolve(port))
    })
  })
}

function req(port, method, p, headers = {}, timeoutMs = 8000) {
  return new Promise((resolve, reject) => {
    const r = http.request(
      { host: '127.0.0.1', port, path: p, method, headers, timeout: timeoutMs },
      (res) => {
        let body = ''
        res.on('data', (c) => (body += c))
        res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body }))
      },
    )
    r.on('timeout', () => { r.destroy(new Error(`http ${method} ${p} timeout`)) })
    r.on('error', reject)
    r.end()
  })
}

async function main() {
  if (!fs.existsSync(EXE)) {
    console.error('MISSING exe:', EXE)
    process.exit(1)
  }
  const port = await getFreePort()
  const token = crypto.randomBytes(32).toString('hex')
  const args = ['--port', String(port), '--token', token]
  if (fs.existsSync(RENDERER)) args.push('--static-dir', RENDERER)

  console.log(`spawning ${path.basename(EXE)} (port ${port})...`)
  const proc = spawn(EXE, args, { env: { ...process.env }, stdio: ['ignore', 'pipe', 'pipe'] })

  let buf = ''
  const readyP = new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('READY timeout (45s)')), 45000)
    proc.stdout.on('data', (d) => {
      const s = d.toString()
      buf += s
      process.stdout.write(`[exe] ${s}`)
      if (buf.includes('SOULBUDDY_READY')) {
        clearTimeout(t)
        resolve()
      }
    })
    proc.stderr.on('data', (d) => process.stderr.write(`[exe!] ${d}`))
    proc.on('exit', (c) => { if (!buf.includes('SOULBUDDY_READY')) reject(new Error(`exited early code=${c}`)) })
  })

  try {
    await readyP
    const checks = []
    const boot = await req(port, 'GET', `/bootstrap?token=${token}`)
    checks.push(['bootstrap 200', boot.status === 200])
    const cookie = boot.headers['set-cookie']?.[0]?.split(';')[0]
    const h = cookie ? { Cookie: cookie } : {}

    const health = await req(port, 'GET', '/api/v1/health', h)
    checks.push(['health 200 (authed)', health.status === 200])

    const sessions = await req(port, 'GET', '/api/v1/sessions', h)
    checks.push(['sessions 200 (authed)', sessions.status === 200])

    const rules = await req(port, 'GET', '/api/v1/permissions/rules', h)
    checks.push(['rules 200 (authed)', rules.status === 200])

    const noCookie = await req(port, 'GET', '/api/v1/sessions')
    checks.push(['sessions 401 w/o cookie', noCookie.status === 401])

    const index = await req(port, 'GET', '/', h)
    checks.push(['GET / -> index.html', index.status === 200 && /html/i.test(index.headers['content-type'] || '')])

    let ok = true
    for (const [name, pass] of checks) {
      console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}`)
      if (!pass) ok = false
    }
    if (!ok) throw new Error('some checks failed')
    console.log('\nALL CHECKS PASSED (packaged exe)')
  } finally {
    try { proc.kill('SIGTERM') } catch {}
  }
}

main().catch((e) => { console.error('FAILED:', e.message); process.exit(1) })
