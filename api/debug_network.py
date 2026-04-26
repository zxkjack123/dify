import os
import time

import httpx
import requests

api_key = os.environ.get("UIUIAPI_API_KEY")
base_url = os.environ.get("UIUIAPI_BASE_URL")

print(f"Testing connection to {base_url} with key {api_key[:5]}...")

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

# Test with requests
try:
    print("Testing with requests...")
    start = time.time()
    resp = requests.get(f"{base_url}/models", headers=headers, timeout=10)
    print(f"Requests status: {resp.status_code}")
    print(f"Requests time: {time.time() - start:.2f}s")
    if resp.status_code != 200:
        print(f"Requests error: {resp.text}")
except Exception as e:
    print(f"Requests failed: {e}")

# Test with httpx
try:
    print("Testing with httpx...")
    start = time.time()
    with httpx.Client(timeout=10) as client:
        resp = client.get(f"{base_url}/models", headers=headers)
        print(f"Httpx status: {resp.status_code}")
        print(f"Httpx time: {time.time() - start:.2f}s")
        if resp.status_code != 200:
            print(f"Httpx error: {resp.text}")
except Exception as e:
    print(f"Httpx failed: {e}")
