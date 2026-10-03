"""The task graph: dependencies, steering, cancellation, revision.
Only the orchestrator changes task.status."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from . import google_api, verifier
from .agents import emailer, researcher, scheduler
from .contract import SETTLED, Artifact, MeetingContext, Op, Task
from .store import Store, default_meeting

AGENTS = {"research": researcher, "email": emailer, "schedule": scheduler}


@dataclass
class RunContext:
    """What one run of one agent sees. Inputs are chosen when the task is scheduled."""

    orch: "Orchestrator"
    task_id: str
    revision: int
    inputs: dict[str, Task]  # upstream snapshots
    meeting: MeetingContext
    transcript: str
    previous: Artifact | None
    feedback: str | None = None
    extra: dict = field(default_factory=dict)

    def current(self) -> bool:
        return self.orch.is_current(self.task_id, self.revision)

    def trace(self, kind: str, text: str) -> None:
        if self.current():
            self.orch.store.trace(self.task_id, kind, text)

    @property
    def external_id(self) -> str | None:
        return self.orch.external_ids.get(self.task_id) or (self.previous.external_id if self.previous else None)

    async def write_external(
        self, write: Callable[[str | None], Awaitable[tuple[str | None, str]]]
    ) -> tuple[str | None, str]:
        """Create or update the task's Google object. Shielded and serialised per task: a run
        cancelled mid-call still records the id, so the next revision updates the same object
        instead of creating a second one."""
        orch, task_id = self.orch, self.task_id
        lock = orch.locks.setdefault(task_id, asyncio.Lock())

        async def guarded():
            async with lock:
                external_id, link = await write(orch.external_ids.get(task_id) or self.external_id)
                if external_id:
                    orch.external_ids[task_id] = external_id
                return external_id, link

        return await asyncio.shield(guarded())


class Orchestrator:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.runs: dict[str, asyncio.Task] = {}
        self.external_ids: dict[str, str] = {}
        self.locks: dict[str, asyncio.Lock] = {}

    def reset(self) -> None:
        for run in self.runs.values():
            run.cancel()
        self.runs.clear()
        self.external_ids.clear()
        self.locks.clear()

    def is_current(self, task_id: str, revision: int) -> bool:
        task = self.store.tasks.get(task_id)
        return task is not None and task.revision == revision

    # ---------- ops from the intent pass ----------

    def apply(self, ops: list[Op]) -> list[str]:
        """Create tasks, resolve #n references, apply updates, then reconcile.
        Returns the ids of created and changed tasks."""
        tasks = self.store.tasks
        refs = {f"#{i}": self.store.new_task_id() for i, op in enumerate(ops) if op.op == "create" and op.type}

        def resolve(deps: list[str], own_id: str) -> list[str]:
            out = []
            for dep in deps:
                dep = refs.get(dep, dep)
                if dep != own_id and (dep in tasks or dep in refs.values()) and dep not in out:
                    out.append(dep)
            return out

        touched = []
        for i, op in enumerate(ops):
            if op.op == "create" and op.type:
                task_id = refs[f"#{i}"]
                task = Task(
                    id=task_id, type=op.type, title=op.title or op.type.capitalize(),
                    brief=op.brief, depends_on=resolve(op.depends_on, task_id),
                )
                self.store.put_task(task, created=True)
                self.store.trace(task_id, "info", f"Heard: {op.brief}")
                touched.append(task_id)
            elif op.op == "update" and op.id in tasks:
                task = tasks[op.id]
                new_deps = [d for d in resolve(op.depends_on, task.id) if d not in task.depends_on]
                if op.brief.strip() == task.brief.strip() and not new_deps:
                    continue
                task.brief = op.brief
                task.depends_on += new_deps
                self.restart(task, op.reason or "Brief changed on the call")
                touched.append(task.id)
        self.reconcile()
        return touched

    # ---------- the graph ----------

    def restart(self, task: Task, reason: str) -> None:
        """Steering and upstream changes are one mechanism."""
        run = self.runs.pop(task.id, None)
        if run is not None:
            run.cancel()
        task.revision += 1
        task.status = "detected"
        task.review = None
        self.store.trace(task.id, "steer", f"v{task.revision}: {reason}")
        self.store.put_task(task)

    def reconcile(self) -> None:
        """Start every waiting task whose upstreams have settled; restart every task whose
        upstream has moved on. No suspension point in here: callers may interleave."""
        tasks = self.store.tasks
        changed = True
        while changed:
            changed = False
            for task in list(tasks.values()):
                deps = [tasks[d] for d in task.depends_on if d in tasks]
                if task.status in ("detected", "blocked"):
                    if all(d.status in SETTLED for d in deps):
                        inputs = {d.id: d.model_copy(deep=True) for d in deps}
                        task.inputs_used = {d.id: d.revision for d in deps}
                        task.status = "running"  # before create_task, so nobody starts it twice
                        self.store.put_task(task)
                        self.runs[task.id] = asyncio.create_task(self._run(task.id, task.revision, inputs))
                        changed = True
                    elif task.status != "blocked":
                        task.status = "blocked"
                        self.store.put_task(task)
                else:
                    stale = [d for d in deps if d.revision > task.inputs_used.get(d.id, 0)]
                    if stale:
                        self.restart(task, f"{stale[0].title} changed (v{stale[0].revision})")
                        changed = True

    async def _run(self, task_id: str, revision: int, inputs: dict[str, Task]) -> None:
        task = self.store.tasks[task_id]
        snapshot = task.model_copy(deep=True)
        ctx = RunContext(
            orch=self, task_id=task_id, revision=revision, inputs=inputs,
            meeting=self.store.meeting or default_meeting(),
            transcript=self.store.transcript(), previous=snapshot.artifact,
        )
        agent = AGENTS[snapshot.type]
        try:
            ctx.trace("info", f"Working on v{revision}" + (f" with {', '.join(i.title for i in inputs.values())}" if inputs else ""))
            artifact = await agent.run(snapshot, ctx)
            self._set_artifact(task_id, revision, artifact, "verifying")
            ok, feedback = await verifier.verify(snapshot, artifact, ctx)
            if not ok:
                ctx.trace("verify", f"Review failed: {feedback} Retrying once.")
                ctx.feedback, ctx.previous = feedback, artifact
                self._set_artifact(task_id, revision, artifact, "running")
                artifact = await agent.run(snapshot, ctx)
                self._set_artifact(task_id, revision, artifact, "verifying")
                ok, feedback = await verifier.verify(snapshot, artifact, ctx)
            if not self.is_current(task_id, revision):
                return
            if ok:
                ctx.trace("verify", "All checks passed")
                task.review = None
            else:
                ctx.trace("verify", f"Review still failing, needs your eyes: {feedback}")
                task.review = feedback
            task.status = "needs_approval" if task.type == "schedule" or not ok else "done"
            self.store.put_task(task)
        except asyncio.CancelledError:
            return  # a cancelled run writes nothing
        except Exception as exc:  # noqa: BLE001
            if not self.is_current(task_id, revision):
                return
            ctx.trace("error", f"{type(exc).__name__}: {exc}")
            task.status = "failed"
            self.store.put_task(task)
        finally:
            if self.runs.get(task_id) is asyncio.current_task():
                del self.runs[task_id]
        self.reconcile()

    def _set_artifact(self, task_id: str, revision: int, artifact: Artifact, status: str) -> None:
        if not self.is_current(task_id, revision):
            raise asyncio.CancelledError
        task = self.store.tasks[task_id]
        task.artifact = artifact
        task.status = status  # type: ignore[assignment]
        self.store.put_task(task)

    # ---------- approval ----------

    async def approve(self, task_id: str) -> Task:
        task = self.store.tasks[task_id]
        if task.status != "needs_approval":
            raise ValueError(f"{task.title} is {task.status}, not waiting for approval")
        revision = task.revision
        if task.type == "schedule" and task.artifact is not None:
            meeting = self.store.meeting or default_meeting()
            attendees = [p.email for p in meeting.others]
            lock = self.locks.setdefault(task_id, asyncio.Lock())
            async with lock:
                await google_api.invite(self.external_ids.get(task_id) or task.artifact.external_id, attendees)
            if not self.is_current(task_id, revision):
                return task
            task.artifact.attendees = attendees
            task.artifact.invited = True
            if google_api.mode() == "links":
                self.store.trace(task_id, "tool", "Opened the invite in Google Calendar; save it there to send")
            else:
                self.store.trace(task_id, "tool", f"Invite sent to {', '.join(attendees)}")
        else:
            self.store.trace(task_id, "info", "Approved")
        task.status = "done"
        self.store.put_task(task)
        self.reconcile()
        return task
