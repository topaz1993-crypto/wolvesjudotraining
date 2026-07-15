#!/usr/bin/env python3
"""
Google OAuth Authentication Script
Generates a fresh token.pickle for Google Sheets/Calendar access
"""

import os
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# Scopes for Sheets and Calendar
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/calendar'
]

# Path to credentials.json (download from Google Cloud Console)
CREDS_FILE = 'credentials.json'
TOKEN_FILE = os.path.expanduser('~/token.pickle')

def main():
    creds = None

    # Check if credentials.json exists
    if not os.path.exists(CREDS_FILE):
        print(f"❌ {CREDS_FILE} not found!")
        print("📝 Download from: https://console.cloud.google.com/")
        print("   1. Create OAuth 2.0 Client ID (Desktop app)")
        print("   2. Download as credentials.json")
        print("   3. Place in this directory")
        return

    # Get authorization
    flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    # Save the token
    with open(TOKEN_FILE, 'wb') as token:
        pickle.dump(creds, token)

    print(f"✅ Token saved to {TOKEN_FILE}")
    print(f"\n📝 Next step:")
    print(f"   base64 -i {TOKEN_FILE} | pbcopy")
    print(f"\n💾 Then update GOOGLE_CREDS_B64 in Render Dashboard")

if __name__ == '__main__':
    main()
