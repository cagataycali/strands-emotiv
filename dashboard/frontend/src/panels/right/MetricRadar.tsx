// MetricRadar: the met metrics visualized.
// Top: 7-axis radar polygon, morphing toward each update, ~10 fading ghosts.
// Bottom: valence/arousal plane (x = relaxation−stress, y = excitement) with a
// comet trail seeded from /api/history and fed live.
import { useEffect, useRef } from 'react'
import { useBrain } from '../../ws'
import './right.css'

const AXES = ['attention', 'engagement', 'excitement', 'longExcitement', 'stress', 'relaxation', 'interest'] as const
const AXIS_LABEL: Record<string, string> = {
  attention: 'focus', engagement: 'engage', excitement: 'excite',
  longExcitement: 'excite∞', stress: 'stress', relaxation: 'relax', interest: 'interest',
}
type Metrics = Partial<Record<(typeof AXES)[number], number>>

const GHOSTS = 10
const TRAIL = 60

interface TrailPt { x: number; y: number; t: number }

export function MetricRadar() {
  const { state } = useBrain()
  const radarRef = useRef<HTMLCanvasElement>(null)
  const planeRef = useRef<HTMLCanvasElement>(null)
  const target = useRef<number[]>(new Array(AXES.length).fill(0))
  const shown = useRef<number[]>(new Array(AXES.length).fill(0))
  const ghosts = useRef<number[][]>([])
  const trail = useRef<TrailPt[]>([])
  const lastUpdated = useRef(0)
  const absent = useRef<boolean[]>(new Array(AXES.length).fill(true))
  const seeded = useRef(false)

  // seed the comet trail from history (once)
  useEffect(() => {
    if (seeded.current) return
    seeded.current = true
    fetch('/api/history?limit=' + TRAIL)
      .then((r) => (r.ok ? r.json() : []))
      .then((rows: (Metrics & { t: number })[]) => {
        if (!Array.isArray(rows)) return
        for (const r of rows) {
          if (r.relaxation == null) continue
          trail.current.push({ x: (r.relaxation ?? 0) - (r.stress ?? 0), y: r.excitement ?? 0, t: r.t })
        }
        trail.current.sort((a, b) => a.t - b.t)
      })
      .catch(() => { /* history optional */ })
  }, [])

  // on each metric update: retarget radar, push ghost, extend trail
  useEffect(() => {
    const m = state?.metrics as Metrics | undefined
    // Cortex only emits an axis when its detector is active (e.g. `foc`/attention is
    // often absent on Basic). Gate on ANY metric, never on one axis: absent ≠ zero.
    if (!m || !AXES.some((a) => m[a] != null) || state?.updated === lastUpdated.current) return
    lastUpdated.current = state?.updated ?? 0
    absent.current = AXES.map((a) => m[a] == null)
    const next = AXES.map((a) => m[a] ?? 0)
    if (target.current.some((v, i) => v !== next[i])) {
      ghosts.current.push([...shown.current])
      if (ghosts.current.length > GHOSTS) ghosts.current.shift()
      target.current = next
      const t = Date.now() / 1000
      const last = trail.current[trail.current.length - 1]
      if (!last || t - last.t > 1) {
        trail.current.push({ x: (m.relaxation ?? 0) - (m.stress ?? 0), y: m.excitement ?? 0, t })
        while (trail.current.length > TRAIL) trail.current.shift()
      }
    }
  }, [state])

  useEffect(() => {
    let raf = 0
    const draw = () => {
      raf = requestAnimationFrame(draw)
      drawRadar()
      drawPlane()
    }

    const setup = (canvas: HTMLCanvasElement | null) => {
      if (!canvas) return null
      const dpr = window.devicePixelRatio || 1
      const w = canvas.clientWidth, h = canvas.clientHeight
      if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.height = h * dpr }
      const ctx = canvas.getContext('2d')!
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, w, h)
      return { ctx, w, h }
    }

    const poly = (ctx: CanvasRenderingContext2D, cx: number, cy: number, R: number, vals: number[]) => {
      ctx.beginPath()
      vals.forEach((v, i) => {
        const a = (i / AXES.length) * Math.PI * 2 - Math.PI / 2
        const r = Math.max(0.02, Math.min(1, v)) * R
        const x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)
      })
      ctx.closePath()
    }

    const drawRadar = () => {
      const s = setup(radarRef.current); if (!s) return
      const { ctx, w, h } = s
      const cx = w / 2, cy = h / 2, R = Math.min(w, h) / 2 - 30

      // morph
      for (let i = 0; i < AXES.length; i++) shown.current[i] += (target.current[i] - shown.current[i]) * 0.06

      // grid rings + axes
      ctx.strokeStyle = 'rgba(28,37,48,0.9)'
      for (const f of [0.25, 0.5, 0.75, 1]) { poly(ctx, cx, cy, R * f, new Array(AXES.length).fill(1)); ctx.stroke() }
      ctx.font = '10px ui-monospace, monospace'
      AXES.forEach((a, i) => {
        const ang = (i / AXES.length) * Math.PI * 2 - Math.PI / 2
        ctx.strokeStyle = 'rgba(28,37,48,0.9)'
        ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(ang) * R, cy + Math.sin(ang) * R); ctx.stroke()
        const lx = cx + Math.cos(ang) * (R + 14), ly = cy + Math.sin(ang) * (R + 14)
        ctx.fillStyle = absent.current[i] ? '#3a4a5c' : a === 'stress' ? '#ffb454' : '#6b7a8c'
        ctx.textAlign = Math.abs(Math.cos(ang)) < 0.3 ? 'center' : Math.cos(ang) > 0 ? 'left' : 'right'
        ctx.textBaseline = 'middle'
        ctx.fillText(AXIS_LABEL[a], lx, ly)
        // value at vertex
        const v = shown.current[i]
        ctx.fillStyle = absent.current[i] ? 'rgba(107,122,140,0.5)' : 'rgba(219,228,238,0.5)'
        ctx.fillText(absent.current[i] ? '·' : v.toFixed(2), lx, ly + 11)
      })
      ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic'

      // ghosts, oldest faintest
      ghosts.current.forEach((g, i) => {
        const a = ((i + 1) / ghosts.current.length) * 0.28
        ctx.strokeStyle = `rgba(122,162,255,${a})`
        poly(ctx, cx, cy, R, g); ctx.stroke()
      })

      // current polygon
      poly(ctx, cx, cy, R, shown.current)
      ctx.fillStyle = 'rgba(78,225,194,0.14)'; ctx.fill()
      ctx.strokeStyle = '#4ee1c2'; ctx.lineWidth = 1.5; ctx.stroke(); ctx.lineWidth = 1
    }

    const drawPlane = () => {
      const s = setup(planeRef.current); if (!s) return
      const { ctx, w, h } = s
      const pad = 18
      const px = (x: number) => pad + ((x + 1) / 2) * (w - pad * 2) // x ∈ [-1,1]
      const py = (y: number) => h - pad - Math.max(0, Math.min(1, y)) * (h - pad * 2) // y ∈ [0,1]

      // axes
      ctx.strokeStyle = 'rgba(28,37,48,0.9)'
      ctx.beginPath(); ctx.moveTo(px(0), pad); ctx.lineTo(px(0), h - pad); ctx.stroke()
      ctx.beginPath(); ctx.moveTo(pad, py(0)); ctx.lineTo(w - pad, py(0)); ctx.stroke()
      ctx.fillStyle = '#3a4a5c'; ctx.font = '9px ui-monospace, monospace'
      ctx.fillText('stressed', pad, py(0) - 4)
      ctx.textAlign = 'right'; ctx.fillText('relaxed', w - pad, py(0) - 4)
      ctx.textAlign = 'center'
      ctx.fillText('excited', px(0), pad - 6 + 14)
      ctx.textAlign = 'left'

      // comet trail
      const pts = trail.current
      for (let i = 1; i < pts.length; i++) {
        const a = (i / pts.length) * 0.7
        ctx.strokeStyle = `rgba(122,162,255,${a})`
        ctx.lineWidth = 1 + (i / pts.length) * 1.5
        ctx.beginPath()
        ctx.moveTo(px(pts[i - 1].x), py(pts[i - 1].y))
        ctx.lineTo(px(pts[i].x), py(pts[i].y))
        ctx.stroke()
      }
      ctx.lineWidth = 1
      const head = pts[pts.length - 1]
      if (head) {
        ctx.fillStyle = '#4ee1c2'
        ctx.beginPath(); ctx.arc(px(head.x), py(head.y), 4, 0, Math.PI * 2); ctx.fill()
        ctx.fillStyle = 'rgba(78,225,194,0.25)'
        ctx.beginPath(); ctx.arc(px(head.x), py(head.y), 9, 0, Math.PI * 2); ctx.fill()
      } else {
        ctx.fillStyle = '#6b7a8c'; ctx.font = '11px ui-monospace, monospace'
        ctx.textAlign = 'center'; ctx.fillText('waiting for metrics…', w / 2, h / 2); ctx.textAlign = 'left'
      }
    }

    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [])

  return (
    <div className="panel metric-radar">
      <div className="panel-title">metric radar <span className="hint">met @ 0.1 to 2 Hz</span></div>
      <canvas ref={radarRef} className="radar-canvas" />
      <div className="plane-caption">valence / arousal · last {TRAIL} readings</div>
      <canvas ref={planeRef} className="plane-canvas" />
    </div>
  )
}
