// The transcript strip: the last two finalised lines plus the line being spoken (grey). Click to
// unfold the whole call (the last 50 lines the panel keeps), scrolled to the newest line.
// Names appear when the meeting bot is in the call (Meet captions carry them); laptop audio has none.

import { useEffect, useRef, useState } from "react";
import type { Line } from "../api/contract";

const Who = ({ line }: { line: Line }) => (line.speaker ? <b className="speaker">{line.speaker}: </b> : null);

export function Transcript({ lines, interim }: { lines: Line[]; interim: Line | null }) {
  const [full, setFull] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const shown = full ? lines : lines.slice(-2);

  useEffect(() => {
    if (full && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [full, lines.length, interim?.text]);

  return (
    <section className={`transcript ${full ? "full" : ""}`} aria-live="polite">
      <div className="transcript-lines" ref={box}>
        {shown.length === 0 && !interim && <p className="muted">The conversation appears here.</p>}
        {shown.map((line, i) => (
          <p key={lines.length - shown.length + i}><Who line={line} />{line.text}</p>
        ))}
        {interim && <p className="interim"><Who line={interim} />{interim.text}</p>}
      </div>
      {lines.length > 2 && (
        <button className="link tiny transcript-toggle" onClick={() => setFull(!full)}>
          {full ? "Show less" : `Whole call · ${lines.length} lines`}
        </button>
      )}
    </section>
  );
}
