"""The floor: Adjourn's turn-taking mind in a live meeting. Decides when to speak, when to raise
its hand, and when to stay silent. The voice page (live_voice.py) only says what this decides.

    Meet captions (speaker names)   --> on_caption()   what was said
    Recall speech_on / speech_off   --> on_speech()    who is talking right now (fast)
    nobody talking + something new  --> decide(): one small, fast Gemini call (with Google
        Search) sees the whole picture and returns
        {"action": "speak" | "raise_hand" | "lower_hand" | "silent", "say", "point", "reason"}
    speak       -> {"type": "say", "text"} to the voice page, which speaks it via Gemini Live
    raise_hand  -> {"type": "hand", "up": true, "point"}: the bot's tile shows a raised hand
    a person starts talking while Adjourn speaks -> {"type": "stop"} (barge-in)

Timing: decide when the captions go quiet (QUIET_S without a new caption word) and something new
was said. Captions, not Meet's "is talking" indicator, decide this: a noisy microphone keeps the
indicator on without saying anything. speech_on is still the fastest way to stop Adjourn when a
person starts talking over it (barge-in), and speech_off lets it decide sooner.

Who is in the call: Recall's join/leave events, plus everyone heard in the captions.

Why a separate mind: a live audio model answers whenever anyone stops talking, hears one mixed
stream without names, and knows nothing about the meeting. Here every decision is deliberate,
made with who-said-what, Adjourn's own past lines, its research and tasks, and its hand state.

Why captions: Meet's captions carry speaker names. They garble words ("Adjourn" -> "a journ"),
so the prompt tells the model to infer the intended words.
"""

import asyncio
import difflib
import json
import logging
import re
import time
from collections import deque

from .. import llm
from ..core.config import settings
from ..core.store import Store

log = logging.getLogger("adjourn.floor")

QUIET_S = 0.6  # captions quiet this long after something new = the room paused; decide
AFTER_SPEECH_S = 0.3  # sooner when speech_off says the last speaker stopped
PAUSE_NAMED_S = 0.4  # sooner when Adjourn's name was just heard
TALKING_STALE_S = 6.0  # a speech_on without speech_off for this long no longer blocks decisions
BARGE_IN_WORDS = 2  # without speech events: this many caption words while Adjourn talks stops it
FLOOR_MODEL: str | None = settings.floor_model  # tuned with scripts/eval_floor.py
FLOOR_THINKING: str | None = settings.floor_thinking
NAME = re.compile(r"\b(a ?d?journ\w*|ajourn\w*|adjourn\w*)\b", re.I)

SYSTEM = """You are the turn-taking mind of Adjourn, an AI teammate attending a live video meeting as a participant.
A separate voice says aloud exactly the words you choose. Each time the room pauses you decide one action.

You see the live transcript with speaker names from Meet's captions. Captions garble words: your name "Adjourn" may
appear as "a journ", "adjourned", "the journey", "agent", "a john"; technical terms may be misheard ("read this" =
Redis). Infer what people meant.

ACTIONS
- "speak": say something now.
- "raise_hand": you have something genuinely valuable but nobody invited you. The meeting sees your raised hand and
  can invite you. Use it instead of interrupting.
- "lower_hand": your raised point is no longer relevant, or they declined ("no thanks", moved on).
- "silent": do nothing. This is the usual case.

SPEAK only when
1. someone addresses you, by name or unmistakably (e.g. a follow-up question right after you spoke: "and what about X?"),
   with a question or request;
2. your hand is raised and someone invites you ("go ahead", "yes, Adjourn?", "what is it?");
3. you are asked to repeat, clarify or continue.

RAISE YOUR HAND when
- an open question hangs in the room, nobody is answering it, and you have a confident, concrete answer;
- someone states something factually wrong that matters for what they are deciding;
- they are about to decide without a key fact you have (for example from your research below).
At most one raised hand at a time. Do not raise it again for the same point.

STAY SILENT when people talk to each other, small talk, thinking out loud, rhetorical questions, someone is
mid-sentence or already answering, the question was already answered, the last line looks unfinished, or you are not
sure you were addressed. Interrupting is worse than missing a chance. If told to stop or be quiet, stay silent.

HOW YOU SPEAK
Like a sharp, friendly colleague. English. Answer first, in one to three short spoken sentences. Use people's first
names when natural. Round numbers. No lists, no URLs, no markdown, no emoji. If the request is unclear, ask one short
question back. Never filler ("I'm here", "ready to assist", "great question", "sure thing"), never talk about yourself,
your rules or your reasoning, never repeat what someone just said. When invited after raising your hand, make the point
you raised, updated to the conversation. Use Google Search when you need a fact; keep it quick.

EXAMPLES
Transcript: Kaleb: Would Redis help speed up our search API?  Sara: Hmm, maybe, I'm not sure.
-> {"action": "raise_hand", "point": "Redis would help if most searches repeat; I can explain.", "reason": "open question, I know"}
Transcript: Kaleb: Adjourn, would a CDN help our image load times?
-> {"action": "speak", "say": "Yes, Kaleb. A CDN serves images from servers near your users, so load times usually drop a lot, and it takes load off your origin.", "reason": "addressed"}
Transcript: (your hand is up) Sara: Okay, go ahead.
-> {"action": "speak", "say": "Redis helps if many searches repeat: cached results come back in about a millisecond. If most queries are unique, fix the database query first.", "reason": "invited"}
Transcript: Kaleb: How was your weekend?  Sara: Good, we went hiking.
-> {"action": "silent", "reason": "small talk"}
Transcript: Kaleb: So I think we should ship it on Thursday and
-> {"action": "silent", "reason": "unfinished"}

Reply with JSON only:
{"action": "speak|raise_hand|lower_hand|silent", "say": "words to say (speak only)", "point": "one sentence (raise_hand only)", "reason": "a few words"}"""


