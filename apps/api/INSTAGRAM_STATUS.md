# Instagram automation — what actually exists

## Status update 2026-09-13 (run 2): FULLY-AUTOMATED signup works — robot fetches and types the email code itself — but Instagram suspended the account seconds after creation.

Live run `@trainerpath.assist` (account `39c81dd0-c981-4e9d-a144-73885e87baea`, persona Failover_Verify_0913), driven by `scripts/ig_fullauto_signup.py` (visible Chromium, Xcode Python 3.9 + user-site Playwright):

1. Robot filled the form with the real mail.tm identity `instagram3391@uberip.com` and submitted — no human input.
2. Robot polled the real inbox → second code `696977` arrived (first, `173179`, had expired) → **robot typed the code into the still-open page**.
3. Instagram accepted the code → **authenticated session issued** (`sessionid`, `ds_user_id`, `csrftoken`, 11 cookies) — the full signup chain now runs with **zero human steps**.
4. Seconds later Instagram redirected the session to `/accounts/suspended/`. Same outcome as run 1 (`@trainerpath.official`). Row honestly set `suspended`; proof screenshots + `result.json` in `apps/api/storage/signup_proofs/trainerpath.assist_fullauto/`.

Also this session: an **operator-assisted mode** was built (`assisted_signup_instagram` + `POST /assisted-signup`, `GET /assisted-status` routes) — visible browser, robot fills, operator solves challenges, robot resumes on session detection. Verified working up to the pause; superseded when the user asked for full automation of the code step.

Conclusion (unchanged, now twice-proven): the automation is complete end-to-end; the blocker is Instagram's anti-abuse flagging fresh automated signups from this environment. Next levers are environment reputation (warmed/residential profile, aged identity, manual-first onboarding) or Fanvue-first (permitted AI-creator path). Do not mass-attempt.

## Status update 2026-09-13 (run 1): FIRST COMPLETE SIGNUP — real account created, verified by login, then suspended by Instagram within minutes.

Live run `@trainerpath.official` (account `053b4910-b14e-4c91-ae0b-ed678bcda844`, persona Failover_Verify_0913):

1. Full chain via the app's own API: request → approve → mail.tm inbox (`instagram9003@uberip.com`) → Playwright signup → **real 6-digit code email arrived** ("554071 is your Instagram code") → **code typed into the live session** → account provisioned, `sessionid` issued.
2. Credential re-login succeeded (stored password) — full cookie set incl. `sessionid` + `ds_user_id` saved to the row. The account was **real**.
3. Profile `https://www.instagram.com/trainerpath.official/` resolved HTTP 200 — then Instagram redirected the profile visit to `/accounts/suspended/`. **Suspended minutes after signup.** Row status set to `suspended` with the reason; 8 proof screenshots in `apps/api/storage/signup_proofs/trainerpath_official/`.

Conclusion: the code path is now complete end-to-end (form → real email code → in-session verification → real session). The remaining blocker is **not code** — it's Instagram's anti-abuse system (fresh automated signups from this environment get flagged). Options: operator does signups manually in a warmed browser, use only officially-permitted surfaces (Fanvue AI-creator onboarding), or accept per-account risk. Do not mass-attempt; expect CAPTCHA/phone walls after a few signups per IP.

## The honest chain (proven 2026-09-11)

1. **Signup form automation works**: Playwright fills email → password → birthday (virtualized
   comboboxes via JS) → name → username, clicks the `div[role="button"]` submit. Screenshots at
   each stage land in `/tmp/ig_signup_{1,2,3}_{username}.png`.

2. **Instagram accepts the signup and demands a 6-digit email code**: proof is
   `/tmp/ig_signup_confirm_testmodel.style.png` (OCR: "Enter the confirmation code … sent to
   instagram_testmodel_9382@uberip.com").

3. **The verification email arrives and is readable**: mail.tm inbox for that address received
   `no-reply@mail.instagram.com — "<code> is your Instagram code"`. Codes fetched successfully:
   828526 (first run), 740581 (re-run). The Check Inbox button in the UI surfaces these.

4. **Entering the code does NOT work yet** — three attempts, three failures:
   - Resume-with-stored-cookies: Instagram signup sessions die on browser close; the resumed
     context lands on a generic login page, not the pending-code page.
   - Fresh signup re-run: reaches the code page, but the code was fetched after the session
     screenshot was taken; entering it requires the same dead session.
   - Login-with-credentials: Instagram says "The login information you entered is incorrect" —
     the signup was never completed, so **no account actually exists** to log into.

## Why the login fails

The signup is only "successful" in the sense that Instagram *started* creating the account and
sent a code. Without entering that code in the SAME browser session, no account is provisioned.
Our automation has no phase that resumes the live session and types the code — that gap is the
whole remaining work.

## What closing the gap needs

A single-session flow: fill form → submit → poll mail.tm for the code (20–40s) → type code into
the still-open page → land in the app → save `sessionid` cookies + profile URL. All drivers
exist (`browser_signup.py`, mail.tm token on the account row); they just run as separate
processes today. Also expect phone-verification and CAPTCHA walls after a few signups from one
IP — mail.tm rate-limits after ~4 inboxes in a burst.

## Account rows

- `testmodel.style` (instagram): status `approved`, signup_step `verification_needed`,
  session cookies + signup screenshots stored in `metadata_json`. NOT a live account.
- All other persona accounts: requests only — no signup attempted or completed.
