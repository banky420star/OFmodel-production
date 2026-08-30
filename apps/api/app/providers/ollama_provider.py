"""Persona Studio — Ollama LLM Provider.

Connects to a local Ollama instance for LLM-based decisions.
Ollama runs open-source models locally (Llama, Gemma, Mistral, etc.)

Requires:
  - Ollama running at OLLAMA_URL (default: http://localhost:11434)
  - A model pulled: ollama pull llama3.1 or ollama pull gemma2
"""

from __future__ import annotations
import json
import os
import time
from typing import Any

import httpx
import structlog

from app.providers.base import LLMProvider, ProviderResult

logger = structlog.get_logger()


class OllamaLLMProvider(LLMProvider):
    """Local LLM via Ollama for structured decision-making.

    Supports:
    - Structured JSON output
    - Persona creation decisions
    - Shoot planning
    - Quality assessment
    - Caption generation
    """

    def __init__(self, base_url: str = "", model: str = ""):
        self._base_url = (base_url or os.getenv("OLLAMA_URL", "http://localhost:11434")).rstrip("/")
        self._model = model or os.getenv("OLLAMA_MODEL", "llama3.1")
        self._provider = "ollama"
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(base_url=self._base_url, timeout=120.0)
        return self._client

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> ProviderResult:
        """Complete with structured JSON output via Ollama."""
        start = time.monotonic()

        try:
            client = await self._get_client()

            # Build prompt with JSON instruction
            full_prompt = user_prompt
            if schema:
                full_prompt += f"\n\nRespond with valid JSON matching this schema: {json.dumps(schema)}"
                full_prompt += "\nOutput ONLY the JSON, no other text."

            payload = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": full_prompt},
                ],
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
                "format": "json" if schema else "",
            }

            resp = await client.post("/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()

            content = data.get("message", {}).get("content", "")
            elapsed_ms = (time.monotonic() - start) * 1000

            # Parse JSON response
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                # Try to extract JSON from the response
                import re
                json_match = re.search(r'\{.*\}', content, re.DOTALL)
                if json_match:
                    parsed = json.loads(json_match.group())
                else:
                    parsed = {"raw_response": content}

            tokens = data.get("eval_count", 0) + data.get("prompt_eval_count", 0)

            return ProviderResult(
                success=True,
                data={
                    "content": parsed,
                    "tokens_used": tokens,
                    "model": self._model,
                    "raw": content,
                },
                provider=self._provider,
                latency_ms=elapsed_ms,
            )

        except httpx.ConnectError:
            return ProviderResult(
                success=False,
                error=f"Cannot connect to Ollama at {self._base_url}. Is Ollama running?",
                provider=self._provider,
            )
        except Exception as e:
            logger.error("ollama_complete_failed", error=str(e))
            return ProviderResult(
                success=False,
                error=str(e),
                provider=self._provider,
                latency_ms=(time.monotonic() - start) * 1000,
            )

    async def health_check(self) -> ProviderResult:
        try:
            client = await self._get_client()
            resp = await client.get("/api/tags")
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                return ProviderResult(
                    success=True,
                    data={
                        "status": "connected",
                        "models": [m.get("name", "") for m in models],
                        "active_model": self._model,
                        "server": self._base_url,
                    },
                    provider=self._provider,
                )
            return ProviderResult(
                success=False,
                error=f"Ollama returned {resp.status_code}",
                provider=self._provider,
            )
        except httpx.ConnectError:
            return ProviderResult(
                success=False,
                error=f"Ollama not reachable at {self._base_url}",
                provider=self._provider,
            )
        except Exception as e:
            return ProviderResult(success=False, error=str(e), provider=self._provider)
