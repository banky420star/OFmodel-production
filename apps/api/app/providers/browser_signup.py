"""
Browser automation service for social platform signups.

Uses Playwright to automate Instagram, Facebook, and TikTok account creation
with real email addresses from mail.tm.
"""

import asyncio
import logging
import random
import string
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SignupResult:
    success: bool
    platform: str
    username: str
    email: str
    status: str  # "completed", "captcha_blocked", "error", "verification_needed"
    message: str
    screenshot_path: str = ""
    session_cookies: list = field(default_factory=list)


def _random_name() -> str:
    """Generate a realistic-sounding first name."""
    names = [
        "Emma", "Olivia", "Ava", "Isabella", "Sophia", "Mia", "Charlotte",
        "Amelia", "Harper", "Evelyn", "Abigail", "Emily", "Ella", "Elizabeth",
        "Camila", "Luna", "Sofia", "Aria", "Scarlett", "Penelope", "Layla",
        "Chloe", "Victoria", "Madison", "Eleanor", "Grace", "Nora", "Riley",
        "Zoey", "Hannah", "Hazel", "Lily", "Ellie", "Stella", "Natalie",
    ]
    return random.choice(names)


def _random_username(base: str) -> str:
    """Generate a username from a base with random suffix."""
    suffix = "".join(random.choices(string.digits, k=random.randint(3, 5)))
    return f"{base.lower().replace(' ', '_').replace('.', '')}{suffix}"


