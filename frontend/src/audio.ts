// Mic -> 16 kHz 16-bit PCM -> WS /ws/audio, in ~100 ms binary frames.
// Echo cancellation is off: the remote voice comes out of this laptop's speakers,
// which is exactly what cancellation would remove.

export interface Capture {
  stop: () => void;
}

export async function startCapture(onLevel: (level: number) => void): Promise<Capture> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: true },
  });
  const context = new AudioContext({ sampleRate: 16000 });
  await context.audioWorklet.addModule("/pcm-worklet.js");
  const source = context.createMediaStreamSource(stream);
  const worklet = new AudioWorkletNode(context, "pcm-worklet");
  source.connect(worklet);

  const proto = location.protocol === "https:" ? "wss" : "ws";
  let socket: WebSocket | null = null;
  let stopped = false;
  const connect = () => {
    socket = new WebSocket(`${proto}://${location.host}/ws/audio`);
    socket.binaryType = "arraybuffer";
    socket.onclose = () => {
      if (!stopped) setTimeout(connect, 1000);
    };
  };
  connect();

  worklet.port.onmessage = (event: MessageEvent<{ pcm: ArrayBuffer; level: number }>) => {
    onLevel(event.data.level);
    if (socket?.readyState === WebSocket.OPEN) socket.send(event.data.pcm);
  };

  return {
    stop: () => {
      stopped = true;
      worklet.port.onmessage = null;
      socket?.close();
      stream.getTracks().forEach((t) => t.stop());
      void context.close();
      onLevel(0);
    },
  };
}
