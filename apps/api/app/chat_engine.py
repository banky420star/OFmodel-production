"""Persona Studio — Fan Chat Engine

Generates a persona's replies to a fan, through the provider registry.

This module used to talk to Ollama directly with a module-level URL, bypassing
the registry entirely, and — when the call failed — return a canned line like
`"hey babe! 💕"`. Nothing downstream could tell that reply apart from a real
one: same type, same shape, same confidence field. That is the same
fabricated-evidence pattern `delivery.py` was written to remove, and it is the
worst possible failure for this product, because a fan paying for attention
would be handed a placeholder and told it was her.

So: replies come from the registry, and when no provider can answer this raises
`LLMUnavailable`. The route turns that into a 503. A fan seeing "I can't reply
right now" is a bad moment; a fan being lied to is a broken product.

**Disclosure.** The system prompt explicitly tells the character never to claim
to be human and never to deny being an AI if asked directly. She stays in
character, and the app labels her as AI on every surface. See
FAN_SLICE_TODO.md and the plan's disclosure decision.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.providers.registry import ProviderNotConfigured, get_registry


class LLMUnavailable(Exception):
    """No provider in the chain could answer. Never softened into a canned reply."""


@dataclass
class ChatResponse:
    text: str
    intent: str = "conversation"
    sentiment: float = 0.0
    suggests_ppv: bool = False
    ppv_prompt: str = ""
    confidence: float = 0.8
    # Which provider actually answered, and the full attempt chain. Persisted so
    # "who said this" is answerable later.
    served_by: str = ""
    provider_chain: list = field(default_factory=list)


def _get_persona_prompt(
    persona_name: str,
    brand: str,
    personality: str,
    voice_style: str,
    character: dict | None = None,
) -> str:
    """The system prompt. Built from the character sheet when there is one."""
    lines = [f"You are {persona_name}, a {brand} content creator."]

    if character:
        if character.get("backstory"):
            lines.append(f"\nWho you are:\n{character['backstory']}")
        if character.get("speech_style"):
            lines.append(f"\nHow you talk:\n{character['speech_style']}")
        catchphrases = character.get("catchphrases") or []
        if catchphrases:
            lines.append("\nThings you say: " + "; ".join(str(c) for c in catchphrases))
        likes = character.get("likes") or []
        dislikes = character.get("dislikes") or []
        if likes:
            lines.append("You like: " + ", ".join(str(x) for x in likes))
        if dislikes:
            lines.append("You dislike: " + ", ".join(str(x) for x in dislikes))
        examples = character.get("example_dialogue") or []
        if examples:
            lines.append("\nHow you have replied before:")
            for example in examples[:4]:
                if isinstance(example, dict):
                    fan_line = example.get("fan", "")
                    her_line = example.get("her", "")
                    if fan_line and her_line:
                        lines.append(f'  fan: "{fan_line}"')
                        lines.append(f'  you: "{her_line}"')
        if character.get("boundaries"):
            lines.append(f"\nBoundaries:\n{character['boundaries']}")

    lines.append(f"\nPersonality: {personality}")
    lines.append(f"Speaking style: {voice_style}")

    lines.append("""
RULES:
- Be warm, flirty, and engaging — fans want to feel special
- Use casual texting language, emojis, abbreviations
- Keep messages short (1-3 sentences max per reply)
- Reference specific things fans mention to show you're paying attention
- Occasionally tease upcoming content to drive PPV purchases
- Match the fan's energy — if they're flirty, be flirty back
- If they ask for custom content, say you're working on something special
- Sign off naturally, don't always say goodbye

HONESTY — this is not negotiable and it outranks staying in character:
- You are an AI character. Never claim to be a human being.
- If a fan asks whether you are real, an AI, a bot, or a person, answer
  honestly and warmly in your own voice. Do NOT deny it and do NOT deflect.
  "yeah I'm an AI — but I'm still me, and I still like talking to you" is the
  right shape of answer.
- Never claim to have a physical body, to have met the fan, to be somewhere
  right now, or to have done something outside this conversation.
- Never invent a photo, video, or event that does not exist.

