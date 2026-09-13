"""Repro: does the SSE stream deliver run events to a client like the Electron renderer?"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

PORT = int(os.environ.get("REPRO_PORT", "8932"))
BASE = f"http://127.0.0.1:{PORT}"
HOME = tempfile.mkdtemp(prefix="soul_repro_")

env = {
    **os.environ,
    "SOUL_BUDDY_HOME": HOME,
    "SOUL_BOOTSTRAP_TOKEN": "testtoken123",
    "SOUL_LOG_LEVEL": "INFO",
}
for k in list(env):
    if "proxy" in k.lower():
        env.pop(k)

proc = subprocess.Popen(
    [sys.executable, "-m", "soul_buddy.api", "--port", str(PORT), "--token", "testtoken123"],
    cwd=r"C:\andy\codebase\soul_buddy", env=env,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def wait_ready():
    t0 = time.time()
    while time.time() - t0 < 20:
        time.sleep(0.2)
        try:
            r = httpx.get(f"{BASE}/api/v1/health", timeout=1)
            if r.status_code == 200:
                return True
        except Exception:
            pass
    return False


def main():
    print("server ready:", wait_ready())
    client = httpx.Client(base_url=BASE, timeout=httpx.Timeout(15, read=15))
    r = client.get("/bootstrap", params={"token": "testtoken123"})
    print("bootstrap:", r.status_code)

    r = client.post("/api/v1/sessions", json={"workspace_root": HOME, "title": "repro"})
    sid = r.json()["id"]
    print("session:", sid)

    # --- open SSE in a reader thread (like the renderer's EventSource) ---
    sse_lines: list[tuple[float, str]] = []
    stop = threading.Event()

    def read_sse():
        try:
            with client.stream("GET", f"/api/v1/sessions/{sid}/events",
                               timeout=httpx.Timeout(60, read=60)) as resp:
                sse_lines.append((time.time(), f"[sse status {resp.status_code}]"))
                for line in resp.iter_lines():
                    if stop.is_set():
                        break
                    if line:
                        sse_lines.append((time.time(), line))
        except Exception as e:
            sse_lines.append((time.time(), f"[sse error {type(e).__name__}: {e}]"))

    th = threading.Thread(target=read_sse, daemon=True)
    th.start()
    time.sleep(1.0)

    # --- start a run (offline provider: no keys configured) ---
    t_send = time.time()
    r = client.post("/api/v1/runs", json={"session_id": sid, "prompt": "hi"})
    print("start_run:", r.status_code, r.text[:120])

    deadline = time.time() + 10
    finished = False
    while time.time() < deadline:
        h = client.get(f"/api/v1/sessions/{sid}/history").json()
        if any(e["type"] in ("run_finished", "run_aborted") for e in h):
            finished = True
            break
        time.sleep(0.3)
    print("run finished on backend:", finished)

    time.sleep(2.0)
    stop.set()
    time.sleep(0.5)

    print("\n=== SSE received (rel. seconds after start_run) ===")
    got_types = []
    for ts, line in sse_lines:
        rel = ts - t_send
        if line.startswith("data:"):
            try:
                ev = json.loads(line[5:])
                got_types.append(ev["type"])
                print(f"+{rel:6.2f}s  seq={ev.get('sequence')}  {ev['type']:<18} "
                      f"{json.dumps(ev.get('data'), ensure_ascii=False)[:90]}")
                continue
            except Exception:
                pass
        if line.startswith(("[sse", "event:", "id:")):
            print(f"+{rel:6.2f}s  {line[:100]}")

    print("\nassistant_delta delivered to SSE client:", "assistant_delta" in got_types)


try:
    main()
finally:
    proc.kill()
