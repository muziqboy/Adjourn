// Mirror of backend/app/core/contract.py. Change both files in one commit and tell the team.
//
// Task types are plain strings: each backend agent registers its own, and the panel learns
// their labels and button text from `snapshot.agents` (AgentInfo).

export type TaskStatus =
  | "detected"        // created by the intent pass, not yet scheduled
  | "blocked"         // waiting for upstream tasks (depends_on)
  | "running"         // the agent is working
  | "verifying"       // the verifier is checking the agent's output
  | "needs_approval"  // waiting for the user's click (or a failed review needs their eyes)
  | "done"
  | "failed"
  | "dismissed";      // the user said the card was wrong
export type TraceKind = "info" | "llm" | "tool" | "verify" | "steer" | "error";

/** An upstream in one of these states can feed its dependants. */
export const SETTLED: TaskStatus[] = ["needs_approval", "done", "failed", "dismissed"];

export interface Source { title: string; url: string }

/** What an agent produced. One flat shape; `kind` says which fields are used. */
export interface Artifact {
  kind: "event" | "draft" | "brief" | "answer" | "issue";
  external_id: string | null; // Calendar event id, Gmail draft id, GitHub issue number
  link: string | null;
  delivered: boolean;         // the click's outward action happened at least once
  title: string | null;       // event, issue
  start: string | null;       // event, ISO 8601 with offset
  end: string | null;
  attendees: string[];
  note: string | null;        // event: "no conflicts", "14:00 was taken, moved to 14:30"
  to: string[];               // draft
  subject: string | null;
  body: string | null;        // draft (plain text), issue (markdown)
  content: string | null;     // brief (markdown), answer (text to speak)
  sources: Source[];
}

export interface TraceEntry { ts: number; kind: TraceKind; text: string }

export interface Task {
  id: string;
  type: string;
  title: string;
  brief: string;
  status: TaskStatus;
  revision: number;                      // bumped on every steer or upstream change
  depends_on: string[];
  inputs_used: Record<string, number>;   // upstream id -> revision used
  trace: TraceEntry[];
  artifact: Artifact | null;
  review: string | null;                 // verifier notes when the review failed twice
}

export interface Person { name: string; email: string }

export interface MeetingContext {
  me: Person;
  others: Person[];
  timezone: string;
  started_at?: string | null;
}

export interface Usage { calls: number; tokens_in: number; tokens_out: number }

/** Card metadata for one agent type, from the backend registry. */
export interface AgentInfo {
  type: string;
  label: string;                 // card heading
  approval: string | null;       // button label for the click; null = no click
  approval_again: string | null; // label once delivered ("Send update")
  opens_link: boolean;           // links mode: the click opens artifact.link
}

export type MeetingState = "idle" | "live" | "ended";

export interface Modes { llm: string; google: string; github: string }

/** A transcript line. `speaker` is known when the meeting bot heard it (Meet captions carry names). */
export interface Line { text: string; speaker: string | null }

/** The Recall.ai meeting bot (backend/app/listen/bot.py). */
export interface BotState {
  state: "none" | "joining" | "waiting_room" | "in_call" | "left" | "error";
  bot_id: string | null;
}

export interface Snapshot {
  state: MeetingState;
  meeting: MeetingContext | null;
  lines: { ts: number; text: string; speaker?: string | null }[];
  tasks: Task[];
  usage: Usage;
  modes: Modes;
  agents: AgentInfo[];
  bot: BotState;
}

export type ServerEvent =
  | { seq: number; ts: number; type: "snapshot"; data: Snapshot }
  | { seq: number; ts: number; type: "meeting.state"; data: { state: MeetingState; meeting?: MeetingContext } }
  | { seq: number; ts: number; type: "transcript.delta"; data: { text: string; final: boolean; speaker?: string | null } }
  | { seq: number; ts: number; type: "bot.state"; data: BotState }
  | { seq: number; ts: number; type: "task.created"; data: Task }
  | { seq: number; ts: number; type: "task.updated"; data: Task }
  | { seq: number; ts: number; type: "task.trace"; data: { task_id: string; entry: TraceEntry } }
  | { seq: number; ts: number; type: "usage.updated"; data: Usage };

export interface Health {
  ok: boolean;
  llm: string;
  google: string;
  github: string;
  agents: string[];
  timezone: string;
  defaults: { me: Person; others: Person[] };
}
