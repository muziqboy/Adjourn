// The audio fallbacks, always present: type what was said, or replay the demo call
// (fixtures/demo_call.jsonl) through the real pipeline. Plus modes and model usage.

import { useState } from "react";
import type { Modes, Usage } from "../api/contract";

interface Props {
  onSay: (text: string) => Promise<unknown>;
  onReplay: () => Promise<unknown>;
  modes: Modes;
  usage: Usage;
}

export function Footer({ onSay, onReplay, modes, usage }: Props) {
  const [text, setText] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    const line = text;
    setText("");
    await onSay(line);
  };
  const tokens = Math.round((usage.tokens_in + usage.tokens_out) / 1000);
  return (
    <footer className="footer">
      <form onSubmit={submit} className="say">
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Type what was said…" />
        <button className="small">Say</button>
      </form>
      <div className="footer-row">
        <button className="link" onClick={() => void onReplay()}>Replay demo</button>
        <span className="spacer" />
        <span className="muted tiny" title="model · Google · GitHub modes">
          {modes.llm} · {modes.google} · {modes.github} · {usage.calls} calls · {tokens}k tok
        </span>
      </div>
    </footer>
  );
}
