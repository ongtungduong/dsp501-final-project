// Ambient declarations for the AudioWorkletGlobalScope.
//
// TypeScript's "DOM" lib models the main-thread Web Audio API only — it has
// no notion of the separate AudioWorkletGlobalScope that recorder-worklet.ts
// actually runs in (registerProcessor, sampleRate, currentFrame, etc. are
// globals injected by the browser into that scope, not by any lib we can
// pull in). Declaring them by hand keeps the worklet file strictly typed
// without pulling in the conflicting "webworker" lib.

declare abstract class AudioWorkletProcessor {
  readonly port: MessagePort
  constructor(options?: AudioWorkletNodeOptions)
  abstract process(
    inputs: Float32Array[][],
    outputs: Float32Array[][],
    parameters: Record<string, Float32Array>,
  ): boolean
}

declare function registerProcessor(
  name: string,
  processorCtor: new (options?: AudioWorkletNodeOptions) => AudioWorkletProcessor,
): void

declare const sampleRate: number
declare const currentFrame: number
declare const currentTime: number
