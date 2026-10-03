"""Settings, read once from `.env` (repo root first, then backend/).

Every switch that changes behaviour lives here, so a teammate can see in one place what the
backend can do. Tests overwrite attributes on `settings` directly.

No extra dependency: `.env` is parsed by the twelve lines below. A variable already set in
the shell wins over the file.
"""

import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
FIXTURES = ROOT / "fixtures"


def _load_env() -> None:
    for path in (ROOT / ".env", BACKEND / ".env"):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env()


def _list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


class Settings:
    def __init__(self) -> None:
        # --- which agents the meeting agent may create tasks for (see app/agents/) ---
        self.agents = _list("AGENTS", "answer,issue,schedule")

        # --- model ---
        self.llm_mode = os.getenv("LLM_MODE", "mock")  # mock | gemini
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        self.model_fast = os.getenv("MODEL_FAST", "gemini-3.8-flash")
        self.model_live = os.getenv("MODEL_LIVE", "gemini-3.8-live")
        # Roles (intent, answer, issue, ...) whose calls go through Condense. Empty = off.
        self.condense_roles = _list("CONDENSE_ROLES", "")
        self.condense_base_url = os.getenv("CONDENSE_BASE_URL", "")
        self.condense_api_key = os.getenv("CONDENSE_API_KEY", "")

        # --- integrations: mock (no network) | links (prefilled pages, no sign-in) | live ---
        self.google_mode = os.getenv("GOOGLE_MODE", "mock")
        self.github_mode = os.getenv("GITHUB_MODE", "mock")
        self.github_repo = os.getenv("GITHUB_REPO", "")  # owner/name
        self.github_token = os.getenv("GITHUB_TOKEN", "")
        self.credentials_file = BACKEND / "credentials.json"  # Google OAuth client
        self.token_file = BACKEND / "token.json"  # Google OAuth token, written by scripts/google_auth.py

        # --- meeting bot (Recall.ai): joins the Meet as its own participant, hears with speaker
        # names, speaks. Needs a public URL for Recall's webhooks (a tunnel to this backend).
        self.recall_api_key = os.getenv("RECALL_API_KEY", "")
        self.recall_region = os.getenv("RECALL_REGION", "us-west-2")  # API keys are region-bound
        self.public_url = os.getenv("PUBLIC_URL", "").rstrip("/")  # e.g. https://xyz.trycloudflare.com
        self.recall_webhook_token = os.getenv("RECALL_WEBHOOK_TOKEN") or secrets.token_urlsafe(16)
        self.bot_name = os.getenv("BOT_NAME", "Adjourn")
        # Calendar auto-join (listen/autojoin.py): on start, connect the demo account's Google
        # Calendar to Recall and send the bot to every Meet on it. Needs token.json.
        self.auto_join = os.getenv("AUTO_JOIN", "false").strip().lower() in ("1", "true", "yes", "on")

        # --- meeting defaults (prefill the setup screen; used by replays) ---
        self.timezone = os.getenv("TIMEZONE", "Europe/Stockholm")
        self.me_name = os.getenv("ME_NAME", "Alex")
        self.me_email = os.getenv("ME_EMAIL", "demo-a@gmail.com")
        self.guest_name = os.getenv("GUEST_NAME", "Bea")
        self.guest_email = os.getenv("GUEST_EMAIL", "demo-b@gmail.com")

        # --- timing ---
        self.mock_delay = float(os.getenv("MOCK_DELAY", "1.0"))  # scales fake model latency; 0 in tests
        self.intent_debounce = float(os.getenv("INTENT_DEBOUNCE", "1.0"))  # seconds after the last line


settings = Settings()
