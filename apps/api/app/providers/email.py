"""
Email provider using mail.tm — free temporary email API.

Creates disposable email addresses for social media account signups.
No API key required. Completely free.

Flow:
1. Get available domains from mail.tm
2. Create an email account (address + password)
3. Use the email to sign up on social platforms
4. Fetch verification emails to complete registration
"""

import httpx
import random
import string
import hashlib
from typing import Optional
from dataclasses import dataclass


MAIL_TM_BASE = "https://api.mail.tm"


@dataclass
class TempEmail:
    address: str
    password: str
    account_id: str
    token: str
    domain: str


async def get_available_domains() -> list[str]:
    """Get available email domains from mail.tm."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{MAIL_TM_BASE}/domains")
        resp.raise_for_status()
        data = resp.json()
        # data is {"hydra:member": [{"domain": "...", "id": "..."}]}
        members = data.get("hydra:member", data) if isinstance(data, dict) else data
        return [d["domain"] for d in members if d.get("isActive", True)]


async def create_temp_email(persona_name: str, prefix: str = "") -> TempEmail:
    """Create a temporary email address for a persona.
    
    Generates a realistic-looking email like:
    - ava.luxury@domain.com
    - noor.designs@domain.com
    - zara.lux@domain.com
    """
    domains = await get_available_domains()
    if not domains:
        raise Exception("No email domains available from mail.tm")
    
    domain = random.choice(domains)
    
    # Build username from persona name
    clean_name = persona_name.lower().replace(" ", ".")
    clean_name = "".join(c for c in clean_name if c.isalnum() or c in "._-")
    
    # Add random suffix to avoid collisions
    suffix = "".join(random.choices(string.digits, k=4))
    username = f"{clean_name}.{suffix}" if not prefix else f"{prefix}.{suffix}"
    
    address = f"{username}@{domain}"
    
    # Generate a simple memorable password
    password = f"{clean_name}{random.randint(1000,9999)}!"
    
    # Create account on mail.tm (retry up to 3 times)
    async with httpx.AsyncClient(timeout=15) as client:
        for attempt in range(3):
            resp = await client.post(
                f"{MAIL_TM_BASE}/accounts",
                json={"address": address, "password": password},
            )
            if resp.status_code == 422:
                # Username taken, try with different suffix
                suffix = "".join(random.choices(string.digits, k=6))
                address = f"{clean_name}.{suffix}@{domain}"
                continue
            resp.raise_for_status()
            break
        else:
            raise Exception(f"Failed to create email after 3 attempts")
        
        account_data = resp.json()
        account_id = account_data.get("id", "")
        # mail.tm normalizes the address (strips dots), use the returned one
        actual_address = account_data.get("address", address)
        
        # Get auth token using the actual address from mail.tm
        token_resp = await client.post(
            f"{MAIL_TM_BASE}/token",
            json={"address": actual_address, "password": password},
        )
        token_resp.raise_for_status()
        token = token_resp.json().get("token", "")
        
        return TempEmail(
            address=actual_address,
            password=password,
            account_id=account_id,
            token=token,
            domain=domain,
        )


async def fetch_emails(token: str) -> list[dict]:
    """Fetch all emails in the inbox."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{MAIL_TM_BASE}/messages",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        data = resp.json()
        messages = data.get("hydra:member", []) if isinstance(data, dict) else data
        return [
            {
                "id": m.get("id", ""),
                "from": m.get("from", {}),
                "subject": m.get("subject", ""),
                "intro": m.get("intro", ""),
                "created_at": m.get("createdAt", ""),
            }
            for m in messages
        ]


async def fetch_email_detail(token: str, message_id: str) -> dict:
    """Fetch full email content (HTML/text body)."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{MAIL_TM_BASE}/messages/{message_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()
