// The first screen: who is on the call, how Adjourn should hear it, and "Start".
// With a Meet link, the Recall.ai bot joins the call as "Adjourn" (hears names, speaks as a
// participant). Without one, the laptop microphone listens and the speakers talk.
// Below the link: calendar auto-join, where the bot joins Meet events on the calendar by itself.

import { useEffect, useState } from "react";
import type { AutoJoinState, Health, MeetingContext, Person } from "../api/contract";

interface Props {
  health: Health | null;
  onStart: (meeting: MeetingContext, meetUrl: string) => Promise<void>;
  autojoin: AutoJoinState;
  onConnectCalendar: () => Promise<unknown>;
}

export function Setup({ health, onStart, autojoin, onConnectCalendar }: Props) {
  const [me, setMe] = useState<Person>({ name: "", email: "" });
  const [others, setOthers] = useState<Person[]>([{ name: "", email: "" }]);
  const [meetUrl, setMeetUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // prefill from .env (ME_*, GUEST_*) unless the user has typed something
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
    setError(null);
    try {
      const timezone = health?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone;
      await onStart({ me, others, timezone }, meetUrl.trim());
    } catch (err) {
      setError((err as Error).message);
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

      <fieldset>
        <legend>Meeting bot (optional)</legend>
        <input
          placeholder="https://meet.google.com/abc-defg-hij"
          value={meetUrl}
          onChange={(e) => setMeetUrl(e.target.value)}
        />
        <p className="muted tiny">
          With a link, Adjourn joins the call as its own participant (admit it from the lobby).
          Without one, it listens through this laptop's microphone.
        </p>
        <AutoJoinLine autojoin={autojoin} onConnect={onConnectCalendar} />
      </fieldset>

      <p className="status-line">
        Agents: <b>{health?.agents.join(", ") ?? "?"}</b>
        <br />
        Model: <b>{health?.llm ?? "?"}</b> · Google: <b>{health?.google ?? "?"}</b> · GitHub: <b>{health?.github ?? "?"}</b>
      </p>
      {!meetUrl && (
        <p className="note">
          Laptop mode: use speakers, not headphones. Adjourn hears the other side through your laptop's
          microphone and speaks through the speakers. Keep this window open beside the call.
        </p>
      )}
      {error && <p className="review">{error}</p>}
      <button className="primary" disabled={!valid || busy}>
        {busy ? "Starting…" : meetUrl ? "Send Adjourn to the call" : "Start listening"}
      </button>
    </form>
  );
}

/** One line: "Auto-join: on for a@b · next: Standup at 14:00", or off with a connect button. */
function AutoJoinLine({ autojoin, onConnect }: { autojoin: AutoJoinState; onConnect: () => Promise<unknown> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const connect = async () => {
    setBusy(true);
    setError(null);
    try {
      await onConnect();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const next = autojoin.next_event;
  // the request's own error first; the backend's covers retries in its loop
  const shown = error ?? autojoin.error;
  return (
    <>
      <p className="muted tiny">
        {autojoin.enabled ? (
          <>
            Auto-join: on for {autojoin.email}
            {next ? ` · next: ${next.title} at ${when(next.start_time)}` : " · no Meet events ahead"}
          </>
        ) : (
          <>
            Auto-join: off ·{" "}
            <button type="button" className="link" disabled={busy} onClick={connect}>
              {busy ? "Connecting…" : "Connect calendar"}
            </button>
          </>
        )}
      </p>
      {shown && <p className="review">{shown}</p>}
    </>
  );
}

/** "14:00" today, "Mon 14:00" on another day. */
function when(iso: string): string {
  const d = new Date(iso);
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  return d.toDateString() === new Date().toDateString() ? time : `${d.toLocaleDateString([], { weekday: "short" })} ${time}`;
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
