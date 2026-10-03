// One task on the panel: type, status, the facts that matter, the click, and the trace.
// Everything type-specific comes from the card face (src/agents) and the backend's AgentInfo.

import { useEffect, useRef, useState } from "react";
import { face } from "../agents";
import { SETTLED, type AgentInfo, type Person, type Task } from "../api/contract";

const STATUS_LABEL: Record<Task["status"], string> = {
  detected: "Detected",
  blocked: "Waiting",
  running: "Working",
  verifying: "Checking",
  needs_approval: "Needs you",
  done: "Done",
  failed: "Failed",
  dismissed: "Dismissed",
};

interface Props {
  task: Task;
  tasks: Task[];
  info: AgentInfo | undefined;
  people: Person[];
  onApprove: () => Promise<Task>;
  onDismiss: () => Promise<unknown>;
}

export function TaskCard({ task, tasks, info, people, onApprove, onDismiss }: Props) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const flash = useFlashOnRevision(task.revision);
  const f = face(task.type);
  const art = task.artifact;
  const working = ["detected", "running", "verifying"].includes(task.status);
  const waitingFor = task.depends_on
    .map((id) => tasks.find((t) => t.id === id))
    .filter((t): t is Task => !!t && !SETTLED.includes(t.status))
    .map((t) => t.title);

  // The click. Links mode opens the prefilled page inside the click handler (popup blockers),
  // then tells the backend; otherwise the backend does the outward action.
  const approve = async () => {
    setError(null);
    if (info?.opens_link && art?.link) window.open(art.link, "_blank");
    setBusy(true);
    try {
      const done = await onApprove();
      await f.afterApprove?.(done);
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
    <article className={`card type-${task.type} ${task.status} ${flash ? "flash" : ""}`}>
      <div className="card-top">
        <span className="type">{info?.label ?? task.type}</span>
        {task.revision > 1 && <span className="rev">v{task.revision}</span>}
        <span className="spacer" />
        <span className={`status ${task.status}`}>
          {working && <span className="spinner" />}
          {statusLabel(task, f.waiting, f.doneLabel)}
        </span>
        {!final && (
          <button className="icon dismiss" title="Dismiss: this card is wrong" aria-label="Dismiss" onClick={() => void onDismiss()}>
            ×
          </button>
        )}
      </div>
      <h2>{task.title}</h2>
      {art ? <p className="facts">{f.facts(task, people)}</p> : working && <p className="facts muted">{task.brief}</p>}
      {waitingFor.length > 0 && <p className="waiting">Waiting for: {waitingFor.join(", ")}</p>}
      {task.review && <p className="review">Review: {task.review}</p>}
      {error && <p className="review">{error}</p>}

      <div className="actions">
        {task.status === "needs_approval" && (
          <button className={task.review ? "small" : "primary small"} onClick={approve} disabled={busy}>
            {busy ? "Working…" : approvalLabel}
          </button>
        )}
        {art && f.links?.(task)}
      </div>

      <button className="link toggle" onClick={() => setOpen(!open)}>
        {open ? "Hide details" : `Details · ${task.trace.length} steps`}
      </button>
      {open && (
        <div className="details">
          {art && f.details?.(task)}
          <ol className="trace">
            {task.trace.map((e, i) => (
              <li key={i} className={e.kind}>
                <span className="kind">{e.kind}</span>
                <span>{e.text}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </article>
  );
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
