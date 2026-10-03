import { useEffect, useRef, useState } from "react";
import { startCapture, type Capture } from "./audio";
import { SETTLED, type Health, type MeetingContext, type Person, type Task } from "./contract";
import { useMeeting } from "./useMeeting";

export default function App() {
  const { view, start, stop, say, replay, approve, reset } = useMeeting();
  const [health, setHealth] = useState<Health | null>(null);
  const [level, setLevel] = useState(0);
  const [micError, setMicError] = useState<string | null>(null);
  const capture = useRef<Capture | null>(null);

  useEffect(() => {
    fetch("/api/health").then((r) => r.json()).then(setHealth).catch(() => setHealth(null));
  }, [view.connected]);

  const listen = async () => {
    setMicError(null);
    try {
      capture.current = await startCapture(setLevel);
    } catch (err) {
      setMicError(`Microphone unavailable (${(err as Error).message}). Type or replay instead.`);
    }
  };

  const stopListening = () => {
    capture.current?.stop();
    capture.current = null;
  };

  const onStart = async (meeting: MeetingContext) => {
    await start(meeting);
    await listen();
  };

  const onStop = async () => {
    stopListening();
    await stop();
  };

  const onReset = async () => {
    stopListening();
    await reset();
  };

  if (!view.connected && view.state === "idle") {
    return <div className="app"><p className="muted pad">Connecting to the Adjourn backend on port 8010…</p></div>;
  }

  if (view.state === "idle") {
    return <Setup health={health} onStart={onStart} />;
  }

  return (
    <div className="app">
      <Header
        state={view.state}
        startedAt={view.meeting?.started_at ?? null}
        level={level}
        micOn={capture.current !== null}
        connected={view.connected}
        onStop={onStop}
        onReset={onReset}
        onListen={listen}
      />
      {micError && <p className="warn">{micError}</p>}
      <Transcript lines={view.lines} interim={view.interim} />
      <main className="tasks">
        {view.tasks.length === 0 && (
          <p className="muted empty">When someone commits to something, it shows up here.</p>
        )}
        {view.tasks.map((task) => (
          <Card
            key={task.id}
            task={task}
            tasks={view.tasks}
            people={[...(view.meeting?.others ?? []), ...(view.meeting ? [view.meeting.me] : [])]}
            googleMode={view.modes.google}
            onApprove={() => approve(task.id)}
          />
        ))}
      </main>
      <Footer onSay={say} onReplay={replay} modes={view.modes} usage={view.usage} />
    </div>
  );
}

// ---------- setup ----------

function Setup({ health, onStart }: { health: Health | null; onStart: (m: MeetingContext) => Promise<void> }) {
  const [me, setMe] = useState<Person>({ name: "", email: "" });
  const [others, setOthers] = useState<Person[]>([{ name: "", email: "" }]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!health) return;
    setMe((m) => (m.name || m.email ? m : health.defaults.me));
    setOthers((o) => (o.some((p) => p.name || p.email) ? o : health.defaults.others));
  }, [health]);

  const valid = me.name && me.email && others.every((p) => p.name && p.email);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    try {
      await onStart({ me, others, timezone: health?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone });
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="app setup" onSubmit={submit}>
      <h1>Adjourn</h1>
      <p className="muted">Sits beside your call and does the follow-ups before you hang up.</p>

      <fieldset>
        <legend>You</legend>
        <PersonRow person={me} onChange={setMe} />
      </fieldset>

      <fieldset>
        <legend>On the call</legend>
        {others.map((p, i) => (
          <PersonRow
            key={i}
            person={p}
            onChange={(next) => setOthers(others.map((o, j) => (j === i ? next : o)))}
            onRemove={others.length > 1 ? () => setOthers(others.filter((_, j) => j !== i)) : undefined}
          />
        ))}
        <button type="button" className="link" onClick={() => setOthers([...others, { name: "", email: "" }])}>
          + Add person
        </button>
      </fieldset>

      <p className="status-line">
        Model: <b>{health?.llm ?? "?"}</b> · Google: <b>{health?.google ?? "?"}</b>
        {health?.google === "links" && " (opens prefilled Calendar and Gmail pages)"}
        {health?.google === "mock" && " (nothing reaches Google)"}
      </p>
      <p className="note">
        Use speakers, not headphones: Adjourn hears the other side through your laptop's microphone.
        Keep this window open beside the call.
      </p>
      <button className="primary" disabled={!valid || busy}>
        {busy ? "Starting…" : "Start listening"}
      </button>
    </form>
  );
}

