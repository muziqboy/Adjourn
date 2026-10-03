"""Sign in once as the demo account (A) and save backend/token.json. Used by the Calendar and
Gmail live modes and by Recall's calendar auto-join (app/listen/autojoin.py).
Needs backend/credentials.json (OAuth client of type Desktop app).
Run: uv run python scripts/google_auth.py
On "Google hasn't verified this app": Advanced, then continue."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.integrations.google import SCOPES  # noqa: E402

if not settings.credentials_file.exists():
    sys.exit(f"Missing {settings.credentials_file}")
flow = InstalledAppFlow.from_client_secrets_file(str(settings.credentials_file), SCOPES)
# offline + consent: always return a refresh token (Recall's calendar connection needs one)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
settings.token_file.write_text(creds.to_json())
print(f"Saved {settings.token_file}")
