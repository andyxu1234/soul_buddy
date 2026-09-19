"""Chat image attachments (multimodal input).

Covers the whole pipeline without a real LLM:
  * provider buffer shape (initial_user_message with image refs)
  * wire conversion (Anthropic base64 blocks / OpenAI image_url data URLs)
  * storage (uploads dir + path-traversal guard)
  * API round-trip (POST /runs with images -> uploads on disk -> MESSAGE
    event metadata -> GET uploads serving -> bootstrap replay)
"""
import asyncio
import base64
import time
from pathlib import Path

from soul_buddy.providers.anthropic import _to_wire_messages as aw_wire
from soul_buddy.providers.base import ModelTurn
from soul_buddy.providers.offline import OfflineProvider
from soul_buddy.providers.openai_chat import _to_wire_messages as oai_wire
from soul_buddy.storage import SessionStore

# 1x1 transparent PNG
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
PNG_B64 = base64.b64encode(PNG_BYTES).decode("ascii")


# --- provider buffer shape -------------------------------------------------

def test_initial_user_message_plain_unchanged():
    msg = OfflineProvider().initial_user_message("hello")
    assert msg == {"role": "user", "content": "hello"}


def test_initial_user_message_with_image_refs():
    ref = {"type": "image", "path": "C:/x/a.png",
           "media_type": "image/png", "name": "a.png"}
    msg = OfflineProvider().initial_user_message("看图", [ref])
    assert msg["role"] == "user"
    assert msg["content"][0] == {"type": "text", "text": "看图"}
    assert msg["content"][1] == ref


# --- wire conversion -------------------------------------------------------

def test_anthropic_wire_converts_ref_to_base64_block(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(PNG_BYTES)
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "hi"},
        {"type": "image", "path": str(p), "media_type": "image/png"},
    ]}]
    wire = aw_wire(msgs)
    src = wire[0]["content"][1]["source"]
    assert src["type"] == "base64"
    assert src["media_type"] == "image/png"
    assert base64.b64decode(src["data"]) == PNG_BYTES
    # in-memory buffer keeps the small ref, the wire copy is separate
    assert msgs[0]["content"][1]["path"] == str(p)


def test_anthropic_wire_missing_file_becomes_text_note(tmp_path):
    msgs = [{"role": "user", "content": [
        {"type": "image", "path": str(tmp_path / "gone.png"),
         "media_type": "image/png", "name": "gone.png"},
    ]}]
    wire = aw_wire(msgs)
    blk = wire[0]["content"][0]
    assert blk["type"] == "text" and "gone.png" in blk["text"]


