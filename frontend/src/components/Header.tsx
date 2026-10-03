// Top bar of the live screen: listening dot (pulses with mic level), call clock, controls.

import { useEffect, useState } from "react";
import type { BotState, MeetingState } from "../api/contract";

const BOT_LABEL: Record<BotState["state"], string> = {
  none: "",
  joining: "Bot joining…",
  waiting_room: "Bot in lobby: admit “Adjourn”",
  in_call: "Bot in call",
  left: "Bot left",
  error: "Bot error",
};

interface Props {
  state: MeetingState;
  startedAt: string | null;
  level: number; // 0..1 peak of the last mic frame
  micOn: boolean;
  connected: boolean;
  bot: BotState;
  onListen: () => void;
  onBotLeave: () => void;
  onStop: () => void;
  onReset: () => void;
}

export function Header(props: Props) {
  const live = props.state === "live";
  const clock = useClock(props.startedAt, live);
  const botActive = ["joining", "waiting_room", "in_call"].includes(props.bot.state);
  const hearing = props.micOn || props.bot.state === "in_call";
  const title = !props.connected ? "Reconnecting…" : live ? (hearing ? "Listening" : botActive ? "Starting" : "Mic off") : "Call ended";
  return (
    <header className="header">
      <span className={`dot ${live && hearing ? "on" : ""}`} style={{ transform: `scale(${1 + Math.min(props.level * 3, 0.8)})` }} />
      <span className="header-title">{title}</span>
      <span className="clock">{clock}</span>
      <span className="spacer" />
      {props.bot.state !== "none" && <span className={`bot-chip ${props.bot.state}`}>{BOT_LABEL[props.bot.state]}</span>}
      {botActive && <button className="small" onClick={props.onBotLeave}>Bot leave</button>}
      {live && !props.micOn && !botActive && <button className="small" onClick={props.onListen}>Mic on</button>}
      {live && <button className="small" onClick={props.onStop}>End</button>}
      <button className="small ghost" onClick={props.onReset} title="Clear everything and go back to setup">Reset</button>
    </header>
  );
}

/** "m:ss" since the meeting started, ticking while live. */
function useClock(startedAt: string | null, running: boolean): string {
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
