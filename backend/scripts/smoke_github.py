"""GitHub spike: create an issue on GITHUB_REPO, edit it in place, and print its link.
Needs GITHUB_REPO=owner/name and GITHUB_TOKEN (fine-grained, "Issues: read and write" on that
repo) in .env. Run: uv run python scripts/smoke_github.py   (close the test issue afterwards)"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.integrations import github  # noqa: E402


async def main():
    settings.github_mode = "live"
    number, link = await github.set_issue(None, "Adjourn smoke test", "first version")
    print("created:", number, link)
    same, _ = await github.set_issue(number, "Adjourn smoke test (edited)", "second version")
    assert same == number, "edit created a second issue"
    print("edited in place; close it on GitHub when you have looked")


asyncio.run(main())
