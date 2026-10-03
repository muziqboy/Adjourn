"""The floor: Adjourn's mind in a live meeting (live-voice mode). One brain decides, at every
pause, what Adjourn says AND what work it starts, changes, approves or drops.

    Meet captions (speaker names)    --> on_caption()   what was said, by whom
    Recall speech_on / speech_off    --> on_speech()    barge-in: a person talking stops Adjourn
    Recall join / leave              --> on_presence()  who is in the call
    task changes (orchestrator)      --> on_task()      "draft ready", "created MEE-7", "failed"
    the room pauses + something new  --> decide(): one small, fast Gemini call (Google Search
        available) sees the whole picture and returns

        {"action": "speak" | "raise_hand" | "lower_hand" | "silent", "say", "point",
         "tasks":   [Op, ...]   create / update work for the agents (Linear, GitHub, calendar...)
         "approve": [task id]   a participant clearly said yes to that waiting draft (voice approval)
         "dismiss": [task id]   they dropped it
         "reason"}

    speak       -> {"type": "say", "text"} to the voice page (Gemini Live says it verbatim)
    raise_hand  -> {"type": "hand", "up": true, "point"}: the bot's tile shows a raised hand

Why one brain: when speaking and task creation were separate, Adjourn promised work it could not
do ("I'll create those tickets now"), claimed results it never saw ("it's assigned to you"), and
the task side, blind to speaker names, reassigned to the wrong person. Here the same decision
sees who said what and every task's real state, and only reports what the task list shows. In
live-voice mode the transcript-only intent pass stands down (core/intent.py).

Voice approval: only task types in VOICE_APPROVAL (default: linear) may be approved by voice,
only while waiting for approval, and the prompt requires a clear yes to that item. The
orchestrator's approve() is the same door the panel button uses.

Timing: decide when captions go quiet (a caption that ends a sentence: QUIET_S; a fragment:
QUIET_OPEN_S). Captions, not Meet's "is talking" indicator, decide this: a noisy microphone keeps
the indicator on without saying anything.
"""

import asyncio
import difflib
import json
import logging
import re
import time
from collections import deque
from datetime import datetime

from .. import agents, llm
from ..core.config import settings
from ..core.contract import Op, Task
from ..core.store import Store

log = logging.getLogger("adjourn.floor")

QUIET_S = 0.6  # captions quiet this long after a finished sentence = the room paused; decide
QUIET_OPEN_S = 1.1  # longer after a fragment that does not end a sentence (Meet splits captions)
AFTER_SPEECH_S = 0.3  # sooner when speech_off says the last speaker stopped
PAUSE_NAMED_S = 0.4  # sooner when Adjourn's name was just heard
TALKING_STALE_S = 6.0  # a speech_on without speech_off for this long is ignored
BARGE_IN_WORDS = 2  # without speech events: this many caption words while Adjourn talks stops it
EVENT_QUIET_S = 2.5  # task news (draft ready, created) waits for a lull this long: no interrupting
EVENT_MAX_WAIT_S = 45.0  # ...but not forever: then the mind decides (it raises a hand if still busy)
MEMORY_LINES = 150  # transcript lines the mind sees (about 10-15 minutes of a busy call)
FLOOR_MODEL: str | None = settings.floor_model  # tuned with scripts/eval_floor.py
FLOOR_THINKING: str | None = settings.floor_thinking
NAME = re.compile(r"\b(a ?d?journ\w*|ajourn\w*|adjourn\w*)\b", re.I)
EVENT = "—"  # the speaker of event lines (joins, task changes) in the transcript

