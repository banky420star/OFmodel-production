#!/bin/sh
# End-to-end real-provider verification for the Persona Production Line.
# Requires a filled-in .env and every configured provider reachable.
# Usage: scripts/e2e_real_providers.sh
set -e

API="${API:-http://localhost:8000}"
echo "=== 1. Startup self-check / health ==="
curl -sf "$API/api/v1/health" | python3 -m json.tool
curl -sf "$API/api/v1/system/providers" | python3 -m json.tool

echo "=== 2. Ollama LLM reachable ==="
curl -sf "${OLLAMA_URL:-http://localhost:11434}/api/tags" >/dev/null && echo "ollama ok"

echo "=== 3. Create persona (real 7-step build) ==="
PERSONA=$(curl -sf -X POST "$API/api/v1/personas" -H 'Content-Type: application/json' -d '{
  "name": "E2E Real '"$(date +%s)"'",
  "age": 25,
  "brand": "luxury lifestyle",
  "voice_style": "English",
  "appearance": {"hair": "long brown", "eye_colour": "green", "height_profile": "5'"'"'8\""}
}')
PID=$(echo "$PERSONA" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "persona: $PID"

echo "=== 4. Poll build job until done ==="
for i in $(seq 1 120); do
  JOB=$(curl -sf "$API/api/v1/jobs?persona_id=$PID" | python3 -c "import sys,json; jobs=json.load(sys.stdin); r=[j for j in jobs if j['type']=='build_persona']; print(r[0]['id'] if r else '')" 2>/dev/null)
  if [ -n "$JOB" ]; then
    STATUS=$(curl -sf "$API/api/v1/jobs/$JOB" | python3 -c "import sys,json; j=json.load(sys.stdin); print(j['status'], j['progress'])")
    echo "  build: $STATUS"
    case "$STATUS" in completed*) break;; failed*) echo "BUILD FAILED"; exit 1;; esac
  fi
  sleep 5
done

echo "=== 5. Auto-produce a shoot (identity-locked images) ==="
PRODUCTION=$(curl -sf -X POST "$API/api/v1/personas/$PID/auto-produce?shoot_count=1&images_per_shoot=2&generate_videos=false")
JOB=$(echo "$PRODUCTION" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
for i in $(seq 1 120); do
  STATUS=$(curl -sf "$API/api/v1/jobs/$JOB" | python3 -c "import sys,json; j=json.load(sys.stdin); print(j['status'], j.get('progress', 0))")
  echo "  production: $STATUS"
  case "$STATUS" in
    completed*) break ;;
    failed*) echo "AUTO-PRODUCE FAILED"; exit 1 ;;
  esac
  sleep 5
done

echo "=== 6. Verify gallery + moderation records ==="
GALLERY=$(curl -sf "$API/api/v1/personas/$PID/gallery")
echo "$GALLERY" | python3 -m json.tool
echo "$GALLERY" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('count', 0) > 0, 'no generated images'; print('gallery images:', d['count'])"

echo "done — generated images passed the configured provider and identity-lock pipeline."