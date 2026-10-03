// The panel: a ~400 px window beside the Meet call. Setup screen until listening starts,
// then the live screen: header, transcript, the task list (needs you / working / done), footer.
//
// Layout of src/:
//   api/         contract (mirror of the backend) and the socket hook
//   audio/       microphone capture (ears) and speech output (voice)
//   agents/      per-task-type card faces
//   components/  the screens and the card

import { useEffect, useRef, useState } from "react";
import type { Health, MeetingContext } from "./api/contract";
import { useMeeting } from "./api/useMeeting";
import { startCapture, type Capture } from "./audio/capture";
import { stopSpeaking } from "./audio/speak";
import { Footer } from "./components/Footer";
import { Header } from "./components/Header";
import { Setup } from "./components/Setup";
import { TaskCard } from "./components/TaskCard";
import { needsYou, TaskList } from "./components/TaskList";
import { Transcript } from "./components/Transcript";

export default function App() {
  const { view, start, stop, say, replay, approve, dismiss, steer, reset, sendBot, botLeave, connectCalendar } = useMeeting();
  const [health, setHealth] = useState<Health | null>(null);
  const [level, setLevel] = useState(0);
  const [micOn, setMicOn] = useState(false);
  const [micError, setMicError] = useState<string | null>(null);
  const capture = useRef<Capture | null>(null);

  // "(2) Adjourn" in the tab: cards waiting for a click are visible even behind the Meet window
  const waiting = needsYou(view.tasks);
  useEffect(() => {
    document.title = waiting ? `(${waiting}) Adjourn` : "Adjourn";
  }, [waiting]);

  useEffect(() => {
    fetch("/api/health").then((r) => r.json()).then(setHealth).catch(() => setHealth(null));
  }, [view.connected]);

  const listen = async () => {
    setMicError(null);
    try {
      capture.current = await startCapture(setLevel);
      setMicOn(true);
    } catch (err) {
      setMicError(`Microphone unavailable (${(err as Error).message}). Type or replay instead.`);
    }
  };

  const stopListening = () => {
    capture.current?.stop();
    capture.current = null;
    setMicOn(false);
    stopSpeaking();
  };

  if (!view.connected && view.state === "idle") {
    return <div className="app"><p className="muted pad">Connecting to the Adjourn backend…</p></div>;
  }

  if (view.state === "idle") {
    // a Meet link sends the bot (it hears and speaks); otherwise the laptop mic listens
    const onStart = async (m: MeetingContext, meetUrl: string) => {
      await start(m);
      if (!meetUrl) return listen();
      try {
        await sendBot(meetUrl);
      } catch (err) {
        // the live screen is already showing; surface the reason there
        setMicError(`The bot could not join: ${(err as Error).message}. Type, replay, or turn the mic on.`);
      }
    };
    return <Setup health={health} onStart={onStart} autojoin={view.autojoin} onConnectCalendar={connectCalendar} />;
  }

  const people = view.meeting ? [...view.meeting.others, view.meeting.me] : [];
  return (
    <div className="app">
      <Header
        state={view.state}
        startedAt={view.meeting?.started_at ?? null}
        level={level}
        micOn={micOn}
        connected={view.connected}
        bot={view.bot}
        onListen={listen}
        onBotLeave={() => void botLeave()}
        onStop={async () => { stopListening(); await stop(); }}
        onReset={async () => { stopListening(); await reset(); }}
      />
      {micError && <p className="warn">{micError}</p>}
      <Transcript lines={view.lines} interim={view.interim} />
      <TaskList
        tasks={view.tasks}
        card={(task, reveal) => (
          <TaskCard
            task={task}
            tasks={view.tasks}
            info={view.agents[task.type]}
            people={people}
            botInCall={view.bot.state === "in_call"}
            onApprove={() => approve(task.id)}
            onDismiss={() => dismiss(task.id)}
            onSteer={(instruction) => steer(task.id, instruction)}
            onReveal={reveal}
          />
        )}
      />
      <Footer onSay={say} onReplay={replay} modes={view.modes} usage={view.usage} />
    </div>
  );
}
