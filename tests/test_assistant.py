"""Ask the map: off by default, signed-in only, tools read real data as the user, answers stream with internal links only.
The chat provider is replaced by a scripted fake; nothing leaves the machine."""
from __future__ import annotations

import json

import pytest

import assistant as AS
from conftest import W


def sse(body: str) -> list[dict]:
    return [json.loads(x[6:]) for x in body.split("\n\n") if x.startswith("data: ")]


class FakeProvider:
    """Replaces assistant._chat_stream: each call plays the next scripted turn."""
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def __call__(self, messages, tools, model=None):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools, "model": model})
        turn = self.script.pop(0)
        for t in turn.get("text", []):
            yield "text", t
        yield "final", {"content": "".join(turn.get("text", [])), "finish": "tool_calls" if turn.get("tools") else "stop",
                        "tool_calls": [{"id": f"c{i}", "name": n, "arguments": json.dumps(a)} for i, (n, a) in enumerate(turn.get("tools", []))]}


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("RINDA_ASSISTANT", "on")
    monkeypatch.setenv("RINDA_LLM_API_KEY", "test-not-a-real-key")


def test_off_by_default_and_needs_key(roles, monkeypatch, tmp_path):
    monkeypatch.setattr(AS, "KEY_FILE", str(tmp_path / "none" / "llm_api_key"))   # ignore any real key on this machine
    monkeypatch.delenv("RINDA_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("RINDA_ASSISTANT", raising=False)
    s = roles["supervisor"].get("/api/v1/assistant/status").json()
    assert s["enabled"] is False and "switched off" in s["reason"] and s["sends_to"] is None
    assert roles["supervisor"].post("/api/v1/assistant/ask", json={"question": "hi"}, headers=W).status_code == 409
    monkeypatch.setenv("RINDA_ASSISTANT", "on")
    monkeypatch.delenv("RINDA_LLM_API_KEY", raising=False)
    s = roles["supervisor"].get("/api/v1/assistant/status").json()
    assert s["enabled"] is False and "API key" in s["reason"] and s["provider"] == "groq"
    monkeypatch.setenv("RINDA_LLM_PROVIDER", "ollama")          # local model: no key needed
    assert roles["supervisor"].get("/api/v1/assistant/status").json()["enabled"] is True


def _walk(x, key=None):
    if isinstance(x, dict):
        for k, v in x.items():
            yield from _walk(v, k)
    elif isinstance(x, list):
        for v in x:
            yield from _walk(v, key)
    else:
        yield key, x


def test_every_tool_reads_real_data_with_internal_links(roles):
    me = roles["supervisor"].get("/api/v1/auth/me").json()
    from security import User
    u = User(me["id"], me["email"], me["name"], me["role"])
    cases = {"situation": {}, "daily_outlook": {"limit": 5}, "site_forecast": {"horizon": 7, "limit": 5}, "location": {"id": "G0442"},
             "satellite_water": {"limit": 5}, "weather": {}, "field_and_operations": {}, "sensors": {}, "search": {"query": "G04"}, "reliability": {}}
    assert set(cases) == set(AS.TOOL_FN)
    for name, args in cases.items():
        out, err = AS._run_tool(name, args, u)
        assert not err, (name, out[:300])
        links = [v for k, v in _walk(json.loads(out)) if k and ("link" in k or k.endswith("_page") or k == "dossier") and v]
        assert all(isinstance(x, str) and x.startswith("#/") for x in links), (name, links[:3])
        if name in ("situation", "daily_outlook", "satellite_water", "reliability"):
            assert links, name
        assert len(out) < 20000, (name, len(out))                  # small enough for free-plan token limits
    assert AS._run_tool("daily_outlook", {"limit": "ten"}, u)[1]   # bad input is reported to the model, not run
    assert AS._run_tool("daily_outlook", {"day_ahead": 30}, u)[1]


def test_streams_answer_after_tool_call(roles, enabled, monkeypatch):
    fake = FakeProvider([{"tools": [("daily_outlook", {"limit": 3})]},
                         {"text": ["There are places to inspect. ", "[Show them](#/map?layers=grid&highlight=G20-1)"]}])
    monkeypatch.setattr(AS, "_chat_stream", fake)
    sup = roles["supervisor"]
    r = sup.post("/api/v1/assistant/ask", json={"question": "Where should we inspect?"}, headers=W)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ev = sse(r.text)
    assert ev[0]["type"] == "start" and ev[-1]["type"] == "done"
    assert any(e["type"] == "status" and "outlook" in e["text"] for e in ev)
    assert "".join(e["text"] for e in ev if e["type"] == "text").endswith("(#/map?layers=grid&highlight=G20-1)")
    second = fake.calls[1]["messages"]
    assert second[0]["role"] == "system" and second[-2]["tool_calls"][0]["function"]["name"] == "daily_outlook"
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "c0" and len(second[-1]["content"]) <= AS.TOOL_CHARS
    assert fake.calls[0]["tools"][0]["type"] == "function"
    # the follow-up resends the earlier turn as plain text only
    cid = ev[-1]["conversation_id"]
    fake2 = FakeProvider([{"text": ["Follow-up answer."]}])
    monkeypatch.setattr(AS, "_chat_stream", fake2)
    ev2 = sse(sup.post("/api/v1/assistant/ask", json={"question": "And inland?", "conversation_id": cid}, headers=W).text)
    assert ev2[0]["conversation_id"] == cid
    roles_sent = [m["role"] for m in fake2.calls[0]["messages"]]
    assert roles_sent == ["system", "user", "assistant", "user"]


def test_provider_errors_are_plain_messages(roles, enabled, monkeypatch):
    def limited(messages, tools, model=None):
        raise AS.ProviderError(429, "rate limited", retry_after=45)
        yield  # pragma: no cover
    monkeypatch.setattr(AS, "_chat_stream", limited)
    ev = sse(roles["supervisor"].post("/api/v1/assistant/ask", json={"question": "hi"}, headers=W).text)
    assert ev[-1]["type"] == "error" and "limit" in ev[-1]["message"]


def test_parses_openai_style_stream(monkeypatch, enabled):
    """The real _chat_stream against a canned server-sent-event body, with a tool call split across chunks."""
    import httpx
    chunks = [{"choices": [{"delta": {"content": "Hel"}}]}, {"choices": [{"delta": {"content": "lo"}}]},
              {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "situ", "arguments": "{\"a\""}}]}}]},
              {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "ation", "arguments": ":1}"}}]}, "finish_reason": "tool_calls"}]}]
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"] = req.headers.get("authorization")
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler)))
    out = list(AS._chat_stream([{"role": "user", "content": "x"}], AS.TOOL_SPECS))
    assert [v for k, v in out if k == "text"] == ["Hel", "lo"]
    final = out[-1][1]
    assert final["tool_calls"] == [{"id": "call_1", "name": "situation", "arguments": "{\"a\":1}"}]
    assert seen["auth"] == "Bearer test-not-a-real-key" and seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["body"]["stream"] is True and seen["body"]["model"] == "openai/gpt-oss-120b"


def test_signed_in_only_and_rate_limited(app, enabled, monkeypatch):
    from conftest import client_as
    assert client_as(None).post("/api/v1/assistant/ask", json={"question": "hi"}, headers=W).status_code in (401, 403)
    monkeypatch.setenv("RINDA_ASSISTANT_HOURLY", "0")
    assert client_as("analyst").post("/api/v1/assistant/ask", json={"question": "hi"}, headers=W).status_code == 429


def test_rate_limited_model_hands_over(roles, enabled, monkeypatch):
    tried = []

    def provider(messages, tools, model=None):
        tried.append(model)
        if model == "openai/gpt-oss-120b":
            raise AS.ProviderError(429, "limit", retry_after=40)
        yield "text", f"answered by {model}"
        yield "final", {"content": "", "tool_calls": [], "finish": "stop"}
    monkeypatch.setattr(AS, "_chat_stream", provider)
    ev = sse(roles["supervisor"].post("/api/v1/assistant/ask", json={"question": "hi"}, headers=W).text)
    assert tried == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    assert "".join(e["text"] for e in ev if e["type"] == "text") == "answered by openai/gpt-oss-20b" and ev[-1]["type"] == "done"
