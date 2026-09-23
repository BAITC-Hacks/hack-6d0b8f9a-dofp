from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import time

import pytest

from moneygraph.ai import AIConfig, AIUnavailable
from moneygraph.ai.provider import ChatCompletionsTransport


@pytest.fixture
def server():
    state = {"status": 200, "headers": {}, "delay": 0, "calls": [],
             "body": json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]}).encode()}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            state["calls"].append({"path": self.path, "body": json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                                   "authorization": self.headers.get("Authorization")})
            time.sleep(state["delay"])
            try:
                if state["headers"].get("Transfer-Encoding") == "chunked":
                    self.protocol_version = "HTTP/1.1"
                self.send_response(state["status"])
                self.send_header("Connection", "close")
                self.close_connection = True
                for key, value in state["headers"].items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(state["body"])
            except (BrokenPipeError, ConnectionResetError):
                pass

    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield state, f"http://127.0.0.1:{instance.server_port}/v1"
    instance.shutdown()
    instance.server_close()
    thread.join(timeout=2)


def call(config):
    return ChatCompletionsTransport(config).complete(system="Return JSON", payload={"facts": []},
        schema={"type": "object"}, deadline=time.monotonic()+config.timeout_seconds, max_tokens=500)


def test_local_transport_and_request_contract(config, server, monkeypatch, caplog):
    state, url = server
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    config = replace(config, base_url=url, api_key="test-secret")
    assert call(config) == '{"ok":true}'
    request = state["calls"][0]
    assert request["path"] == "/v1/chat/completions"
    assert request["body"]["model"] == config.model
    assert request["body"]["stream"] is False
    assert request["authorization"] == "Bearer test-secret"
    assert "tools" not in request["body"]
    assert "test-secret" not in caplog.text


@pytest.mark.parametrize("padding", [0, 20000])
@pytest.mark.parametrize("framing", ["length", "chunked"])
def test_complete_response_framing(config, server, padding, framing):
    state, url = server
    state["body"] += b" " * padding
    if framing == "length":
        state["headers"]["Content-Length"] = str(len(state["body"]))
    else:
        state["headers"]["Transfer-Encoding"] = "chunked"
        state["body"] = chunk_frame(state["body"]) + b"0\r\n\r\n"
    assert call(replace(config, base_url=url)) == '{"ok":true}'
    assert len(state["calls"]) == 1


def chunk_frame(body):
    return f"{len(body):x}\r\n".encode() + body + b"\r\n"


@pytest.mark.parametrize("framing", ["length", "chunked"])
def test_incomplete_http_body_is_rejected_even_with_valid_json(config, server, framing):
    state, url = server
    if framing == "length":
        state["headers"]["Content-Length"] = str(len(state["body"]) + 1)
    else:
        state["headers"]["Transfer-Encoding"] = "chunked"
        state["body"] = chunk_frame(state["body"])  # Missing terminating chunk.
    with pytest.raises(AIUnavailable) as error:
        call(replace(config, base_url=url))
    assert error.value.code == "incomplete_response"


@pytest.mark.parametrize("kind", ["redirect", "rate", "error", "oversize", "invalid", "truncated", "refusal"])
def test_bad_http_responses_do_not_leak_body(config, server, kind):
    state, url = server
    config = replace(config, base_url=url, api_key="test-secret", max_response_bytes=4096)
    expected = "invalid_response"
    if kind == "redirect":
        state["status"] = 302
        state["headers"]["Location"] = url + "/leak"
        expected = "provider_error"
    elif kind == "rate":
        state["status"] = 429
        expected = "rate_limited"
    elif kind == "error":
        state["status"] = 500
        expected = "provider_error"
    elif kind == "oversize":
        state["body"] = b"x" * 4097
        expected = "response_too_large"
    elif kind == "truncated":
        state["body"] = json.dumps({"choices": [{"finish_reason": "length"}]}).encode()
        expected = "incomplete_response"
    elif kind == "refusal":
        state["body"] = json.dumps({"choices": [{"finish_reason": "stop", "message": {"refusal": "no"}}]}).encode()
    else:
        state["body"] = b"test-secret financial-payload"
    with pytest.raises(AIUnavailable) as error:
        call(config)
    assert error.value.code == expected
    assert str(error.value) == "AI-объяснение недоступно"
    assert len(state["calls"]) == 1


def test_provider_timeout_is_bounded(config, server):
    state, url = server
    state["delay"] = 1.5
    started = time.monotonic()
    with pytest.raises(AIUnavailable) as error:
        call(replace(config, base_url=url, timeout_seconds=1))
    assert error.value.code == "timeout"
    assert time.monotonic() - started < 1.4


@pytest.mark.parametrize("url", ["http://example.invalid/v1", "https://user:password@example.invalid/v1",
                                "https://example.invalid/v1?token=x", "https://example.invalid/v1#x",
                                "file:///v1", "http://127.0.0.1:0/v1", "http://127.0.0.1/v1/other"])
def test_unsafe_endpoints_rejected(config, url):
    with pytest.raises(AIUnavailable):
        replace(config, base_url=url, allow_external=True, api_key="fake").endpoint()


def test_external_requires_explicit_gate_and_key(config):
    external = replace(config, base_url="https://example.invalid/v1", api_key="fake")
    with pytest.raises(AIUnavailable):
        external.endpoint()
    assert replace(external, allow_external=True).endpoint() == "https://example.invalid/v1/chat/completions"
    assert "fake" not in repr(external)


def test_environment_disabled_by_default_and_strict_flags(monkeypatch):
    monkeypatch.delenv("MONEYGRAPH_AI_ENABLED", raising=False)
    monkeypatch.delenv("MONEYGRAPH_AI_ALLOW_EXTERNAL", raising=False)
    assert AIConfig.from_env().enabled is False
    monkeypatch.setenv("MONEYGRAPH_AI_ALLOW_EXTERNAL", "maybe")
    with pytest.raises(AIUnavailable):
        AIConfig.from_env()