async def signup_instagram(
    email: str,
    password: str,
    display_name: str,
    username: str,
    *,
    headless: bool = True,
    timeout_ms: int = 30000,
) -> SignupResult:
    """
    Automate Instagram account signup.

    Flow:
    1. Navigate to instagram.com/accounts/emailsignup/
    2. Fill in email, full name, username, password
    3. Submit form
    4. Handle any CAPTCHA or phone verification
    5. Return result
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return SignupResult(
            success=False,
            platform="instagram",
            username=username,
            email=email,
            status="error",
            message="Playwright not installed. Run: pip install playwright && playwright install chromium",
        )

    logger.info(f"Starting Instagram signup for @{username} ({email})")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
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
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="America/New_York",
        )

        # Remove webdriver detection
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            delete navigator.__proto__.webdriver;
        """)

        page = await context.new_page()

        try:
            # Navigate to signup page
            await page.goto(
                "https://www.instagram.com/accounts/emailsignup/",
                wait_until="networkidle",
                timeout=timeout_ms,
            )
            await asyncio.sleep(2)

            # Take screenshot of initial state
            screenshot_path = f"/tmp/ig_signup_1_{username}.png"
            await page.screenshot(path=screenshot_path)

            # Check if we hit a cookie/consent dialog
            try:
                accept_btn = page.locator("button:has-text('Allow'), button:has-text('Accept'), button:has-text('OK')")
                if await accept_btn.count() > 0:
                    await accept_btn.first.click()
                    await asyncio.sleep(1)
            except Exception:
                pass

            # Instagram form: email, password, birthday (comboboxes), full name, username
            import random as _random

            # Fill email (text input 0)
            text_inputs = page.locator('input[type="text"]')
            if await text_inputs.count() >= 1:
                await text_inputs.nth(0).fill(email)
                await asyncio.sleep(0.3)

            # Fill password
            password_input = page.locator('input[type="password"]')
            if await password_input.count() > 0:
                await password_input.first.fill(password)
                await asyncio.sleep(0.3)

            # Birthday — Instagram uses custom combobox dropdowns
            months = ['January','February','March','April','May','June',
                      'July','August','September','October','November','December']
            month = _random.choice(months)
            day = str(_random.randint(1, 28))
            year = str(_random.randint(1995, 2000))

            try:
                await page.locator('[role="combobox"]:has-text("Month")').click()
                await asyncio.sleep(0.5)
                await page.get_by_role("option", name=month).click()
                await asyncio.sleep(0.3)

                await page.locator('[role="combobox"]:has-text("Day")').click()
                await asyncio.sleep(0.5)
                await page.get_by_role("option", name=day, exact=True).click()
                await asyncio.sleep(0.3)

                await page.locator('[role="combobox"]:has-text("Year")').click()
                await asyncio.sleep(0.5)
                await page.get_by_role("option", name=year, exact=True).click()
                await asyncio.sleep(0.3)
            except Exception as e:
                logger.warning(f"Birthday selection failed: {e}")

            # Fill full name (text input 1)
            if await text_inputs.count() >= 2:
                await text_inputs.nth(1).fill(display_name)
                await asyncio.sleep(0.3)

            # Fill username
            username_input = page.locator('input[aria-label="Username"]')
            if await username_input.count() > 0:
                await username_input.fill(username)
                await asyncio.sleep(1)

            # Take screenshot after filling
            screenshot_path = f"/tmp/ig_signup_2_{username}.png"
            await page.screenshot(path=screenshot_path)

            # Click Submit — Instagram uses div[role="button"], not <button>
            submit = page.get_by_role("button", name="Submit")
            if await submit.count() > 0:
                await submit.click()
            else:
                # Fallback: try regular button
                signup_btn = page.locator('button[type="submit"]')
                if await signup_btn.count() > 0:
                    await signup_btn.first.click()
                else:
                    await page.keyboard.press("Enter")
            await asyncio.sleep(5)

            # Take screenshot after submission
            screenshot_path = f"/tmp/ig_signup_3_{username}.png"
            await page.screenshot(path=screenshot_path)

            # Check for CAPTCHA
            captcha = page.locator('iframe[src*="captcha"], .captcha, [data-testid="captcha"], div:has-text("Suspicious activity")')
            if await captcha.count() > 0:
                logger.warning(f"CAPTCHA detected for @{username}")
                screenshot_path = f"/tmp/ig_signup_captcha_{username}.png"
                await page.screenshot(path=screenshot_path)
                cookies = await context.cookies()
                await browser.close()
                return SignupResult(
                    success=False,
                    platform="instagram",
                    username=username,
                    email=email,
                    status="captcha_blocked",
                    message="CAPTCHA detected — manual intervention required",
                    screenshot_path=screenshot_path,
                    session_cookies=cookies,
                )

            # Check for phone verification
            phone = page.locator('input[name="phoneNumber"], input[aria-label*="phone" i], div:has-text("Enter your phone number")')
            if await phone.count() > 0:
                screenshot_path = f"/tmp/ig_signup_phone_{username}.png"
                await page.screenshot(path=screenshot_path)
                cookies = await context.cookies()
                await browser.close()
                return SignupResult(
                    success=False,
                    platform="instagram",
                    username=username,
                    email=email,
                    status="verification_needed",
                    message="Phone verification required",
                    screenshot_path=screenshot_path,
                    session_cookies=cookies,
                )

            # Check for "Confirm your email" page
            confirm = page.locator('div:has-text("confirm"), div:has-text("Confirm"), div:has-text("verification code")')
            if await confirm.count() > 0:
                screenshot_path = f"/tmp/ig_signup_confirm_{username}.png"
                await page.screenshot(path=screenshot_path)
                cookies = await context.cookies()
                await browser.close()
                return SignupResult(
                    success=True,
                    platform="instagram",
                    username=username,
                    email=email,
                    status="verification_needed",
                    message="Signup successful — email verification pending",
                    screenshot_path=screenshot_path,
                    session_cookies=cookies,
                )

            # Check current URL for success indicators
            current_url = page.url
            if "accounts/signup" not in current_url:
                # We navigated away from signup — likely success
                screenshot_path = f"/tmp/ig_signup_success_{username}.png"
                await page.screenshot(path=screenshot_path)
                cookies = await context.cookies()
                await browser.close()
                return SignupResult(
                    success=True,
                    platform="instagram",
                    username=username,
                    email=email,
                    status="verification_needed",
                    message="Signup submitted — check email for verification",
                    screenshot_path=screenshot_path,
                    session_cookies=[c for c in cookies],
                )

            # If still on signup page, something went wrong
            page_text = await page.inner_text("body")
            screenshot_path = f"/tmp/ig_signup_fail_{username}.png"
            await page.screenshot(path=screenshot_path)
            cookies = await context.cookies()
            await browser.close()

            # Try to extract error message
            error_msg = "Unknown error"
            for line in page_text.split("\n"):
                line = line.strip()
                if "error" in line.lower() or "try again" in line.lower() or "taken" in line.lower():
                    error_msg = line
                    break

            return SignupResult(
                success=False,
                platform="instagram",
                username=username,
                email=email,
                status="error",
                message=f"Signup failed: {error_msg}",
                screenshot_path=screenshot_path,
                session_cookies=cookies,
            )

        except Exception as e:
            logger.error(f"Instagram signup error for @{username}: {e}")
            try:
                screenshot_path = f"/tmp/ig_signup_error_{username}.png"
                await page.screenshot(path=screenshot_path)
            except Exception:
                screenshot_path = ""

            cookies = []
            try:
                cookies = await context.cookies()
            except Exception:
                pass

            await browser.close()
            return SignupResult(
                success=False,
                platform="instagram",
                username=username,
                email=email,
                status="error",
                message=f"Error: {str(e)[:200]}",
                screenshot_path=screenshot_path,
                session_cookies=cookies,
            )


