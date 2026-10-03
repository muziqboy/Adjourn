// The last two finalised lines, plus the line being spoken (grey).

export function Transcript({ lines, interim }: { lines: string[]; interim: string }) {
  const last = lines.slice(-2);
  return (
    <section className="transcript" aria-live="polite">
      {last.length === 0 && !interim && <p className="muted">Transcript appears here.</p>}
      {last.map((line, i) => <p key={lines.length - last.length + i}>{line}</p>)}
      {interim && <p className="interim">{interim}</p>}
    </section>
  );
}
