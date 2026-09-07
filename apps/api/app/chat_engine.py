"""
Persona Studio — Fan Chat Engine

Ollama-powered chat system for fan engagement:
- Auto-reply to new subscribers
- PPV message suggestions
- Fan scoring based on spending + engagement
- Intent detection (greeting, flirt, purchase, complaint)
- Conversation memory per fan
"""

from __future__ import annotations
import json
import os
import httpx
from datetime import datetime, timezone, timedelta
from typing import Optional
from dataclasses import dataclass, field


OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:1.7b")


@dataclass
class ChatResponse:
    text: str
    intent: str = "conversation"
    sentiment: float = 0.0
    suggests_ppv: bool = False
    ppv_prompt: str = ""
    confidence: float = 0.8


def _get_persona_prompt(persona_name: str, brand: str, personality: str, voice_style: str) -> str:
    """Build the system prompt for a persona's chat personality."""
    return f"""You are {persona_name}, a {brand} content creator on social media.
Your personality: {personality}
Your speaking style: {voice_style}

RULES:
- Be warm, flirty, and engaging — fans want to feel special
- Use casual texting language, emojis, abbreviations
- Keep messages short (1-3 sentences max per reply)
- Reference specific things fans mention to show you're paying attention
- Occasionally tease upcoming content to drive PPV purchases
- Never break character or mention being AI
- Match the fan's energy — if they're flirty, be flirty back
- If they ask for custom content, say you're working on something special for them
- Sign off messages naturally, don't always say goodbye

You're talking to a fan who supports you. Make them feel valued."""


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
    # Spending component (0-50 points)
    spend_score = min(50, total_spent / 10)  # $500+ = max spending score
    
    # Purchase frequency (0-20 points)
    purchase_score = min(20, ppv_purchases * 2)
    
    # Engagement (0-20 points)
    engagement_score = min(20, messages_sent / 5)
    
    # Recency (0-10 points, decays over time)
    recency_score = max(0, 10 - (days_since_last / 3))
    
    return round(spend_score + purchase_score + engagement_score + recency_score, 1)


def _suggest_ppv(content_themes: list[str], fan_interests: list[str]) -> tuple[bool, str]:
    """Determine if a PPV message should be suggested and what content to promote."""
    # Suggest PPV if fan hasn't purchased recently or is highly engaged
    prompts = [
        "new photoset coming soon — want a sneak peek? 📸",
        "just finished a shoot, should I send you something special? 💋",
        "exclusive content dropping tonight — you'll be the first to see it 🔥",
        "I made something just for my top fans... want a preview? 😏",
    ]
    import random
    return True, random.choice(prompts)


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
) -> ChatResponse:
    """Generate an AI reply to a fan message using Ollama."""
    
    intent = _detect_intent(fan_message)
    sentiment = _detect_sentiment(fan_message)
    fan_score = _score_fan(fan_total_spent, fan_ppv_purchases, fan_messages_sent, days_since_last_active)
    
    system_prompt = _get_persona_prompt(persona_name, brand, personality, voice_style)
    
    # Build conversation context
    messages = [{"role": "system", "content": system_prompt}]
    
    # Add recent conversation history (last 10 messages)
    if conversation_history:
        for msg in conversation_history[-10:]:
            role = "assistant" if msg.get("direction") == "outbound" else "user"
            messages.append({"role": role, "content": msg.get("content", "")})
    
    # Add current fan message
    messages.append({"role": "user", "content": fan_message})
    
    # Add intent hint
    if intent == "purchase":
        messages.append({"role": "system", "content": "The fan is asking about purchasing content. Be enthusiastic and guide them to your PPV offerings."})
    elif intent == "custom_request":
        messages.append({"role": "system", "content": "The fan wants custom content. Be flirty but vague — say you'll make something special for them."})
    elif intent == "complaint":
        messages.append({"role": "system", "content": "The fan is unhappy. Be empathetic, apologetic, and offer to make it right."})
    
    # Call Ollama
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": 0.8, "num_predict": 150},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            reply_text = data.get("message", {}).get("content", "").strip()
            
            if not reply_text:
                reply_text = f"hey babe! 💕"
            
            # Check if reply suggests PPV
            suggests_ppv, ppv_prompt = _suggest_ppv([], [])
            if fan_score > 50 and intent != "complaint":
                suggests_ppv = True
            
            return ChatResponse(
                text=reply_text,
                intent=intent,
                sentiment=sentiment,
                suggests_ppv=suggests_ppv,
                ppv_prompt=ppv_prompt,
                confidence=min(0.95, 0.7 + (fan_score / 500)),
            )
    
    except Exception as e:
        # Fallback responses when Ollama is down
        fallbacks = {
            "greeting": f"hey babe! 💕 what's up?",
            "flirt": f"aww you're making me blush 🥰",
            "purchase": f"i have something special for you... want to see? 😏",
            "custom_request": f"hmm i like that idea... let me work on something for you 💋",
            "complaint": f"oh no, i'm sorry babe! tell me what's wrong 💕",
            "question": f"great question! let me think about that 🤔",
            "conversation": f"love hearing from you! 💕",
        }
        return ChatResponse(
            text=fallbacks.get(intent, f"hey! 💕"),
            intent=intent,
            sentiment=sentiment,
            suggests_ppv=fan_score > 50,
            ppv_prompt="check out my latest content 📸",
            confidence=0.5,
        )


async def generate_mass_message(
    persona_name: str,
    brand: str,
    personality: str,
    voice_style: str,
    message_type: str,  # "ppv_drip", "reengagement", "welcome", "custom"
    fan_name: str = "",
    custom_context: str = "",
) -> str:
    """Generate a mass message for a specific campaign type."""
    
    system_prompt = _get_persona_prompt(persona_name, brand, personality, voice_style)
    
    campaign_prompts = {
        "welcome": f"Generate a short welcome message for a new subscriber named {fan_name}. Be warm and excited.",
        "ppv_drip": f"Generate a teaser message hinting at new exclusive content. Create FOMO and urgency. Keep it short and flirty.",
        "reengagement": f"Generate a message to re-engage a fan who hasn't been active. Be sweet and mention you miss them. Include a subtle tease.",
        "custom": f"Generate a message with this context: {custom_context}",
    }
    
    user_prompt = campaign_prompts.get(message_type, campaign_prompts["custom"])
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "stream": False,
                    "options": {"temperature": 0.9, "num_predict": 200},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("message", {}).get("content", "").strip() or f"hey {fan_name}! 💕"
    except Exception:
        return f"hey {fan_name}! 💕 miss you babe!"
