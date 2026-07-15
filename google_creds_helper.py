"""Helper to load Google credentials from multiple locations."""
import os
import pickle
from pathlib import Path

def get_token_pickle_path():
    """Return path to token.pickle from local or Render persistent disk."""
    for path in [
        os.path.expanduser("~/token.pickle"),  # Local
        "/data/token.pickle",  # Render persistent disk
        "/var/data/token.pickle",  # Alternative path
    ]:
        if os.path.exists(path):
            return path
    raise FileNotFoundError("token.pickle not found in ~/token.pickle or /data/token.pickle")

def load_google_credentials():
    """Load and return Google credentials."""
    path = get_token_pickle_path()
    with open(path, "rb") as f:
        return pickle.load(f)
