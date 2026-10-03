// Card faces: how each task type looks on the panel. The backend registry supplies the label
// and button text (AgentInfo); this file supplies what is drawn inside the card.
//
// To add a face for a new agent: add an entry to FACES. Without one, a card still works with
// DEFAULT (first line of whatever the artifact has).

import type { ReactNode } from "react";
import { speak } from "../audio/speak";
import type { Person, Task } from "../api/contract";

export interface CardFace {
  /** The one line under the title, e.g. "Thu 8 Oct, 14:00–14:30 · no conflicts". */
  facts: (task: Task, people: Person[]) => string;
  /** The full output, shown when the card is expanded. */
  details?: (task: Task) => ReactNode;
  /** Links next to the approval button ("Open in Calendar", source chips). */
  links?: (task: Task) => ReactNode;
  /** Runs in the panel after the click succeeded (e.g. speak the answer). */
  afterApprove?: (task: Task, ctx: { botInCall: boolean }) => void | Promise<void>;
  /** Headline shown while the card waits for its click, e.g. "✋ Adjourn has an answer". */
  waiting?: string;
  /** Status shown once done, if "Done" is not the right word (e.g. "Spoken"). */
  doneLabel?: string;
}

const firstLine = (text: string | null | undefined) =>
  (text ?? "")
    .split("\n")
    .map((l) => l.replace(/^[-*#>\s]+/, "").replace(/\*+/g, "").trim())
    .find((l) => l && !/^sample brief/i.test(l)) ?? "";

const time = (d: Date) => d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
const day = (d: Date) => d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });

const Pre = ({ children }: { children: string }) => <pre className="body">{children}</pre>;

const sourceChips = (task: Task) =>
  task.artifact?.sources.map((s) => (
    <a key={s.url} className="source" href={s.url} target="_blank" rel="noreferrer" title={s.url}>
      {s.title}
    </a>
  ));

const openLink = (label: string) => (task: Task) =>
  task.artifact?.link && task.artifact.external_id ? (
    <a className="button small" href={task.artifact.link} target="_blank" rel="noreferrer">{label}</a>
  ) : null;

const DEFAULT: CardFace = {
  facts: (t) => firstLine(t.artifact?.content ?? t.artifact?.body ?? t.artifact?.title),
  details: (t) => (t.artifact?.content || t.artifact?.body ? <Pre>{t.artifact.content ?? t.artifact.body ?? ""}</Pre> : null),
};

const FACES: Record<string, CardFace> = {
  answer: {
    facts: (t) => firstLine(t.artifact?.content),
    details: (t) => <Pre>{t.artifact?.content ?? ""}</Pre>,
    links: sourceChips,
    // with a meeting bot in the call, the backend already had the bot say it
    afterApprove: (t, { botInCall }) => (botInCall ? undefined : speak(t.artifact?.content ?? "")),
    waiting: "✋ Adjourn has an answer",
    doneLabel: "Spoken",
  },
  issue: {
    facts: (t) => {
      const a = t.artifact!;
      return a.external_id ? `#${a.external_id} · ${a.title}` : `Draft · ${a.title}`;
    },
    details: (t) => <Pre>{`# ${t.artifact?.title}\n\n${t.artifact?.body ?? ""}`}</Pre>,
    links: openLink("Open on GitHub"),
  },
  linear: {
    facts: (t, people) => {
      const a = t.artifact!;
      const who = a.to.length ? people.find((p) => p.email === a.to[0])?.name ?? a.to[0] : "unassigned";
      return `${a.external_id ?? "Plan"} · ${a.title} → ${who}`;
    },
    details: (t) => <Pre>{`# ${t.artifact?.title}\n\n${t.artifact?.body ?? ""}${t.artifact?.note ? `\n\n${t.artifact.note}` : ""}`}</Pre>,
    links: openLink("Open in Linear"),
  },
  schedule: {
    facts: (t) => {
      const a = t.artifact!;
      if (!a.start || !a.end) return "";
      const start = new Date(a.start);
      const end = new Date(a.end);
      return `${day(start)}, ${time(start)}–${time(end)}${a.note ? ` · ${a.note}` : ""}${a.delivered ? " · invite sent" : ""}`;
    },
    links: (t) => (
      <>
        {t.artifact?.meet_link ? (
          <a className="button small" href={t.artifact.meet_link} target="_blank" rel="noreferrer">Join Meet</a>
        ) : null}
        {openLink("Open in Calendar")(t)}
      </>
    ),
  },
  email: {
    facts: (t, people) => {
      const a = t.artifact!;
      const names = a.to.map((e) => people.find((p) => p.email.toLowerCase() === e.toLowerCase())?.name ?? e);
      return `To ${names.join(", ")} · ${a.subject ?? ""}`;
    },
    details: (t) => <Pre>{`To: ${t.artifact?.to.join(", ")}\nSubject: ${t.artifact?.subject}\n\n${t.artifact?.body ?? ""}`}</Pre>,
    // a draft is private, so its link is shown in every mode (links mode: a prefilled compose page)
    links: (t) =>
      t.artifact?.link ? (
        <a className="button small" href={t.artifact.link} target="_blank" rel="noreferrer">Open in Gmail</a>
      ) : null,
  },
  research: {
    facts: (t) => firstLine(t.artifact?.content),
    details: (t) => <Pre>{t.artifact?.content ?? ""}</Pre>,
    links: sourceChips,
  },
};

export function face(type: string): CardFace {
  return FACES[type] ?? DEFAULT;
}
