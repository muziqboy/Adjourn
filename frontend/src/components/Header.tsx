// Top bar of the live screen: listening dot (pulses with mic level), call clock, the bot, and the
// controls. Rare or destructive actions (bot leave, reset) sit in the ⋯ menu, away from "End".

import { useEffect, useRef, useState } from "react";
import type { BotState, MeetingState } from "../api/contract";

const BOT_LABEL: Record<BotState["state"], string> = {
  none: "",
  joining: "Bot joining…",
  waiting_room: "Admit “Adjourn” in Meet",
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
      {props.bot.state !== "none" && <span className={`bot-chip ${props.bot.state}`}>{BOT_LABEL[props.bot.state]}</span>}
      <span className="spacer" />
      {live && !props.micOn && !botActive && <button className="small" onClick={props.onListen}>Mic on</button>}
      {live && <button className="small" onClick={props.onStop}>End</button>}
      <Menu>
        {botActive && <button onClick={props.onBotLeave}>Make the bot leave</button>}
        <button
          className="danger"
          onClick={() => window.confirm("Clear the transcript and every card, and go back to setup?") && props.onReset()}
        >
          Reset everything
        </button>
      </Menu>
    </header>
  );
}

/** A small ⋯ dropdown that closes on any click outside it or on one of its items. */
function Menu({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  return (
    <div className="menu" ref={ref}>
      <button className="small ghost" aria-label="More" aria-expanded={open} onClick={() => setOpen(!open)}>⋯</button>
      {open && <div className="menu-items" onClick={() => setOpen(false)}>{children}</div>}
    </div>
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
