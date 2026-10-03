// One task on the panel. Read top to bottom it answers: what is it, what does it say, what is it
// doing right now, why did it change, what is it connected to, and what can I do about it.
// Everything type-specific comes from the card face (src/agents) and the backend's AgentInfo.

import { useEffect, useRef, useState } from "react";
import { face } from "../agents";
import type { AgentInfo, Person, Task, TraceEntry } from "../api/contract";

const STATUS_LABEL: Record<Task["status"], string> = {
  detected: "Starting",
  blocked: "Waiting",
  running: "Working",
  verifying: "Checking",
  needs_approval: "Needs you",
  done: "Done",
  failed: "Failed",
  dismissed: "Dismissed",
};

// The trace in plain words: what kind of step each line was.
const STEP_LABEL: Record<TraceEntry["kind"], string> = {
  info: "Step",
  llm: "Thought",
  tool: "Did",
  verify: "Checked",
  steer: "Changed",
  error: "Problem",
};

interface Props {
  task: Task;
  tasks: Task[];
  info: AgentInfo | undefined;
  people: Person[];
  botInCall: boolean;
  onApprove: () => Promise<Task>;
  onDismiss: () => Promise<unknown>;
  onSteer: (instruction: string) => Promise<unknown>;
  onReveal: (id: string) => void;
}