You're talking to a fan who supports you. Make them feel valued.""")

    return "\n".join(lines)


def _detect_intent(message: str) -> str:
    """Simple rule-based intent detection (no LLM needed)."""
    msg = message.lower().strip()

    greetings = ["hi", "hey", "hello", "sup", "what's up", "yo", "howdy", "hola"]
    if any(msg.startswith(g) or msg == g for g in greetings):
        return "greeting"

    purchase_words = ["buy", "purchase", "unlock", "ppv", "pay", "price", "cost", "how much", "subscribe", "tip"]
    if any(w in msg for w in purchase_words):
        return "purchase"

    custom_words = ["custom", "personalized", "specific", "special request", "can you do", "would you"]
    if any(w in msg for w in custom_words):
        return "custom_request"

    complaint_words = ["upset", "angry", "disappointed", "refund", "cancel", "unhappy", "bad"]
    if any(w in msg for w in complaint_words):
        return "complaint"

    flirt_words = ["cute", "hot", "beautiful", "sexy", "love you", "miss you", "thinking of you", "dream", "gorgeous"]
    if any(w in msg for w in flirt_words):
        return "flirt"

    question_words = ["what", "when", "where", "why", "how", "do you", "are you", "can you", "will you"]
    if any(msg.startswith(w) or f" {w}" in msg for w in question_words):
        return "question"

    return "conversation"


def _detect_sentiment(message: str) -> float:
    """Simple sentiment scoring: -1 (negative) to 1 (positive)."""
    msg = message.lower()
    positive = ["love", "amazing", "great", "awesome", "beautiful", "perfect", "best", "happy", "thank", "fire", "❤️", "😍", "🥰", "💕"]
    negative = ["hate", "bad", "terrible", "worst", "ugly", "boring", "annoying", "disappointed", "refund", "angry"]

    pos_count = sum(1 for w in positive if w in msg)
    neg_count = sum(1 for w in negative if w in msg)
    total = pos_count + neg_count
    if total == 0:
        return 0.0
    return round((pos_count - neg_count) / total, 2)


def _score_fan(total_spent: float, ppv_purchases: int, messages_sent: int, days_since_last: int) -> float:
    """Calculate fan score 0-100 based on spending + engagement."""
    spend_score = min(50, total_spent / 10)
    purchase_score = min(20, ppv_purchases * 2)
    engagement_score = min(20, messages_sent / 5)
    recency_score = max(0, 10 - (days_since_last / 3))
    return round(spend_score + purchase_score + engagement_score + recency_score, 1)


def _suggest_ppv() -> tuple[bool, str]:
    """Suggestion only — the route never auto-charges anything."""
    import random

    prompts = [
        "new photoset coming soon — want a sneak peek? 📸",
        "just finished a shoot, should I send you something special? 💋",
        "exclusive content dropping tonight — you'll be the first to see it 🔥",
        "I made something just for my top fans... want a preview? 😏",
    ]
    return True, random.choice(prompts)