function PersonRow({ person, onChange, onRemove }: { person: Person; onChange: (p: Person) => void; onRemove?: () => void }) {
  return (
    <div className="person">
      <input placeholder="Name" value={person.name} onChange={(e) => onChange({ ...person, name: e.target.value })} />
      <input
        placeholder="email@gmail.com"
        type="email"
        value={person.email}
        onChange={(e) => onChange({ ...person, email: e.target.value })}
      />
      {onRemove && (
        <button type="button" className="icon" aria-label="Remove" onClick={onRemove}>×</button>
      )}
    </div>
  );
}

// ---------- live ----------

function Header(props: {
  state: string;
  startedAt: string | null;
  level: number;
  micOn: boolean;
  connected: boolean;
  onStop: () => void;
  onReset: () => void;
  onListen: () => void;
}) {
  const clock = useClock(props.startedAt, props.state === "live");
  const live = props.state === "live";
  return (
    <header className="header">
      <span
        className={`dot ${live && props.micOn ? "on" : ""}`}
        style={{ transform: `scale(${1 + Math.min(props.level * 3, 0.8)})` }}
      />
      <span className="header-title">
        {!props.connected ? "Reconnecting…" : live ? (props.micOn ? "Listening" : "Live (mic off)") : "Call ended"}
      </span>
      <span className="clock">{clock}</span>
      <span className="spacer" />
      {live && !props.micOn && <button className="small" onClick={props.onListen}>Mic on</button>}
      {live && <button className="small" onClick={props.onStop}>End</button>}
      <button className="small ghost" onClick={props.onReset} title="Clear everything and go back to setup">Reset</button>
    </header>
  );
}

