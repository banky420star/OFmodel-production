"""Persona Studio — a shoot's images must be of the persona.

`generate_shoot_images_handler` used to call the image provider directly with a
`session_id` string and no reference image. The provider had no way to know
which face was wanted, so a content pack could ship photos of someone else
entirely — and the persona's trained LoRA was never applied, because only the
identity path passes `lora_path`.

These tests pin the three things that matter: the identity path is what runs,
the adapter name it used is recorded, and a shoot that was supposed to be the
persona and produced nothing says so instead of ending COMPLETED and empty.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app import identity_engine
from app.models import (
    GeneratedImage, Identity, IdentityLock, IdentityStatus, Persona, Shoot,
    ShootStatus,
)
from app.providers.base import ImageProvider, ProviderResult
from app.workflows.content_flow import generate_shoot_images_handler


class LoraAwareImageProvider(ImageProvider):
    """An image provider that accepts `lora_path` — the shape ComfyUI has.

    `FakeImageProvider.edit_image` has no `lora_path` parameter, which is also
    worth testing (the identity engine filters by signature rather than
    assuming). This one records what it was handed so the test can assert the
    adapter reached the provider instead of being computed and dropped.
    """

    def __init__(self, fail: bool = False):
        self.calls: list[dict] = []
        self.fail = fail

    async def generate(self, prompt, negative_prompt="", width=1024, height=1024,
                       steps=30, cfg_scale=7.0, seed=-1, lora_path="",
                       lora_strength=0.8, session_id="") -> ProviderResult:
        self.calls.append({"path": "generate", "prompt": prompt, "lora_path": lora_path})
        return ProviderResult(True, {"image_bytes": b"\x89PNG\r\n\x1a\n"},
                              provider="lora_aware", latency_ms=3)

    async def edit_image(self, reference_image_bytes, prompt, negative_prompt="",
                         width=1024, height=1024, seed=-1, lora_path="",
                         lora_strength=0.8) -> ProviderResult:
        self.calls.append({
            "path": "edit_image", "prompt": prompt, "lora_path": lora_path,
            "reference_bytes": len(reference_image_bytes or b""),
        })
        if self.fail:
            return ProviderResult(False, {}, provider="lora_aware",
                                  error="comfyui refused the graph")
        return ProviderResult(True, {"image_bytes": b"\x89PNG\r\n\x1a\n"},
                              provider="lora_aware", latency_ms=3)

    async def upscale(self, image_key, scale=2) -> ProviderResult:
        return ProviderResult(True, {"image_bytes": b"\x89PNG\r\n\x1a\n"},
                              provider="lora_aware")

    async def img2img(self, image_key, prompt, strength=0.75, **kwargs) -> ProviderResult:
        return ProviderResult(True, {"image_bytes": b"\x89PNG\r\n\x1a\n"},
                              provider="lora_aware")

    async def health_check(self) -> ProviderResult:
        return ProviderResult(True, {"ok": True}, provider="lora_aware")


@pytest.fixture
def avatar_file():
    """A real decodable avatar at the path the identity engine looks in.

    It has to be a real image: `get_avatar_bytes` opens it with PIL and resizes
    it, so a placeholder byte string fails for a reason that has nothing to do
    with what the test is checking.
    """
    from PIL import Image

    name = f"ShootLock{uuid.uuid4().hex[:8]}"
    # Read the module attribute rather than an import-time copy: conftest
    # redirects storage into a tmp dir for the session, and the identity engine
    # looks the directory up on the same module object at call time.
    path = identity_engine.AVATAR_DIR / f"{name.lower()}.jpg"
    identity_engine.AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), (120, 90, 60)).save(path, format="JPEG")
    yield name, path
    path.unlink(missing_ok=True)


async def _seed_persona_with_lock(db, name: str, lora: str = "") -> Persona:
    persona = Persona(id=uuid.uuid4(), name=name, age=26)
    db.add(persona)
    await db.flush()

    db.add(IdentityLock(
        persona_id=str(persona.id).replace("-", ""),
        seed=1234,
        identity_prompt=f"{name}, a synthetic creator",
        negative_prompt="blurry",
        style_tags=[],
    ))
    if lora:
        db.add(Identity(
            id=uuid.uuid4(), persona_id=persona.id, name=f"{name} identity",
            status=IdentityStatus.READY, lora_model_path=lora,
        ))
    await db.flush()
    return persona


_NO_PERSONA = object()


async def _seed_shoot(db, persona_id=_NO_PERSONA, **kwargs) -> Shoot:
    """A shoot row. Pass `persona_id=None` for one that belongs to nobody."""
    shoot = Shoot(
        id=uuid.uuid4(),
        persona_id=uuid.uuid4() if persona_id is _NO_PERSONA else persona_id,
        name="Shoot", status=ShootStatus.DRAFT, theme="lifestyle",
        image_count=kwargs.pop("image_count", 2), **kwargs,
    )
    db.add(shoot)
    await db.flush()
    return shoot


async def _run(db, shoot, workflow_id, **input_data):
    return await generate_shoot_images_handler(
        workflow_id=workflow_id,
        step_id=uuid.uuid4(),
        input_data={"shoot_id": str(shoot.id), "image_count": 2, **input_data},
        db=db,
    )


@pytest.mark.asyncio
async def test_a_shoot_for_a_persona_uses_the_identity_path_and_the_lora(
    db, registry_override, avatar_file,
):
    """The whole point: the adapter the build trained is handed to the provider,
    with the locked avatar as the reference — not a bare prompt."""
    name, _ = avatar_file
    persona = await _seed_persona_with_lock(db, name, lora="persona_shootlock.safetensors")
    shoot = await _seed_shoot(db, persona.id)
    provider = registry_override("image", LoraAwareImageProvider())

    out = await _run(db, shoot, uuid.uuid4(), persona_id=str(persona.id))

    assert out["image_count"] == 2
    assert out["identity_locked"] is True
    assert out["lora_applied"] == ["persona_shootlock.safetensors"]
    assert all(c["path"] == "edit_image" for c in provider.calls), (
        "every shot must go through the identity-locked edit path"
    )
    assert all(c["lora_path"] == "persona_shootlock.safetensors" for c in provider.calls)
    assert all(c["reference_bytes"] > 0 for c in provider.calls), (
        "no reference image means the face was never locked"
    )
    assert shoot.status == ShootStatus.COMPLETED


@pytest.mark.asyncio
async def test_the_lora_is_recorded_on_every_image_it_made(
    db, registry_override, avatar_file,
):
    """A pack is only reproducible if each image says which adapter produced it."""
    name, _ = avatar_file
    persona = await _seed_persona_with_lock(db, name, lora="persona_shootlock.safetensors")
    shoot = await _seed_shoot(db, persona.id)
    registry_override("image", LoraAwareImageProvider())
    workflow_id = uuid.uuid4()

    await _run(db, shoot, workflow_id, persona_id=str(persona.id))

    rows = (await db.execute(
        select(GeneratedImage).where(GeneratedImage.workflow_id == workflow_id)
    )).scalars().all()
    assert len(rows) == 2
    for row in rows:
        assert row.metadata_json["identity_locked"] is True
        assert row.metadata_json["lora_name"] == "persona_shootlock.safetensors"
        assert row.metadata_json["bytes_persisted"] is True


@pytest.mark.asyncio
async def test_a_provider_that_cannot_apply_a_lora_still_locks_the_face(
    db, avatar_file,
):
    """The default fake's `edit_image` has no `lora_path` parameter — the shape
    DashScope has. The image is still identity-locked via the avatar; the
    adapter is simply not applicable, and `lora_applied` says so rather than
    claiming an adapter that was never handed over."""
    name, _ = avatar_file
    persona = await _seed_persona_with_lock(db, name, lora="persona_shootlock.safetensors")
    shoot = await _seed_shoot(db, persona.id)

    out = await _run(db, shoot, uuid.uuid4(), persona_id=str(persona.id))

    assert out["image_count"] == 2
    assert out["identity_locked"] is True
    assert out["lora_applied"] == [], "nothing was applied, so nothing is claimed"


@pytest.mark.asyncio
async def test_the_persona_is_read_off_the_shoot_when_the_run_does_not_carry_it(
    db, registry_override, avatar_file,
):
    """A shoot started from the UI carries the persona on the shoot row. Reading
    it only from the run's input would silently produce a generic shoot."""
    name, _ = avatar_file
    persona = await _seed_persona_with_lock(db, name, lora="persona_shootlock.safetensors")
    shoot = await _seed_shoot(db, persona.id)
    provider = registry_override("image", LoraAwareImageProvider())

    out = await _run(db, shoot, uuid.uuid4())  # no persona_id in the input

    assert out["identity_locked"] is True
    assert all(c["path"] == "edit_image" for c in provider.calls)