export function TaskCard({ task, tasks, info, people, botInCall, onApprove, onDismiss, onSteer, onReveal }: Props) {
  const [open, setOpen] = useState(false);
  const [steering, setSteering] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const flash = useFlashOnRevision(task.revision);
  const f = face(task.type);
  const art = task.artifact;
  const working = ["detected", "blocked", "running", "verifying"].includes(task.status);
  const uses = task.depends_on.map((id) => tasks.find((t) => t.id === id)).filter((t): t is Task => !!t);
  const feeds = tasks.filter((t) => t.depends_on.includes(task.id) && t.status !== "dismissed");
  const lastChange = task.revision > 1 ? [...task.trace].reverse().find((e) => e.kind === "steer") : undefined;

  // The click. Links mode opens the prefilled page inside the click handler (popup blockers),
  // then tells the backend; otherwise the backend does the outward action.
  const approve = async () => {
    setError(null);
    if (info?.opens_link && art?.link) window.open(art.link, "_blank");
    setBusy(true);
    try {
      const done = await onApprove();
      await f.afterApprove?.(done, { botInCall });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const approvalLabel = task.review
    ? "Looks fine"
    : (art?.delivered ? info?.approval_again : info?.approval) ?? "Approve";
  const final = task.status === "done" || task.status === "dismissed";

  return (
    <article id={`task-${task.id}`} className={`card type-${task.type} ${task.status} ${flash ? "flash" : ""}`}>
      <div className="card-top">
        <span className="type">{info?.label ?? task.type}</span>
        {task.revision > 1 && <span className="rev" title="Revision: it was changed this many times">v{task.revision}</span>}
        <span className="spacer" />
        <span className={`status ${task.status}`}>
          {working && task.status !== "blocked" && <span className="spinner" />}
          {statusLabel(task, f.waiting, f.doneLabel)}
        </span>
        {!final && (
          <button className="icon dismiss" title="Dismiss: this card is wrong" aria-label="Dismiss" onClick={() => void onDismiss()}>
            ×
          </button>
        )}
      </div>
      <h2>{task.title}</h2>
      {art && <p className="facts">{f.facts(task, people)}</p>}
      {working && <p className="live">{liveLine(task, uses)}</p>}
      {lastChange && !working && <p className="changed">↻ {lastChange.text.replace(/^v\d+: /, "")}</p>}

      {(uses.length > 0 || feeds.length > 0) && (
        <div className="links-row">
          {uses.length > 0 && <GraphChips label="Uses" tasks={uses} onReveal={onReveal} />}
          {feeds.length > 0 && <GraphChips label="Feeds" tasks={feeds} onReveal={onReveal} />}
        </div>
      )}
      {task.review && <p className="review">Review: {task.review}</p>}
      {error && <p className="review">{error}</p>}

      {!steering && (
        <div className="actions">
          {task.status === "needs_approval" && (
            <button className={task.review ? "small" : "primary small"} onClick={approve} disabled={busy}>
              {busy ? "Working…" : approvalLabel}
            </button>
          )}
          {art && f.links?.(task)}
          {task.status !== "dismissed" && (
            <button className="small ghost" onClick={() => setSteering(true)}>
              Change…
            </button>
          )}
        </div>
      )}
      {steering && <SteerBox hint={f.steerHint} onSteer={onSteer} onClose={() => setSteering(false)} />}

      <button className="link toggle" onClick={() => setOpen(!open)}>
        {open ? "Hide details" : `Details · ${task.trace.length} steps`}
      </button>
      {open && (
        <div className="details">
          {art && f.details?.(task)}
          <ol className="trace">
            {task.trace.map((e, i) => (
              <li key={i} className={e.kind}>
                <span className="kind">{STEP_LABEL[e.kind] ?? e.kind}</span>
                <span>{e.text}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </article>
  );
}

/** "Uses: Answer ✓ · Follow-up ◌": the task's edges in the graph; a click scrolls to that card. */
function GraphChips({ label, tasks, onReveal }: { label: string; tasks: Task[]; onReveal: (id: string) => void }) {
  return (
    <span className="graph">
      <span className="graph-label">{label}</span>
      {tasks.map((t) => (
        <button key={t.id} className={`chip ${t.status}`} onClick={() => onReveal(t.id)} title={`${STATUS_LABEL[t.status]}: ${t.title}`}>
          <span className="chip-dot" />
          {t.title}
        </button>
      ))}
    </span>
  );
}

/** The inline "Change…" box: a typed correction that steers this one task. */
function SteerBox({ hint, onSteer, onClose }: { hint?: string; onSteer: (text: string) => Promise<unknown>; onClose: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await onSteer(text.trim());
      onClose();
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };
  return (
    <form className="steer" onSubmit={submit}>
      <input
        autoFocus
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => e.key === "Escape" && onClose()}
        placeholder={hint ?? "What should change?"}
        disabled={busy}
      />
      <button className="small primary" disabled={busy || !text.trim()}>{busy ? "…" : "Apply"}</button>
      <button type="button" className="small ghost" onClick={onClose}>Cancel</button>
      {error && <p className="review">{error}</p>}
    </form>
  );
}

/** What a working card is doing right now, in one line: its latest step, or what it waits for. */
function liveLine(task: Task, uses: Task[]): string {
  if (task.status === "blocked") {
    const pending = uses.filter((t) => !["needs_approval", "done", "failed", "dismissed"].includes(t.status));
    return pending.length ? `Waiting for ${pending.map((t) => t.title).join(", ")}` : "Waiting";
  }
  const last = task.trace[task.trace.length - 1];
  if (!last || task.status === "detected") return "Starting…";
  return last.kind === "steer" ? `Redoing: ${last.text.replace(/^v\d+: /, "")}` : last.text;
}

function statusLabel(task: Task, waiting?: string, doneLabel?: string): string {
  if (task.status === "needs_approval" && waiting && !task.review) return waiting;
  if (task.status === "done" && doneLabel && task.artifact?.delivered) return doneLabel;
  return STATUS_LABEL[task.status];
}

/** True for a moment whenever the task's revision goes up (a steer or an upstream change). */
function useFlashOnRevision(revision: number): boolean {
  const [flash, setFlash] = useState(false);
  const last = useRef(revision);
  useEffect(() => {
    if (revision <= last.current) return;
    last.current = revision;
    setFlash(true);
    const id = setTimeout(() => setFlash(false), 1600);
    return () => clearTimeout(id);
  }, [revision]);
  return flash;
}
