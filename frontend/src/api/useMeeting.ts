// The panel's whole connection to the backend: one WebSocket (/ws) folded into a view by a
// reducer, plus the POST actions. Components never fetch on their own.

import { useCallback, useEffect, useReducer, useRef } from "react";
import type { AgentInfo, AutoJoinState, BotState, Line, MeetingContext, MeetingState, Modes, ServerEvent, Snapshot, Task, Usage } from "./contract";

export interface MeetingView {
  connected: boolean;
  state: MeetingState;
  meeting: MeetingContext | null;
  lines: Line[];
  interim: Line | null; // the line being spoken, not final yet
  tasks: Task[]; // creation order
  usage: Usage;
  modes: Modes;
  agents: Record<string, AgentInfo>; // by type
  bot: BotState;
  autojoin: AutoJoinState; // calendar auto-join
}

const initial: MeetingView = {
  connected: false,
  state: "idle",
  meeting: null,
  lines: [],
  interim: null,
  tasks: [],
  usage: { calls: 0, tokens_in: 0, tokens_out: 0 },
  modes: { llm: "?", google: "?", github: "?" },
  agents: {},
  bot: { state: "none", bot_id: null },
  autojoin: { enabled: false },
};

type Action = { type: "connected"; value: boolean } | { type: "event"; event: ServerEvent };

function fromSnapshot(s: Snapshot): Partial<MeetingView> {
  return {
    state: s.state,
    meeting: s.meeting,
    lines: s.lines.map((l) => ({ text: l.text, speaker: l.speaker ?? null })),
    interim: null,
    tasks: s.tasks,
    usage: s.usage,
    modes: s.modes,
    agents: Object.fromEntries(s.agents.map((a) => [a.type, a])),
    bot: s.bot ?? initial.bot,
    autojoin: s.autojoin ?? initial.autojoin,
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
    case "transcript.delta": {
      const line = { text: event.data.text, speaker: event.data.speaker ?? null };
      return event.data.final
        ? { ...view, lines: [...view.lines.slice(-49), line], interim: null }
        : { ...view, interim: line };
    }
    case "bot.state":
      return { ...view, bot: event.data };
    case "autojoin.state":
      return { ...view, autojoin: event.data };
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
  if (!res.ok) {
    // FastAPI errors are {"detail": "..."}; show just the sentence
    const text = await res.text();
    let message = text || res.statusText;
    try {
      message = JSON.parse(text).detail ?? message;
    } catch {
      /* not JSON */
    }
    throw new Error(message);
  }
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
    steer: useCallback((id: string, instruction: string): Promise<Task> => post(`/api/tasks/${id}/steer`, { instruction }), []),
    reset: useCallback(() => post("/api/reset"), []),
    sendBot: useCallback((meeting_url: string) => post("/api/bot/join", { meeting_url }), []),
    botLeave: useCallback(() => post("/api/bot/leave"), []),
    connectCalendar: useCallback(() => post("/api/autojoin/connect"), []),
  };
}
