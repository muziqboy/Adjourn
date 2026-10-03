"""Spike 3 step 5: sign in once as account A and save backend/token.json.
Needs backend/credentials.json (OAuth client of type Desktop app).
Run: uv run python scripts/google_auth.py
On "Google hasn't verified this app": Advanced, then continue."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

from app.config import settings  # noqa: E402
from app.google_api import SCOPES  # noqa: E402

if not settings.credentials_file.exists():
    sys.exit(f"Missing {settings.credentials_file}")
flow = InstalledAppFlow.from_client_secrets_file(str(settings.credentials_file), SCOPES)
creds = flow.run_local_server(port=0)
settings.token_file.write_text(creds.to_json())
print(f"Saved {settings.token_file}")
