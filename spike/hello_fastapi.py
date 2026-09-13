"""P1.5 packaging Spike — hello-world FastAPI that drags in the REAL stack.

Goal: prove the highest-risk unknown ("can we ship Python as a Windows exe
that actually runs FastAPI + uvicorn + the LLM SDKs") before committing to the
Electron + sidecar architecture. This is intentionally throwaway verification,
not the real sidecar entry point.

Build:  pyinstaller --onefile spike/hello_fastapi.py
Run:    dist/hello_fastapi.exe
Probe:  curl http://127.0.0.1:8099/api/v1/health
"""
from __future__ import annotations

import uvicorn
from fastapi import FastAPI

# Pull in the heavy deps so PyInstaller has to bundle them. If ANY of these
# fail to import at freeze time or runtime, the spike is a FAIL.
import openai                 # noqa: F401
import anthropic              # noqa: F401
import sqlalchemy             # noqa: F401

app = FastAPI(title="soul_buddy_spike")


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "stack": ["fastapi", "uvicorn", "openai",
                                       "anthropic", "sqlalchemy"]}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8099, log_level="warning")
