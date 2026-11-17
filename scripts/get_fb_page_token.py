"""One-time helper: turn a short-lived Facebook user token into a long-lived Page access token.

1. developers.facebook.com -> My Apps -> Create App (type "Business" or "Other"), note App ID + App Secret.
2. Tools -> Graph API Explorer -> select your app -> "Get User Access Token" with permissions:
   pages_show_list, pages_read_engagement, pages_manage_posts   (+ business_management if your Page is in a Business portfolio)
   -> Generate, log in, pick the Unknown Atlas Page -> copy the token.
3. Run on your computer:
       python scripts/get_fb_page_token.py APP_ID APP_SECRET SHORT_LIVED_USER_TOKEN
4. Put the printed FB_PAGE_ID and FB_PAGE_TOKEN into GitHub -> Settings -> Secrets and variables -> Actions.

A Page token made from a long-lived user token does not expire (until you change your password or remove the app).
"""
import sys

import requests

V = "v25.0"

if len(sys.argv) != 4:
    sys.exit(__doc__)
app_id, app_secret, short_token = sys.argv[1:]

r = requests.get(f"https://graph.facebook.com/{V}/oauth/access_token", params={
    "grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": app_secret, "fb_exchange_token": short_token,
}, timeout=30).json()
if "access_token" not in r:
    sys.exit(f"Token exchange failed: {r}")
long_user = r["access_token"]

pages = requests.get(f"https://graph.facebook.com/{V}/me/accounts",
                     params={"access_token": long_user, "fields": "id,name,access_token,tasks"}, timeout=30).json()
if not pages.get("data"):
    sys.exit(f"No Pages found for this login (did you tick your Page when generating the token?): {pages}")

print("\nPages you manage:\n")
for p in pages["data"]:
    print(f"  {p['name']}")
    print(f"    FB_PAGE_ID    = {p['id']}")
    print(f"    FB_PAGE_TOKEN = {p['access_token']}")
    print(f"    tasks         = {', '.join(p.get('tasks', []))}\n")
print("Copy the two values for the Unknown Atlas Page into GitHub secrets.")
