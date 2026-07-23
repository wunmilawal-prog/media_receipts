import os
from getpass import getpass

import requests
from dotenv import load_dotenv

load_dotenv()

app_key = os.environ["DROPBOX_APP_KEY"]
app_secret = os.environ["DROPBOX_APP_SECRET"]

authorization_code = getpass("Paste the Dropbox authorization code: ").strip()
if not authorization_code:
    raise RuntimeError("No Dropbox authorization code was entered")

response = requests.post(
    "https://api.dropboxapi.com/oauth2/token",
    data={
        "code": authorization_code,
        "grant_type": "authorization_code",
    },
    auth=(app_key, app_secret),
    timeout=30,
)

if not response.ok:
    print("Dropbox token exchange failed:", response.text)
    response.raise_for_status()

result = response.json()

print("Refresh token:", result.get("refresh_token"))
print("Access-token lifetime:", result.get("expires_in"))
print("Account ID:", result.get("account_id"))
