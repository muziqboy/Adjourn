// Voice: says an answer out loud through the laptop speakers with the browser's built-in
// speech synthesis. Meet then hears it through the laptop microphone like a person in the room.
//
// Whether Meet passes it on (its echo cancellation may remove it) is the open "voice into the
// meeting" spike in docs/SCOPE.md. Swapping this for Gemini TTS or a virtual microphone only
// changes this file.

export function speak(text: string): Promise<void> {
  return new Promise((resolve) => {
    if (!("speechSynthesis" in window)) return resolve();
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1.05;
    const voice = window.speechSynthesis
      .getVoices()
      .find((v) => /en[-_](US|GB)/i.test(v.lang) && /Samantha|Google|Daniel|Natural/i.test(v.name));
    if (voice) utterance.voice = voice;
    utterance.onend = () => resolve();
    utterance.onerror = () => resolve();
    window.speechSynthesis.speak(utterance);
  });
}

export function stopSpeaking(): void {
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
}
