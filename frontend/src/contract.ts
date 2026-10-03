// Mirror of backend/app/contract.py. Change both in one commit.

export type TaskType = "schedule" | "email" | "research";
export type TaskStatus =
  | "detected" | "blocked" | "running" | "verifying"
  | "needs_approval" | "done" | "failed";
export type TraceKind = "info" | "llm" | "tool" | "verify" | "steer" | "error";

export const SETTLED: TaskStatus[] = ["needs_approval", "done", "failed"];

export interface Source { title: string; url: string }

export interface Artifact {
  kind: "event" | "draft" | "brief";
  external_id: string | null;
  link: string | null;
  // event
  title: string | null;
  start: string | null;
  end: string | null;
  attendees: string[];
  note: string | null;
  invited: boolean;
  // draft
  to: string[];
  subject: string | null;
  body: string | null;
  // brief
  content: string | null;
  sources: Source[];
}

export interface TraceEntry { ts: number; kind: TraceKind; text: string }

export interface Task {
  id: string;
  type: TaskType;
  title: string;
  brief: string;
  status: TaskStatus;
  revision: number;
  depends_on: string[];
  inputs_used: Record<string, number>;
  trace: TraceEntry[];
  artifact: Artifact | null;
  review: string | null;
}

export interface Person { name: string; email: string }

export interface MeetingContext {
  me: Person;
  others: Person[];
  timezone: string;
  started_at?: string | null;
}

export interface Usage { calls: number; tokens_in: number; tokens_out: number }

export type MeetingState = "idle" | "live" | "ended";

export interface Snapshot {
  state: MeetingState;
  meeting: MeetingContext | null;
  lines: { ts: number; text: string }[];
  tasks: Task[];
  usage: Usage;
  modes: { llm: string; google: string };
}

export type ServerEvent =
  | { seq: number; ts: number; type: "snapshot"; data: Snapshot }
  | { seq: number; ts: number; type: "meeting.state"; data: { state: MeetingState; meeting?: MeetingContext } }
  | { seq: number; ts: number; type: "transcript.delta"; data: { text: string; final: boolean } }
  | { seq: number; ts: number; type: "task.created"; data: Task }
  | { seq: number; ts: number; type: "task.updated"; data: Task }
  | { seq: number; ts: number; type: "task.trace"; data: { task_id: string; entry: TraceEntry } }
  | { seq: number; ts: number; type: "usage.updated"; data: Usage };

export interface Health {
  ok: boolean;
  llm: string;
  google: string;
  timezone: string;
  defaults: { me: Person; others: Person[] };
}
