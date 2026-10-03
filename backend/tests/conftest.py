"""Shared test setup: mock everything, make the fake model fast, and give each test a fresh
store, orchestrator and set of fake integrations. No test touches the network."""

import asyncio
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import integrations
from app.core.config import FIXTURES, settings
from app.core.orchestrator import Orchestrator
from app.core.store import default_meeting, store

settings.llm_mode = "mock"
settings.google_mode = "mock"
settings.github_mode = "mock"
settings.mock_delay = 0.02
settings.intent_debounce = 0.05
TZ = settings.timezone


@pytest.fixture
def orch():
    settings.agents = ["answer", "research", "issue", "email", "schedule"]
    store.reset()
    store.line_listeners.clear()
    integrations.reset_mocks()
    store.start_meeting(default_meeting())
    orchestrator = Orchestrator(store)
    yield orchestrator
    orchestrator.reset()


def run(coro):
    """Run one async test body with a hard timeout."""
    return asyncio.run(asyncio.wait_for(coro, 15))


async def until(condition, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.01)


def slot(days: int, hour: int = 15) -> datetime:
    day = datetime.now(ZoneInfo(TZ)) + timedelta(days=days)
    return day.replace(hour=hour, minute=0, second=0, microsecond=0)


def task_of(type_: str):
    return next(t for t in store.tasks.values() if t.type == type_)


def fixture_lines(name: str) -> list[str]:
    path = FIXTURES / f"{name}.jsonl"
    return [json.loads(l)["text"] for l in path.read_text().splitlines() if l.strip()]
