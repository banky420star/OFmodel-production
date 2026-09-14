"""One-shot FULLY-AUTOMATED visible Instagram signup.

Robot does EVERYTHING in one visible browser session:
  1. Fill the signup form (real mail.tm identity from the account row)
  2. Submit
  3. Poll the real inbox for the 6-digit code
  4. TYPE the code into the still-open page
  5. Land logged-in, save FULL session cookies + proof screenshots

Run from a real Terminal.app tab so Playwright owns a proper event loop.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/Users/bank/Downloads/Gemma_Local_Agent_v3_MODEL_FIX/apps/api")

DB_PATH = "/Users/bank/Downloads/Gemma_Local_Agent_v3_MODEL_FIX/apps/api/persona_studio.db"
ACCOUNT_ID = sys.argv[1] if len(sys.argv) > 1 else ""
STATUS_PATH = Path(os.environ.get("IG_FULLAUTO_STATUS_FILE", "/tmp/ig_fullauto_status.json"))


def write_status(**kw) -> None:
    """Live progress for the Studio UI (routes read this file)."""
    try:
        payload = {"account_id": ACCOUNT_ID, "updated_at": datetime.now(timezone.utc).isoformat(), **kw}
        STATUS_PATH.write_text(json.dumps(payload))
    except Exception:
        pass


def load_account(account_id: str) -> dict:
    import sqlite3
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM social_accounts WHERE id = ?", (account_id,)
    ).fetchone()
    con.close()
    if not row:
        raise SystemExit(f"Account {account_id} not found")
    return dict(row)


def extract_code(text: str):
    m = re.search(r"\b(\d{6})\b", text or "")
    return m.group(1) if m else None


async def poll_code(token: str, deadline_s: float = 120) -> str | None:
    from app.providers.email import fetch_emails, fetch_email_detail

    loop = asyncio.get_event_loop()
    end = loop.time() + deadline_s
    while loop.time() < end:
        try:
            emails = await fetch_emails(token)
            for e in emails:
                subj = e.get("subject", "")
                if "instagram" in subj.lower() and "code" in subj.lower():
                    code = extract_code(subj)
                    if not code:
                        detail = await fetch_email_detail(token, e["id"])
                        body = detail.get("text") or ""
                        if isinstance(detail.get("html"), list):
                            body += " " + " ".join(detail["html"])
                        code = extract_code(body)
                    if code:
                        return code
        except Exception as exc:
            print(f"  inbox poll error: {exc}")
        await asyncio.sleep(5)
    return None


async def type_code(page, code: str) -> bool:
    """Instagram's code input is a custom overlay — click then keyboard-type."""
    try:
        label = page.locator('div:has-text("Confirmation code")').last
        await label.click(timeout=5000)
        await asyncio.sleep(1)
        await page.keyboard.type(code, delay=120)
        return True
    except Exception as exc:
        print(f"  click-then-type failed ({str(exc)[:60]}), trying selectors")
    for sel in [
        'input[name="confirmation_code"]',
        'input[inputmode="numeric"]',
        'input[autocomplete*="one-time"]',
        'input[type="tel"]',
    ]:
        loc = page.locator(sel)
        if await loc.count() > 0:
            try:
                await loc.first.fill(code, timeout=5000)
                return True
            except Exception:
                continue
    return False


