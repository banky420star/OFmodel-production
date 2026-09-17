# First-user and developer QA report

**Date:** 2026-03-09  
**Environment:** macOS Apple M4, local API on `127.0.0.1:8000`, Next.js UI on `127.0.0.1:3000`, ComfyUI on `127.0.0.1:8188`

## Verification summary

| Area | Result | Evidence |
|---|---|---|
| Backend regression suite | PASS | `29 passed` |
| Frontend production build | PASS | `next build` completed successfully |
| Browser smoke flow | PASS | 3 Playwright tests passed |
| Final repeated browser/provider verification | PASS | 5 consecutive loops passed; 4 Playwright tests per loop |
| Top-level routes | PASS | Overview, Models, Create model, Production, Chat, Mailbox, Socials, Calendar, Analytics, Monetization, Settings returned 200 |
| Ollama | PASS | Reachable |
| ComfyUI | PASS | Reachable and workflow preflight passes |
| macOS voice | PASS | Real WAV synthesis smoke test passed |
| Wan video | BLOCKED | No server reachable at `http://localhost:8080` |
| Moderation | BLOCKED | `HUGGINGFACE_API_KEY` is not configured; provider is hosted, not fully offline |
| ComfyUI image render | BLOCKED | No compatible checkpoint installed (`sd_xl_base_1.0.safetensors`) |

## Issues found and status

### Fixed

1. **High — Content tab returned 405**
   - **Reproduction:** Create a model, open its Content tab.
   - **Evidence:** UI called `GET /personas/{persona_id}/packs`; API only exposed `GET /packs`, resulting in `API 405: Method Not Allowed`.
   - **Fix:** Added the persona-scoped GET route while preserving the existing collection route.
   - **Verification:** Complete create → persona → shoot → pack Playwright flow passes.

2. **Medium — Dashboard error message referenced the wrong API port**
   - **Evidence:** Error state instructed users to run the API on port 8001, while this project runs on port 8000.
   - **Fix:** Updated the copy to port 8000.

3. **Medium — Browser test selectors were coupled to stale uppercase copy**
   - **Evidence:** Tests searched for `PERSONA STUDIO`, `CREATE MODEL`, and `GENERATE PACK`; the UI intentionally renders title case and uses `+ Generate Pack`.
   - **Fix:** Replaced broad/case-sensitive selectors with accessible role/name assertions and scoped duplicate tab labels to the main content area.
   - **Verification:** 3 Playwright tests pass.

4. **Medium — Browser test reused a fixed model name**
   - **Evidence:** Retries could collide with the existing test persona and make navigation assertions nondeterministic.
   - **Fix:** Test model names now include a timestamp.

5. **Medium — Provider health overstated video readiness**
   - **Evidence:** `WanVideoProvider` was reported green when only `WAN_VIDEO_URL` was configured, despite no server listening on port 8080.
   - **Fix:** Provider health now probes `/health` and reports yellow when the configured Wan server is unreachable.

6. **Medium — Provider health omitted moderation**
   - **Evidence:** `moderation` was listed in required capabilities but absent from the capability rows returned by `/api/v1/system/providers`.
   - **Fix:** Moderation is now included in the health report and startup provider rows.

### Open / environment-dependent

7. **High — Image production cannot be verified until a ComfyUI checkpoint is installed**
   - **Impact:** The ComfyUI server and node preflight work, but no image can be rendered from the default workflow.
   - **Solution:** Install a legally obtained, compatible checkpoint at `.local/ComfyUI/models/checkpoints/sd_xl_base_1.0.safetensors` or update the workflow/checkpoint setting to an installed model, then run an actual txt2img and identity-locked img2img smoke test.

8. **High — Video production cannot be verified because no Wan-compatible server is running**
   - **Impact:** Image-to-video and text-to-video remain unavailable.
   - **Solution:** Start a local Wan-compatible service implementing `/health`, `/generate/image_to_video`, `/generate/text_to_video`, `/status/{job_id}`, and `/download/{job_id}`, then rerun the provider and browser media checks.

9. **High — Moderation is not fully local**
   - **Impact:** The application fails closed without a Hugging Face token and still depends on a hosted inference endpoint.
   - **Solution:** Keep the current fail-closed behavior for production safety, or add a documented local moderation adapter and make the provider choice explicit in `.env`.

10. **Medium — Local LoRA training can download a base model on first use**
    - **Impact:** First training can be slow or fail without network/cache availability.
    - **Solution:** Add a preflight that checks the base model cache and reports the required disk/network prerequisites before a training job starts.

11. **Medium — Training fallback can create synthetic images when the dataset is empty**
    - **Impact:** A first-time user could interpret a completed job as training on their own data when no dataset was supplied.
    - **Solution:** Make an empty dataset a hard validation error, or label any generated fallback artifacts prominently as non-training fixtures.

12. **Low — Pydantic v2 deprecation warnings**
    - **Evidence:** Backend tests pass with 14 warnings for class-based `Config`.
    - **Solution:** Migrate settings and response schemas to `ConfigDict` before upgrading to Pydantic v3.

13. **Low — ComfyUI optional package warnings**
    - **Evidence:** ComfyUI logs mention missing workflow-template and embedded-doc packages; the server and required-node preflight still pass.
    - **Solution:** Install the optional packages if the richer ComfyUI template/documentation UI is required; otherwise document them as non-blocking.

14. **Low — Development process cleanup can invalidate the Next bundle**
    - **Evidence:** Running `next build` while a dev server was serving the same `.next` directory recreated missing-chunk HTTP 500 errors.
    - **Solution:** Use separate build/dev cache directories or stop the dev server before production builds; the local launcher should own process lifecycle and prevent duplicate app instances.

15. **Fixed — DashScope image authorization header**
    - **Evidence:** Cloud image requests used a literal placeholder instead of the configured bearer token.
    - **Fix:** Both DashScope image request paths now send the configured API key as a bearer token.

16. **Fixed — Identity QA fabricated approval and scores**
    - **Evidence:** Missing evaluator fields defaulted to approved, high scores, and hard-coded image counts.
    - **Fix:** Missing structured evaluator fields now produce a blocked/failed QA result with zero score and no fabricated counts.

17. **Fixed — Analytics route import failure**
    - **Evidence:** Manual analytics referenced `ManualAnalyticsInput` without importing or defining it in the analytics module.
    - **Fix:** Added the shared input schema and imported it into the route module.

18. **Fixed — Dashboard health counted optional integrations**
    - **Evidence:** Optional Instagram configuration affected the required service total.
    - **Fix:** Dashboard online/total now counts only required capabilities; optional integrations remain visible in the detailed checks.

## Safety and production-readiness notes

- The application remains real-provider-only and does not silently fall back to mock media.
- Identity, consent, disclosure, moderation, and rights controls should remain enabled for every monetized publishing path.
- Only use datasets and likenesses with documented rights and consent. Do not train on scraped creator-platform material.
- Actual image/video quality is not signed off until a compatible local checkpoint and Wan-compatible video server have been exercised end to end.
- Five consecutive post-fix UI/provider loops passed. A real local macOS voice WAV was produced successfully. Image generation remains blocked by the missing ComfyUI checkpoint, and video generation remains blocked by the absent Wan-compatible server; neither is represented as successful.
