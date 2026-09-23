"""Server-only configuration; OpenAI defaults apply only when explicitly selected."""
from dataclasses import dataclass, field
import math
import os
from urllib.parse import urlsplit

from .errors import AIUnavailable


@dataclass(frozen=True)
class AIConfig:
    enabled: bool = False
    provider: str = ""
    model: str = ""
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    allow_external: bool = False
    timeout_seconds: float = 30.0
    max_output_tokens: int = 4096
    review_max_tokens: int = 600
    reasoning_effort: str | None = None
    max_response_bytes: int = 65536
    cache_entries: int = 128
    cache_ttl_seconds: float = 900.0
    max_concurrent: int = 2

    @classmethod
    def from_env(cls) -> "AIConfig":
        def flag(name):
            value = os.environ.get(name, "false").lower()
            if value not in ("true", "false"):
                raise AIUnavailable("configuration")
            return value == "true"
        try:
            provider = os.environ.get("MONEYGRAPH_AI_PROVIDER", "").strip().lower()
            is_openai = provider == "openai"
            model = os.environ.get("MONEYGRAPH_AI_MODEL", "gpt-6-luna" if is_openai else "")
            base_url = os.environ.get("MONEYGRAPH_AI_BASE_URL", "https://api.openai.com/v1" if is_openai else "")
            api_key = os.environ.get("MONEYGRAPH_AI_API_KEY", "")
            if is_openai and not api_key:
                api_key = os.environ.get("OPENAI_API_KEY", "")
            return cls(enabled=flag("MONEYGRAPH_AI_ENABLED"),
                       provider=provider, model=model, base_url=base_url, api_key=api_key,
                       allow_external=flag("MONEYGRAPH_AI_ALLOW_EXTERNAL"),
                       review_max_tokens=2048 if is_openai else 600,
                       reasoning_effort=os.environ.get("MONEYGRAPH_AI_REASONING_EFFORT",
                           "low" if is_openai and model.startswith("gpt-6-") else None),
                       timeout_seconds=float(os.environ.get("MONEYGRAPH_AI_TIMEOUT_SECONDS", "30")))
        except ValueError:
            raise AIUnavailable("configuration") from None

    def endpoint(self) -> str:
        if not self.enabled:
            raise AIUnavailable("disabled")
        if not self.provider.strip() or not self.model.strip() or len(self.model) > 200:
            raise AIUnavailable("configuration")
        try:
            url = urlsplit(self.base_url)
            port = url.port
        except ValueError:
            raise AIUnavailable("configuration") from None
        if (url.username or url.password or url.query or url.fragment
                or url.path.rstrip("/") != "/v1" or not url.hostname
                or port == 0 or any(c.isspace() for c in self.base_url)):
            raise AIUnavailable("configuration")
        # Never send an automatically loaded OpenAI key to another host.
        if self.provider == "openai" and (url.scheme != "https" or url.hostname != "api.openai.com"
                                         or port not in (None, 443)):
            raise AIUnavailable("configuration")
        local = url.hostname in ("127.0.0.1", "::1")
        if url.scheme not in ("https", "http") or (not local and url.scheme != "https"):
            raise AIUnavailable("configuration")
        if not local and not self.allow_external:
            raise AIUnavailable("external_transfer_disabled")
        if not local and not self.api_key:
            raise AIUnavailable("missing_api_key")
        if any(c in self.api_key for c in "\r\n"):
            raise AIUnavailable("configuration")
        for value, low, high in ((self.timeout_seconds, 1, 120),
                                 (self.cache_ttl_seconds, 1, 3600)):
            if isinstance(value, bool) or not math.isfinite(value) or not low <= value <= high:
                raise AIUnavailable("configuration")
        for value, low, high in ((self.max_output_tokens, 256, 8192),
                                 (self.review_max_tokens, 256, 8192),
                                 (self.max_response_bytes, 4096, 262144),
                                 (self.cache_entries, 1, 512), (self.max_concurrent, 1, 8)):
            if type(value) is not int or not low <= value <= high:
                raise AIUnavailable("configuration")
        if self.reasoning_effort not in (None, "none", "low", "medium", "high", "xhigh", "max"):
            raise AIUnavailable("configuration")
        if self.reasoning_effort is not None and self.provider != "openai":
            raise AIUnavailable("configuration")
        return self.base_url.rstrip("/") + "/chat/completions"
