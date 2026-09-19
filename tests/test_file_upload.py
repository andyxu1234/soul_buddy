"""Chat file attachments (text files, name-only in the UI).

Covers the whole pipeline without a real LLM:
  * buffer shape (agent injects lightweight file refs into the first user
    message; the transcript stores metadata only, never content)
  * wire conversion (file refs resolved to fenced text at wire time; binary /
    missing files degrade to a note)
  * storage (uploads dir reuse + replay rebuilds refs)
  * API round-trip (POST /runs with files -> uploads on disk -> MESSAGE event
    metadata -> replay buffer)
"""
import asyncio
import base64
import time
from pathlib import Path

from soul_buddy.providers.anthropic import _to_wire_messages as aw_wire
from soul_buddy.providers.base import ModelTurn, file_ref_text, with_file_refs
from soul_buddy.providers.offline import OfflineProvider
from soul_buddy.providers.openai_chat import _to_wire_messages as oai_wire

TXT_B64 = base64.b64encode("<html><body>hi</body></html>".encode()).decode()


# --- buffer shape ------------------------------------------------------------

def test_with_file_refs_plain_string_message():
    msg = with_file_refs({"role": "user", "content": "看文件"}, None)
    assert msg == {"role": "user", "content": "看文件"}
    ref = {"type": "file", "path": "C:/x/index.html", "name": "index.html"}
    msg = with_file_refs({"role": "user", "content": "看文件"}, [ref])
    assert msg["content"][0] == {"type": "text", "text": "看文件"}
    assert msg["content"][1] == ref


def test_agent_loop_passes_file_refs_and_emits_metadata(make_agent):
    captured = {}

    def scripted(req):
        captured["messages"] = list(req.messages)
        return ModelTurn(text="看完了")

    agent, session, storage = make_agent(script=[scripted])
    meta = storage.save_upload(session.id, session.workspace_root,
                               "index.html", "text/html", b"<html></html>")
    ref = {"type": "file", "name": meta["name"], "mime": "text/html",
           "size": meta["size"], "file": meta["file"],
           "path": str(storage._session_dir(session.id, session.workspace_root)
                       / "uploads" / meta["file"])}
    asyncio.run(agent.run(session, "看文件", object(), files=[ref]))

    # buffer carries the light ref block (no file content in the buffer)
    first = captured["messages"][0]
    assert isinstance(first["content"], list)
    assert first["content"][1]["type"] == "file"
    assert first["content"][1]["path"] == ref["path"]
    # MESSAGE event records metadata only — content stays out of the transcript
    events = storage.read_transcript(session.id)
    user_ev = next(e for e in events
                   if e.type == "message" and e.data.get("role") == "user")
    assert user_ev.data["text"] == "看文件"
    assert user_ev.data["files"][0]["file"] == meta["file"]
    assert "path" not in user_ev.data["files"][0]


# --- wire conversion (content is parsed only at wire time) -------------------

def test_wire_resolves_file_ref_to_fenced_text(tmp_path):
    p = tmp_path / "index.html"
    p.write_text("<html>hello</html>", encoding="utf-8")
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "看文件"},
        {"type": "file", "path": str(p), "name": "index.html"},
    ]}]
    for wire in (aw_wire(msgs), oai_wire(msgs)):
        blk = wire[0]["content"][1]
        assert blk["type"] == "text"
        assert "index.html" in blk["text"]
        assert "<html>hello</html>" in blk["text"]
    # the in-memory buffer keeps the light ref untouched
    assert msgs[0]["content"][1]["type"] == "file"


def test_file_ref_binary_becomes_note(tmp_path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"\x00\x01\xff\xfe binary")
    blk = file_ref_text({"type": "file", "path": str(p), "name": "a.bin"})
    assert blk["type"] == "text" and "二进制" in blk["text"]


def test_file_ref_missing_file_becomes_note(tmp_path):
    blk = file_ref_text({"type": "file", "path": str(tmp_path / "gone.html"),
                         "name": "gone.html"})
    assert blk["type"] == "text" and "gone.html" in blk["text"]


def test_file_ref_long_content_truncated(tmp_path):
    p = tmp_path / "big.txt"
    p.write_text("x" * 30_000, encoding="utf-8")
    blk = file_ref_text({"type": "file", "path": str(p), "name": "big.txt"})
    assert "已截断" in blk["text"]