@pytest.mark.asyncio
async def test_a_run_with_no_persona_anywhere_says_it_is_not_identity_locked(
    db, registry_override,
):
    """A theme run with no persona — no shoot row and no persona in the input —
    is legitimate. It must be labelled, so it can never be mistaken later for a
    persona's pack.

    (`shoots.persona_id` is NOT NULL, so a shoot always has a persona; this
    branch is reached by an image step that runs before any shoot exists.)
    """
    provider = registry_override("image", LoraAwareImageProvider())

    out = await generate_shoot_images_handler(
        workflow_id=uuid.uuid4(), step_id=uuid.uuid4(),
        input_data={"image_count": 2, "theme": "lifestyle"}, db=db,
    )

    assert out["image_count"] == 2
    assert out["identity_locked"] is False
    assert out["lora_applied"] == []
    assert all(c["path"] == "generate" for c in provider.calls), (
        "with no persona there is nothing to lock to, so the plain path is right"
    )


@pytest.mark.asyncio
async def test_an_identity_shoot_that_produces_nothing_fails_and_says_why(
    db, registry_override, avatar_file,
):
    """The failure mode this handler exists to stop repeating: a shoot that
    quietly produced nothing and was marked COMPLETED."""
    name, _ = avatar_file
    persona = await _seed_persona_with_lock(db, name)
    shoot = await _seed_shoot(db, persona.id)
    registry_override("image", LoraAwareImageProvider(fail=True))

    out = await _run(db, shoot, uuid.uuid4(), persona_id=str(persona.id))

    assert out["image_count"] == 0
    assert out["errors"], "a shoot with no images and no reason is the defect"
    assert "comfyui refused the graph" in out["errors"][0]
    assert shoot.status == ShootStatus.FAILED


