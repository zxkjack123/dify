import requests
import os
from automation.infra.config import Config

config = Config()
base_url = config.DIFY_CONSOLE_URL.rstrip('/')
email = config.DIFY_EMAIL
password = config.DIFY_PASSWORD

print(f"URL: {base_url}/console/api/login")
print(f"Email: {email}")
print(f"Password: {password}")

try:
    response = requests.get(f"{base_url}/console/api/setup")
    print(f"Setup Status Code: {response.status_code}")
    print(f"Setup Response: {response.text}")
except Exception as e:
    print(f"Setup Error: {e}")

try:
    response = requests.post(
        f"{base_url}/console/api/login",
        json={"email": email, "password": password}
    )
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.text}")
    print(f"Cookies: {response.cookies}")
except Exception as e:
    print(f"Error: {e}")
