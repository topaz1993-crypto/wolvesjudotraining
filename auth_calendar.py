"""
One-time script to add Google Calendar scope to existing credentials.
Run: python3 auth_calendar.py
"""
import os
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/calendar",
]

TOKEN_PATH = os.path.expanduser("~/token.pickle")
CREDS_PATH = os.path.expanduser("~/wolves_credentials.json")

creds = None
if os.path.exists(TOKEN_PATH):
    creds = pickle.load(open(TOKEN_PATH, "rb"))

if not creds or not creds.valid or not all(s in (creds.scopes or []) for s in SCOPES):
    flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
    creds = flow.run_local_server(port=0)
    pickle.dump(creds, open(TOKEN_PATH, "wb"))
    print("✅ Authorized! Token saved.")
else:
    print("✅ Already authorized.")
