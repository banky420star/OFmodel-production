"""The LLM fallback chain.

The property under test is not "fallback works" — it is **"fallback never
fabricates"**. This project already had to remove canned replies from the fan
chat once, and a chain that returns a cheerful placeholder when every provider
is down would put that failure straight back, now behind a reassuring
`served_by` field.

So the load-bearing test here is `test_all_members_failing_never_returns_content`.
"""

from __future__ import annotations

import pytest

from app.providers.base import LLMProvider, ProviderResult
from app.providers.fallback_llm import FallbackLLMProvider


class StubProvider(LLMProvider):
    """A real-shaped provider that returns whatever it was told to."""

    def __init__(self, name: str, *, ok: bool = True, text: str = "hi"):
        self.name = name
        self._ok = ok
        self._text = text
        self.calls = 0

    async def complete(self, system_prompt, user_prompt, schema=None,
                       temperature=0.7, max_tokens=2048) -> ProviderResult:
        self.calls += 1
        if not self._ok:
            return ProviderResult(False, None, error=f"{self.name} is down",
                                  provider=self.name, latency_ms=1)
        return ProviderResult(True, {"content": self._text, "model": self.name},
                              provider=self.name, latency_ms=1)

    async def health_check(self) -> ProviderResult:
        return ProviderResult(self._ok, {"ok": self._ok}, provider=self.name)


def _chain(first: StubProvider, second: StubProvider, enabled=True):
    return FallbackLLMProvider([(first.name, first), (second.name, second)], enabled=enabled)


@pytest.mark.asyncio
async def test_the_head_answers_and_the_tail_is_not_called():
    a, b = StubProvider("a", text="from a"), StubProvider("b", text="from b")
    result = await _chain(a, b).complete_with(system_prompt="s", user_prompt="u")

    assert result.success is True
    assert result.data["served_by"] == "a"
    assert b.calls == 0, "a healthy head must not trigger a fallback"
    assert [h["provider"] for h in result.data["provider_chain"]] == ["a"]


@pytest.mark.asyncio
async def test_a_failing_head_falls_through_and_says_so():
    a, b = StubProvider("a", ok=False), StubProvider("b", text="from b")
    result = await _chain(a, b).complete_with(system_prompt="s", user_prompt="u")

    assert result.success is True
    assert result.data["content"] == "from b"
    assert result.data["served_by"] == "b"

    chain = result.data["provider_chain"]
    assert [h["provider"] for h in chain] == ["a", "b"]
    assert chain[0]["ok"] is False and chain[1]["ok"] is True, (
        "the chain must record the failure, not just the winner"
    )


@pytest.mark.asyncio
async def test_preferred_provider_starts_the_chain():
    """Conversation stickiness: the pinned provider is tried first."""
    a, b = StubProvider("a", text="from a"), StubProvider("b", text="from b")
    result = await _chain(a, b).complete_with(
        preferred="b", system_prompt="s", user_prompt="u"
    )

    assert result.data["served_by"] == "b"
    assert result.data["provider_chain"][0]["provider"] == "b"
    assert a.calls == 0


@pytest.mark.asyncio
async def test_preferred_provider_is_still_a_preference_not_a_promise():
    """A pinned provider that is down must not be a dead end."""
    a, b = StubProvider("a", text="from a"), StubProvider("b", ok=False)
    result = await _chain(a, b).complete_with(
        preferred="b", system_prompt="s", user_prompt="u"
    )
    assert result.success is True
    assert result.data["served_by"] == "a"


@pytest.mark.asyncio
async def test_all_members_failing_never_returns_content():
    """The one that matters. No text, no invention, an error naming both."""
    a, b = StubProvider("a", ok=False), StubProvider("b", ok=False)
    result = await _chain(a, b).complete_with(system_prompt="s", user_prompt="u")

    assert result.success is False
    # The chain still reports who it tried and how each failed — but there is no
    # reply text anywhere in it. `content` absent is the point of this test.
    assert "content" not in (result.data or {})
    assert (result.data or {})["served_by"] == ""

    error = result.error or ""
    assert "a is down" in error and "b is down" in error


@pytest.mark.asyncio
async def test_disabled_fallback_uses_only_the_head():
    a, b = StubProvider("a", ok=False), StubProvider("b", text="from b")
    result = await _chain(a, b, enabled=False).complete_with(system_prompt="s", user_prompt="u")

    assert result.success is False
    assert b.calls == 0, "with fallback off, a single-provider deployment means one attempt"
    assert "b" not in (result.error or "")


@pytest.mark.asyncio
async def test_health_check_probes_every_member_and_reports_each():
    """One reachable member means the chain works — but both are still reported.

    A green chain status with a red member underneath is the honest reading:
    replies are being served, and one provider is down. Collapsing that to a
    single bit is what makes an outage invisible until the second one hits.
    """
    a, b = StubProvider("a", ok=True), StubProvider("b", ok=False)
    report = await _chain(a, b).health_check()

    assert report.success is True, "a reachable member means the chain can answer"
    members = report.data["members"]
    assert members["a"]["ok"] is True
    assert members["b"]["ok"] is False


@pytest.mark.asyncio
async def test_health_check_is_red_only_when_every_member_is_down():
    a, b = StubProvider("a", ok=False), StubProvider("b", ok=False)
    report = await _chain(a, b).health_check()

    assert report.success is False
    assert report.data["status"] == "unreachable"
    assert set(report.data["members"]) == {"a", "b"}


@pytest.mark.asyncio
async def test_a_chain_needs_at_least_one_member():
    with pytest.raises(ValueError):
        FallbackLLMProvider([])


@pytest.mark.asyncio
async def test_member_names_are_reported_in_order():
    a, b = StubProvider("ollama"), StubProvider("openrouter")
    assert _chain(a, b).member_names == ["ollama", "openrouter"]