def test_wire_keeps_both_image_and_file_refs(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("txt", encoding="utf-8")
    msgs = [{"role": "user", "content": [
        {"type": "image", "path": "C:/gone.png", "media_type": "image/png",
         "name": "gone.png"},
        {"type": "file", "path": str(p), "name": "a.txt"},
    ]}]
    wire = oai_wire(msgs)
    kinds = [b.get("type") for b in wire[0]["content"]]
    assert kinds == ["text", "text"]  # image note + fenced file text


# --- replay ------------------------------------------------------------------

def test_bootstrap_replay_rebuilds_file_refs(client):
    _bootstrap(client)
    sid = _new_session(client)
    r = client.post("/api/v1/runs", json={
        "session_id": sid, "prompt": "看文件",
        "files": [{"filename": "index.html", "mime": "text/html",
                   "data": TXT_B64}]})
    assert r.status_code == 200, r.text
    _wait_finished(client, sid)

    store = client.app.state.runtime.storage
    session = store.get_session(sid)
    msgs = store.bootstrap_messages(session, OfflineProvider())
    refs = [b for m in msgs if isinstance(m.get("content"), list)
            for b in m["content"]
            if isinstance(b, dict) and b.get("type") == "file"]
    assert refs and Path(refs[0]["path"]).read_text(encoding="utf-8") \
        == "<html><body>hi</body></html>"


# --- API round-trip ----------------------------------------------------------

def _bootstrap(client):
    token = client.app.state.runtime.bootstrap_token
    r = client.get(f"/bootstrap?token={token}", headers={"host": "127.0.0.1"})
    assert r.status_code == 200


def _new_session(client):
    ws = str(client.app.state.runtime.storage.base)
    r = client.post("/api/v1/sessions", json={"workspace_root": ws})
    assert r.status_code == 200
    return r.json()["id"]


def _wait_finished(client, sid) -> list[dict]:
    for _ in range(200):
        h = client.get(f"/api/v1/sessions/{sid}/history").json()
        if any(e["type"] == "run_finished" or e["type"] == "run_aborted"
               for e in h):
            return h
        time.sleep(0.05)
    raise AssertionError("run did not finish in time")


def test_run_with_files_roundtrip(client):
    _bootstrap(client)
    sid = _new_session(client)
    r = client.post("/api/v1/runs", json={
        "session_id": sid, "prompt": "看文件",
        "files": [{"filename": "index.html", "mime": "text/html",
                   "data": TXT_B64}]})
    assert r.status_code == 200, r.text

    h = _wait_finished(client, sid)
    user_ev = next(e for e in h
                   if e["type"] == "message" and e["data"].get("role") == "user")
    assert user_ev["data"]["text"] == "看文件"
    f = user_ev["data"]["files"][0]
    assert f["name"] == "index.html" and f["mime"] == "text/html"

    store = client.app.state.runtime.storage
    p = store.upload_path(sid, f["file"])
    assert p is not None
    assert p.read_bytes() == b"<html><body>hi</body></html>"

    # serving endpoint (same one images use) returns the raw bytes
    r = client.get(f"/api/v1/sessions/{sid}/uploads/{f['file']}")
    assert r.status_code == 200 and b"<html>" in r.content


def test_run_default_prompt_when_only_files(client):
    _bootstrap(client)
    sid = _new_session(client)
    r = client.post("/api/v1/runs", json={
        "session_id": sid, "prompt": "",
        "files": [{"filename": "a.txt", "mime": "text/plain",
                   "data": TXT_B64}]})
    assert r.status_code == 200
    h = _wait_finished(client, sid)
    user_ev = next(e for e in h
                   if e["type"] == "message" and e["data"].get("role") == "user")
    assert user_ev["data"]["text"]   # fallback text was filled in
    assert user_ev["data"]["files"]


def test_run_rejects_too_many_files(client):
    _bootstrap(client)
    sid = _new_session(client)
    r = client.post("/api/v1/runs", json={
        "session_id": sid, "prompt": "x",
        "files": [{"filename": f"{i}.txt", "mime": "text/plain",
                   "data": TXT_B64} for i in range(6)]})
    assert r.status_code == 400


def test_run_rejects_oversized_file(client):
    _bootstrap(client)
    sid = _new_session(client)
    big = base64.b64encode(b"x" * (8 * 1024 * 1024 + 1)).decode()
    r = client.post("/api/v1/runs", json={
        "session_id": sid, "prompt": "x",
        "files": [{"filename": "big.txt", "mime": "text/plain", "data": big}]})
    assert r.status_code == 400
    assert "上限" in str(r.json()["detail"])


def test_run_rejects_corrupt_base64_files(client):
    _bootstrap(client)
    sid = _new_session(client)
    r = client.post("/api/v1/runs", json={
        "session_id": sid, "prompt": "x",
        "files": [{"filename": "a.txt", "mime": "text/plain",
                   "data": "!!!not-base64!!!"}]})
    assert r.status_code == 400
