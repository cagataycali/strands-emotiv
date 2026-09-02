// events.ts: client-side event derivation from state transitions (until the
// server events cover everything). Debounced, hysteretic,
// fires exactly once per gesture. Pure class: no React, testable with fed states.
import type { BrainState } from '../../../ws'

export interface DerivedEvent {
  kind: string
  t: number // seconds (epoch)
  meta?: Record<string, unknown>
}

const BLINK_DEBOUNCE = 0.3 // s
const FACE_DEBOUNCE = 0.5

export class EventDetector {
  private lastEye = 'neutral'
  private lastLower = 'neutral'
  private lastFire: Record<string, number> = {}
  // hysteresis arms: true = armed (may fire on crossing)
  private focusHighArmed = true
  private focusLowArmed = true
  private stressArmed = true

  private fire(out: DerivedEvent[], kind: string, t: number, debounce: number, meta?: Record<string, unknown>) {
    const last = this.lastFire[kind] ?? -Infinity
    if (t - last < debounce) return
    this.lastFire[kind] = t
    out.push({ kind, t, meta })
  }

  /** Feed one state snapshot; returns newly fired events. */
  feed(state: BrainState, t: number): DerivedEvent[] {
    const out: DerivedEvent[] = []
    const f = state.facial
    if (f?.eye != null) {
      if (f.eye !== this.lastEye) {
        if (f.eye === 'blink') this.fire(out, 'blink', t, BLINK_DEBOUNCE)
        else if (f.eye === 'winkL') this.fire(out, 'wink_left', t, FACE_DEBOUNCE)
        else if (f.eye === 'winkR') this.fire(out, 'wink_right', t, FACE_DEBOUNCE)
        this.lastEye = f.eye
      }
    }
    if (f?.lower != null) {
      const pow = f.lower_pow ?? 0
      if (f.lower !== this.lastLower) {
        if (f.lower === 'clench' && pow > 0.5) this.fire(out, 'clench', t, FACE_DEBOUNCE, { pow })
        else if (f.lower === 'smile' && pow > 0.3) this.fire(out, 'smile', t, FACE_DEBOUNCE, { pow })
        this.lastLower = f.lower
      }
    }
    const m = state.metrics
    if (m?.attention != null) {
      const a = m.attention
      if (this.focusHighArmed && a > 0.6) { this.fire(out, 'focus_high', t, 1, { attention: a }); this.focusHighArmed = false }
      else if (!this.focusHighArmed && a < 0.55) this.focusHighArmed = true
      if (this.focusLowArmed && a < 0.4) { this.fire(out, 'focus_low', t, 1, { attention: a }); this.focusLowArmed = false }
      else if (!this.focusLowArmed && a > 0.45) this.focusLowArmed = true
    }
    if (m?.stress != null) {
      const s = m.stress
      if (this.stressArmed && s > 0.6) { this.fire(out, 'stress_high', t, 1, { stress: s }); this.stressArmed = false }
      else if (!this.stressArmed && s < 0.5) this.stressArmed = true
    }
    return out
  }
}

/** glyph + color per event kind (server kinds normalized here too) */
export function eventStyle(kind: string): { glyph: string; color: string } {
  switch (kind) {
    case 'blink': return { glyph: '👁', color: '#7aa2ff' }
    case 'double_blink': return { glyph: '👁👁', color: '#7aa2ff' }
    case 'wink_left': case 'wink_L': return { glyph: '😉', color: '#7aa2ff' }
    case 'wink_right': case 'wink_R': return { glyph: '😜', color: '#7aa2ff' }
    case 'clench': return { glyph: '😬', color: '#ff5470' }
    case 'smile': return { glyph: '🙂', color: '#4ee16a' }
    case 'head_turn_left': return { glyph: '↩', color: '#dbe4ee' }
    case 'head_turn_right': return { glyph: '↪', color: '#dbe4ee' }
    case 'nod': return { glyph: '⇣', color: '#dbe4ee' }
    case 'focus_high': return { glyph: '🎯', color: '#4ee1c2' }
    case 'focus_low': return { glyph: '🌫', color: '#6b7a8c' }
    case 'stress_high': return { glyph: '⚡', color: '#ffb454' }
    default:
      if (kind.startsWith('command:')) return { glyph: '🧠', color: '#c792ea' }
      return { glyph: '·', color: '#6b7a8c' }
  }
}

/** row lane per kind so the river reads at a glance: 0 face · 1 focus/stress · 2 head/command */
export function eventLane(kind: string): number {
  if (['blink', 'double_blink', 'wink_left', 'wink_right', 'wink_L', 'wink_R', 'clench', 'smile'].includes(kind)) return 0
  if (['focus_high', 'focus_low', 'stress_high'].includes(kind)) return 1
  return 2
}
