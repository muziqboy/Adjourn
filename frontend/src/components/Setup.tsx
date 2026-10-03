// The first screen: who is on the call, which modes are on, and "Start listening".

import { useEffect, useState } from "react";
import type { Health, MeetingContext, Person } from "../api/contract";

interface Props {
  health: Health | null;
  onStart: (meeting: MeetingContext) => Promise<void>;
}

export function Setup({ health, onStart }: Props) {
  const [me, setMe] = useState<Person>({ name: "", email: "" });
  const [others, setOthers] = useState<Person[]>([{ name: "", email: "" }]);
  const [busy, setBusy] = useState(false);

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
        Agents: <b>{health?.agents.join(", ") ?? "?"}</b>
        <br />
        Model: <b>{health?.llm ?? "?"}</b> · Google: <b>{health?.google ?? "?"}</b> · GitHub: <b>{health?.github ?? "?"}</b>
      </p>
      <p className="note">
        Use speakers, not headphones: Adjourn hears the other side through your laptop's microphone, and
        speaks its answers through the speakers. Keep this window open beside the call.
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
