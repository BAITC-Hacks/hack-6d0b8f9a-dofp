"""Bounded generation + factual review; no fallback that impersonates a model."""
from collections import OrderedDict
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from threading import BoundedSemaphore, RLock
import time

from pydantic import ValidationError

from .config import AIConfig
from .context import ClientContext, MAX_CONTEXT_BYTES, json_bytes
from .errors import AIUnavailable, InvalidExplanation
from .models import ModelExplanation, Review
from .prompt import PROMPT_VERSION, REVIEW_VERSION, REVIEW_PROMPT, SYSTEM_PROMPT
from .provider import ChatCompletionsTransport, Transport, strict_json
from .validation import validate_explanation


class ExplanationService:
    def __init__(self, config: AIConfig, *, transport: Transport | None = None):
        self._config = config
        self._transport = transport if transport is not None else ChatCompletionsTransport(config)
        self._cache = OrderedDict()
        self._lock = RLock()
        self._inflight = set()
        self._slots = BoundedSemaphore(config.max_concurrent)

    @property
    def config(self) -> AIConfig:
        return self._config

    def explain_client(self, context: ClientContext) -> dict:
        endpoint = self.config.endpoint()  # Recheck gate even on a cache hit.
        if not isinstance(context, ClientContext):
            raise AIUnavailable("invalid_context")
        packet = context.packet()
        if len(json_bytes(packet)) > MAX_CONTEXT_BYTES:
            raise AIUnavailable("request_too_large")
        key = (context.snapshot_id, context.client_id, context.digest,
               self.config.provider, endpoint, self.config.model, PROMPT_VERSION, REVIEW_VERSION,
               self.config.reasoning_effort, self.config.max_output_tokens, self.config.review_max_tokens,
               sha256(SYSTEM_PROMPT.encode()).hexdigest(), sha256(REVIEW_PROMPT.encode()).hexdigest())
        now = time.monotonic()
        with self._lock:
            for expired in [k for k, (deadline, _) in self._cache.items() if deadline <= now]:
                del self._cache[expired]
            if key in self._cache:
                self._cache.move_to_end(key)
                result = deepcopy(self._cache[key][1])
                result["metadata"]["cached"] = True
                return result
            if key in self._inflight or not self._slots.acquire(blocking=False):
                raise AIUnavailable("busy")
            self._inflight.add(key)
        try:
            result = self._generate(context, packet, deadline=now + self.config.timeout_seconds)
            with self._lock:
                self._cache[key] = (time.monotonic() + self.config.cache_ttl_seconds, deepcopy(result))
                self._cache.move_to_end(key)
                while len(self._cache) > self.config.cache_entries:
                    self._cache.popitem(last=False)
            return result
        finally:
            with self._lock:
                self._inflight.discard(key)
            self._slots.release()

    def _complete(self, *, deadline, **kwargs):
        if time.monotonic() >= deadline:
            raise AIUnavailable("timeout")
        response = self._transport.complete(deadline=deadline, **kwargs)
        if time.monotonic() >= deadline:
            raise AIUnavailable("timeout")
        if not isinstance(response, str) or len(response.encode("utf-8")) > self.config.max_response_bytes:
            raise AIUnavailable("response_too_large")
        return response

    def _generate(self, context, packet, *, deadline):
        try:
            raw = self._complete(system=SYSTEM_PROMPT, payload=packet,
                                 schema=ModelExplanation.model_json_schema(), deadline=deadline,
                                 max_tokens=self.config.max_output_tokens)
            explanation = validate_explanation(raw, context)
            review_raw = self._complete(system=REVIEW_PROMPT,
                                        payload={"facts": packet, "proposed_explanation": explanation.model_dump()},
                                        schema=Review.model_json_schema(), deadline=deadline,
                                        max_tokens=self.config.review_max_tokens)
            review = Review.model_validate(strict_json(review_raw))
            if not review.supported or review.issues:
                raise AIUnavailable("unsupported_claim")
        except AIUnavailable:
            raise
        except (InvalidExplanation, ValidationError, ValueError, TypeError, RecursionError):
            raise AIUnavailable("invalid_response") from None
        return {**explanation.model_dump(), "facts": packet["facts"],
                "metadata": {"snapshot_id": context.snapshot_id,
                             "context_hash": context.digest, "provider": self.config.provider,
                             "model": self.config.model, "prompt_version": PROMPT_VERSION,
                             "review_version": REVIEW_VERSION, "cached": False,
                             "human_review_required": True}}


@lru_cache(maxsize=4)
def _service(config: AIConfig) -> ExplanationService:
    return ExplanationService(config)


def explain_client(context: ClientContext) -> dict:
    """Server-side public function; raises AIUnavailable with a safe error code.

    Environment configuration is reread so disabling external transfer immediately
    takes effect. Call from a synchronous endpoint or run_in_threadpool.
    """
    return _service(AIConfig.from_env()).explain_client(context)
