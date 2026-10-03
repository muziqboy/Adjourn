"""Panel routes that do more than forward a call: in process through the ASGI app, no network."""

import httpx
from fastapi import FastAPI

from app.api.routes import build_router
from app.core.contract import Op
from app.core.intent import IntentSession
from app.core.store import store
from app.integrations import calendar
from conftest import run, until


def test_steer_from_the_panel_reruns_the_same_task(orch):
    """The card's "Change…" box: same task, next revision, same calendar event, reason in the trace."""
    async def body():
        app = FastAPI()
        app.include_router(build_router(store, orch, IntentSession(store, orch)))
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://panel")

        orch.apply([Op(op="create", type="schedule", title="Follow-up with Bea",
                       brief="Book a 30-minute follow-up with Bea tomorrow at 15:00.")])
        task = next(iter(store.tasks.values()))
        await until(lambda: task.status == "needs_approval")
        event_id = task.artifact.external_id

        res = await client.post(f"/api/tasks/{task.id}/steer", json={"instruction": "make it 45 minutes"})
        assert res.status_code == 200 and res.json()["revision"] == 2
        await until(lambda: task.status == "needs_approval")
        assert "make it 45 minutes" in task.brief
        assert task.artifact.external_id == event_id and len(calendar.fake.events) == 1
        assert any(e.kind == "steer" and "Changed on the panel" in e.text for e in task.trace)

        assert (await client.post(f"/api/tasks/{task.id}/steer", json={"instruction": " "})).status_code == 422
        assert (await client.post("/api/tasks/nope/steer", json={"instruction": "x"})).status_code == 404
        await client.aclose()

    run(body())
