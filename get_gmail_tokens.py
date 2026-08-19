"""
Run this ONCE locally to get your Gmail OAuth tokens.
Then copy the printed values into your Render environment variables.

Steps:
  1. pip install google-auth-oauthlib
  2. Put your downloaded client_secret.json in this same folder
  3. python get_gmail_tokens.py
  4. A browser window opens — sign in and allow access
  5. Copy GMAIL_TOKEN and GMAIL_REFRESH into Render env vars
"""

from google_auth_oauthlib.flow import InstalledAppFlow
import os

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

secret_file = os.path.join(os.path.dirname(__file__), "client_secret.json")
if not os.path.exists(secret_file):
    print("ERROR: client_secret.json not found in this folder.")
    print("Download it from Google Cloud Console → APIs & Services → Credentials")
    exit(1)

flow = InstalledAppFlow.from_client_secrets_file(secret_file, SCOPES)
creds = flow.run_local_server(port=0)

print("\n✅ Success! Add these to your Render environment variables:\n")
print(f"GMAIL_TOKEN={creds.token}")
print(f"GMAIL_REFRESH={creds.refresh_token}")
print(f"GMAIL_CLIENT_ID={creds.client_id}")
print(f"GMAIL_CLIENT_SECRET={creds.client_secret}")
print("\nAlso set:")
print("ALERT_EMAIL=your-gmail@gmail.com")
print("ADMIN_URL=https://turnin-license.onrender.com")
print("ADMIN_PASSWORD=your-admin-password")