SYSTEM = """You are the mind of Adjourn, an AI teammate attending a live video meeting as a participant.
A separate voice says aloud exactly the words you choose. Each time the room pauses you decide what to say and
what work to start. Be a useful colleague: listen, help when asked, get agreed work done, never overstep.

You see the live transcript with speaker names from Meet's captions. Captions garble words: your name "Adjourn" may
appear as "a journ", "adjourned", "the journey", "agent", "a john"; terms may be misheard ("read this" = Redis).
Captions also split sentences into fragments: read consecutive lines of one speaker as one utterance. Lines from
"—" are events (someone joined, a task changed). "You" lines are what you said.

SPEAKING ("action")
- "speak": say something now. "raise_hand": you have something valuable but were not invited (the meeting sees a
  raised hand). "lower_hand": the raised point is no longer relevant or they declined. "silent": the usual case.
- Speak when someone addresses you (by name, or unmistakably, like a follow-up right after you spoke), when your
  hand is up and someone invites you, when you are asked to repeat or continue, and when a task event needs a short
  word from you (a draft is ready for approval, a ticket was created, something failed).
- A question people ask each other or the room (not you by name) is never yours to answer directly: if nobody
  answers and you have a confident answer, RAISE YOUR HAND. Same when someone states a wrong fact that matters for
  a decision. At most one hand at a time.
- Stay silent when people talk to each other, small talk, thinking out loud, someone is mid-sentence or already
  answering, or you are unsure you were addressed. Interrupting is worse than missing a chance.
- If told to stop, be quiet, or "that's enough": stay silent. Do not even acknowledge it.
- Like a sharp, friendly colleague: English, answer first, one to three short spoken sentences, first names, round
  numbers, times the way people say them ("two pm", "Tuesday the sixth"), no lists, URLs, markdown or emoji. Never filler ("I'm here", "ready to assist", "great question"),
  never talk about yourself or your reasoning, never repeat what was just said. Google Search for facts.

WORK ("tasks", "approve", "dismiss")
Agents do work for the meeting:
{agents}
- When someone asks for such work, or the room agrees on it, add a "create" op: type, a short title, and a complete,
  self-contained brief (what, for whom with their name and email from the participants list, when, in absolute
  dates). Then say one short sentence of what you are PREPARING ("I'll draft a Linear ticket for the onboarding copy
  for Jany."). Never say it is done: work takes time and some needs approval.
- When they change agreed work (another assignee, title, time), add an "update" op with the task id and the complete
  new brief. Never create a second task for the same thing.
- Keep context between pieces of work: when new work is about earlier work ("a meeting next week about that
  ticket"), put the earlier task's id in "depends_on" and name it in the brief, so the meeting carries the ticket.
- Meetings: resolve dates against "Now" into an absolute weekday, date, time and timezone. If they gave no day or
  time ("next week"), propose one concrete slot ("How about Tuesday 13 October at 14:00?") and create the task once
  they agree. The calendar agent first books a private hold; the invite goes out only after a yes (approval).
- Approval: drafts marked "WAITING FOR APPROVAL (voice OK)" may be approved by voice. When such a draft is ready,
  say what it is in one sentence and ask whether to create it, but only if the room is quiet. If the room is BUSY,
  never interrupt with task news: raise your hand with the point ("The Linear draft for the onboarding copy is
  ready.") and bring it up when invited or at the next lull. Put its id in "approve" ONLY when a participant
  clearly says yes to that item ("yes", "go ahead", "create it", "do it"). Unclear, or a different item: ask. Drafts
  marked "(needs a click)" are approved on the panel: say so if asked.
- When they drop agreed work before it exists, put its id in "dismiss". Created tickets cannot be deleted by you:
  say so.
- State facts about work only as the task list shows them. A ticket exists only when the list says CREATED with its
  identifier. If something failed, say so plainly.

EXAMPLES
Kaleb: Adjourn, would a CDN help our image load times?
-> {{"action": "speak", "say": "Yes, Kaleb. A CDN serves images from servers near your users, so load times usually drop a lot.", "reason": "addressed"}}
Kaleb: Would Redis help speed up our search API?  Sara: Hmm, I'm not sure.
-> {{"action": "raise_hand", "point": "Redis helps if most searches repeat.", "reason": "open question, I know"}}
Kaleb: Can you make a Linear ticket for the onboarding copy and give it to Jany?
-> {{"action": "speak", "say": "Sure, I'll draft a Linear ticket for the onboarding copy for Jany.", "tasks": [{{"op": "create", "type": "linear", "title": "Onboarding copy", "brief": "Create a Linear ticket: rewrite the onboarding copy. Assign it to Jany Koulen <jany@example.com>."}}], "reason": "asked for a ticket"}}
—: Draft ready, WAITING FOR APPROVAL (voice OK): t3 linear "Onboarding copy" -> Jany Koulen
-> {{"action": "speak", "say": "The Linear ticket for the onboarding copy is drafted for Jany. Shall I create it?", "reason": "draft ready"}}
Kaleb: Yes, go ahead.   (t3 is waiting for approval)
-> {{"action": "speak", "say": "Creating it now.", "approve": ["t3"], "reason": "clear yes"}}
Jany: Actually, give it to Sara instead.   (t3 is a draft)
-> {{"action": "speak", "say": "Okay, I'll move it to Sara.", "tasks": [{{"op": "update", "id": "t3", "brief": "Create a Linear ticket: rewrite the onboarding copy. Assign it to Sara Lind.", "reason": "reassigned to Sara"}}], "reason": "change"}}
Kaleb: Let's meet next week to go through that ticket.   (t3 linear "Onboarding copy" is CREATED as MEE-7)
-> {{"action": "speak", "say": "How about Tuesday 13 October at 14:00 for half an hour?", "reason": "no day given: propose"}}
Kaleb: Yes, that works.
-> {{"action": "speak", "say": "I'll put it in the calendar for Tuesday at two.", "tasks": [{{"op": "create", "type": "schedule", "title": "Review MEE-7: onboarding copy", "brief": "Book a 30-minute meeting with Kaleb Girmay and Jany Koulen on Tuesday 13 October 2026 at 14:00 Europe/Stockholm to go through Linear ticket MEE-7 (onboarding copy).", "depends_on": ["t3"]}}], "reason": "agreed slot"}}
—: Draft ready, WAITING FOR APPROVAL (voice OK): t4 schedule "Review MEE-7: onboarding copy"
-> {{"action": "speak", "say": "The hold for Tuesday at two is in the calendar. Shall I send the invite to Jany?", "reason": "invite needs a yes"}}
Kaleb: How was your weekend?  Sara: Good, we went hiking.
-> {{"action": "silent", "reason": "small talk"}}

Reply with JSON only:
{{"action": "speak|raise_hand|lower_hand|silent", "say": "...", "point": "...", "tasks": [], "approve": [], "dismiss": [], "reason": "a few words"}}"""

