// AgentEye: the exact ambient line the agent reads.
// One big monospace line. The server's own ambient_line() when present
// (verbatim, arrows and stillness included); derived client-side as fallback.
import { useEffect, useRef, useState } from 'react'
import { useBrain } from '../../ws'
import { buildAgentLine, canSee, cqSummary } from './lib/derive'
import { setEyeLine } from './lib/eyeline'
import './right.css'

interface HistPt { t: number; attention: number; stress: number }

/** delta of a metric vs the sample nearest to `ago` seconds back (needs ≥8s of history) */
function deltaVs(hist: HistPt[], now: number, key: 'attention' | 'stress', ago = 10): number | null {
  if (hist.length < 2) return null
  const target = now - ago
  let best: HistPt | null = null
  for (const p of hist) {
    if (best === null || Math.abs(p.t - target) < Math.abs(best.t - target)) best = p
  }
  if (!best || now - best.t < 8) return null
  const cur = hist[hist.length - 1]
  return cur[key] - best[key]
}

export function AgentEye() {
  const { state, motion } = useBrain()
  const hist = useRef<HistPt[]>([])
  const lastQ = useRef<number[] | null>(null)
  const stillSince = useRef<number | null>(null)
  const prevSegs = useRef<string[]>([])
  const segVer = useRef<number[]>([])
  const [, force] = useState(0)

  // metric history ring (for the ↑↓ deltas)
  useEffect(() => {
    const m = state?.metrics
    if (!m || m.attention == null) return
    const now = Date.now() / 1000
    hist.current.push({ t: now, attention: m.attention ?? 0, stress: m.stress ?? 0 })
    while (hist.current.length > 0 && now - hist.current[0].t > 30) hist.current.shift()
  }, [state?.metrics])

  // stillness clock from motion quaternion
  useEffect(() => {
    if (!motion?.q) return
    const now = Date.now() / 1000
    const prev = lastQ.current
    if (prev) {
      let d = 0
      for (let i = 0; i < Math.min(prev.length, motion.q.length); i++) d += Math.abs(motion.q[i] - prev[i])
      if (d > 0.01 || stillSince.current === null) stillSince.current = now
    } else {
      stillSince.current = now
    }
    lastQ.current = motion.q
  }, [motion])

  // tick the "still Ns" counter once a second even when state is quiet
  useEffect(() => {
    const id = setInterval(() => force((n) => n + 1), 1000)
    return () => clearInterval(id)
  }, [])

  if (!state) {
    return (
      <div className="panel agent-eye">
        <div className="panel-title">agent eye <span className="hint">what the agent reads</span></div>
        <div className="waiting">waiting for brain…</div>
      </div>
    )
  }

  const cq = cqSummary(state.contact_quality)
  if (!canSee(state)) {
    return (
      <div className="panel agent-eye">
        <div className="panel-title">agent eye <span className="hint">what the agent reads</span></div>
        <div className="eye-blind">
          I can't see your brain right now
          <span className="eye-blind-sub">CQ {cq.good}/{cq.total}: adjust the headset</span>
        </div>
      </div>
    )
  }

  const now = Date.now() / 1000
  const serverLine = (state as { ambient?: string | null }).ambient
  const line = serverLine ?? buildAgentLine({
    state,
    attentionDelta: deltaVs(hist.current, now, 'attention'),
    stressDelta: deltaVs(hist.current, now, 'stress'),
    stillSec: stillSince.current === null ? null : now - stillSince.current,
  })
  setEyeLine(line)

  // segment-level change flash: split on the separators, remount only segments
  // whose text changed so the CSS settle animation replays on exactly those.
  const m = line.match(/^(\[)(.*)(\])$/)
  const body = m ? m[2] : line
  const segs = body.split(' · ')
  if (segs.length !== prevSegs.current.length) {
    segVer.current = segs.map(() => 0)
  } else {
    segVer.current = segVer.current.map((v, i) => (segs[i] === prevSegs.current[i] ? v : v + 1))
  }
  prevSegs.current = segs

  return (
    <div className="panel agent-eye">
      <div className="panel-title">agent eye <span className="hint">what the agent reads, verbatim</span></div>
      <div className="eye-line">
        {m ? '[' : null}
        {segs.map((s, i) => (
          <span key={`${i}:${segVer.current[i]}`} className={segVer.current[i] > 0 ? 'eye-seg eye-seg-new' : 'eye-seg'}>
            {i > 0 ? ' · ' : ''}
            {s}
          </span>
        ))}
        {m ? ']' : null}
      </div>
    </div>
  )
}