def test_openai_wire_converts_ref_to_image_url(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(PNG_BYTES)
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "hi"},
        {"type": "image", "path": str(p), "media_type": "image/png"},
    ]}]
    wire = oai_wire(msgs)
    part = wire[0]["content"][1]
    assert part["type"] == "image_url"
    url = part["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == PNG_BYTES


# --- storage ----------------------------------------------------------------

def test_save_upload_and_traversal_guard():
    store = SessionStore()
    meta = store.save_upload("s_up1", "", "Clipboard_Screenshot.png",
                             "image/png", PNG_BYTES)
    assert meta["mime"] == "image/png"
    assert meta["size"] == len(PNG_BYTES)
    p = store.upload_path("s_up1", meta["file"])
    assert p is not None and p.read_bytes() == PNG_BYTES
    # traversal / missing names are refused, never resolved
    assert store.upload_path("s_up1", "..\\evil.png") is None
    assert store.upload_path("s_up1", "sub/../../x.png") is None
    assert store.upload_path("s_up1", "nope.png") is None


# --- agent loop -------------------------------------------------------------

def test_agent_loop_passes_refs_and_emits_metadata(make_agent):
    captured = {}

    def scripted(req):
        captured["messages"] = list(req.messages)
        return ModelTurn(text="看完了")

    agent, session, storage = make_agent(script=[scripted])
    meta = storage.save_upload(session.id, session.workspace_root,
                               "pic.png", "image/png", PNG_BYTES)
    ref = {"type": "image", "media_type": "image/png", "name": meta["name"],
           "size": meta["size"], "file": meta["file"],
           "path": str(storage._session_dir(session.id, session.workspace_root)
                       / "uploads" / meta["file"])}
    asyncio.run(agent.run(session, "看图", object(), images=[ref]))

    # buffer carries the ref block (no base64 in the LLM buffer)
    first = captured["messages"][0]
    assert isinstance(first["content"], list)
    assert first["content"][1]["type"] == "image"
    assert first["content"][1]["path"] == ref["path"]
    # MESSAGE event records metadata only
    events = storage.read_transcript(session.id)
    user_ev = next(e for e in events
                   if e.type == "message" and e.data.get("role") == "user")
    assert user_ev.data["text"] == "看图"
    assert user_ev.data["images"][0]["file"] == meta["file"]
    # no-image runs keep the old flat event shape
    assert "images" not in user_ev.data or user_ev.data["images"]


# --- provider contract (regression) -----------------------------------------
# A provider override of initial_user_message without the images param raised
# TypeError BEFORE the run emitted any event — every DeepSeek/GPT session hung
# at 运行中 with an empty chat. All built-in providers must accept both arities.

def test_all_providers_initial_user_message_contract(tmp_path):
    from soul_buddy.providers.anthropic import AnthropicProvider
    from soul_buddy.providers.deepseek import DeepSeekProvider
    from soul_buddy.providers.openai_chat import OpenAIChatProvider

    p = tmp_path / "a.png"
    p.write_bytes(PNG_BYTES)
    ref = {"type": "image", "path": str(p), "media_type": "image/png"}
    for cls in (OfflineProvider, AnthropicProvider,
                OpenAIChatProvider, DeepSeekProvider):
        plain = cls.initial_user_message(None, "hi")       # unbound, self unused
        assert plain == {"role": "user", "content": "hi"}, cls
        with_img = cls.initial_user_message(None, "hi", [dict(ref)])
        assert with_img["content"][0] == {"type": "text", "text": "hi"}, cls
        assert with_img["content"][1]["type"] == "image", cls


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


def test_run_with_images_roundtrip(client):
    _bootstrap(client)
    sid = _new_session(client)
    r = client.post("/api/v1/runs", json={
        "session_id": sid, "prompt": "看图",
        "images": [{"filename": "shot.png", "mime": "image/png",
                    "data": PNG_B64}]})
    assert r.status_code == 200, r.text

    h = _wait_finished(client, sid)
    user_ev = next(e for e in h
                   if e["type"] == "message" and e["data"].get("role") == "user")
    assert user_ev["data"]["text"] == "看图"
    img = user_ev["data"]["images"][0]
    assert img["mime"] == "image/png" and img["size"] == len(PNG_BYTES)

    store = client.app.state.runtime.storage
    p = store.upload_path(sid, img["file"])
    assert p is not None and p.read_bytes() == PNG_BYTES

    # serving endpoint returns the raw bytes
    r = client.get(f"/api/v1/sessions/{sid}/uploads/{img['file']}")
    assert r.status_code == 200 and r.content == PNG_BYTES

    # replay rebuilds the ref block for the next turn's LLM buffer
    session = store.get_session(sid)
    msgs = store.bootstrap_messages(session, OfflineProvider())
    refs = [b for m in msgs if isinstance(m.get("content"), list)
            for b in m["content"]
            if isinstance(b, dict) and b.get("type") == "image"]
    assert refs and Path(refs[0]["path"]).read_bytes() == PNG_BYTES


def test_run_rejects_bad_mime(client):
    _bootstrap(client)
    sid = _new_session(client)
    r = client.post("/api/v1/runs", json={
        "session_id": sid, "prompt": "x",
        "images": [{"filename": "a.zip", "mime": "application/zip",
                    "data": PNG_B64}]})
    assert r.status_code == 400
    assert "不支持的图片类型" in str(r.json()["detail"])


def test_run_rejects_too_many_images(client):
    _bootstrap(client)
    sid = _new_session(client)
    r = client.post("/api/v1/runs", json={
        "session_id": sid, "prompt": "x",
        "images": [{"filename": f"{i}.png", "mime": "image/png",
                    "data": PNG_B64} for i in range(6)]})
    assert r.status_code == 400


def test_run_rejects_corrupt_base64(client):
    _bootstrap(client)
    sid = _new_session(client)
    r = client.post("/api/v1/runs", json={
        "session_id": sid, "prompt": "x",
        "images": [{"filename": "a.png", "mime": "image/png",
                    "data": "!!!not-base64!!!"}]})
    assert r.status_code == 400


def test_run_default_prompt_when_only_images(client):
    _bootstrap(client)
    sid = _new_session(client)
    r = client.post("/api/v1/runs", json={
        "session_id": sid, "prompt": "",
        "images": [{"filename": "a.png", "mime": "image/png",
                    "data": PNG_B64}]})
    assert r.status_code == 200
    h = _wait_finished(client, sid)
    user_ev = next(e for e in h
                   if e["type"] == "message" and e["data"].get("role") == "user")
    assert user_ev["data"]["text"]   # fallback text was filled in
    assert user_ev["data"]["images"]
