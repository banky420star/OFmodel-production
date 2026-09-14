"""One-shot: log into Instagram with a stored account's credentials, persist the
FULL session cookie set back to the account row, and screenshot the profile.

Proof-of-life for an account created by auto-signup — normal browser login,
no scraping.
"""
import asyncio
import base64
import json
import sqlite3
import sys

sys.path.insert(0, "/Users/bank/Downloads/Gemma_Local_Agent_v3_MODEL_FIX/apps/api")

DB_PATH = "/Users/bank/Downloads/Gemma_Local_Agent_v3_MODEL_FIX/apps/api/persona_studio.db"
ACCOUNT_ID = "053b4910-b14e-4c91-ae0b-ed678bcda844"
USERNAME = "trainerpath.official"
PROFILE_URL = f"https://www.instagram.com/{USERNAME}/"


async def main() -> int:
    from playwright.async_api import async_playwright

    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        "SELECT password_hash, metadata_json FROM social_accounts WHERE id = ?",
        (ACCOUNT_ID,),
    )
    row = cur.fetchone()
    if not row:
        print("ACCOUNT_NOT_FOUND")
        return 1
    stored_pw_hash, metadata_raw = row
    password = base64.b64decode(stored_pw_hash or "").decode()
    metadata = json.loads(metadata_raw or "{}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            timezone_id="America/New_York",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        page = await context.new_page()

        await page.goto("https://www.instagram.com/accounts/login/",
                        wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(3)
        await page.screenshot(path=f"/tmp/ig_login_proof_0_{USERNAME}.png")

        # Cookie/consent banner if present
        for label in ("Allow all cookies", "Allow", "Accept"):
            try:
                btn = page.get_by_role("button", name=label)
                if await btn.count() > 0:
                    await btn.first.click()
                    await asyncio.sleep(1)
                    break
            except Exception:
                pass

        # Username field: name attr varies by build — try in order
        user_sel = None
        for sel in ('input[name="username"]',
                    'input[aria-label*="username" i]',
                    'input[aria-label*="Phone" i]',
                    'form input[name="username"]',
                    'form input[type="text"]'):
            loc = page.locator(sel)
            if await loc.count() > 0:
                user_sel = sel
                break
        if not user_sel:
            body = (await page.inner_text("body"))[:300]
            print(json.dumps({
                "error": "login form not found",
                "url": page.url,
                "body_excerpt": body.replace("\n", " ")[:200],
            }))
            await page.screenshot(path=f"/tmp/ig_login_proof_noform_{USERNAME}.png")
            await browser.close()
            return 3

        await page.locator(user_sel).fill(USERNAME)
        await page.locator('input[type="password"]').first.fill(password)
        await asyncio.sleep(0.5)
        await page.locator('button[type="submit"], div[role="button"]:has-text("Log in")').first.click()

        # Wait for the session cookie (real login) up to ~30s
        logged_in = False
        for _ in range(10):
            await asyncio.sleep(3)
            names = {c["name"] for c in await context.cookies()}
            if "sessionid" in names:
                logged_in = True
                break

        # Dismiss save-login / notifications dialogs if they appear
        for label in ("Not now", "Not Now"):
            try:
                btn = page.get_by_role("button", name=label)
                if await btn.count() > 0:
                    await btn.first.click()
                    await asyncio.sleep(1)
            except Exception:
                pass

        cookies = await context.cookies()
        await page.screenshot(path=f"/tmp/ig_login_proof_1_{USERNAME}.png")

        if not logged_in:
            body = (await page.inner_text("body"))[:300]
            await page.screenshot(path=f"/tmp/ig_login_proof_failed_{USERNAME}.png")
            print(json.dumps({
                "logged_in": False,
                "url": page.url,
                "body_excerpt": body.replace("\n", " ")[:200],
            }))
            await browser.close()
            return 2

        # Visit the profile and screenshot it as the logged-in account
        profile_ok = False
        try:
            await page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(4)
            body = (await page.inner_text("body")).lower()
            profile_ok = "sorry, this page isn" not in body and "login" not in page.url
        except Exception as exc:
            print(f"profile_visit_error: {str(exc)[:120]}")
        await page.screenshot(path=f"/tmp/ig_login_proof_profile_{USERNAME}.png")

        # Persist the FULL session back to the account row
        metadata["session_cookies_full"] = cookies
        con.execute(
            "UPDATE social_accounts SET metadata_json = ?, api_connected = 1 WHERE id = ?",
            (json.dumps(metadata), ACCOUNT_ID),
        )
        con.commit()
        con.close()

        print(json.dumps({
            "logged_in": True,
            "profile_ok": profile_ok,
            "cookie_names": sorted({c["name"] for c in cookies}),
            "final_url": page.url,
        }, indent=2))
        await browser.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
