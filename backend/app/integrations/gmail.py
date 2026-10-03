"""Gmail drafts for the email agent. GOOGLE_MODE picks the backend (see calendar.py).
Adjourn never sends email: it creates or replaces a draft, and the user sends it from Gmail."""

import asyncio
import base64
from email.message import EmailMessage
from urllib.parse import quote, urlencode

from ..core.config import settings
from .google import service

DRAFTS_LINK = "https://mail.google.com/mail/u/0/#drafts"


class FakeGmail:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.n = 0
        self.drafts: dict[str, dict] = {}


fake = FakeGmail()


async def set_draft(existing_id: str | None, to: list[str], subject: str, body: str) -> tuple[str | None, str]:
    """Create the draft, or replace the existing one by id. Returns (draft id, link)."""
    if settings.google_mode == "links":
        query = urlencode({"view": "cm", "fs": "1", "to": ",".join(to), "su": subject, "body": body}, quote_via=quote)
        return None, f"https://mail.google.com/mail/?{query}"
    if settings.google_mode == "mock":
        if existing_id is None:
            fake.n += 1
            existing_id = f"draft_{fake.n}"
        fake.drafts[existing_id] = {"to": to, "subject": subject, "body": body}
        return existing_id, DRAFTS_LINK
    return await asyncio.to_thread(_live_set_draft, existing_id, to, subject, body)


def _live_set_draft(existing_id, to, subject, body) -> tuple[str, str]:
    message = EmailMessage()
    message["To"] = ", ".join(to)
    message["Subject"] = subject
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    drafts = service("gmail").users().drafts()
    if existing_id:
        draft = drafts.update(userId="me", id=existing_id, body={"message": {"raw": raw}}).execute()
    else:
        draft = drafts.create(userId="me", body={"message": {"raw": raw}}).execute()
    return draft["id"], DRAFTS_LINK
