#!/usr/bin/env python3
"""Test DashScope Wan video provider with correct API endpoints."""
import asyncio, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'apps', 'api'))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from app.providers.wan_dashscope import DashScopeWanProvider

API_KEY = os.getenv("WAN_API_KEY", "")

async def main():
    print(f"API key: {API_KEY[:12]}...{API_KEY[-4:]}" if API_KEY else "No API key found!")
    if not API_KEY:
        print("FAIL: No WAN_API_KEY in environment")
        return

    provider = DashScopeWanProvider(api_key=API_KEY)

    # Test 1: Health check
    print("\n=== Health Check ===")
    health = await provider.health_check()
    print(f"Success: {health.success}")
    print(f"Provider: {health.provider}")
    print(f"Data: {health.data}")
    print(f"Error: {health.error}")

    # Test 2: Submit a quick text-to-video task (don't wait for completion)
    print("\n=== Text-to-Video Submit Test ===")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis",
                json={
                    "model": "wan2.1-t2v-t214p",
                    "input": {"prompt": "a cat sitting on a windowsill, sunlight streaming in"},
                    "parameters": {
                        "resolution": "720P",
                        "ratio": "16:9",
                        "duration": 2,
                    },
                },
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json",
                    "X-DashScope-Async": "enable",
                },
            )
            print(f"Status: {resp.status_code}")
            data = resp.json()
            print(f"Response: {data}")

            task_id = data.get("output", {}).get("task_id", "")
            if task_id:
                print(f"\n✅ Task submitted! ID: {task_id}")
                print(f"Check status: curl -H 'Authorization: Bearer {API_KEY[:12]}...' https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}")
            else:
                print(f"\n⚠️  No task_id — response: {data}")

    except Exception as e:
        print(f"Error: {e}")

asyncio.run(main())
