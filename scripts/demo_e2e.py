#!/usr/bin/env python3
"""Persona Studio — Live API demo. Runs the complete E2E workflow."""
import json, urllib.request, sys, time

BASE = "http://127.0.0.1:8001/api/v1"
_ts = int(time.time())

def api(method, path, data=None):
    url = f"{BASE}{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method,
                                headers={"Content-Type": "application/json"} if body else {})
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": e.read().decode()}

def p(label, obj, keys=None):
    if isinstance(obj, dict) and "_error" in obj:
        print(f"  ✗ {label}: HTTP {obj['_error']} — {obj.get('_body','')[:120]}")
        return
    if keys:
        vals = ", ".join(f"{k}={obj.get(k, '?')}" for k in keys)
        print(f"  ✓ {label}: {vals}")
    else:
        print(f"  ✓ {label}: {json.dumps(obj, indent=None)[:200]}")

print("═" * 55)
print("  PERSONA STUDIO — Live E2E Workflow Demo")
print("═" * 55)
print()

# 1. Health
print("▶ Health check")
h = api("GET", "/health") if False else json.loads(urllib.request.urlopen("http://127.0.0.1:8001/health").read())
p("API", h, ["status"])

# 2. Create persona
print(f"\n▶ STEP 1: Create Persona 'Ava_{_ts}'")
resp = api("POST", "/personas", {
    "name": f"Ava_{_ts}", "age": 24, "adult_verified": True, "synthetic_identity": True,
    "appearance": {"hair": "long blonde", "eye_colour": "blue", "skin": "fair"},
    "personality": ["confident", "playful"],
    "brand": "luxury lifestyle",
    "voice_profile": {"accent": "South African English", "tone": "warm", "speed": 1.0},
    "publishing_frequency": "5 packs/week"
})
p("Persona created", resp, ["name", "status", "age", "brand"])
PID = resp.get("id", "")
print(f"  → ID: {PID}")

# 3. Identities
print("\n▶ STEP 2: Identity candidates")
ids = api("GET", f"/personas/{PID}/identities")
if isinstance(ids, list):
    for i in ids:
        p("Candidate", i, ["identity_id", "validation_score", "status"])
else:
    p("Identities", ids)

# 4. Approve identity
if isinstance(ids, list) and ids:
    IID = ids[0]["id"]
    print(f"\n▶ STEP 3: Approve identity {IID[:12]}...")
    appr = api("POST", f"/personas/{PID}/identities/{IID}/approve")
    p("Approved", appr, ["status"])

# 5. Create shoot
print("\n▶ STEP 4: Create Shoot")
shoot = api("POST", f"/personas/{PID}/shoots", {
    "concept": "Sunday apartment lifestyle",
    "location": "modern apartment",
    "lighting": "morning natural",
    "wardrobe": "casual luxury",
    "camera_style": "editorial",
    "image_shots": 4,
    "video_shots": 1
})
p("Shoot created", shoot, ["shoot_id", "concept", "image_shots", "video_shots"])
SID = shoot.get("shoot_id", "")

# 6. Generate shoot content
if SID:
    print(f"\n▶ STEP 5: Generate content for shoot")
    gen = api("POST", f"/shoots/{SID}/generate")
    p("Content generated", gen, ["images", "videos"])

# 7. Create content pack
print("\n▶ STEP 6: Create Content Pack")
pack = api("POST", f"/personas/{PID}/packs", {
    "campaign_id": "campaign_001",
    "concept": "Sunday at home",
    "location": "apartment",
    "wardrobe": "silk pajamas",
    "style": "editorial"
})
p("Pack created", pack, ["pack_id", "status"])
PKID = pack.get("pack_id", "")

# 8. Assemble pack
if PKID:
    print(f"\n▶ STEP 7: Assemble Content Pack")
    asm = api("POST", f"/packs/{PKID}/assemble")
    p("Assembled", asm, ["images", "quality_score", "approval_state"])

# 9. Analytics
print("\n▶ STEP 8: Generate Analytics")
ana = api("POST", f"/personas/{PID}/analytics/generate")
p("Analytics", ana, ["impressions", "engagement_rate", "revenue"])

# 10. 24-month forecast
print("\n▶ STEP 9: 24-Month Forecast (Base scenario)")
fc = api("POST", f"/personas/{PID}/forecasts/generate?scenario=base")
if "_error" not in fc:
    months = fc.get("monthly_projections", [])
    if months:
        m1 = months[0]
        m12 = months[min(11, len(months)-1)]
        print(f"  ✓ Month 1:  revenue=R{m1.get('revenue',0):,.0f}, customers={m1.get('active_customers',0)}")
        print(f"  ✓ Month 12: revenue=R{m12.get('revenue',0):,.0f}, customers={m12.get('active_customers',0)}")
        total = sum(m.get("revenue", 0) for m in months)
        print(f"  ✓ 24-month total projected revenue: R{total:,.0f}")
else:
    p("Forecast", fc)

# 11. Workflows
print("\n▶ STEP 10: Workflows")
wfs = api("GET", "/workflows")
if isinstance(wfs, list):
    for w in wfs:
        p("Workflow", w, ["name", "status"])
else:
    p("Workflows", wfs)

# 12. Autopilot
print("\n▶ STEP 11: Enable Autopilot (ASSISTED)")
ap = api("POST", f"/personas/{PID}/autopilot", {"mode": "assisted"})
p("Autopilot", ap)

print()
print("═" * 55)
print("  ✓ FULL E2E WORKFLOW COMPLETE")
print("═" * 55)
print()
print(f"  API docs:   http://127.0.0.1:8001/docs")
print(f"  Health:     http://127.0.0.1:8001/health")
print(f"  Personas:   http://127.0.0.1:8001/api/v1/personas")
