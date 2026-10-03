"""GitHub issues for the issue agent. GITHUB_MODE picks the backend:

    mock   in-memory issues (`fake`), no network
    links  no token: a prefilled "new issue" page that the panel opens on the click
    live   the REST API with GITHUB_TOKEN on GITHUB_REPO (owner/name)

Live mode needs a fine-grained token with "Issues: read and write" on that one repository.
Uses the standard library (urllib), so there is no extra dependency.
"""

import asyncio
import json
import urllib.request
from urllib.parse import quote, urlencode

from ..core.config import settings


class FakeGitHub:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.n = 0
        self.issues: dict[str, dict] = {}  # number -> {title, body}


fake = FakeGitHub()


def mode() -> str:
    return settings.github_mode


def new_issue_link(title: str, body: str) -> str:
    """The prefilled "new issue" page (links mode)."""
    repo = settings.github_repo or "OWNER/REPO"
    return f"https://github.com/{repo}/issues/new?" + urlencode({"title": title, "body": body}, quote_via=quote)


async def set_issue(number: str | None, title: str, body: str) -> tuple[str | None, str]:
    """Create the issue, or edit the existing one by number. Returns (number, link).
    Only called from the approval click: an issue is visible to the whole team."""
    if mode() == "links":
        return None, new_issue_link(title, body)
    if mode() == "mock":
        if number is None:
            fake.n += 1
            number = str(fake.n)
        fake.issues[number] = {"title": title, "body": body}
        repo = settings.github_repo or "example/repo"
        return number, f"https://github.com/{repo}/issues/{number}"
    return await asyncio.to_thread(_live_set_issue, number, title, body)


def _live_set_issue(number: str | None, title: str, body: str) -> tuple[str, str]:
    if not settings.github_repo or not settings.github_token:
        raise RuntimeError("GITHUB_MODE=live needs GITHUB_REPO and GITHUB_TOKEN in .env")
    url = f"https://api.github.com/repos/{settings.github_repo}/issues"
    method = "POST"
    if number:
        url, method = f"{url}/{number}", "PATCH"
    request = urllib.request.Request(
        url, method=method, data=json.dumps({"title": title, "body": body}).encode(),
        headers={
            "Authorization": f"Bearer {settings.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        issue = json.load(response)
    return str(issue["number"]), issue["html_url"]
