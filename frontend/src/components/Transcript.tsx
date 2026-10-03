// The last two finalised lines, plus the line being spoken (grey). Names appear when the
// meeting bot is in the call (Meet captions carry them); laptop audio has none.

import type { Line } from "../api/contract";

const Who = ({ line }: { line: Line }) => (line.speaker ? <b className="speaker">{line.speaker}: </b> : null);

export function Transcript({ lines, interim }: { lines: Line[]; interim: Line | null }) {
  const last = lines.slice(-2);
  return (
    <section className="transcript" aria-live="polite">
      {last.length === 0 && !interim && <p className="muted">Transcript appears here.</p>}
      {last.map((line, i) => (
        <p key={lines.length - last.length + i}><Who line={line} />{line.text}</p>
      ))}
      {interim && <p className="interim"><Who line={interim} />{interim.text}</p>}
    </section>
  );
}
