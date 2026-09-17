# Instagram automation — what actually exists

## Status: signup automates up to the email-verification wall. No live profile exists yet.

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