async def main():
    from playwright.async_api import async_playwright

    acc = load_account(ACCOUNT_ID)
    email = acc["email"]
    token = acc.get("email_token") or (acc.get("metadata_json") or {}).get("email_token", "")
    password = base64.b64decode(acc.get("password_hash") or "").decode()
    username = acc["username"]
    display_name = acc.get("display_name") or username

    if not email or not token or not password:
        raise SystemExit(f"Account missing email/token/password: email={bool(email)} token={bool(token)} pw={bool(password)}")

    proof = Path(f"/Users/bank/Downloads/Gemma_Local_Agent_v3_MODEL_FIX/apps/api/storage/signup_proofs/{username}_fullauto")
    proof.mkdir(parents=True, exist_ok=True)

    write_status(
        state="launching", stage="Opening a visible Chrome window", progress=0,
        username=username, email=email,
    )
    print(f"FULL-AUTO visible signup for @{username} / {email}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--window-size=1280,900",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 860},
            locale="en-US",
            timezone_id="America/New_York",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "delete navigator.__proto__.webdriver;"
        )
        page = await context.new_page()
        result = {"success": False}

        write_status(state="running", stage="Loading Instagram signup page", progress=5)

        try:
            await page.goto(
                "https://www.instagram.com/accounts/emailsignup/",
                wait_until="domcontentloaded",
                timeout=45000,
            )
            await asyncio.sleep(3)
            try:
                accept = page.locator("button:has-text('Allow'), button:has-text('Accept')")
                if await accept.count() > 0:
                    await accept.first.click()
                    await asyncio.sleep(1.5)
            except Exception:
                pass

            # ── fill form ──
            write_status(state="running", stage="Filling signup form (email, password, birthday, name, username)", progress=10)
            texts = page.locator('input[type="text"]')
            await texts.nth(0).fill(email)
            await asyncio.sleep(0.4)
            await page.locator('input[type="password"]').first.fill(password)
            await asyncio.sleep(0.4)

            import random
            months = ["January","February","March","April","May","June","July",
                      "August","September","October","November","December"]
            month = random.choice(months); day = str(random.randint(1, 28)); year = str(random.randint(1995, 2000))
            for box, val in [("Month", month), ("Day", day), ("Year", year)]:
                await page.locator(f'[role="combobox"]:has-text("{box}")').click()
                await asyncio.sleep(0.5)
                await page.get_by_role("option", name=val, exact=(box != "Month")).click()
                await asyncio.sleep(0.3)

            await texts.nth(1).fill(display_name)
            await asyncio.sleep(0.4)
            await page.locator('input[aria-label="Username"]').fill(username)
            await asyncio.sleep(1)
            await page.screenshot(path=str(proof / "1_form_filled.png"))

            write_status(state="running", stage="Submitting form", progress=30)

            submit = page.get_by_role("button", name="Submit")
            if await submit.count() > 0:
                await submit.click()
            else:
                btn = page.locator('button[type="submit"]')
                if await btn.count() > 0:
                    await btn.first.click()
                else:
                    await page.keyboard.press("Enter")
            print("  form submitted; polling inbox for the code…")
            write_status(state="running", stage="Form submitted — polling the real inbox for the verification code", progress=40)
            await asyncio.sleep(4)
            await page.screenshot(path=str(proof / "2_after_submit.png"))

            # ── poll real inbox and type the code ──
            code = await poll_code(token, 150)
            if not code:
                write_status(state="failed", stage="No code arrived within 150s", progress=100)
                raise SystemExit("No code arrived within 150s — inbox: mail.tm webmail for " + email)
            print(f"  code received: {code} — typing it into the live page")
            write_status(state="running", stage=f"Verification code {code} received — typing it into the live page", progress=70, code=code)

            body_text = (await page.inner_text("body")).lower()
            if not ("code" in body_text or "confirm" in body_text):
                print(f"  NOTE: page doesn't look like the code screen ({page.url}) — typing anyway")

            ok = await type_code(page, code)
            if not ok:
                write_status(state="failed", stage="Code arrived but the input never became editable", progress=100)
                await page.screenshot(path=str(proof / "3_code_entry_failed.png"))
                raise SystemExit("Code arrived but the input never became editable")
            await page.screenshot(path=str(proof / "4_code_typed.png"))

            clicked = False
            for sel in [
                'div[role="button"]:has-text("Next")',
                'div[role="button"]:has-text("Continue")',
                'button:has-text("Next")',
                'button:has-text("Continue")',
                'button[type="submit"]',
            ]:
                loc = page.locator(sel)
                if await loc.count() > 0:
                    await loc.first.click()
                    clicked = True
                    break
            if not clicked:
                await page.keyboard.press("Enter")

            # ── wait for the authenticated session ──
            write_status(state="running", stage="Code submitted — waiting for Instagram to issue the session", progress=80)
            for _ in range(15):
                await asyncio.sleep(3)
                names = {c["name"] for c in await context.cookies()}
                if "sessionid" in names:
                    break

            cookies = await context.cookies()
            logged_in = any(c["name"] == "sessionid" for c in cookies)
            await page.screenshot(path=str(proof / "5_after_code.png"))
            final_url = page.url
            suspended = "suspended" in final_url
            if not suspended and logged_in:
                try:
                    body = (await page.inner_text("body"))[:1200].lower()
                    suspended = "suspended" in body or "account has been disabled" in body
                except Exception:
                    pass

            profile_url = ""
            if logged_in and not suspended:
                profile_url = f"https://www.instagram.com/{username}/"
                try:
                    await page.goto(profile_url, wait_until="domcontentloaded", timeout=25000)
                    await asyncio.sleep(3)
                    b = (await page.inner_text("body"))[:600].lower()
                    if "sorry, this page isn" in b or "page isn" in b or "suspended" in b:
                        profile_url = ""
                    await page.screenshot(path=str(proof / "6_profile.png"))
                except Exception:
                    pass

            result = {
                "success": logged_in,
                "status": "suspended" if (logged_in and suspended) else ("completed" if logged_in else "no_session"),
                "username": username,
                "email": email,
                "profile_url": profile_url,
                "final_url": final_url,
                "cookie_names": [c["name"] for c in cookies],
                "screenshot": str(proof / "5_after_code.png"),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
            (proof / "result.json").write_text(json.dumps(result, indent=2))
            write_status(
                state="done" if result["success"] else "failed",
                stage=("Authenticated session captured — profile: " + (result.get("profile_url") or "(not public)"))
                if result["success"] else f"Finished without a session ({result.get('status')})",
                progress=100, result=result["status"], profile_url=result.get("profile_url", ""),
                screenshot=result.get("screenshot", ""),
            )
            print(json.dumps(result, indent=2))

            # ── persist to the account row ──
            import sqlite3
            try:
                prior_meta = json.loads(acc.get("metadata_json") or "{}")
                if not isinstance(prior_meta, dict):
                    prior_meta = {}
            except Exception:
                prior_meta = {}
            con = sqlite3.connect(DB_PATH)
            status = "suspended" if (logged_in and suspended) else ("active" if logged_in else "pending_approval")
            con.execute(
                "UPDATE social_accounts SET status=?, signup_step=?, profile_url=?, approval_notes=?, metadata_json=? WHERE id=?",
                (
                    status,
                    result["status"],
                    profile_url,
                    result.get("status", "") + ": " + ("full-auto signup run" if logged_in else "no session landed"),
                    json.dumps({**prior_meta,
                                "fullauto_result": result["status"],
                                "fullauto_finished_at": result["finished_at"],
                                "session_cookies": cookies if logged_in else []}),
                    ACCOUNT_ID,
                ),
            )
            con.commit()
            con.close()

            await asyncio.sleep(6)
            await browser.close()
        except SystemExit:
            raise
        except Exception as exc:
            write_status(state="failed", stage=f"Error: {str(exc)[:200]}", progress=100)
            try:
                await page.screenshot(path=str(proof / "error.png"))
                await browser.close()
            except Exception:
                pass
            print(f"ERROR: {exc}")
            sys.exit(1)

        if not result.get("success"):
            sys.exit(2)


asyncio.run(main())