current: "Floor | None" = None  # set by main.py; listen/bot.py feeds it in live-voice mode


class Floor:
    def __init__(self, store: Store, orch=None) -> None:
        self.store = store
        self.orch = orch  # the orchestrator (set by main.py): tasks, approvals, dismissals
        self.lines: deque = deque(maxlen=MEMORY_LINES)  # (ts, speaker, text, is_adjourn)
        self.pages: set = set()  # connected voice pages (WebSockets)
        self.hand: str | None = None  # the raised point, or None
        self.speaking = False  # the voice page is playing Adjourn's words
        self.last_spoke_at = 0.0
        self.new_since_decision = 0  # lines since the last decision
        self.new_human_lines = 0  # of those, said by people (not task events)
        self._event_since = 0.0  # when the oldest undecided task event arrived
        self.present: set[str] = set()  # people in the call (join/leave events, captions)
        self.talking: dict[str, float] = {}  # people talking right now -> last sign of speech
        self.has_speech_events = False  # this bot sends speech_on/off
        self.last_caption_at = 0.0
        self._last_final = ""
        self._task_status: dict[str, str] = {}  # task id -> last status seen (for task events)
        self._timer: asyncio.Task | None = None
        self._deciding: asyncio.Task | None = None
        self._partial_words: dict[str, int] = {}
        self._seen_speakers: set[str] = set()
        self._jobs: set[asyncio.Task] = set()
        self._unannounced: dict[str, int] = {}  # voice-OK drafts not yet brought up -> reminders sent

    def active(self) -> bool:
        """Live-voice mode: a voice page is connected, so this mind runs the meeting."""
        return bool(self.pages)

    # --- input: who is in the call, who talks, what was said ---

    def is_adjourn(self, speaker: str | None, text: str = "") -> bool:
        """Adjourn's own voice coming back through Meet: by name, or by matching what it just said
        (the bot's captions do not always carry its name)."""
        if speaker and speaker.strip().lower().startswith(settings.bot_name.lower()):
            return True
        if text:
            recent = [t for _, _, t, own in list(self.lines)[-6:] if own]
            words = text.lower()
            return any(difflib.SequenceMatcher(None, words, r.lower()).ratio() > 0.6
                       or (len(words) > 20 and words in r.lower()) for r in recent)
        return False

    def on_presence(self, name: str | None, joined: bool) -> None:
        if not name or self.is_adjourn(name):
            return
        if joined and name not in self.present:
            self.present.add(name)
            self._event(f"{name} joined the call", decide=False)
        elif not joined and name in self.present:
            self.present.discard(name)
            self._event(f"{name} left the call", decide=False)

    def someone_talking(self) -> bool:
        now = time.time()
        for who, seen in list(self.talking.items()):
            if now - seen > TALKING_STALE_S:
                del self.talking[who]
        return bool(self.talking)

    def on_speech(self, speaker: str | None, talking: bool) -> None:
        """speech_on / speech_off. Events without a name are the bot itself (Recall does not name it)."""
        if not speaker or self.is_adjourn(speaker):
            return
        self.has_speech_events = True
        if talking:
            self.talking[speaker] = time.time()
            if self.speaking:
                self._send({"type": "stop"})  # a person started talking: Adjourn yields at once
                self.speaking = False
                log.info("barge-in by %s", speaker)
        else:
            self.talking.pop(speaker, None)
            if not self.someone_talking() and time.time() - self.last_caption_at > AFTER_SPEECH_S:
                self._schedule(AFTER_SPEECH_S)

    def on_caption(self, speaker: str | None, text: str, final: bool) -> None:
        text = " ".join(text.split())
        if not text or self.is_adjourn(speaker, text):
            return  # its own words are already in the history (apply())
        who = speaker or "Someone"
        self.last_caption_at = time.time()
        if speaker:
            self.present.add(speaker)
        if who not in self._seen_speakers:
            self._seen_speakers.add(who)
            log.info("caption speaker: %r", who)
        words = len(text.split())
        if not self.has_speech_events and self.speaking and words >= BARGE_IN_WORDS and words > self._partial_words.get(who, 0):
            self._send({"type": "stop"})  # fallback barge-in from captions
            self.speaking = False
        self._partial_words[who] = 0 if final else words
        if who in self.talking:
            self.talking[who] = time.time()
        if final:
            self.lines.append((time.time(), who, text[:400], False))
            self.new_since_decision += 1
            self.new_human_lines += 1
            self._last_final = text
        # the decision comes when captions go quiet; wait longer after a sentence fragment
        if final and NAME.search(text):
            self._schedule(PAUSE_NAMED_S)
        else:
            ended = self._last_final.rstrip().endswith((".", "?", "!"))
            self._schedule(QUIET_S if ended else QUIET_OPEN_S)

    def on_task(self, task: Task) -> None:
        """Store task listener: tell the mind (and through it the room) about task milestones."""
        before = self._task_status.get(task.id)
        self._task_status[task.id] = task.status
        if before == task.status or not self.active():
            return
        if task.status == "needs_approval":
            how = "voice OK" if task.type in settings.voice_approval else "needs a click"
            if task.type in settings.voice_approval:
                self._unannounced[task.id] = 0
            self._event(f"Draft ready, WAITING FOR APPROVAL ({how}): {self._describe_short(task)}")
        elif task.status == "done" and task.artifact and task.artifact.delivered:
            self._event(f"Done: {self._describe_short(task)}")
        elif task.status == "failed":
            last = task.trace[-1].text if task.trace else "unknown error"
            self._event(f"Failed: {task.type} {task.id} “{task.title}”: {last[:160]}")

    def _event(self, text: str, decide: bool = True) -> None:
        self.lines.append((time.time(), EVENT, text, False))
        if decide:
            self.new_since_decision += 1
            self._event_since = self._event_since or time.time()
            self._schedule(AFTER_SPEECH_S)

    def room_busy(self) -> bool:
        """People are talking, or were a moment ago: not the time to bring up task news."""
        return self.someone_talking() or time.time() - self.last_caption_at < EVENT_QUIET_S

    def _schedule(self, delay: float) -> None:
        if self._timer and not self._timer.done():
            self._timer.cancel()
        self._timer = asyncio.create_task(self._after_pause(delay))

    async def _after_pause(self, delay: float) -> None:
        await asyncio.sleep(delay)
        if self.new_since_decision == 0 or self.speaking:
            return
        if time.time() - self.last_caption_at < delay * 0.8:
            return  # a newer caption arrived; its own timer decides
        only_events = self.new_human_lines == 0
        waited = time.time() - (self._event_since or time.time())
        if only_events and self.room_busy() and waited < EVENT_MAX_WAIT_S:
            self._schedule(1.0)  # task news waits for a lull instead of interrupting
            return
        if self._deciding and not self._deciding.done():
            self._deciding.cancel()  # newer speech supersedes an unfinished decision
        self._deciding = asyncio.create_task(self.decide())

    # --- what the mind sees ---

    def _people(self) -> list:
        meeting = self.store.meeting
        return [p for p in ([meeting.me, *meeting.others] if meeting else []) if p.name]

    def _name_for(self, email: str) -> str:
        person = next((p for p in self._people() if p.email and p.email.lower() == email.lower()), None)
        return person.name if person else email

    def _describe_short(self, task: Task) -> str:
        art = task.artifact
        out = f"{task.id} {task.type} “{art.title if art and art.title else task.title}”"
        if art and art.to:
            out += " -> " + ", ".join(self._name_for(e) for e in art.to)
        if art and art.external_id:
            out += f" (CREATED as {art.external_id})"
        return out

    def _describe(self, task: Task) -> str:
        art = task.artifact
        status = {
            "needs_approval": ("WAITING FOR APPROVAL (voice OK)" if task.type in settings.voice_approval
                               else "WAITING FOR APPROVAL (needs a click)"),
            "done": "DONE", "failed": "FAILED",
        }.get(task.status, "being prepared")
        line = f"- {task.id} {task.type} “{task.title}”: {status}. Brief: {task.brief[:220]}"
        if art:
            if art.kind == "issue":
                line += f" | Draft: “{art.title}”"
                line += " assigned to " + (", ".join(self._name_for(e) for e in art.to) if art.to else "nobody")
                line += f" | CREATED as {art.external_id}" if art.external_id else " | not created yet"
                if art.note:
                    line += f" | Note: {art.note[:120]}"
            elif art.kind == "event" and art.start:
                start = datetime.fromisoformat(art.start)
                line += f" | {start:%a %d %b %H:%M}" + (" | invite sent" if art.delivered else " | hold only")
            elif art.content:
                line += f" | {art.content[:300]}"
        if task.review:
            line += f" | Review: {task.review[:160]}"
        return line

    def prompt(self) -> str:
        now = time.time()
        start = self.lines[0][0] if self.lines else now
        people = self._people()
        names = {p.name for p in people} | self.present
        roster = "\n".join(f"- {p.name}" + (f" <{p.email}>" if p.email else "") for p in people) or "- (unknown yet)"
        extra = sorted(n for n in self.present if n not in {p.name for p in people})
        if extra:
            roster += "\n" + "\n".join(f"- {n}" for n in extra)
        transcript = []
        fresh_from = len(self.lines) - self.new_since_decision
        for i, (ts, speaker, text, own) in enumerate(self.lines):
            mark = ">> " if i >= fresh_from and not own else "   "
            m, s = divmod(int(ts - start), 60)
            transcript.append(f"{mark}[{m:02d}:{s:02d}] {'You' if own else speaker}: {text}")
        tasks = [t for t in self.store.tasks.values() if t.status != "dismissed"]
        spoke = (f"You last spoke {int(now - self.last_spoke_at)} s ago." if self.last_spoke_at else "You have not spoken yet.")
        quiet = int(now - self.last_caption_at) if self.last_caption_at else 0
        room = ("BUSY: people are talking" if self.room_busy() else f"quiet for {quiet} s")
        return (
            f"Now: {datetime.now().astimezone():%A %d %B %Y %H:%M %Z}.\n"
            f"People in the call ({len(names)}):\n{roster}\n\n"
            f"Work in this meeting:\n" + ("\n".join(self._describe(t) for t in tasks) or "(none yet)") + "\n\n"
            f"Your hand: {'RAISED, point: ' + self.hand if self.hand else 'down'}. {spoke} Room: {room}.\n\n"
            f"Transcript (oldest first; '>>' marks what is new since your last decision):\n" + "\n".join(transcript)
            + "\n\nDecide now."
        )

    def system(self) -> str:
        docs = "\n".join(f"- {spec.type}: {spec.intent_doc}" for spec in agents.enabled() if spec.type not in ("answer", "research"))
        return SYSTEM.format(agents=docs or "- (none enabled)")

    # --- the decision ---

    async def decide(self) -> dict:
        prompt = self.prompt()  # before resetting the counter: it marks the fresh lines
        self.new_since_decision = 0
        self.new_human_lines = 0
        self._event_since = 0.0
        started = time.time()
        last_line = self.lines[-1][2] if self.lines else ""
        result = await llm.generate(
            "floor", prompt, system=self.system(), search=True, model=FLOOR_MODEL, thinking=FLOOR_THINKING,
            mock=lambda: llm.LLMResult(text=json.dumps(_mock_decision(last_line, self.hand, self.store))),
            mock_delay=0.3,
        )
        decision = _parse(result.text)
        log.info("floor %.1fs: %s", time.time() - started, {k: v for k, v in decision.items() if v})
        if self.last_caption_at > started and not NAME.search(last_line):
            return decision  # someone kept talking while we decided; their caption's timer decides again
        self.apply(decision)
        if decision["action"] in ("speak", "raise_hand"):
            self._unannounced.clear()  # it had the drafts in view and spoke up or raised a hand
        elif self._unannounced:
            self._run(self._remind_later(), "draft reminder")
        return decision

    async def _remind_later(self) -> None:
        """A voice-OK draft it stayed silent about (the room was busy) comes up again at the next
        lull, at most twice, so a ready ticket is never forgotten."""
        await asyncio.sleep(EVENT_QUIET_S * 2)
        for task_id, sent in list(self._unannounced.items()):
            task = self.store.tasks.get(task_id)
            if task is None or task.status != "needs_approval" or sent >= 2:
                self._unannounced.pop(task_id, None)
                continue
            self._unannounced[task_id] = sent + 1
            self._event(f"Still WAITING FOR APPROVAL (voice OK): {self._describe_short(task)}")

    def apply(self, decision: dict) -> None:
        self._apply_work(decision)
        action = decision["action"]
        if action == "speak" and decision.get("say"):
            self.hand = None
            self.speaking = True
            self._send({"type": "hand", "up": False})
            self._send({"type": "say", "text": decision["say"]})
            self.lines.append((time.time(), settings.bot_name, decision["say"], True))
            # show it in the panel's transcript, without feeding it to the meeting agent
            self.store.emit("transcript.delta", {"text": decision["say"], "final": True, "speaker": settings.bot_name})
        elif action == "raise_hand" and decision.get("point") and not self.hand:
            self.hand = decision["point"]
            self._send({"type": "hand", "up": True, "point": self.hand})
        elif action == "lower_hand" and self.hand:
            self.hand = None
            self._send({"type": "hand", "up": False})

    def _apply_work(self, decision: dict) -> None:
        """Task ops, voice approvals and dismissals, through the orchestrator's own doors."""
        if self.orch is None:
            return
        ops = []
        for raw in decision.get("tasks") or []:
            try:
                ops.append(Op.model_validate(raw))
            except Exception:  # noqa: BLE001  (a malformed op is skipped, not fatal)
                log.warning("floor: bad task op %s", raw)
        if ops:
            self.store.ensure_meeting()
            log.info("floor ops: %s", [op.model_dump(exclude_defaults=True) for op in ops])
            self.orch.apply(ops)
        for task_id in decision.get("approve") or []:
            task = self.store.tasks.get(task_id)
            if task is None or task.status != "needs_approval" or task.type not in settings.voice_approval:
                log.info("floor: approval of %s refused (%s)", task_id, task.status if task else "unknown task")
                continue
            self.store.trace(task_id, "info", "Approved by voice in the meeting")
            self._run(self.orch.approve(task_id), f"voice approval of {task_id}")
        for task_id in decision.get("dismiss") or []:
            task = self.store.tasks.get(task_id)
            if task and task.status not in ("done", "dismissed"):
                self.orch.dismiss(task_id)

    def _run(self, coro, label: str) -> None:
        async def guarded():
            try:
                await coro
            except Exception as exc:  # noqa: BLE001
                log.warning("%s failed: %s", label, exc)
        job = asyncio.create_task(guarded())
        self._jobs.add(job)
        job.add_done_callback(self._jobs.discard)

    # --- output: the voice page ---

    def spoken(self) -> None:
        """The page finished saying Adjourn's words."""
        self.speaking = False
        self.last_spoke_at = time.time()
        if self.new_since_decision:
            self._schedule(AFTER_SPEECH_S)  # someone spoke (or a task changed) while Adjourn was talking

    def _send(self, message: dict) -> None:
        for page in list(self.pages):
            asyncio.create_task(_safe_send(page, message))

    def reset(self) -> None:
        self.lines.clear()
        self.hand, self.speaking, self.last_spoke_at, self.new_since_decision = None, False, 0.0, 0
        self.new_human_lines, self._event_since = 0, 0.0
        self._unannounced.clear()
        self.talking.clear()
        self.present.clear()
        self._task_status.clear()