async def signup_facebook(
    email: str,
    password: str,
    display_name: str,
    username: str,
    *,
    headless: bool = True,
    timeout_ms: int = 30000,
) -> SignupResult:
    """
    Automate Facebook account signup.

    Flow:
    1. Navigate to facebook.com
    2. Fill in first name, last name, email, password, birthday
    3. Submit form
    4. Handle verification
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return SignupResult(
            success=False,
            platform="facebook",
            username=username,
            email=email,
            status="error",
            message="Playwright not installed",
        )

    logger.info(f"Starting Facebook signup for @{username} ({email})")

    # Split display name into first/last
    name_parts = display_name.replace(" Official", "").strip().split()
    first_name = name_parts[0] if name_parts else _random_name()
    last_name = name_parts[-1] if len(name_parts) > 1 else "Model"

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )

        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        page = await context.new_page()

        try:
            await page.goto(
                "https://www.facebook.com/r.php?entry_point=desktop",
                wait_until="networkidle",
                timeout=timeout_ms,
            )
            await asyncio.sleep(2)

            # Get all text inputs (Facebook uses unlabeled inputs)
            inputs = page.locator('input[type="text"]')
            input_count = await inputs.count()
            print(f"  Facebook: found {input_count} text inputs")

            # Facebook form order: First name, Surname, Email/Mobile, (Birthday selects), Password
            # Fill first name (input 0)
            if input_count >= 1:
                await inputs.nth(0).fill(first_name)
                await asyncio.sleep(0.3)

            # Fill surname (input 1)
            if input_count >= 2:
                await inputs.nth(1).fill(last_name)
                await asyncio.sleep(0.3)

            # Fill email (input 2)
            if input_count >= 3:
                await inputs.nth(2).fill(email)
                await asyncio.sleep(0.3)

            # Fill password (input[type=password])
            pass_input = page.locator('input[type="password"]')
            if await pass_input.count() > 0:
                await pass_input.first.fill(password)
                await asyncio.sleep(0.3)

            # Set birthday via selects
            selects = page.locator('select')
            select_count = await selects.count()
            print(f"  Facebook: found {select_count} selects")
            # Selects order: Day, Month, Year, Gender
            if select_count >= 1:
                await selects.nth(0).select_option(str(random.randint(1, 28)))  # Day
                await asyncio.sleep(0.2)
            if select_count >= 2:
                await selects.nth(1).select_option(str(random.randint(1, 12)))  # Month
                await asyncio.sleep(0.2)
            if select_count >= 3:
                await selects.nth(2).select_option(str(random.randint(1995, 2000)))  # Year
                await asyncio.sleep(0.2)
            if select_count >= 4:
                await selects.nth(3).select_option('2')  # Female (value=2)
                await asyncio.sleep(0.2)

            await asyncio.sleep(1)

            # Take screenshot
            screenshot_path = f"/tmp/fb_signup_2_{username}.png"
            await page.screenshot(path=screenshot_path)

            # Click Sign Up
            signup_btn = page.locator('button[name="websubmit"], button[type="submit"], button:has-text("Sign Up"), button:has-text("Create account")')
            if await signup_btn.count() > 0:
                await signup_btn.first.click()
                await asyncio.sleep(5)
            else:
                await page.keyboard.press("Enter")
                await asyncio.sleep(5)

            # Screenshot after submit
            screenshot_path = f"/tmp/fb_signup_3_{username}.png"
            await page.screenshot(path=screenshot_path)

            # Check for CAPTCHA
            captcha = page.locator('iframe[src*="captcha"], div:has-text("Suspicious activity"), div:has-text("security check")')
            if await captcha.count() > 0:
                cookies = await context.cookies()
                await browser.close()
                return SignupResult(
                    success=False,
                    platform="facebook",
                    username=username,
                    email=email,
                    status="captcha_blocked",
                    message="CAPTCHA/security check detected",
                    screenshot_path=screenshot_path,
                    session_cookies=cookies,
                )

            # Check for confirmation
            current_url = page.url
            if "confirm" in current_url or "checkpoint" in current_url:
                cookies = await context.cookies()
                await browser.close()
                return SignupResult(
                    success=True,
                    platform="facebook",
                    username=username,
                    email=email,
                    status="verification_needed",
                    message="Signup submitted — email verification pending",
                    screenshot_path=screenshot_path,
                    session_cookies=cookies,
                )

            # Check if we got past signup
            page_text = await page.inner_text("body")
            if "Welcome" in page_text or "account" in page_text.lower():
                cookies = await context.cookies()
                await browser.close()
                return SignupResult(
                    success=True,
                    platform="facebook",
                    username=username,
                    email=email,
                    status="verification_needed",
                    message="Signup successful — check email for verification",
                    screenshot_path=screenshot_path,
                    session_cookies=cookies,
                )

            # Error
            error_msg = "Signup may have failed"
            cookies = await context.cookies()
            await browser.close()
            return SignupResult(
                success=False,
                platform="facebook",
                username=username,
                email=email,
                status="error",
                message=error_msg,
                screenshot_path=screenshot_path,
                session_cookies=cookies,
            )

        except Exception as e:
            logger.error(f"Facebook signup error: {e}")
            try:
                screenshot_path = f"/tmp/fb_signup_error_{username}.png"
                await page.screenshot(path=screenshot_path)
            except Exception:
                screenshot_path = ""
            cookies = []
            try:
                cookies = await context.cookies()
            except Exception:
                pass
            await browser.close()
            return SignupResult(
                success=False,
                platform="facebook",
                username=username,
                email=email,
                status="error",
                message=f"Error: {str(e)[:200]}",
                screenshot_path=screenshot_path,
                session_cookies=cookies,
            )


async def signup_tiktok(
    email: str,
    password: str,
    display_name: str,
    username: str,
    *,
    headless: bool = True,
    timeout_ms: int = 30000,
) -> SignupResult:
    """
    Automate TikTok account signup via web.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return SignupResult(
            success=False,
            platform="tiktok",
            username=username,
            email=email,
            status="error",
            message="Playwright not installed",
        )

    logger.info(f"Starting TikTok signup for @{username} ({email})")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )

        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        page = await context.new_page()

        try:
            await page.goto(
                "https://www.tiktok.com/signup",
                wait_until="networkidle",
                timeout=timeout_ms,
            )
            await asyncio.sleep(3)

            # Click "Use phone or email" if visible
            try:
                email_option = page.locator('div:has-text("Use phone or email"), a:has-text("Use email")')
                if await email_option.count() > 0:
                    await email_option.first.click()
                    await asyncio.sleep(1)
            except Exception:
                pass

            # Fill birthday (must be 18+)
            try:
                for selector, value in [
                    ('select[aria-label*="Month" i]', str(random.randint(1, 12))),
                    ('select[aria-label*="Day" i]', str(random.randint(1, 28))),
                    ('select[aria-label*="Year" i]', str(random.randint(1995, 2000))),
                ]:
                    dd = page.locator(selector)
                    if await dd.count() > 0:
                        await dd.first.select_option(value)
                        await asyncio.sleep(0.3)
            except Exception:
                pass

            # Switch to email signup
            try:
                email_tab = page.locator('div:has-text("Email"), span:has-text("Email"), a:has-text("Email")')
                if await email_tab.count() > 0:
                    await email_tab.first.click()
                    await asyncio.sleep(1)
            except Exception:
                pass

            # Fill email
            email_input = page.locator('input[name="email"], input[type="email"], input[aria-label*="Email" i]')
            if await email_input.count() > 0:
                await email_input.first.fill(email)
                await asyncio.sleep(0.5)

            # Fill username
            username_input = page.locator('input[name="username"], input[aria-label*="Username" i]')
            if await username_input.count() > 0:
                await username_input.first.fill(username)
                await asyncio.sleep(0.5)

            # Fill password
            pass_input = page.locator('input[type="password"], input[name="password"]')
            if await pass_input.count() > 0:
                await pass_input.first.fill(password)
                await asyncio.sleep(1)

            screenshot_path = f"/tmp/tt_signup_2_{username}.png"
            await page.screenshot(path=screenshot_path)

            # Submit
            submit = page.locator('button[type="submit"], button:has-text("Send code"), button:has-text("Sign up")')
            if await submit.count() > 0:
                await submit.first.click()
                await asyncio.sleep(5)
            else:
                await page.keyboard.press("Enter")
                await asyncio.sleep(5)

            screenshot_path = f"/tmp/tt_signup_3_{username}.png"
            await page.screenshot(path=screenshot_path)

            # Check result
            current_url = page.url
            cookies = await context.cookies()

            if "verification" in current_url or "confirm" in current_url:
                await browser.close()
                return SignupResult(
                    success=True,
                    platform="tiktok",
                    username=username,
                    email=email,
                    status="verification_needed",
                    message="Signup submitted — email verification pending",
                    screenshot_path=screenshot_path,
                    session_cookies=cookies,
                )

            # Check for CAPTCHA
            captcha = page.locator('div:has-text("Verify"), iframe[src*="captcha"], div:has-text("Security check")')
            if await captcha.count() > 0:
                await browser.close()
                return SignupResult(
                    success=False,
                    platform="tiktok",
                    username=username,
                    email=email,
                    status="captcha_blocked",
                    message="CAPTCHA detected",
                    screenshot_path=screenshot_path,
                    session_cookies=cookies,
                )

            page_text = await page.inner_text("body")
            await browser.close()

            return SignupResult(
                success=True,
                platform="tiktok",
                username=username,
                email=email,
                status="verification_needed",
                message="Signup flow completed — check email",
                screenshot_path=screenshot_path,
                session_cookies=cookies,
            )

        except Exception as e:
            logger.error(f"TikTok signup error: {e}")
            try:
                screenshot_path = f"/tmp/tt_signup_error_{username}.png"
                await page.screenshot(path=screenshot_path)
            except Exception:
                screenshot_path = ""
            cookies = []
            try:
                cookies = await context.cookies()
            except Exception:
                pass
            await browser.close()
            return SignupResult(
                success=False,
                platform="tiktok",
                username=username,
                email=email,
                status="error",
                message=f"Error: {str(e)[:200]}",
                screenshot_path=screenshot_path,
                session_cookies=cookies,
            )



