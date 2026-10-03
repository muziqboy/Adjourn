"""Google sign-in shared by calendar.py and gmail.py (live mode only).

One OAuth token for both APIs, created once by `scripts/google_auth.py` as the demo account
and stored in backend/token.json (git-ignored). The client libraries are synchronous: callers
wrap every call in asyncio.to_thread.
"""

from ..core.config import settings

SCOPES = [
    "https://www.googleapis.com/auth/calendar",  # our hold, move and invite (schedule agent)
    "https://www.googleapis.com/auth/calendar.events.readonly",  # what Recall needs to auto-join meetings
    "https://www.googleapis.com/auth/gmail.compose",  # drafts (email agent)
    "https://www.googleapis.com/auth/userinfo.email",  # Recall wants the account's email
    "openid",
]

_services: dict = {}


def service(name: str):
    """A cached API client: service("calendar") or service("gmail")."""
    if name not in _services:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials.from_authorized_user_file(str(settings.token_file), SCOPES)
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
            settings.token_file.write_text(creds.to_json())
        version = "v3" if name == "calendar" else "v1"
        _services[name] = build(name, version, credentials=creds, cache_discovery=False)
    return _services[name]
