"""Persona Studio — what the Ollama provider actually puts on the wire.

Two things here were only discoverable by looking at a real build. The
configured model is `qwen3:8b`, a *thinking* model, and it took ~110 s to answer
the candidate prompt against a 120 s read timeout: one longer prompt from a
failure, and a failed call is answered by `generate_candidates` with invented
candidates carrying random consistency scores. Both the reasoning trace and the
timeout are pinned here so neither comes back quietly.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.providers.ollama_provider import OllamaLLMProvider

SCHEMA = {"candidates": [{"name": "string"}]}


def _provider_returning(content: str, captured: list[httpx.Request]) -> OllamaLLMProvider:
    """A provider whose client answers locally and records what it was sent."""

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "message": {"content": content},
                "eval_count": 7,
                "prompt_eval_count": 3,
            },
        )

    provider = OllamaLLMProvider(base_url="http://ollama.test", model="qwen3:8b")
    provider._client = httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handler)
    )
    return provider


async def test_a_schema_call_turns_thinking_off():
    """`num_predict` caps the reasoning trace and the answer together, so on a
    structured extraction the trace can eat the whole budget and the JSON is
    reached late or not at all. The trace is not the answer and nothing reads
    it, so asking for it is pure latency."""
    sent: list[httpx.Request] = []
    provider = _provider_returning(json.dumps({"candidates": []}), sent)

    result = await provider.complete("sys", "user", schema=SCHEMA)

    assert result.success
    payload = json.loads(sent[0].content)
    assert payload["think"] is False
    assert payload["format"] == "json"


async def test_a_call_without_a_schema_is_left_alone():
    """Only the structured path changes: a free-text completion has no schema to
    reach, so there is nothing for a trace to delay the arrival of."""
    sent: list[httpx.Request] = []
    provider = _provider_returning(json.dumps({"caption": "hi"}), sent)

    result = await provider.complete("sys", "user")

    assert result.success
    payload = json.loads(sent[0].content)
    assert "think" not in payload
    assert payload["format"] == ""


async def test_the_answer_is_parsed_out_of_its_wrapping():
    """A thinking model can still wrap its JSON in prose even with `format=json`
    set, and a wrapper that fails to parse reads downstream as "no candidates"
    — which is the path that invents them."""
    sent: list[httpx.Request] = []
    provider = _provider_returning(
        'Here you go:\n{"candidates": [{"name": "Naomi"}]}\nHope that helps!', sent
    )

    result = await provider.complete("sys", "user", schema=SCHEMA)

    assert result.success
    assert result.data["content"]["candidates"] == [{"name": "Naomi"}]


async def test_the_read_timeout_is_configurable_and_not_a_tight_constant(monkeypatch):
    """A local 8B thinking model is not a remote API and does not answer in
    remote-API time. The default has to clear the observed ~110 s with room,
    and an operator on a slower machine has to be able to raise it."""
    monkeypatch.delenv("OLLAMA_TIMEOUT_SECONDS", raising=False)
    default_provider = OllamaLLMProvider(base_url="http://ollama.test", model="qwen3:8b")
    default_timeout = (await default_provider._get_client()).timeout.read
    assert default_timeout > 110.0, (
        "the default must clear the measured ~110 s answer time, or a normal "
        "call times out and its step silently invents its results"
    )

    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS", "1800")
    raised_provider = OllamaLLMProvider(base_url="http://ollama.test", model="qwen3:8b")
    assert (await raised_provider._get_client()).timeout.read == 1800.0


async def test_a_non_json_answer_is_reported_rather_than_raised():
    """`raw_response` is how a caller can tell "the model did not return JSON"
    from "the model returned no candidates" — they take different fixes."""
    sent: list[httpx.Request] = []
    provider = _provider_returning("I'm sorry, I can't help with that.", sent)

    result = await provider.complete("sys", "user", schema=SCHEMA)

    assert result.success
    assert result.data["content"]["raw_response"] == "I'm sorry, I can't help with that."


async def test_a_connection_failure_names_ollama_rather_than_leaking_httpx():
    """This is the message an operator sees when the daemon is down, and
    `ConnectError(...)` alone does not say what to start."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = OllamaLLMProvider(base_url="http://ollama.test", model="qwen3:8b")
    provider._client = httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handler)
    )

    result = await provider.complete("sys", "user", schema=SCHEMA)

    assert result.success is False
    assert "Cannot connect to Ollama" in result.error
