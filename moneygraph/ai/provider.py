"""Small synchronous Chat Completions transport using the standard library.

No SDK, network retries, redirects, proxy inheritance, tools, or prompt logging.
The owner must run it in a worker thread, not on FastAPI's event loop.
"""
import json
import socket
import time
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .config import AIConfig
from .context import json_bytes
from .errors import AIUnavailable


def strict_json(text: str | bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON property")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("Nonfinite JSON constant")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


class Transport(Protocol):
    def complete(self, *, system: str, payload: dict, schema: dict,
                 deadline: float, max_tokens: int) -> str: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ChatCompletionsTransport:
    def __init__(self, config: AIConfig):
        self.config = config

    def complete(self, *, system, payload, schema, deadline, max_tokens):
        endpoint = self.config.endpoint()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AIUnavailable("timeout")
        # JSON mode has broad compatibility; backend validation remains mandatory.
        body = json_bytes({"model": self.config.model, "stream": False,
                           "max_tokens": max_tokens, "response_format": {"type": "json_object"},
                           "messages": [{"role": "system", "content": system},
                                        {"role": "user", "content": json_bytes(
                                            {"data": payload, "response_schema": schema}).decode("utf-8")} ]})
        if len(body) > 120000:
            raise AIUnavailable("request_too_large")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = "Bearer " + self.config.api_key
        request = Request(endpoint, data=body, headers=headers, method="POST")
        # The URL is an operator setting, never a parameter supplied by a browser.
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=remaining) as response:
                if response.status != 200:
                    raise AIUnavailable("provider_error")
                limit = self.config.max_response_bytes
                size = response.headers.get("Content-Length")
                if size is not None and int(size) > limit:
                    raise AIUnavailable("response_too_large")
                chunks, count = [], 0
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise AIUnavailable("timeout")
                    # CPython HTTPResponse socket: update per read so slow streams
                    # cannot restart a full timeout for every chunk.
                    response.fp.raw._sock.settimeout(remaining)
                    chunk = response.read1(min(8192, limit - count + 1))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    count += len(chunk)
                    if count > limit:
                        raise AIUnavailable("response_too_large")
                result = strict_json(b"".join(chunks))
            choices = result["choices"]
            if len(choices) != 1 or choices[0]["finish_reason"] != "stop":
                raise AIUnavailable("incomplete_response")
            message = choices[0]["message"]
            if message.get("refusal") or message.get("tool_calls"):
                raise AIUnavailable("invalid_response")
            content = message["content"]
            if not isinstance(content, str) or not content.strip():
                raise AIUnavailable("invalid_response")
            return content
        except AIUnavailable:
            raise
        except (TimeoutError, socket.timeout):
            raise AIUnavailable("timeout") from None
        except HTTPError as error:
            error.close()
            raise AIUnavailable("rate_limited" if error.code == 429 else "provider_error") from None
        except URLError as error:
            raise AIUnavailable("timeout" if isinstance(error.reason, TimeoutError) else "provider_unavailable") from None
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
            raise AIUnavailable("invalid_response") from None
