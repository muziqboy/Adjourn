"""The task graph: dependencies, steering, cancellation, revision, approval.

Only the orchestrator changes `task.status`. Agents return an Artifact and write trace lines;
they know nothing about the graph.

Life of a task:

    detected -> blocked (upstreams not settled) -> running -> verifying -> needs_approval -> done
                                                                       \\-> done (no click needed)
    any state -> dismissed (the user said the card is wrong)
    any state -> detected again on restart (steer, or an upstream changed): revision + 1

Four traps this file is written around (each bit an earlier version or would have):
1. A task's upstream inputs are chosen when it is scheduled, inside `reconcile`, and passed into
   the run. Reading them later races with an upstream being restarted.
2. `reconcile` sets status to "running" before it creates the asyncio task and has no
   suspension point, so two concurrent callers can never start the same task twice.
3. A cancelled run writes nothing: every write checks that the run's revision is current.
4. External writes are shielded and serialised per task (RunContext.write_external), so a
   steer never produces a second calendar event or issue.
"""

import asyncio
import logging

from .. import agents
from ..agents.base import RunContext
from .contract import SETTLED, Artifact, Op, Task
from .store import Store, default_meeting

log = logging.getLogger("adjourn.orchestrator")


class Orchestrator:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.runs: dict[str, asyncio.Task] = {}  # task id -> the running agent
        self.external_ids: dict[str, str] = {}  # task id -> event / draft / issue id, kept across revisions
        self.locks: dict[str, asyncio.Lock] = {}  # task id -> serialises external writes
        self._side_effects: set[asyncio.Task] = set()  # on_waiting hooks in flight

    def reset(self) -> None:
        for run in self.runs.values():
            run.cancel()
        self.runs.clear()
        self.external_ids.clear()
        self.locks.clear()

    def is_current(self, task_id: str, revision: int) -> bool:
        task = self.store.tasks.get(task_id)
        return task is not None and task.revision == revision and task.status != "dismissed"

    # ---------- ops from the intent pass ----------

    def apply(self, ops: list[Op]) -> list[str]:
        """Create tasks, resolve "#n" references, apply updates, then reconcile.
        Returns the ids of created and changed tasks."""
        tasks = self.store.tasks
        refs = {f"#{i}": self.store.new_task_id()
                for i, op in enumerate(ops) if op.op == "create" and op.type and agents.get(op.type)}

        def resolve(deps: list[str], own_id: str) -> list[str]:
            out = []
            for dep in deps:
                dep = refs.get(dep, dep)
                if dep != own_id and (dep in tasks or dep in refs.values()) and dep not in out:
                    out.append(dep)
            return out

        touched = []
        for i, op in enumerate(ops):
            if op.op == "create":
                if f"#{i}" not in refs:
                    log.warning("ignoring create for unknown agent type %r", op.type)
                    continue
                task_id = refs[f"#{i}"]
                task = Task(id=task_id, type=op.type, title=op.title or op.type.capitalize(),
                            brief=op.brief, depends_on=resolve(op.depends_on, task_id))
                self.store.put_task(task, created=True)
                self.store.trace(task_id, "info", f"Heard: {op.brief}")
                touched.append(task_id)
            elif op.op == "update" and op.id in tasks:
                task = tasks[op.id]
                if task.status == "dismissed":
                    continue
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
        """Steering and upstream changes are one mechanism: cancel, bump revision, re-run.
        The agent gets the previous artifact and updates the same external object."""
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
        upstream has moved on. No suspension point in here (trap 2)."""
        tasks = self.store.tasks
        changed = True
        while changed:
            changed = False
            for task in list(tasks.values()):
                if task.status == "dismissed":
                    continue
                deps = [tasks[d] for d in task.depends_on if d in tasks]
                if task.status in ("detected", "blocked"):
                    if all(d.status in SETTLED for d in deps):
                        inputs = {d.id: d.model_copy(deep=True) for d in deps}  # trap 1
                        task.inputs_used = {d.id: d.revision for d in deps}
                        task.status = "running"  # trap 2: before create_task
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

    def _context(self, task: Task, inputs: dict[str, Task]) -> RunContext:
        return RunContext(
            orch=self, task_id=task.id, revision=task.revision, inputs=inputs,
            meeting=self.store.meeting or default_meeting(),
            transcript=self.store.transcript(), previous=task.artifact.model_copy(deep=True) if task.artifact else None,
        )

    async def _run(self, task_id: str, revision: int, inputs: dict[str, Task]) -> None:
        """agent -> verifier -> one retry with the verifier's feedback -> needs_approval or done."""
        task = self.store.tasks[task_id]
        snapshot = task.model_copy(deep=True)  # the agent works from the brief as it was when scheduled
        spec = agents.get(task.type)
        ctx = self._context(task, inputs)
        try:
            names = ", ".join(i.title for i in inputs.values())
            ctx.trace("info", f"Working on v{revision}" + (f" with {names}" if names else ""))
            artifact = await spec.run(snapshot, ctx)
            self._set_artifact(task_id, revision, artifact, "verifying")
            problems = await self._verify(spec, snapshot, artifact, ctx)
            if problems:
                ctx.trace("verify", "Review failed; retrying once with the feedback")
                ctx.feedback, ctx.previous = " ".join(problems), artifact
                self._set_artifact(task_id, revision, artifact, "running")
                artifact = await spec.run(snapshot, ctx)
                self._set_artifact(task_id, revision, artifact, "verifying")
                problems = await self._verify(spec, snapshot, artifact, ctx)
            if not self.is_current(task_id, revision):
                return
            if problems:  # never hide a failed review: deliver it with the notes on the card
                task.review = " ".join(problems)
                ctx.trace("verify", "Review still failing; it needs your eyes")
            else:
                task.review = None
                ctx.trace("verify", "All checks passed")
            task.status = "needs_approval" if spec.approval or problems else "done"
            self.store.put_task(task)
            if task.status == "needs_approval" and spec.on_waiting and not problems:
                self._fire(spec.on_waiting(task.model_copy(deep=True)), f"{task.id} on_waiting")
        except asyncio.CancelledError:
            return  # trap 3: a cancelled run writes nothing
        except Exception as exc:  # noqa: BLE001
            if not self.is_current(task_id, revision):
                return
            log.exception("task %s failed", task_id)
            ctx.trace("error", f"{type(exc).__name__}: {exc}")
            task.status = "failed"
            self.store.put_task(task)
        finally:
            if self.runs.get(task_id) is asyncio.current_task():
                del self.runs[task_id]
        self.reconcile()

    def _fire(self, coro, label: str) -> None:
        """Run a side effect in the background; log its failure instead of raising."""
        async def guarded():
            try:
                await coro
            except Exception:  # noqa: BLE001
                log.exception("%s failed", label)
        task = asyncio.create_task(guarded())
        self._side_effects.add(task)
        task.add_done_callback(self._side_effects.discard)

    async def _verify(self, spec, task: Task, artifact: Artifact, ctx: RunContext) -> list[str]:
        problems = await spec.verify(task, artifact, ctx)
        for problem in problems:
            ctx.trace("verify", f"✗ {problem}")
        return problems

    def _set_artifact(self, task_id: str, revision: int, artifact: Artifact, status: str) -> None:
        if not self.is_current(task_id, revision):
            raise asyncio.CancelledError  # stale run: stop quietly
        task = self.store.tasks[task_id]
        task.artifact = artifact
        task.status = status  # type: ignore[assignment]
        self.store.put_task(task)

    # ---------- the user's clicks ----------

    async def approve(self, task_id: str) -> Task:
        """The click: run the agent's outward action (invite, speak, create issue), then done."""
        task = self.store.tasks[task_id]
        if task.status != "needs_approval":
            raise ValueError(f"{task.title} is {task.status}, not waiting for approval")
        spec = agents.get(task.type)
        revision = task.revision
        created_before = task.artifact.external_id if task.artifact else None
        if spec.approve is not None and task.artifact is not None:
            artifact, line = await spec.approve(task, self._context(task, {}))
            if not self.is_current(task_id, revision):
                return task  # steered while the click was in flight; the new revision will ask again
            task.artifact = artifact.model_copy(update={"delivered": True})
            self.store.trace(task_id, "tool", line)
        else:
            self.store.trace(task_id, "info", "Approved")
        task.status = "done"
        self.store.put_task(task)
        # If the click created the object (a ticket got its identifier), work that used this task
        # while it was a draft re-runs and picks it up (a meeting about the ticket gets its id and
        # link, on the same event). An invite or an update creates nothing new: no re-run.
        created_now = task.artifact is not None and task.artifact.external_id and task.artifact.external_id != created_before
        if created_now:
            for other in list(self.store.tasks.values()):
                if task_id in other.depends_on and other.status not in ("dismissed", "detected", "blocked"):
                    self.restart(other, f"{task.title} was created")
        self.reconcile()
        return task

    def dismiss(self, task_id: str) -> Task:
        """The user says this card is wrong. Dependants carry on without it."""
        task = self.store.tasks[task_id]
        run = self.runs.pop(task_id, None)
        if run is not None:
            run.cancel()
        self.store.trace(task_id, "info", "Dismissed")
        was_waiting = task.status == "needs_approval"
        task.status = "dismissed"
        self.store.put_task(task)
        spec = agents.get(task.type)
        if was_waiting and spec and spec.on_dismiss:
            self._fire(spec.on_dismiss(task.model_copy(deep=True)), f"{task_id} on_dismiss")
        self.reconcile()
        return task
