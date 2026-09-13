"""FastAPI sidecar: health, auth, bootstrap, sessions, history, eventbus."""
import asyncio

from fastapi.testclient import TestClient
from soul_buddy.events import EventBus
from soul_buddy.models import Event


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] in ("ok", "degraded")


def test_unauth_returns_401(client):
    r = client.get("/api/v1/sessions")
    assert r.status_code == 401


def test_bootstrap_sets_cookie(client):
    token = client.app.state.runtime.bootstrap_token
    r = client.get(f"/bootstrap?token={token}", headers={"host": "127.0.0.1"})
    assert r.status_code == 200
    assert "sb_session" in client.cookies


def test_bootstrap_replay_rejected(client):
    token = client.app.state.runtime.bootstrap_token
    client.get(f"/bootstrap?token={token}", headers={"host": "127.0.0.1"})  # consume
    r2 = client.get(f"/bootstrap?token={token}", headers={"host": "127.0.0.1"})  # replay
    assert r2.status_code == 401


def test_authed_session_crud(client):
    token = client.app.state.runtime.bootstrap_token
    client.get(f"/bootstrap?token={token}", headers={"host": "127.0.0.1"})
    body = {"workspace_root": str(client.app.state.runtime.storage.base)}
    r = client.post("/api/v1/sessions", json=body)
    assert r.status_code == 200
    sid = r.json()["id"]
    lst = client.get("/api/v1/sessions")
    assert lst.status_code == 200 and any(s["id"] == sid for s in lst.json())
    h = client.get(f"/api/v1/sessions/{sid}/history")
    assert h.status_code == 200 and h.json() == []


def test_eventbus_publish_subscribe():
    bus = EventBus()
    got = []

    async def consume():
        async for ev in bus.subscribe("sess1"):
            got.append(ev)
            break

    async def produce():
        await bus.publish("sess1", Event(session_id="sess1", sequence=1,
                                         type="message", data={"x": 1}))

    async def main():
        t = asyncio.create_task(consume())
        await asyncio.sleep(0.01)
        await produce()
        await t

    asyncio.run(main())
    assert got and got[0].data["x"] == 1


def test_sse_live_stream_delivers_delta_and_plain_event_names():
    """Regression (real TCP — TestClient buffers infinite SSE streams):
      1. `event:` must carry the enum VALUE ("run_started"), not "EventType.RUN_STARTED"
         (Python 3.11+ str-Enum format change; EventSource matches by exact name).
      2. assistant_delta is bus-only (sequence=0) and must not be swallowed by
         the `sequence > last_id` replay filter.
    """
    import threading
    import time

    import httpx
    import uvicorn

    from soul_buddy.api.main import create_app
    from soul_buddy.models import EventType

    app = create_app()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    holder: dict = {}

    def _serve():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        holder["loop"] = loop
        loop.run_until_complete(server.serve())

    th = threading.Thread(target=_serve, daemon=True)
    th.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"
    port = server.servers[0].sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"

    try:
        client = httpx.Client(base_url=base, timeout=10)
        token = app.state.runtime.bootstrap_token
        r = client.get("/bootstrap", params={"token": token})
        assert r.status_code == 200
        ws = str(app.state.runtime.storage.base)
        sid = client.post("/api/v1/sessions",
                          json={"workspace_root": ws}).json()["id"]

        # persisted replay event, appended with the enum exactly like _aemit
        app.state.runtime.storage.append_event(sid, EventType.MESSAGE,
                                               {"role": "user", "text": "hi"})

        lines: list[str] = []
        done = threading.Event()

        def read_sse():
            try:
                with client.stream("GET",
                                   f"/api/v1/sessions/{sid}/events") as resp:
                    for line in resp.iter_lines():
                        lines.append(line)
                        if line == "event: run_started":
                            done.set()
                            return
            except Exception:
                pass
            finally:
                done.set()

        th_sse = threading.Thread(target=read_sse, daemon=True)
        th_sse.start()
        time.sleep(0.5)          # let the replay (user) flush first

        # live events, appended+published exactly like the agent does. The
        # delta goes first: SSE preserves queue order, and the reader stops
        # at run_started, so both lines must be captured.
        rt = app.state.runtime
        delta = Event(session_id=sid, sequence=0,
                      type=EventType.ASSISTANT_DELTA, data={"text": "hel"})
        asyncio.run_coroutine_threadsafe(rt.events.publish(sid, delta),
                                         holder["loop"]).result(timeout=5)
        ev = rt.storage.append_event(sid, EventType.RUN_STARTED,
                                     {"session_id": sid})
        asyncio.run_coroutine_threadsafe(rt.events.publish(sid, ev),
                                         holder["loop"]).result(timeout=5)

        assert done.wait(timeout=15), (
            f"run_started never arrived; got: {lines!r}")

        assert "event: message" in lines
        assert "event: assistant_delta" in lines
        assert not any(l.startswith("event: EventType.") for l in lines)
        client.close()
    finally:
        server.should_exit = True
        th.join(timeout=5)