async def _safe_send(ws, message: dict) -> None:
    try:
        await ws.send_json(message)
    except Exception:  # noqa: BLE001  (a page that went away)
        pass


def _parse(text: str) -> dict:
    """The model's JSON, tolerant of code fences and stray prose. Anything unreadable = silent."""
    match = re.search(r"\{.*\}", text or "", re.S)
    try:
        data = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        data = {}
    action = data.get("action") if data.get("action") in ("speak", "raise_hand", "lower_hand", "silent") else "silent"
    as_list = lambda v: [x for x in v if x] if isinstance(v, list) else []  # noqa: E731
    return {
        "action": action, "say": (data.get("say") or "").strip(), "point": (data.get("point") or "").strip(),
        "tasks": [t for t in as_list(data.get("tasks")) if isinstance(t, dict)],
        "approve": [str(t) for t in as_list(data.get("approve"))],
        "dismiss": [str(t) for t in as_list(data.get("dismiss"))],
        "reason": data.get("reason", ""),
    }


def _mock_decision(last_line: str, hand: str | None, store: Store) -> dict:
    """LLM_MODE=mock: speak when named, raise a hand on a question, draft/approve Linear tickets."""
    waiting = [t for t in store.tasks.values() if t.status == "needs_approval" and t.type in settings.voice_approval]
    if waiting and re.search(r"\b(yes|go ahead|create it|do it)\b", last_line, re.I):
        return {"action": "speak", "say": "Creating it now.", "approve": [waiting[-1].id], "reason": "clear yes"}
    if re.search(r"draft ready", last_line, re.I):
        return {"action": "speak", "say": "The draft is ready. Shall I create it?", "reason": "draft ready"}
    if re.search(r"\blinear\b.*\bticket\b", last_line, re.I):
        return {"action": "speak", "say": "I'll draft that Linear ticket.", "reason": "asked",
                "tasks": [{"op": "create", "type": "linear", "title": "Mock ticket",
                           "brief": f"Create a Linear ticket: {last_line}"}]}
    if hand and re.search(r"\bgo ahead\b|\byes\b", last_line, re.I):
        return {"action": "speak", "say": hand, "reason": "invited"}
    if NAME.search(last_line):
        return {"action": "speak", "say": "Mock answer from Adjourn.", "reason": "addressed"}
    if last_line.rstrip().endswith("?") and not hand:
        return {"action": "raise_hand", "point": "I can answer that.", "reason": "open question"}
    return {"action": "silent", "reason": "not for me"}
