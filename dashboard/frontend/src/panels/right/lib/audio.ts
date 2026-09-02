// audio.ts: the Sonify graph.
// alpha (mean band power) → warm 110 Hz drone volume · beta → soft click tempo ·
// blink → short tick. Compressed at the output so it can never distort or startle.
export class SonifyEngine {
  private ctx: AudioContext | null = null
  private droneGain: GainNode | null = null
  private clickGain: GainNode | null = null
  private master: GainNode | null = null
  private nextClick = 0
  private clickHz = 1
  private timer: number | null = null
  private noiseBuf: AudioBuffer | null = null

  get running(): boolean { return this.ctx !== null }

  start() {
    if (this.ctx) return
    const ctx = new AudioContext()
    this.ctx = ctx

    // output safety: master gain → compressor → speakers
    const master = ctx.createGain(); master.gain.value = 0.25
    const comp = ctx.createDynamicsCompressor()
    comp.threshold.value = -24; comp.ratio.value = 8; comp.knee.value = 12
    master.connect(comp); comp.connect(ctx.destination)
    this.master = master

    // drone: triangle 110 Hz, slow ±3 cent LFO for warmth, lowpass keeps it soft
    const osc = ctx.createOscillator(); osc.type = 'triangle'; osc.frequency.value = 110
    const lfo = ctx.createOscillator(); lfo.frequency.value = 0.08
    const lfoAmt = ctx.createGain(); lfoAmt.gain.value = 110 * (Math.pow(2, 3 / 1200) - 1) // ±3 cents
    lfo.connect(lfoAmt); lfoAmt.connect(osc.frequency)
    const lp = ctx.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 400
    const droneGain = ctx.createGain(); droneGain.gain.value = 0
    osc.connect(lp); lp.connect(droneGain); droneGain.connect(master)
    osc.start(); lfo.start()
    this.droneGain = droneGain

    // click bed: shared noise buffer, bandpassed bursts scheduled with lookahead
    const len = Math.floor(ctx.sampleRate * 0.006)
    const buf = ctx.createBuffer(1, len, ctx.sampleRate)
    const d = buf.getChannelData(0)
    for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / len)
    this.noiseBuf = buf
    const clickGain = ctx.createGain(); clickGain.gain.value = 0.5
    clickGain.connect(master)
    this.clickGain = clickGain

    this.nextClick = ctx.currentTime + 0.2
    this.timer = window.setInterval(() => this.schedule(), 80)
  }

  private burst(t: number, freq: number, gain: number, dur = 0.006) {
    if (!this.ctx || !this.noiseBuf || !this.clickGain) return
    const src = this.ctx.createBufferSource(); src.buffer = this.noiseBuf
    const bp = this.ctx.createBiquadFilter(); bp.type = 'bandpass'; bp.frequency.value = freq; bp.Q.value = 6
    const g = this.ctx.createGain(); g.gain.value = gain
    src.connect(bp); bp.connect(g); g.connect(this.clickGain)
    src.start(t); src.stop(t + dur + 0.02)
  }

  private schedule() {
    if (!this.ctx) return
    const ahead = this.ctx.currentTime + 0.25
    while (this.nextClick < ahead) {
      this.burst(this.nextClick, 1200, 0.35)
      this.nextClick += 1 / Math.max(0.5, Math.min(4, this.clickHz))
    }
  }

  /** feed normalized [0,1] alpha + beta once per state update */
  update(alphaNorm: number, betaNorm: number) {
    if (!this.ctx || !this.droneGain) return
    const target = Math.max(0, Math.min(1, alphaNorm)) * 0.12
    this.droneGain.gain.setTargetAtTime(target, this.ctx.currentTime, 0.6) // slow slew, never a jump
    this.clickHz = 0.5 + Math.max(0, Math.min(1, betaNorm)) * 3.5
  }

  blink() {
    if (!this.ctx) return
    this.burst(this.ctx.currentTime, 2000, 0.5, 0.02)
  }

  stop() {
    if (this.timer !== null) { clearInterval(this.timer); this.timer = null }
    const ctx = this.ctx
    this.ctx = null; this.droneGain = null; this.clickGain = null; this.master = null
    if (ctx) void ctx.close()
  }
}
