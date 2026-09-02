// derive.ts: pure derivations for the AgentEye ambient line.
// No React, no side effects: testable against fixture states.
import type { BrainState } from '../../../ws'

export const CHANNELS = ['AF3', 'F7', 'F3', 'FC5', 'T7', 'P7', 'O1', 'O2', 'P8', 'T8', 'FC6', 'F4', 'F8', 'AF4'] as const
export const BANDS = ['theta', 'alpha', 'betaL', 'betaH', 'gamma'] as const
export type Band = (typeof BANDS)[number]

const BAND_LABEL: Record<Band, string> = { theta: 'theta', alpha: 'alpha', betaL: 'betaL', betaH: 'betaH', gamma: 'gamma' }

/** argmax band summed over all channels + the top-2 channels of that band */
export function bandDominance(bp: BrainState['band_power']): { band: string; top: string[] } | null {
  if (!bp) return null
  const sums: Partial<Record<Band, number>> = {}
  for (const band of BANDS) {
    let s = 0
    for (const ch of Object.keys(bp)) s += bp[ch]?.[band] ?? 0
    sums[band] = s
  }
  let best: Band | null = null
  for (const band of BANDS) if (best === null || (sums[band] ?? 0) > (sums[best] ?? 0)) best = band
  if (best === null) return null
  const top = Object.keys(bp)
    .sort((a, b) => (bp[b]?.[best!] ?? 0) - (bp[a]?.[best!] ?? 0))
    .slice(0, 2)
  return { band: BAND_LABEL[best], top }
}

/** count of contact_quality === 4 out of total; label per thresholds */
export function cqSummary(cq: BrainState['contact_quality']): { good: number; total: number; label: string } {
  const total = cq ? Object.keys(cq).length : 14
  const good = cq ? Object.values(cq).filter((v) => v === 4).length : 0
  const label = good >= 12 ? 'good' : good >= 10 ? 'ok' : 'poor'
  return { good, total, label }
}

/** arrow for a delta vs ~10s ago; blank when flat or unknown */
export function arrow(delta: number | null, eps = 0.02): string {
  if (delta === null) return ''
  if (delta > eps) return ' ↑'
  if (delta < -eps) return ' ↓'
  return ''
}

export interface AgentLineInput {
  state: BrainState
  attentionDelta: number | null // vs ~10s ago, null = not enough history
  stressDelta: number | null
  stillSec: number | null // null = no motion stream yet
}

/** Fewer than 10 green electrodes ⇒ the agent must not pretend to see. */
export function canSee(state: BrainState | null): boolean {
  if (!state) return false
  return cqSummary(state.contact_quality).good >= 10
}

/** The exact one-line context the agent reads. */
export function buildAgentLine(inp: AgentLineInput): string {
  const { state, attentionDelta, stressDelta, stillSec } = inp
  const m = state.metrics ?? {}
  const focus = m.attention != null ? m.attention.toFixed(2) : 'n/a'
  const stress = m.stress != null ? m.stress.toFixed(2) : 'n/a'
  const dom = bandDominance(state.band_power)
  const domTxt = dom ? `${dom.band} dominant ${dom.top.join('/')}` : 'bands n/a'
  const stillTxt = stillSec === null ? 'motion: n/a' : `still ${Math.floor(stillSec)}s`
  const cq = cqSummary(state.contact_quality)
  return `[brain: focus ${focus}${arrow(attentionDelta)} · stress ${stress}${arrow(stressDelta)} · ${domTxt} · ${stillTxt} · CQ ${cq.good}/${cq.total} ${cq.label}]`
}
