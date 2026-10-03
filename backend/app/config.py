"""Settings from .env (repo root, then backend/). No extra dependency: a tiny parser."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
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


class Settings:
    def __init__(self) -> None:
        self.llm_mode = os.getenv("LLM_MODE", "mock")  # mock | gemini
        self.google_mode = os.getenv("GOOGLE_MODE", "mock")  # mock | links | live
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        self.model_fast = os.getenv("MODEL_FAST", "gemini-3.8-flash")
        self.model_live = os.getenv("MODEL_LIVE", "gemini-3.8-live")
        self.timezone = os.getenv("TIMEZONE", "Europe/Stockholm")
        self.me_name = os.getenv("ME_NAME", "Alex")
        self.me_email = os.getenv("ME_EMAIL", "demo-a@gmail.com")
        self.guest_name = os.getenv("GUEST_NAME", "Bea")
        self.guest_email = os.getenv("GUEST_EMAIL", "demo-b@gmail.com")
        self.mock_delay = float(os.getenv("MOCK_DELAY", "1.0"))
        self.intent_debounce = float(os.getenv("INTENT_DEBOUNCE", "1.0"))
        self.credentials_file = BACKEND / "credentials.json"
        self.token_file = BACKEND / "token.json"


settings = Settings()
