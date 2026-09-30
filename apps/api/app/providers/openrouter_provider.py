"""Persona Studio — OpenRouter LLM provider.

A second live LLM, used by the fan chat chain. It is a real network provider,
not a mock: when it fails it returns `success=False` and says why, and nothing
downstream invents a reply to cover for it.

**The `data` contract is deliberately identical to `OllamaLLMProvider`'s** —
`content`, `model`, `tokens_used`, `raw` — so no consumer has to branch on
which provider answered. `content` is the parsed value when a schema was
requested, and `{"raw_response": <text>}` for plain chat, matching Ollama's
behaviour when the reply is not JSON.

Two hazards, both borrowed from the Ollama adapter's own comments:

  * A thinking model emits a reasoning trace before its answer, and the token
    budget covers both. Left alone, a one-line persona reply can be spent
    entirely on reasoning and come back empty. `reasoning.enabled = false` is
    sent by default; if a provider rejects the field we retry without it once
    rather than failing the call.
  * Free-tier `:free` routes are genuinely unreliable (measured 40–67% failure
    across a run). `health_check` therefore issues a real one-token completion,
    because a `/models` listing would report green on a route that never
    answers.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import httpx
import structlog

from app.providers.base import LLMProvider, ProviderResult

logger = structlog.get_logger()

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterLLMProvider(LLMProvider):
    name = "openrouter"

    def __init__(self, api_key: str = "", model: str = "", timeout: float = 45.0):
        self._api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        self._model = model or os.getenv(
            "LLM_OPENROUTER_MODEL", "inclusionai/ling-3.0-flash-sante:free"
        )
        self._base_url = os.getenv("OPENROUTER_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self._timeout = float(
            timeout or os.getenv("LLM_FALLBACK_HOP_TIMEOUT_SECONDS", "45")
        )
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    # OpenRouter attributes traffic with these; neither is
                    # required, and neither carries anything sensitive.
                    "X-Title": "Persona Studio",
                },
            )
        return self._client

    def _payload(
        self, system_prompt: str, full_prompt: str, schema: dict | None,
        temperature: float, max_tokens: int, suppress_reasoning: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": full_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if suppress_reasoning:
            payload["reasoning"] = {"enabled": False}
        if schema:
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> ProviderResult:
        start = time.monotonic()

        if not self._api_key:
            return ProviderResult(
                success=False,
                error="OPENROUTER_API_KEY is not set",
                provider=self.name,
            )

        full_prompt = user_prompt
        if schema:
            full_prompt += (
                f"\n\nRespond with valid JSON matching this schema: {json.dumps(schema)}"
                "\nOutput ONLY the JSON, no other text."
            )

        try:
            client = await self._get_client()

            async def _post(suppress_reasoning: bool):
                return await client.post(
                    "/chat/completions",
                    json=self._payload(
                        system_prompt, full_prompt, schema, temperature,
                        max_tokens, suppress_reasoning,
                    ),
                )

            resp = await _post(suppress_reasoning=True)
            if resp.status_code == 400 and "reasoning" in resp.text.lower():
                # An older or stricter model rejected the field. Retry once
                # without it rather than letting the whole chain fail for a
                # nicety.
                resp = await _post(suppress_reasoning=False)

            if resp.status_code != 200:
                detail = _error_detail(resp)
                return ProviderResult(
                    success=False,
                    error=f"OpenRouter HTTP {resp.status_code}: {detail}",
                    provider=self.name,
                    latency_ms=(time.monotonic() - start) * 1000,
                )

            body = resp.json()
            if body.get("error"):
                return ProviderResult(
                    success=False,
                    error=_error_message(body["error"]),
                    provider=self.name,
                    latency_ms=(time.monotonic() - start) * 1000,
                )

            choices = body.get("choices") or []
            if not choices:
                return ProviderResult(
                    success=False,
                    error="OpenRouter returned no choices",
                    provider=self.name,
                    latency_ms=(time.monotonic() - start) * 1000,
                )

            message = choices[0].get("message") or {}
            text = (message.get("content") or "").strip()
            reasoning = message.get("reasoning") or ""

            if not text:
                # A route that spent its whole budget on reasoning returns an
                # empty answer. That is a failure here, not an empty reply —
                # an empty chat bubble would look like the persona went silent.
                return ProviderResult(
                    success=False,
                    error=(
                        "OpenRouter returned empty content"
                        f" (reasoning_chars={len(reasoning)})"
                    ),
                    provider=self.name,
                    latency_ms=(time.monotonic() - start) * 1000,
                )

            usage = body.get("usage") or {}
            tokens = int(usage.get("total_tokens") or 0)

            return ProviderResult(
                success=True,
                data={
                    "content": _parse_content(text, schema),
                    "tokens_used": tokens,
                    "model": body.get("model") or self._model,
                    "raw": text,
                },
                provider=self.name,
                latency_ms=(time.monotonic() - start) * 1000,
            )

        except httpx.TimeoutException:
            return ProviderResult(
                success=False,
                error=f"OpenRouter timed out after {self._timeout:g}s",
                provider=self.name,
                latency_ms=(time.monotonic() - start) * 1000,
            )
        except httpx.ConnectError:
            return ProviderResult(
                success=False,
                error=f"Cannot reach OpenRouter at {self._base_url}",
                provider=self.name,
            )
        except Exception as exc:
            logger.error("openrouter_complete_failed", error=str(exc))
            return ProviderResult(
                success=False,
                error=str(exc),
                provider=self.name,
                latency_ms=(time.monotonic() - start) * 1000,
            )

    async def health_check(self) -> ProviderResult:
        """A real one-token completion.

        Not a `/models` listing: free routes fail often enough that a listing
        check would report green on a route that never answers. This is the
        only check that can tell the difference.
        """
        if not self._api_key:
            return ProviderResult(
                success=False, error="OPENROUTER_API_KEY is not set", provider=self.name
            )
        probe = await self.complete(
            "You are a health check.", "Reply with the single word OK.",
            schema=None, temperature=0.0, max_tokens=8,
        )
        if not probe.success:
            return probe
        text = _text_of(probe.data)
        return ProviderResult(
            success=True,
            data={"status": "connected", "model": self._model,
                  "reply": text[:32], "server": self._base_url},
            provider=self.name,
            latency_ms=probe.latency_ms,
        )


def _parse_content(text: str, schema: dict | None):
    """Match OllamaLLMProvider's content shape exactly."""
    if not schema:
        return {"raw_response": text}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return {"raw_response": text}


def _text_of(data: dict) -> str:
    content = (data or {}).get("content")
    if isinstance(content, dict):
        return str(content.get("raw_response", ""))
    return str(content or "")


def _error_message(error) -> str:
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error)


def _error_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except Exception:
        return resp.text[:200]
    error = body.get("error")
    if isinstance(error, dict):
        reasons = (error.get("metadata") or {}).get("ineligibility_reasons")
        if reasons:
            return str(reasons[0].get("reason") or reasons[0])[:200]
        return str(error.get("message") or error)[:200]
    return str(error or body)[:200]
