#!/usr/bin/env python3
"""
YouTube OAuth Setup Helper
Run this ONCE on your local machine to generate tokens for GitHub Actions.

Prerequisites:
  1. Go to https://console.cloud.google.com/
  2. Create project → Enable YouTube Data API v3
  3. Create OAuth 2.0 credentials (Desktop app)
  4. Download JSON → save as client_secrets.json in this folder

Usage:
  python youtube_auth_setup.py
"""

import base64
import os
import pickle
import sys

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("pip install google-auth-oauthlib google-api-python-client")
    sys.exit(1)

SCOPES = [
    "https://www.googleapis.com/auth/youtube",            # Full management
    "https://www.googleapis.com/auth/youtube.upload",     # Upload videos
    "https://www.googleapis.com/auth/youtube.force-ssl",  # Comments + metadata
    "https://www.googleapis.com/auth/youtube.readonly",   # Read channel info
]
CLIENT_SECRETS = "client_secrets.json"
TOKEN_FILE = "youtube_token.pickle"


def main():
    print("=" * 50)
    print("  YouTube OAuth Setup")
    print("=" * 50)

    if not os.path.exists(CLIENT_SECRETS):
        print(f"\nERROR: {CLIENT_SECRETS} not found!")
        print()
        print("Step 1: Go to https://console.cloud.google.com/")
        print("Step 2: Create a new project (or select existing)")
        print("Step 3: Enable YouTube Data API v3")
        print("        → APIs & Services → Library → search 'YouTube Data API v3' → Enable")
        print("Step 4: Create OAuth credentials")
        print("        → APIs & Services → Credentials → Create Credentials → OAuth client ID")
        print("        → Application type: Desktop app → Create")
        print("Step 5: Download JSON → save as 'client_secrets.json' in this folder")
        print()
        sys.exit(1)

    print("\nOpening browser for YouTube authorization...")
    print("Login with your YouTube channel account.\n")

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS, SCOPES)
    creds = flow.run_local_server(port=8080)

    with open(TOKEN_FILE, "wb") as f:
        pickle.dump(creds, f)
    print(f"\n✅ Token saved to: {TOKEN_FILE}")

    # Generate base64 for GitHub secrets
    with open(TOKEN_FILE, "rb") as f:
        token_b64 = base64.b64encode(f.read()).decode()

    with open(CLIENT_SECRETS, "rb") as f:
        secrets_b64 = base64.b64encode(f.read()).decode()

    print("\n" + "=" * 50)
    print("  ADD THESE AS GITHUB SECRETS")
    print("=" * 50)
    print()
    print("Go to: https://github.com/Mohan-Kumar-Swamynathan/am/settings/secrets/actions")
    print()
    print("Secret 1: YOUTUBE_TOKEN_BASE64")
    print("-" * 40)
    print(token_b64)
    print()
    print("Secret 2: CLIENT_SECRETS_BASE64")
    print("-" * 40)
    print(secrets_b64)
    print()
    print("=" * 50)
    print("  DONE! YouTube auto-upload is now ready.")
    print("=" * 50)


if __name__ == "__main__":
    main()
