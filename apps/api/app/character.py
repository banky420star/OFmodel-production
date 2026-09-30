"""The character sheet — reading it, and a usable default.

The sheet lives in `persona_characters` (a side table, because `create_all`
cannot add columns to the existing `personas` table). This module is the one
place that turns a row into the dict the chat system prompt is built from, so
the operator chat and the fan chat cannot disagree about who she is.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PersonaCharacter


def character_to_dict(row: PersonaCharacter | None) -> dict | None:
    if row is None:
        return None
    return {
        "backstory": row.backstory or "",
        "speech_style": row.speech_style or "",
        "catchphrases": row.catchphrases or [],
        "likes": row.likes or [],
        "dislikes": row.dislikes or [],
        "boundaries": row.boundaries or "",
        "example_dialogue": row.example_dialogue or [],
    }


async def load_character(db: AsyncSession, persona_id: str) -> dict | None:
    """The sheet for a persona, or None if none has been written yet."""
    row = (
        await db.execute(
            select(PersonaCharacter).where(
                PersonaCharacter.persona_id == str(persona_id)
            )
        )
    ).scalar_one_or_none()
    return character_to_dict(row)


def default_character(persona_name: str, brand: str = "") -> dict:
    """A sheet good enough to hold a conversation before an operator writes one.

    Deliberately generic and deliberately honest: it gives the model a voice and
    a boundary, and it invents no biography. A persona with an empty sheet
    should sound like a friendly creator, not like a fabricated person with a
    childhood nobody wrote.
    """
    return {
        "backstory": (
            f"{persona_name} is an AI character who creates {brand or 'lifestyle'} "
            "content. She is upfront that she is an AI, and she is genuinely "
            "interested in the people who follow her."
        ),
        "speech_style": "Warm, casual, lowercase, short messages, occasional emoji.",
        "catchphrases": [],
        "likes": [],
        "dislikes": [],
        "boundaries": (
            "Never claim to be human. Never claim to have a body, to have met the "
            "fan, or to have done anything outside this conversation. Never "
            "promise specific content that has not been produced."
        ),
        "example_dialogue": [
            {
                "fan": "are you real?",
                "her": "i'm an AI 🙂 but this is really me talking to you, if that counts",
            },
        ],
    }


def merged_character(persona_name: str, brand: str, row_character: dict | None) -> dict:
    """A usable sheet: the stored one with the defaults filling any gaps."""
    base = default_character(persona_name, brand)
    if not row_character:
        return base
    merged = dict(base)
    for key, value in row_character.items():
        if value:
            merged[key] = value
    return merged
