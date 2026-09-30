import os
import sys
import json
import requests

PERSONA_ID = "daa88481-ee2e-4687-a0e0-04441fc6b045"
API_URL = "http://127.0.0.1:8000"
COMFY_URL = "http://127.0.0.1:8188"

def main():
    print("=== Checking Local Persona Studio Pipeline ===")
    
    # Check ComfyUI
    try:
        r_comfy = requests.get(f"{COMFY_URL}/system_stats", timeout=5)
        stats = r_comfy.json()
        device = stats.get("devices", [{}])[0].get("name", "unknown")
        print(f"[OK] ComfyUI reachable on {COMFY_URL} (Running on {device})")
    except Exception as e:
        print(f"[ERROR] ComfyUI unreachable: {e}")
        return

    # Check API & Persona
    try:
        r_api = requests.get(f"{API_URL}/api/v1/personas/{PERSONA_ID}", timeout=5)
        if r_api.status_code == 200:
            persona = r_api.json()
            print(f"[OK] Persona '{persona['name']}' ({PERSONA_ID}) registered in Persona Studio.")
            print(f"     Brand: {persona['brand']}")
            print(f"     Status: {persona['status']}")
        else:
            print(f"[ERROR] Failed to fetch persona: {r_api.status_code}")
    except Exception as e:
        print(f"[ERROR] Persona API unreachable: {e}")
        return

    print("\nLocal Persona Engine is initialized and ready.")

if __name__ == "__main__":
    main()
