// The task graph as a to-do list for the person in the call: what needs you first, then what
// Adjourn is working on, then what is finished (folded away).
//
// All cards are siblings of one list, with the group headings in between, so a card that moves
// from "Working" to "Needs you" keeps its React state (open details, the revision flash).
// Folded "Done" cards are hidden, not unmounted, for the same reason.

import { useState, type ReactNode } from "react";
import type { Task } from "../api/contract";

type Group = "needs" | "working" | "done";

const GROUP_OF: Record<Task["status"], Group> = {
  needs_approval: "needs",
  failed: "needs",
  detected: "working",
  blocked: "working",
  running: "working",
  verifying: "working",
  done: "done",
  dismissed: "done",
};

const HEADING: Record<Group, string> = { needs: "Needs you", working: "Working on it", done: "Done" };

interface Props {
  tasks: Task[];
  card: (task: Task, reveal: (id: string) => void) => ReactNode;
}

export function TaskList({ tasks, card }: Props) {
  const [showDone, setShowDone] = useState(false);
  const groups: Record<Group, Task[]> = { needs: [], working: [], done: [] };
  for (const t of tasks) groups[GROUP_OF[t.status]].push(t);

  // A graph chip was clicked: unfold "Done" if needed, scroll there and highlight it.
  const reveal = (id: string) => {
    const target = tasks.find((t) => t.id === id);
    if (target && GROUP_OF[target.status] === "done") setShowDone(true);
    requestAnimationFrame(() => {
      const el = document.getElementById(`task-${id}`);
      if (!el) return;
      el.scrollIntoView({ behavior: "smooth", block: "nearest" });
      el.classList.remove("pinged");
      void el.offsetWidth; // restart the animation
      el.classList.add("pinged");
    });
  };

  if (tasks.length === 0) {
    return (
      <main className="tasks">
        <p className="muted empty">
          Nothing yet. When someone asks a question, or agrees to do something or to meet, it appears here.
        </p>
      </main>
    );
  }

  const items: ReactNode[] = [];
  for (const g of ["needs", "working", "done"] as Group[]) {
    const list = groups[g];
    if (list.length === 0) continue;
    const folded = g === "done" && !showDone;
    items.push(
      g === "done" ? (
        <button key="h-done" className="group-heading fold" onClick={() => setShowDone(!showDone)} aria-expanded={!folded}>
          {HEADING[g]} · {list.length} <span className="caret">{folded ? "▸" : "▾"}</span>
        </button>
      ) : (
        <h3 key={`h-${g}`} className={`group-heading ${g}`}>
          {HEADING[g]} · {list.length}
        </h3>
      ),
    );
    for (const t of list) {
      items.push(
        <div key={t.id} className={`slot ${g}`} hidden={folded}>
          {card(t, reveal)}
        </div>,
      );
    }
  }
  return <main className="tasks">{items}</main>;
}

/** How many cards wait for the person: shown in the tab title so it is visible behind Meet. */
export function needsYou(tasks: Task[]): number {
  return tasks.filter((t) => GROUP_OF[t.status] === "needs").length;
}