async def signup_onlyfans(
    email: str,
    password: str,
    display_name: str,
    username: str,
    *,
    headless: bool = True,
    timeout_ms: int = 30000,
) -> SignupResult:
    """
    Automate OnlyFans account signup via web.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return SignupResult(
            success=False,
            platform="onlyfans",
            username=username,
            email=email,
            status="error",
            message="Playwright not installed",
        )

    logger.info(f"Starting OnlyFans signup for @{username} ({email})")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )

        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        page = await context.new_page()

        try:
            await page.goto(
                "https://onlyfans.com/register",
                wait_until="networkidle",
                timeout=timeout_ms,
            )
            await asyncio.sleep(3)

            screenshot_path = f"/tmp/of_signup_1_{username}.png"
            await page.screenshot(path=screenshot_path)

            # Fill email
            email_input = page.locator('input[name="email"], input[type="email"], input[placeholder*="Email" i]')
            if await email_input.count() > 0:
                await email_input.first.fill(email)
                await asyncio.sleep(0.5)

            # Fill username
            username_input = page.locator('input[name="username"], input[placeholder*="Username" i]')
            if await username_input.count() > 0:
                await username_input.first.fill(username)
                await asyncio.sleep(0.5)

            # Fill password
            pass_input = page.locator('input[type="password"], input[name="password"]')
            if await pass_input.count() > 0:
                await pass_input.first.fill(password)
                await asyncio.sleep(1)

            screenshot_path = f"/tmp/of_signup_2_{username}.png"
            await page.screenshot(path=screenshot_path)

            # Submit
            submit = page.locator('button[type="submit"], button:has-text("Register"), button:has-text("Sign Up"), button:has-text("Continue")')
            if await submit.count() > 0:
                await submit.first.click()
                await asyncio.sleep(5)
            else:
                await page.keyboard.press("Enter")
                await asyncio.sleep(5)

            screenshot_path = f"/tmp/of_signup_3_{username}.png"
            await page.screenshot(path=screenshot_path)

            # Check result
            current_url = page.url
            cookies = await context.cookies()

            if "confirm" in current_url or "verify" in current_url or "check" in current_url:
                await browser.close()
                return SignupResult(
                    success=True,
                    platform="onlyfans",
                    username=username,
                    email=email,
                    status="verification_needed",
                    message="Signup submitted — email verification pending",
                    screenshot_path=screenshot_path,
                    session_cookies=cookies,
                )

            # Check for CAPTCHA
            captcha = page.locator('div:has-text("Verify"), iframe[src*="captcha"], div:has-text("Security check")')
            if await captcha.count() > 0:
                await browser.close()
                return SignupResult(
                    success=False,
                    platform="onlyfans",
                    username=username,
                    email=email,
                    status="captcha_blocked",
                    message="CAPTCHA detected",
                    screenshot_path=screenshot_path,
                    session_cookies=cookies,
                )

            page_text = await page.inner_text("body")
            await browser.close()

            return SignupResult(
                success=True,
                platform="onlyfans",
                username=username,
                email=email,
                status="verification_needed",
                message="Signup flow completed — check email",
                screenshot_path=screenshot_path,
                session_cookies=cookies,
            )

        except Exception as e:
            logger.error(f"OnlyFans signup error: {e}")
            try:
                screenshot_path = f"/tmp/of_signup_error_{username}.png"
                await page.screenshot(path=screenshot_path)
            except Exception:
                screenshot_path = ""
            cookies = []
            try:
                cookies = await context.cookies()
            except Exception:
                pass
            await browser.close()
            return SignupResult(
                success=False,
                platform="onlyfans",
                username=username,
                email=email,
                status="error",
                message=f"Error: {str(e)[:200]}",
                screenshot_path=screenshot_path,
                session_cookies=cookies,
            )


# Platform dispatcher
SIGNUP_HANDLERS = {
    "instagram": signup_instagram,
    "facebook": signup_facebook,
    "tiktok": signup_tiktok,
    "onlyfans": signup_onlyfans,
}


async def auto_signup(
    platform: str,
    email: str,
    password: str,
    display_name: str,
    username: str,
    *,
    headless: bool = True,
) -> SignupResult:
    """
    Dispatch signup to the appropriate platform handler.
    """
    handler = SIGNUP_HANDLERS.get(platform.lower())
    if not handler:
        return SignupResult(
            success=False,
            platform=platform,
            username=username,
            email=email,
            status="error",
            message=f"Unsupported platform: {platform}. Supported: {list(SIGNUP_HANDLERS.keys())}",
        )

    return await handler(
        email=email,
        password=password,
        display_name=display_name,
        username=username,
        headless=headless,
    )
