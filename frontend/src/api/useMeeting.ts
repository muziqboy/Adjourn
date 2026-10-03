// The panel's whole connection to the backend: one WebSocket (/ws) folded into a view by a
// reducer, plus the POST actions. Components never fetch on their own.

import { useCallback, useEffect, useReducer, useRef } from "react";
import type { AgentInfo, MeetingContext, MeetingState, Modes, ServerEvent, Snapshot, Task, Usage } from "./contract";

export interface MeetingView {
  connected: boolean;
  state: MeetingState;
  meeting: MeetingContext | null;
  lines: string[];
  interim: string;
  tasks: Task[]; // creation order
  usage: Usage;
  modes: Modes;
  agents: Record<string, AgentInfo>; // by type
}

const initial: MeetingView = {
  connected: false,
  state: "idle",
  meeting: null,
  lines: [],
  interim: "",
  tasks: [],
  usage: { calls: 0, tokens_in: 0, tokens_out: 0 },
  modes: { llm: "?", google: "?", github: "?" },
  agents: {},
};

type Action = { type: "connected"; value: boolean } | { type: "event"; event: ServerEvent };

function fromSnapshot(s: Snapshot): Partial<MeetingView> {
  return {
    state: s.state,
    meeting: s.meeting,
    lines: s.lines.map((l) => l.text),
    interim: "",
    tasks: s.tasks,
    usage: s.usage,
    modes: s.modes,
    agents: Object.fromEntries(s.agents.map((a) => [a.type, a])),
  };
}

function upsert(tasks: Task[], task: Task): Task[] {
  const i = tasks.findIndex((t) => t.id === task.id);
  if (i === -1) return [...tasks, task];
  const next = tasks.slice();
  next[i] = task;
  return next;
}

/** Folds one server event into the view. task.updated always carries the full task. */
function reducer(view: MeetingView, action: Action): MeetingView {
  if (action.type === "connected") return { ...view, connected: action.value };
  const { event } = action;
  switch (event.type) {
    case "snapshot":
      return { ...view, ...fromSnapshot(event.data) };
    case "meeting.state":
      return { ...view, state: event.data.state, meeting: event.data.meeting ?? view.meeting };
    case "transcript.delta":
      return event.data.final
        ? { ...view, lines: [...view.lines.slice(-49), event.data.text], interim: "" }
        : { ...view, interim: event.data.text };
    case "task.created":
    case "task.updated":
      return { ...view, tasks: upsert(view.tasks, event.data) };
    case "task.trace": {
      const { task_id, entry } = event.data;
      return {
        ...view,
        tasks: view.tasks.map((t) =>
          t.id === task_id && !t.trace.some((e) => e.ts === entry.ts && e.text === entry.text)
            ? { ...t, trace: [...t.trace, entry] }
            : t,
        ),
      };
    }
    case "usage.updated":
      return { ...view, usage: event.data };
    default:
      return view;
  }
}

async function post(path: string, body?: unknown) {
  const res = await fetch(path, {
    method: "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error((await res.text()) || res.statusText);
  return res.json();
}

export function useMeeting() {
  const [view, dispatch] = useReducer(reducer, initial);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let closed = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const socket = new WebSocket(`${proto}://${location.host}/ws`);
      socketRef.current = socket;
      socket.onopen = () => dispatch({ type: "connected", value: true });
      socket.onmessage = (msg) => dispatch({ type: "event", event: JSON.parse(msg.data) });
      socket.onclose = () => {
        dispatch({ type: "connected", value: false });
        if (!closed) retry = setTimeout(connect, 1000);
      };
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(retry);
      socketRef.current?.close();
    };
  }, []);

  return {
    view,
    start: useCallback((meeting: MeetingContext) => post("/api/meeting/start", meeting), []),
    stop: useCallback(() => post("/api/meeting/stop"), []),
    say: useCallback((text: string) => post("/api/transcript", { text }), []),
    replay: useCallback(() => post("/api/replay?name=demo_call&speed=1"), []),
    approve: useCallback((id: string): Promise<Task> => post(`/api/tasks/${id}/approve`), []),
    dismiss: useCallback((id: string) => post(`/api/tasks/${id}/dismiss`), []),
    reset: useCallback(() => post("/api/reset"), []),
  };
}
