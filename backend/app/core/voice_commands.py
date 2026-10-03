"""Spoken approvals: people in the call answer Adjourn's raised hand by voice.

    "Go ahead, Adjourn" / "Yes Adjourn" / "Adjourn, go ahead"  -> approve the waiting answer (it speaks)
    "No thanks, Adjourn" / "Not now, Adjourn"                  -> dismiss it (it lowers its hand)

Only the answer agent is voice-approvable: speaking affects nothing outside this call, so anyone
in it may say yes. Invites and issues reach people outside the call and stay panel-only.

A plain store line listener, deterministic and instant; it does not wait for the intent pass.
Meet captions spell the name in odd ways ("a journ", "adjourned"), hence the loose pattern.
"""

import asyncio
import logging
import re

from .orchestrator import Orchestrator
from .store import Store

log = logging.getLogger("adjourn.voice")

NAME = r"(adjourn\w*|a ?journ\w*|ajourn\w*)"
YES = r"(go ahead|go for it|yes|yeah|yep|sure|please|tell us|let'?s hear it)"
NO = r"(no thanks|no thank you|not now|never ?mind|skip it|that'?s fine|no)"
APPROVE = re.compile(rf"\b{YES}\b[\s,.!]*\b{NAME}\b|\b{NAME}\b[\s,.!]*\b{YES}\b", re.I)
DISMISS = re.compile(rf"\b{NO}\b[\s,.!]*\b{NAME}\b|\b{NAME}\b[\s,.!]*\b{NO}\b", re.I)


def make_listener(store: Store, orch: Orchestrator):
    pending: set[asyncio.Task] = set()

    def waiting_answer():
        waiting = [t for t in store.tasks.values() if t.type == "answer" and t.status == "needs_approval"]
        return waiting[-1] if waiting else None

    async def approve(task_id: str) -> None:
        try:
            await orch.approve(task_id)
        except Exception:  # noqa: BLE001  (e.g. already approved from the panel)
            log.exception("voice approval of %s failed", task_id)

    def on_line(text: str) -> None:
        task = waiting_answer()
        if task is None:
            return
        if DISMISS.search(text):
            log.info("voice: dismiss %s (%r)", task.id, text)
            store.trace(task.id, "info", f"Dismissed by voice: “{text}”")
            orch.dismiss(task.id)
        elif APPROVE.search(text):
            log.info("voice: approve %s (%r)", task.id, text)
            store.trace(task.id, "info", f"Approved by voice: “{text}”")
            job = asyncio.create_task(approve(task.id))
            pending.add(job)
            job.add_done_callback(pending.discard)

    return on_line
