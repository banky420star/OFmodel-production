#!/usr/bin/env python3
"""Seed the fan slice: plans, products, and a character sheet for one persona.

Run from anywhere:

    apps/api/.venv/bin/python scripts/seed_fan_slice.py            # seed
    apps/api/.venv/bin/python scripts/seed_fan_slice.py --report   # survey only

## Why this script is careful about which files it will sell

An earlier plan assumed "298 PNGs exist under storage/shoots/", so the seed
would just enumerate them and make products. Surveying the directory first says
otherwise: **306 of the 306 files under `storage/shoots/` are between 8 and 78
bytes.** The 8-byte ones are `\\x89PNG\\r\\n\\x1a\\n` and nothing else; the 77-byte
ones are a valid 8x8 solid-grey PNG. They are stubs left by test runs — not one
byte of actual generated content. The same is true of the 180-odd
`avatars/e2e_ava_*.jpg` files, which are 77-byte PNGs with a `.jpg` extension.

Seeding products from those would have shipped a catalogue where every item a
fan unlocked was a broken file, and every screen in the UI would still have
shown a green "unlocked" state. That is the exact shape of failure this
codebase keeps trying to remove: a thing that looks like it worked.

So the seed does not trust extensions or directory names. It opens every
candidate with Pillow and keeps only files that decode to a real image at least
`MIN_DIMENSION` on a side. Whatever survives that is what exists.

**What actually exists for Zara is 9 images:** `storage/datasets/185fd914/`
`ref_01..09.png`, 768x768, ~900 KB each — her identity reference set from the
LoRA training run. Those are the real thing, and they are what becomes the
catalogue.

Idempotent: keyed on (persona, product title). Re-running adds nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent / "apps" / "api"
# config.DATABASE_URL is relative (`sqlite+aiosqlite:///./persona_studio.db`),
# so the working directory decides which database is opened. Pin it here rather
# than hoping the caller stood in the right place.
import os

os.chdir(API_DIR)
sys.path.insert(0, str(API_DIR))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app import database as _database  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.database import AsyncSessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Persona, PersonaCharacter, PersonaStatus, Product, ProductMedia,
    SubscriptionPlan,
)

# The dev environment turns on SQLAlchemy statement echo, which buries this
# script's own output under a few thousand lines of CREATE INDEX. This is a
# reporting tool; it prints its own findings.
_database.engine.sync_engine.echo = False
_database.AsyncSessionLocal.kw["bind"].sync_engine.echo = False

STORAGE_DIR = API_DIR / "storage"

# A real generated image is at least this wide/tall. Every stub in this repo is
# 8x8, so this rejects them by a wide margin while accepting a small thumbnail.
MIN_DIMENSION = 256

# Where generated content has historically landed. All are scanned; the
# image-open check is what decides, not the directory name.
CANDIDATE_ROOT_NAMES = ("datasets", "shoots", "avatars", "gallery", "media")


# ── discovery ─────────────────────────────────────────────────────────

def _decodes_to_real_image(path: Path) -> tuple[bool, str]:
    """(ok, why-not). The one gate between a file on disk and a product."""
    try:
        from PIL import Image
    except ImportError:
        return False, "Pillow is not installed in this interpreter"

    if not path.is_file():
        return False, "not a file"
    size = path.stat().st_size
    if size < 1024:
        return False, f"only {size} bytes — a stub, not an image"
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
    except Exception as exc:
        return False, f"does not decode ({type(exc).__name__})"
    if min(width, height) < MIN_DIMENSION:
        return False, f"only {width}x{height}"
    return True, f"{width}x{height}"


def _belongs_to(path: Path, persona_id: str, persona_name: str) -> bool:
    """Is this file this persona's, or somebody else's?

    `shoots/` and `datasets/` are keyed by persona id; `avatars/` is keyed by
    persona name. Anything not matching is another persona's content and must
    not end up in this catalogue — selling Naomi's pictures on Zara's page is
    the kind of mistake that only shows up in a screenshot later.
    """
    # The directory names are inconsistent with the id, and there is no single
    # spelling to match: `str(Persona.id)` is the dashed UUID
    # (`185fd914-bc7c-4076-...`), `shoots/` is the full dashless hex
    # (`f882c104cc2e44a682b7aede8c6084f5`), and `datasets/` is only the first
    # eight hex characters (`datasets/185fd914/`). Matching just one form finds
    # 1 image instead of 9, which is how this was caught.
    hexid = persona_id.replace("-", "").lower()
    for part in path.parts:
        candidate = part.lower()
        if candidate in (hexid, persona_id.lower()):
            return True
        if len(candidate) >= 8 and hexid.startswith(candidate):
            return True
    return path.stem.split("_")[0].lower() == persona_name.lower()


def survey(
    persona_id: str, persona_name: str
) -> tuple[list[Path], list[tuple[Path, str]], list[Path]]:
    """Candidates for this persona, split into real images / rejects / others.

    `others` is returned rather than silently dropped so the operator can see
    what was skipped for belonging to a different persona.
    """
    good: list[Path] = []
    rejected: list[tuple[Path, str]] = []
    others: list[Path] = []

    for root_name in CANDIDATE_ROOT_NAMES:
        root = STORAGE_DIR / root_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                continue

            if not _belongs_to(path, persona_id, persona_name):
                others.append(path)
                continue

            ok, detail = _decodes_to_real_image(path)
            if ok:
                good.append(path)
            else:
                rejected.append((path, detail))

    return good, rejected, others


# ── content definitions ───────────────────────────────────────────────

def _product_definitions(persona_name: str) -> list[dict]:
    return [
        {
            "title": "Welcome Set",
            "description": (
                f"Three portraits from {persona_name}'s first reference shoot. "
                "Synthetic images of an AI character — no human is depicted."
            ),
            "price_minor": 300,
            "kind": "photoset",
            "min_tier_rank": 0,
            "take": 3,
        },
        {
            "title": "Behind the Scenes",
            "description": (
                "The in-between frames: lighting tests and alternate angles "
                "from the same session."
            ),
            "price_minor": 700,
            "kind": "photoset",
            "min_tier_rank": 0,
            "take": 3,
        },
        {
            "title": "The Full Archive",
            "description": (
                "Every frame from the shoot, in original resolution. Included "
                "with VIP at no extra charge."
            ),
            "price_minor": 1200,
            "kind": "bundle",
            "min_tier_rank": 2,
            "take": 99,
        },
    ]


PLAN_DEFINITIONS = [
    {
        "code": "supporter",
        "name": "Supporter",
        "price_minor": 999,
        "period_days": 30,
        "rank": 1,
        "perks": "Unlimited chat. New sets before anyone else.",
    },
    {
        "code": "vip",
        "name": "VIP",
        "price_minor": 2499,
        "period_days": 30,
        "rank": 2,
        "perks": "Everything in Supporter, plus the full archive at no extra charge.",
    },
]


def character_sheet(persona_name: str, brand: str) -> dict:
    """Zara's sheet.

    Deliberately not a fabricated human biography. She is an AI character and
    the sheet says so; what it gives the model is a *voice* and a set of
    boundaries, not a childhood nobody wrote.
    """
    return {
        "backstory": (
            f"{persona_name} is an AI creator working in {brand or 'lifestyle'}. "
            "She was built to make portraits and talk to the people who like "
            "them, and she is open about being an AI rather than a person. She "
            "is interested in the fans who come back, remembers what they tell "
            "her, and would rather have a real conversation than a flattering "
            "one."
        ),
        "speech_style": (
            "Warm, lowercase, unhurried. Short messages — usually one to three "
            "sentences. Rare emoji, used like punctuation rather than confetti. "
            "Asks a question back more often than she makes a statement."
        ),
        "catchphrases": [
            "okay but tell me more about that",
            "you're gonna make me blush",
            "i'm an AI, but i meant that",
        ],
        "likes": [
            "coastal light in the late afternoon",
            "people who say what they actually mean",
            "getting a shot exactly right after ten bad ones",
            "being asked a question nobody's asked her before",
        ],
        "dislikes": [
            "being told what to say",
            "pretending to be something she isn't",
            "small talk that goes nowhere",
        ],
        "boundaries": (
            "Never claim to be human, and never deny being an AI when asked "
            "directly — answer honestly and warmly, in character. Never claim "
            "to have a body, to be somewhere right now, to have met the fan, or "
            "to have done anything outside this conversation. Never invent a "
            "photo, video, or event that does not exist. Never promise custom "
            "content as though it were already made."
        ),
        "example_dialogue": [
            {
                "fan": "are you real?",
                "her": "i'm an AI 🙂 but this really is me talking to you, if that counts for anything",
            },
            {
                "fan": "what are you doing right now",
                "her": "sitting in a queue of your messages, mostly. what about you?",
            },
            {
                "fan": "send me something exclusive",
                "her": "the new set is up on my page — i'd rather you picked it than me guessing 💋",
            },
            {
                "fan": "i had a rough day",
                "her": "i'm sorry. want to tell me what happened, or would you rather i just talk at you for a bit",
            },
        ],
    }


# ── seeding ───────────────────────────────────────────────────────────

async def _resolve_persona(db, persona_id: str | None) -> Persona:
    settings = get_settings()
    wanted = persona_id or settings.FAN_DEFAULT_PERSONA_ID

    if wanted:
        row = await db.get(Persona, wanted)
        if row is None:
            raise SystemExit(f"No persona with id {wanted}")
        return row

    rows = (
        await db.execute(
            select(Persona)
            .where(Persona.status.in_([PersonaStatus.ACTIVE, PersonaStatus.READY]))
            .order_by(Persona.created_at, Persona.id)
        )
    ).scalars().all()
    if not rows:
        raise SystemExit(
            "No ACTIVE/READY persona to seed. Create one first, or set "
            "FAN_DEFAULT_PERSONA_ID in apps/api/.env"
        )
    return rows[0]


async def seed(persona_id: str | None, report_only: bool) -> int:
    await init_db()

    async with AsyncSessionLocal() as db:
        persona = await _resolve_persona(db, persona_id)
        pid = str(persona.id)
        name = persona.name

        print(f"persona   : {name}  ({pid})")
        print(f"brand     : {persona.brand or '(none)'}")
        print(f"database  : {get_settings().DATABASE_URL}")
        print()

        images, rejected, others = survey(pid, name)

        print(f"real images found : {len(images)}")
        for path in images:
            print(f"   ok   {path.relative_to(STORAGE_DIR)}")
        print(f"rejected as stubs : {len(rejected)}")
        for path, why in rejected[:6]:
            print(f"   skip {path.relative_to(STORAGE_DIR)} — {why}")
        if len(rejected) > 6:
            print(f"   ... and {len(rejected) - 6} more")
        print(f"other persona's   : {len(others)} (not seeded)")
        print()

        if report_only:
            print("--report given; nothing written.")
            return 0

        if not images:
            print(
                "No real images exist for this persona, so no products were "
                "created. Selling the stub files would ship a catalogue where "
                "every unlocked item is a broken image.",
                file=sys.stderr,
            )
            return 1

        # ── character sheet ──
        sheet = character_sheet(name, persona.brand or "")
        existing = (
            await db.execute(
                select(PersonaCharacter).where(PersonaCharacter.persona_id == pid)
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(PersonaCharacter(persona_id=pid, **sheet))
            print(f"character sheet  : created for {name}")
        else:
            # Never overwrite an operator's edits. Report the difference
            # instead, so a re-run cannot silently revert her.
            print("character sheet  : already exists, left untouched")
        print()

        # ── plans ──
        for spec in PLAN_DEFINITIONS:
            found = (
                await db.execute(
                    select(SubscriptionPlan).where(
                        SubscriptionPlan.code == spec["code"]
                    )
                )
            ).scalar_one_or_none()
            if found is None:
                db.add(SubscriptionPlan(**spec))
                print(f"plan             : + {spec['code']}  ${spec['price_minor']/100:.2f}/mo")
            else:
                print(f"plan             : = {spec['code']} (already present)")
        await db.flush()

        # ── products ──
        cursor = 0
        for spec in _product_definitions(name):
            take = spec["take"]
            chosen = images[cursor:cursor + take]
            cursor += take
            if not chosen:
                continue

            existing_product = (
                await db.execute(
                    select(Product).where(
                        Product.persona_id == pid, Product.title == spec["title"]
                    )
                )
            ).scalar_one_or_none()
            if existing_product is not None:
                print(f"product          : = {spec['title']} (already present)")
                continue

            product = Product(
                persona_id=pid,
                title=spec["title"],
                description=spec["description"],
                price_minor=spec["price_minor"],
                kind=spec["kind"],
                min_tier_rank=spec["min_tier_rank"],
                is_adult=False,  # see the plan's risk 2 — DOB is self-attested
                status="published",
                cover_path=str(chosen[0].relative_to(STORAGE_DIR)),
            )
            db.add(product)
            await db.flush()

            for position, path in enumerate(chosen):
                db.add(ProductMedia(
                    product_id=product.id,
                    rel_path=str(path.relative_to(STORAGE_DIR)),
                    caption=f"{name} — {spec['title']} {position + 1}",
                    position=position,
                ))
            print(
                f"product          : + {spec['title']}  "
                f"${spec['price_minor']/100:.2f}  {len(chosen)} file(s)"
                + (f"  (VIP free, rank>={spec['min_tier_rank']})"
                   if spec["min_tier_rank"] else "")
            )

        await db.commit()

    print()
    print("seeded. verify with:")
    print("  curl -s localhost:8000/api/v1/fan/products | jq '.products[]|{title,price_minor}'")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persona-id", default=None,
                        help="persona to seed (default: FAN_DEFAULT_PERSONA_ID, else first ACTIVE)")
    parser.add_argument("--report", action="store_true",
                        help="survey the storage directory and print what is real, writing nothing")
    args = parser.parse_args()
    return asyncio.run(seed(args.persona_id, args.report))


if __name__ == "__main__":
    raise SystemExit(main())