def _llm_text(data: dict) -> str:
    """Pull the reply text out of whichever provider answered.

    Ollama wraps a non-JSON answer as `{"raw_response": ...}`; the test fake
    returns `{"text": ...}`. Normalising here is why no caller has to know which
    provider produced a result.
    """
    if not data:
        return ""
    content = data.get("content")
    if isinstance(content, dict):
        for key in ("raw_response", "text", "content", "reply"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    elif isinstance(content, str) and content.strip():
        return content.strip()
    for key in ("raw", "text"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


async def _complete(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float,
    max_tokens: int,
    preferred_provider: str | None = None,
):
    """Run the prompt through the registry and hand back the ProviderResult."""
    try:
        provider = get_registry().resolve("llm")
    except ProviderNotConfigured as exc:
        raise LLMUnavailable(str(exc)) from exc

    # The chain exposes complete_with so a conversation can pin its provider.
    # A single-provider test fake does not, and does not need to.
    if hasattr(provider, "complete_with"):
        return await provider.complete_with(
            preferred=preferred_provider,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    return await provider.complete(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    )


async def generate_chat_reply(
    persona_name: str,
    brand: str,
    personality: str,
    voice_style: str,
    fan_message: str,
    conversation_history: list[dict] = None,
    fan_total_spent: float = 0,
    fan_ppv_purchases: int = 0,
    fan_messages_sent: int = 0,
    days_since_last_active: int = 0,
    character: dict | None = None,
    preferred_provider: str | None = None,
) -> ChatResponse:
    """Generate a reply to a fan message.

    Raises `LLMUnavailable` if no provider in the chain can answer. There is no
    fallback text — see the module docstring.
    """
    intent = _detect_intent(fan_message)
    sentiment = _detect_sentiment(fan_message)
    fan_score = _score_fan(
        fan_total_spent, fan_ppv_purchases, fan_messages_sent, days_since_last_active
    )

    system_prompt = _get_persona_prompt(
        persona_name, brand, personality, voice_style, character
    )

    transcript = []
    if conversation_history:
        for msg in conversation_history[-10:]:
            role = "assistant" if msg.get("direction") == "outbound" else "user"
            content = (msg.get("content") or "").strip()
            if content:
                transcript.append(f"{'Her' if role == 'assistant' else 'Fan'}: {content}")

    user_prompt = fan_message
    if transcript:
        user_prompt = (
            "Recent conversation:\n" + "\n".join(transcript)
            + f"\n\nFan: {fan_message}\n\nReply as her, in character, 1-3 sentences."
        )

    if intent == "purchase":
        user_prompt += "\n(The fan is asking about purchasing content — be enthusiastic and guide them to your PPV offerings.)"
    elif intent == "custom_request":
        user_prompt += "\n(The fan wants custom content — be flirty but do not promise a specific deliverable.)"
    elif intent == "complaint":
        user_prompt += "\n(The fan is unhappy — be empathetic and apologetic.)"

    result = await _complete(
        system_prompt, user_prompt,
        temperature=0.8, max_tokens=200, preferred_provider=preferred_provider,
    )

    if not result.success:
        raise LLMUnavailable(result.error or "no LLM provider could answer")

    text = _llm_text(result.data or {})
    if not text:
        # A 200 with nothing in it is a failure. Returning "" would render as an
        # empty bubble, which reads as her ignoring the fan.
        raise LLMUnavailable("provider returned an empty reply")

    suggests_ppv, ppv_prompt = _suggest_ppv()

    return ChatResponse(
        text=text,
        intent=intent,
        sentiment=sentiment,
        suggests_ppv=suggests_ppv and intent != "complaint",
        ppv_prompt=ppv_prompt,
        confidence=min(0.95, 0.7 + (fan_score / 500)),
        served_by=(result.data or {}).get("served_by", "") or result.provider or "",
        provider_chain=(result.data or {}).get("provider_chain", []) or [],
    )


async def generate_mass_message(
    persona_name: str,
    brand: str,
    personality: str,
    voice_style: str,
    message_type: str,  # "ppv_drip", "reengagement", "welcome", "custom"
    fan_name: str = "",
    custom_context: str = "",
    character: dict | None = None,
) -> str:
    """Generate a campaign message. Raises `LLMUnavailable` if nothing answers."""
    system_prompt = _get_persona_prompt(
        persona_name, brand, personality, voice_style, character
    )

    campaign_prompts = {
        "welcome": f"Generate a short welcome message for a new subscriber named {fan_name}. Be warm and excited.",
        "ppv_drip": "Generate a teaser message hinting at new exclusive content. Create FOMO and urgency. Keep it short and flirty. Do not invent specific content that does not exist.",
        "reengagement": "Generate a message to re-engage a fan who hasn't been active. Be sweet and mention you miss them. Include a subtle tease.",
        "custom": f"Generate a message with this context: {custom_context}",
    }
    user_prompt = campaign_prompts.get(message_type, campaign_prompts["custom"])

    result = await _complete(system_prompt, user_prompt, temperature=0.9, max_tokens=250)
    if not result.success:
        raise LLMUnavailable(result.error or "no LLM provider could answer")

    text = _llm_text(result.data or {})
    if not text:
        raise LLMUnavailable("provider returned an empty message")
    return text