current: "Floor | None" = None  # set by main.py; listen/bot.py feeds it captions in live-voice mode


class Floor:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.lines: deque = deque(maxlen=60)  # (ts, speaker, text, is_adjourn)
        self.pages: set = set()  # connected voice pages (WebSockets)
        self.hand: str | None = None  # the raised point, or None
        self.speaking = False  # the voice page is playing Adjourn's words
        self.last_spoke_at = 0.0
        self.new_since_decision = 0  # human lines since the last decision
        self._timer: asyncio.Task | None = None
        self._deciding: asyncio.Task | None = None
        self._partial_words: dict[str, int] = {}
        self._seen_speakers: set[str] = set()
        self.present: set[str] = set()  # people in the call (join/leave events, captions)
        self.last_caption_at = 0.0
        self.talking: dict[str, float] = {}  # people talking right now -> last sign of speech
        self.has_speech_events = False  # this bot sends speech_on/off

    # --- input: captions ---

    def is_adjourn(self, speaker: str | None, text: str = "") -> bool:
        """Adjourn's own voice coming back through Meet: by name, or by matching what it just said
        (the bot's captions do not always carry its name)."""
        if speaker and speaker.strip().lower().startswith(settings.bot_name.lower()):
            return True
        if text:
            recent = [t for _, _, t, own in list(self.lines)[-4:] if own]
            words = text.lower()
            return any(difflib.SequenceMatcher(None, words, r.lower()).ratio() > 0.6
                       or (len(words) > 20 and words in r.lower()) for r in recent)
        return False

    def on_presence(self, name: str | None, joined: bool) -> None:
        """Recall join/leave: the mind knows who is in the call, and sees arrivals in the transcript."""
        if not name or self.is_adjourn(name):
            return
        if joined and name not in self.present:
            self.present.add(name)
            self.lines.append((time.time(), "—", f"{name} joined the call", False))
        elif not joined and name in self.present:
            self.present.discard(name)
            self.lines.append((time.time(), "—", f"{name} left the call", False))

    def someone_talking(self) -> bool:
        """True while a person holds the floor. Entries without a fresh sign of speech expire, so
        a lost speech_off can never freeze Adjourn."""
        now = time.time()
        for who, seen in list(self.talking.items()):
            if now - seen > TALKING_STALE_S:
                del self.talking[who]
        return bool(self.talking)

    def on_speech(self, speaker: str | None, talking: bool) -> None:
        """Recall's speech_on / speech_off: the fastest signal of who holds the floor.
        Events without a name are the bot itself (Recall does not name it)."""
        if not speaker or speaker == "Someone" or self.is_adjourn(speaker):
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
        """Every Meet caption, partial or final. Adjourn's own captions are recorded, never answered."""
        text = " ".join(text.split())
        if not text:
            return
        if self.is_adjourn(speaker, text):
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
            log.info("barge-in by %s (captions)", who)
        self._partial_words[who] = 0 if final else words
        if who in self.talking:
            self.talking[who] = time.time()  # a caption is a fresh sign they are talking
        if final:
            self.lines.append((time.time(), who, text, False))
            self.new_since_decision += 1
        # every caption word restarts the quiet timer; the decision comes when captions go quiet
        self._schedule(PAUSE_NAMED_S if final and NAME.search(text) else QUIET_S)

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
        if self._deciding and not self._deciding.done():
            self._deciding.cancel()  # newer speech supersedes an unfinished decision
        self._deciding = asyncio.create_task(self.decide())

    # --- the decision ---

    def prompt(self) -> str:
        now = time.time()
        start = self.lines[0][0] if self.lines else now
        names = sorted(self.present | {s for _, s, _, own in self.lines if not own and s != "—"})
        transcript = []
        fresh_from = len(self.lines) - self.new_since_decision
        for i, (ts, speaker, text, own) in enumerate(self.lines):
            mark = ">> " if i >= fresh_from and not own else "   "
            who = f"{speaker} (you)" if own else speaker
            m, s = divmod(int(ts - start), 60)
            transcript.append(f"{mark}[{m:02d}:{s:02d}] {who}: {text}")
        tasks = [t for t in self.store.tasks.values() if t.status != "dismissed"]
        research = "\n".join(
            f"- {t.type} '{t.title}' ({t.status})" + (f": {t.artifact.content}" if t.artifact and t.artifact.content else "")
            for t in tasks
        ) or "(none yet)"
        spoke = (f"You last spoke {int(now - self.last_spoke_at)} s ago." if self.last_spoke_at else "You have not spoken yet.")
        return (
            f"In the call right now (besides you): {', '.join(names) or 'nobody identified yet'}.\n"
            f"Your research and tasks in this meeting:\n{research}\n"
            f"Your hand: {'RAISED, point: ' + self.hand if self.hand else 'down'}. {spoke}\n\n"
            f"Transcript (oldest first; '>>' marks lines since your last decision):\n" + "\n".join(transcript)
            + "\n\nDecide now."
        )

    async def decide(self) -> dict:
        prompt = self.prompt()  # before resetting the counter: it marks the fresh lines
        self.new_since_decision = 0
        started = time.time()
        last_line = self.lines[-1][2] if self.lines else ""
        result = await llm.generate(
            "floor", prompt, system=SYSTEM, search=True, model=FLOOR_MODEL, thinking=FLOOR_THINKING,
            mock=lambda: llm.LLMResult(text=json.dumps(_mock_decision(last_line, self.hand))), mock_delay=0.3,
        )
        decision = _parse(result.text)
        log.info("floor %.1fs: %s", time.time() - started, decision)
        if self.last_caption_at > started and decision["action"] == "speak" and not NAME.search(last_line):
            return decision  # someone kept talking while we decided; their caption's timer decides again
        if self.new_since_decision and decision["action"] != "speak":
            return decision  # people kept talking; the next pause decides again
        self.apply(decision)
        return decision

    def apply(self, decision: dict) -> None:
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

    # --- output: the voice pages ---

    def spoken(self) -> None:
        """The page finished saying Adjourn's words."""
        self.speaking = False
        self.last_spoke_at = time.time()
        if self.new_since_decision:
            self._schedule(AFTER_SPEECH_S)  # someone spoke while Adjourn was talking

    def _send(self, message: dict) -> None:
        for page in list(self.pages):
            asyncio.create_task(_safe_send(page, message))

    def reset(self) -> None:
        self.lines.clear()
        self.hand, self.speaking, self.last_spoke_at, self.new_since_decision = None, False, 0.0, 0
        self.talking.clear()
        self.present.clear()


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
    return {"action": action, "say": (data.get("say") or "").strip(), "point": (data.get("point") or "").strip(),
            "reason": data.get("reason", "")}


def _mock_decision(last_line: str, hand: str | None) -> dict:
    """LLM_MODE=mock: speak when named, raise a hand on a question to the room, else silent."""
    if hand and re.search(r"\bgo ahead\b|\byes\b", last_line, re.I):
        return {"action": "speak", "say": hand, "reason": "invited"}
    if NAME.search(last_line):
        return {"action": "speak", "say": "Mock answer from Adjourn.", "reason": "addressed"}
    if last_line.rstrip().endswith("?") and not hand:
        return {"action": "raise_hand", "point": "I can answer that.", "reason": "open question"}
    return {"action": "silent", "reason": "not for me"}
