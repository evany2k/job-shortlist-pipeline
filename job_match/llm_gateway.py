"""Thin wrapper around OpenRouter for schema-constrained chat completions."""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

import httpx
from dotenv import load_dotenv
from openrouter import OpenRouter, errors
from openrouter.errors.openroutererror import OpenRouterError
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)
R = TypeVar("R")

# Network-level failures (dropped connections, read/connect timeouts): retry.
TRANSIENT_ERRORS = (httpx.TransportError, errors.NoResponseError)
# Errors that won't go away by retrying any model: stop the run.
FATAL_ERRORS = (errors.UnauthorizedResponseError, errors.PaymentRequiredResponseError)


class OverallTimeout(Exception):
    pass


class MissingAPIKey(Exception):
    pass


def call_with_deadline(fn: Callable[[], R], timeout_s: float) -> R:
    """Run blocking ``fn()`` in a daemon thread and wait at most ``timeout_s``.

    httpx's timeout is per read, so a slowly trickling response never raises.
    This enforces a total deadline. On timeout the orphaned thread stays
    blocked until its own read timeout fires, but the caller moves on.
    """
    q: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

    def target() -> None:
        try:
            q.put(("ok", fn()))
        except Exception as e:  # noqa: BLE001 - re-raised in the caller
            q.put(("err", e))

    threading.Thread(target=target, daemon=True).start()
    try:
        kind, value = q.get(timeout=timeout_s)
    except queue.Empty:
        raise OverallTimeout(f"no response in {timeout_s}s") from None
    if kind == "err":
        raise value
    return value


class LLMClient:
    def __init__(
        self,
        api_key: str,
        request_timeout_s: float = 45,
        overall_timeout_s: float = 90,
        attempts: int = 2,
        log_path: str | Path | None = None,
    ) -> None:
        self.api_key = api_key
        self.request_timeout_s = request_timeout_s
        self.overall_timeout_s = overall_timeout_s
        self.attempts = attempts
        self.log_path = Path(log_path) if log_path else None

    @classmethod
    def from_env(cls, **kwargs: Any) -> LLMClient:
        load_dotenv()
        api_key = os.getenv("OPENROUTER_KEY")
        if not api_key:
            raise MissingAPIKey("OPENROUTER_KEY is not set. Copy .env.example to .env and add your key.")
        return cls(api_key, **kwargs)

    def _log_response(self, model: str, content: Any) -> None:
        """Append raw model output to a JSONL file for debugging prompt/schema issues."""
        if not self.log_path:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {"timestamp": datetime.now().isoformat(), "model": model, "content": content}
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _send(self, model: str, schema: type[BaseModel], system_prompt: str, user_prompt: str) -> Any:
        with OpenRouter(
            api_key=self.api_key, timeout_ms=int(self.request_timeout_s * 1000), retry_config=0
        ) as client:
            return client.chat.send(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema.__name__,
                        "schema": schema.model_json_schema(),
                        "strict": True,
                    },
                },
                provider={"require_parameters": True},
            )

    def structured_completion(
        self, model: str, schema: type[M], system_prompt: str, user_prompt: str
    ) -> tuple[M, str] | None:
        """Ask ``model`` for output matching ``schema``.

        Returns ``(parsed, model_used)``, or None if this model should be
        skipped (timeout, bad request, provider down, repeated invalid output).
        """
        for attempt in range(1, self.attempts + 1):
            tag = f"{model} (attempt {attempt}/{self.attempts})"
            log.info("Calling %s", tag)
            try:
                response = call_with_deadline(
                    lambda: self._send(model, schema, system_prompt, user_prompt),
                    self.overall_timeout_s,
                )
                raw = response.choices[0].message.content
                self._log_response(response.model, raw)
                parsed = (
                    schema.model_validate_json(raw)
                    if isinstance(raw, (str, bytes))
                    else schema.model_validate(raw)
                )
                return parsed, response.model

            except OverallTimeout as e:
                # A slow model is likely to stay slow: move on instead of retrying.
                log.warning("%s timed out: %s", tag, e)
                return None
            except ValidationError as e:
                log.warning("%s returned output not matching the schema: %s", tag, e)
            except TRANSIENT_ERRORS as e:
                log.warning("%s transport error: %s", tag, e)
            except FATAL_ERRORS:
                raise
            except errors.ResponseValidationError as e:
                if "provider_unavailable" in str(e):
                    log.warning("%s provider unavailable", tag)
                else:
                    log.warning("%s unexpected response: %s", tag, e)
            except errors.BadRequestResponseError as e:
                log.warning("%s rejected the request: %s", tag, getattr(e, "response_data", e))
                return None
            except OpenRouterError as e:
                # Rate limits, overloaded providers, 5xx: try the next model.
                log.warning("%s failed: %s: %s", tag, type(e).__name__, e)
                return None

        log.warning("Giving up on %s after %d attempts", model, self.attempts)
        return None
