"""An ordered chain of real LLM providers.

This is not the mock cascade this project removed. The distinction matters and
is worth stating plainly:

  * every member is a **real network provider** that either answers or does not;
  * fallback fires **only** on an explicit `success=False` from the member above;
  * when every member fails this returns `success=False` — it does **not**
    invent a reply. The fan sees an error, because a canned line pretending to
    be the persona is exactly the fabricated-evidence failure this project has
    already had to remove once;
  * which member served the reply, and the full attempt chain, are stamped onto
    the result and persisted, so "who answered" is always answerable.

Rotation is per *conversation*, not per message. Two models alternating inside
one thread would change her voice between consecutive replies, and character
consistency is the thing being sold. A conversation pins a provider when its
first reply is generated and sticks to it; `complete_with(preferred=...)`
expresses that, and the chat route supplies the pin.
"""

from __future__ import annotations

from typing import Sequence

import structlog

from app.providers.base import LLMProvider, ProviderResult

logger = structlog.get_logger()


class FallbackLLMProvider(LLMProvider):
    name = "fallback"

    def __init__(
        self,
        members: Sequence[tuple[str, LLMProvider]],
        enabled: bool = True,
    ):
        if not members:
            raise ValueError("FallbackLLMProvider needs at least one member")
        self._members = list(members)
        self._enabled = enabled

    @property
    def member_names(self) -> list[str]:
        return [name for name, _ in self._members]

    def _order(self, preferred: str | None) -> list[tuple[str, LLMProvider]]:
        """The chain, rotated so `preferred` goes first.

        With fallback disabled only the head is ever tried, which is what a
        single-provider deployment looks like.
        """
        if not self._enabled:
            available = self._members[:1]
        else:
            available = list(self._members)

        if not preferred:
            return available
        index = next(
            (i for i, (name, _) in enumerate(available) if name == preferred), None
        )
        if index is None:
            return available
        return available[index:] + available[:index]

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> ProviderResult:
        return await self.complete_with(
            preferred=None,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def complete_with(
        self,
        *,
        preferred: str | None = None,
        system_prompt: str,
        user_prompt: str,
        schema: dict | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> ProviderResult:
        attempts: list[dict] = []

        for label, provider in self._order(preferred):
            result = await provider.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            attempts.append({
                "provider": label,
                "ok": bool(result.success),
                "error": result.error or "",
                "latency_ms": round(float(result.latency_ms or 0), 1),
            })

            if result.success:
                if len(attempts) > 1:
                    logger.info("llm_fallback_used", served_by=label, attempts=attempts)
                data = dict(result.data or {})
                data["served_by"] = label
                data["provider_chain"] = attempts
                return ProviderResult(
                    success=True,
                    data=data,
                    provider=result.provider or label,
                    latency_ms=result.latency_ms,
                )

            logger.warning("llm_fallback_attempt", provider=label, error=result.error)

        tried = ", ".join(f"{a['provider']}: {a['error']}" for a in attempts)
        return ProviderResult(
            success=False,
            data={"served_by": "", "provider_chain": attempts},
            error=f"all LLM providers failed — {tried}",
            provider=self.name,
        )

    async def health_check(self) -> ProviderResult:
        """Probe every member. One reachable provider means the chain works."""
        detail: dict = {}
        any_ok = False
        for label, provider in self._members:
            probe = await provider.health_check()
            detail[label] = {
                "ok": bool(probe.success),
                "detail": (
                    (probe.data or {}).get("status")
                    or (probe.data or {}).get("model")
                    or probe.error
                ),
            }
            any_ok = any_ok or bool(probe.success)

        return ProviderResult(
            success=any_ok,
            data={"status": "connected" if any_ok else "unreachable", "members": detail},
            error="" if any_ok else "no LLM provider in the chain is reachable",
            provider=self.name,
        )

    def chain_report(self) -> list[dict]:
        """Names only — used by the health endpoint's `chain` array."""
        return [
            {"name": name, "class": type(provider).__name__}
            for name, provider in self._members
        ]