@pytest.mark.asyncio
async def test_missing_identity_lock_fails_rather_than_substituting(
    db, registry_override, avatar_file,
):
    """No lock means no persona image. The engine has no placeholder path, and
    the handler must surface that instead of pressing on."""
    name, _ = avatar_file
    persona = Persona(id=uuid.uuid4(), name=name, age=26)
    db.add(persona)
    await db.flush()
    shoot = await _seed_shoot(db, persona.id)
    registry_override("image", LoraAwareImageProvider())

    out = await _run(db, shoot, uuid.uuid4(), persona_id=str(persona.id))

    assert out["image_count"] == 0
    assert "No identity lock" in out["errors"][0]
    assert shoot.status == ShootStatus.FAILED


@pytest.mark.asyncio
async def test_a_malformed_shoot_id_is_reported_not_raised(db, registry_override):
    """The engine marks a step FAILED on an exception, but the message is then a
    traceback. A bad id is a reason, and the reason is what the pack needs."""
    registry_override("image", LoraAwareImageProvider())

    out = await generate_shoot_images_handler(
        workflow_id=uuid.uuid4(), step_id=uuid.uuid4(),
        input_data={"shoot_id": "not-a-shoot", "image_count": 2}, db=db,
    )

    assert out["image_count"] == 0
    assert "'not-a-shoot' is not a shoot id" in out["errors"][0]


@pytest.mark.asyncio
async def test_a_malformed_persona_id_is_reported_not_raised(db, registry_override):
    registry_override("image", LoraAwareImageProvider())

    out = await generate_shoot_images_handler(
        workflow_id=uuid.uuid4(), step_id=uuid.uuid4(),
        input_data={"persona_id": "not-a-persona", "image_count": 2}, db=db,
    )

    assert out["image_count"] == 0
    assert "'not-a-persona' is not a persona id" in out["errors"][0]
