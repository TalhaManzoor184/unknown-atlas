"""One-time helper: run on YOUR computer to get a YouTube refresh token for the Unknown Atlas channel.

    pip install google-auth-oauthlib
    python scripts/get_refresh_token.py path/to/client_secret.json

A browser opens -> sign in -> when asked, pick the *Unknown Atlas* channel (not your Sunny channel) -> allow.
Copy the printed values into GitHub -> repo Settings -> Secrets and variables -> Actions.
"""
import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube"]

if len(sys.argv) != 2:
    sys.exit(__doc__)

flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPES)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
info = json.load(open(sys.argv[1]))
client = info.get("installed") or info.get("web")
print("\nAdd these as GitHub Actions secrets:\n")
print("YOUTUBE_CLIENT_ID     =", client["client_id"])
print("YOUTUBE_CLIENT_SECRET =", client["client_secret"])
print("YOUTUBE_REFRESH_TOKEN =", creds.refresh_token)
