// Runs inside AudioWorkletGlobalScope (see audio-worklet.d.ts for why the
// ambient types are hand-declared). Forwards raw 128-sample mono blocks to
// the main thread untouched — no resampling, no filtering, no gain. All
// signal processing happens server-side (plan decision #1): a client-side
// resampler would make queries diverge from how the corpus was built and
// silently wreck recognition.
class RecorderProcessor extends AudioWorkletProcessor {
  process(inputs: Float32Array[][]): boolean {
    const channel = inputs[0]?.[0]
    if (channel && channel.length > 0) {
      // The Float32Array passed into process() is reused by the audio
      // engine across calls — copy it before posting or the data races.
      this.port.postMessage(channel.slice())
    }
    return true // keep the processor alive for the whole recording session
  }
}

registerProcessor('recorder-processor', RecorderProcessor)