function useClock(startedAt: string | null, running: boolean) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [running]);
  if (!startedAt) return "";
  const s = Math.max(0, Math.floor((now - Date.parse(startedAt)) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function Transcript({ lines, interim }: { lines: string[]; interim: string }) {
  const last = lines.slice(-2);
  return (
    <section className="transcript" aria-live="polite">
      {last.length === 0 && !interim && <p className="muted">Transcript appears here.</p>}
      {last.map((line, i) => <p key={lines.length - last.length + i}>{line}</p>)}
      {interim && <p className="interim">{interim}</p>}
    </section>
  );
}

const TYPE_LABEL: Record<Task["type"], string> = { research: "Research", email: "Email", schedule: "Schedule" };
const STATUS_LABEL: Record<Task["status"], string> = {
  detected: "Detected",
  blocked: "Waiting",
  running: "Working",
  verifying: "Checking",
  needs_approval: "Needs you",
  done: "Done",
  failed: "Failed",
};

function Card({ task, tasks, people, googleMode, onApprove }: {
  task: Task;
  tasks: Task[];
  people: Person[];
  googleMode: string;
  onApprove: () => Promise<unknown>;
}) {
  const [open, setOpen] = useState(false);
  const [flash, setFlash] = useState(false);
  const [approving, setApproving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastRevision = useRef(task.revision);

  useEffect(() => {
    if (task.revision > lastRevision.current) {
      setFlash(true);
      const id = setTimeout(() => setFlash(false), 1600);
      lastRevision.current = task.revision;
      return () => clearTimeout(id);
    }
  }, [task.revision]);

  const waitingFor = task.depends_on
    .map((id) => tasks.find((t) => t.id === id))
    .filter((t): t is Task => !!t && !SETTLED.includes(t.status))
    .map((t) => t.title);
  const art = task.artifact;
  const working = ["detected", "running", "verifying"].includes(task.status);

  const sendInvite = async () => {
    setError(null);
    // links mode: the prefilled Calendar page is the invite; open it inside the click
    if (task.type === "schedule" && googleMode === "links" && art?.link) window.open(art.link, "_blank");
    setApproving(true);
    try {
      await onApprove();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setApproving(false);
    }
  };

  return (
    <article className={`card ${task.type} ${task.status} ${flash ? "flash" : ""}`}>
      <div className="card-top">
        <span className="type">{TYPE_LABEL[task.type]}</span>
        {task.revision > 1 && <span className="rev">v{task.revision}</span>}
        <span className="spacer" />
        <span className={`status ${task.status}`}>
          {working && <span className="spinner" />}
          {STATUS_LABEL[task.status]}
        </span>
      </div>
      <h2>{task.title}</h2>
      {art && <p className="facts">{facts(task, people)}</p>}
      {!art && working && <p className="facts muted">{task.brief}</p>}
      {waitingFor.length > 0 && <p className="waiting">Waiting for: {waitingFor.join(", ")}</p>}
      {task.review && <p className="review">Review: {task.review}</p>}
      {error && <p className="review">{error}</p>}

      <div className="actions">
        {task.type === "schedule" && task.status === "needs_approval" && (
          <button className="primary small" onClick={sendInvite} disabled={approving}>
            {approving ? "Sending…" : art?.invited ? "Send update" : "Send invite"}
          </button>
        )}
        {task.type !== "schedule" && task.status === "needs_approval" && (
          <button className="small" onClick={sendInvite} disabled={approving}>Looks fine</button>
        )}
        {task.type === "schedule" && art?.link && googleMode !== "links" && (
          <a className="button small" href={art.link} target="_blank" rel="noreferrer">Open in Calendar</a>
        )}
        {task.type === "email" && art?.link && (
          <a className="button small" href={art.link} target="_blank" rel="noreferrer">Open in Gmail</a>
        )}
        {task.type === "research" && art?.sources.map((s) => (
          <a key={s.url} className="source" href={s.url} target="_blank" rel="noreferrer" title={s.url}>
            {s.title}
          </a>
        ))}
      </div>

      <button className="link toggle" onClick={() => setOpen(!open)}>
        {open ? "Hide details" : `Details · ${task.trace.length} steps`}
      </button>
      {open && (
        <div className="details">
          {art?.kind === "brief" && art.content && <pre className="body">{art.content}</pre>}
          {art?.kind === "draft" && art.body && (
            <pre className="body">{`To: ${art.to.join(", ")}\nSubject: ${art.subject}\n\n${art.body}`}</pre>
          )}
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

function facts(task: Task, people: Person[]): string {
  const art = task.artifact!;
  if (art.kind === "event" && art.start && art.end) {
    const start = new Date(art.start);
    const end = new Date(art.end);
    const day = start.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
    const time = (d: Date) => d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    const invited = art.invited ? " · invite sent" : "";
    return `${day}, ${time(start)}–${time(end)}${art.note ? ` · ${art.note}` : ""}${invited}`;
  }
  if (art.kind === "draft") {
    const names = art.to.map((email) => people.find((p) => p.email.toLowerCase() === email.toLowerCase())?.name ?? email);
    return `To ${names.join(", ")} · ${art.subject ?? ""}`;
  }
  const first = (art.content ?? "")
    .split("\n")
    .map((l) => l.replace(/^[-*#>\s]+/, "").replace(/\*+/g, "").trim())
    .find((l) => l && !/^sample brief/i.test(l));
  return first ?? "";
}

function Footer({ onSay, onReplay, modes, usage }: {
  onSay: (text: string) => Promise<unknown>;
  onReplay: () => Promise<unknown>;
  modes: { llm: string; google: string };
  usage: { calls: number; tokens_in: number; tokens_out: number };
}) {
  const [text, setText] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    const line = text;
    setText("");
    await onSay(line);
  };
  return (
    <footer className="footer">
      <form onSubmit={submit} className="say">
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Type what was said…" />
        <button className="small">Say</button>
      </form>
      <div className="footer-row">
        <button className="link" onClick={() => void onReplay()}>Replay demo</button>
        <span className="spacer" />
        <span className="muted tiny">
          {modes.llm} · {modes.google} · {usage.calls} calls · {Math.round((usage.tokens_in + usage.tokens_out) / 1000)}k tok
        </span>
      </div>
    </footer>
  );
}
