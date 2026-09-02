// EventRiver: the last 60 seconds of events, flowing left→right.
// Client-derived gesture events + server {"type":"event"} + agent {"type":"marker"}
// bars in one canvas. Hover an event to inspect it.
import { useEffect, useRef, useState } from 'react'
import { getBrain, useBrain } from '../../ws'
import { EventDetector, eventStyle, eventLane, type DerivedEvent } from './lib/events'
import './right.css'

const WINDOW_S = 60
const LANES = 3
const LANE_LABELS = ['face', 'mind', 'head']

interface RiverEvent extends DerivedEvent {
  label?: string
  isMarker?: boolean
}

export function EventRiver() {
  const { state, connected } = useBrain()
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const detector = useRef(new EventDetector())
  const river = useRef<RiverEvent[]>([])
  const seenServer = useRef(0) // how many of ws events[] we've ingested
  const lastUpdated = useRef(0)
  const mouse = useRef<{ x: number; y: number } | null>(null)
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null)

  // ingest: derive from each new state (dedup on state.updated)
  useEffect(() => {
    if (!state || state.updated === lastUpdated.current) return
    lastUpdated.current = state.updated ?? 0
    const t = Date.now() / 1000
    river.current.push(...detector.current.feed(state, t))
  }, [state])

  // render loop
  useEffect(() => {
    let raf = 0
    const draw = () => {
      raf = requestAnimationFrame(draw)
      const canvas = canvasRef.current
      if (!canvas) return
      const dpr = window.devicePixelRatio || 1
      const w = canvas.clientWidth, h = canvas.clientHeight
      if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.height = h * dpr }
      const ctx = canvas.getContext('2d')!
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, w, h)

      // ingest server events/markers (ws store keeps a rolling array)
      const brain = getBrain()
      if (brain.events.length !== seenServer.current) {
        const fresh = brain.events.slice(seenServer.current)
        seenServer.current = brain.events.length
        for (const ev of fresh) {
          river.current.push({ kind: ev.kind, t: ev.t, meta: ev.meta, label: ev.label, isMarker: ev.type === 'marker' })
        }
      }

      const now = Date.now() / 1000
      river.current = river.current.filter((e) => now - e.t < WINDOW_S + 5)

      const axisH = 16
      const plotH = h - axisH
      const xOf = (t: number) => w - ((now - t) / WINDOW_S) * w
      const laneY = (lane: number) => plotH * ((lane + 0.5) / LANES)

      // lane guides + labels
      ctx.font = '9px ui-monospace, monospace'
      for (let i = 0; i < LANES; i++) {
        const y = laneY(i)
        ctx.strokeStyle = 'rgba(28,37,48,0.8)'
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke()
        ctx.fillStyle = '#3a4a5c'
        ctx.fillText(LANE_LABELS[i], 4, y - 6)
      }
      // time axis: a tick every 10s
      ctx.fillStyle = '#6b7a8c'
      ctx.strokeStyle = 'rgba(107,122,140,0.25)'
      for (let s = 0; s <= WINDOW_S; s += 10) {
        const x = xOf(now - s)
        ctx.beginPath(); ctx.moveTo(x, plotH); ctx.lineTo(x, plotH + 4); ctx.stroke()
        const lbl = s === 0 ? 'now' : `-${s}s`
        ctx.fillText(lbl, Math.min(Math.max(x - 10, 2), w - 26), h - 4)
      }

      // events
      let hover: { x: number; y: number; text: string } | null = null
      for (const ev of river.current) {
        const x = xOf(ev.t)
        if (x < -20) continue
        const age = (now - ev.t) / WINDOW_S
        const alpha = Math.max(0.15, 1 - age * 0.9)
        if (ev.isMarker) {
          // agent marker: vertical bar across all lanes
          ctx.globalAlpha = alpha
          ctx.strokeStyle = '#c792ea'
          ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, plotH); ctx.stroke()
          ctx.fillStyle = '#c792ea'
          ctx.font = '9px ui-monospace, monospace'
          ctx.save(); ctx.translate(x + 3, 10); ctx.rotate(Math.PI / 2); ctx.fillText((ev.label ?? 'agent').slice(0, 18), 0, 0); ctx.restore()
          ctx.globalAlpha = 1
        } else {
          const { glyph, color } = eventStyle(ev.kind)
          const y = laneY(eventLane(ev.kind))
          ctx.globalAlpha = alpha
          ctx.font = '14px ui-monospace, monospace'
          ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
          // soft halo so glyphs read on dark
          ctx.fillStyle = color
          ctx.beginPath(); ctx.arc(x, y, 9, 0, Math.PI * 2); ctx.globalAlpha = alpha * 0.15; ctx.fill()
          ctx.globalAlpha = alpha
          ctx.fillText(glyph, x, y)
          ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic'
          ctx.globalAlpha = 1
        }
        // hover hit-test
        const m = mouse.current
        if (m && Math.abs(m.x - x) < 12) {
          const y = ev.isMarker ? m.y : laneY(eventLane(ev.kind))
          if (ev.isMarker || Math.abs(m.y - y) < 14) {
            const ago = (now - ev.t).toFixed(1)
            const extra = ev.meta ? ' ' + Object.entries(ev.meta).map(([k, v]) => `${k}=${typeof v === 'number' ? (v as number).toFixed(2) : v}`).join(' ') : ''
            hover = { x: m.x, y, text: `${ev.isMarker ? '⧙agent⧘ ' + (ev.label ?? '') : ev.kind}${extra} · ${ago}s ago` }
          }
        }
      }
      setTip((prev) => {
        if (hover === null && prev === null) return prev
        if (hover && prev && hover.text === prev.text && Math.abs(hover.x - prev.x) < 2) return prev
        return hover
      })
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [])

  const empty = river.current.length === 0
  return (
    <div className="panel event-river">
      <div className="panel-title">event river <span className="hint">blinks · gestures · focus shifts · agent markers · 60s</span></div>
      <div className="river-wrap">
        <canvas
          ref={canvasRef}
          className="river-canvas"
          onMouseMove={(e) => {
            const r = e.currentTarget.getBoundingClientRect()
            mouse.current = { x: e.clientX - r.left, y: e.clientY - r.top }
          }}
          onMouseLeave={() => { mouse.current = null; setTip(null) }}
        />
        {tip && (
          <div className="river-tip" style={{ left: Math.min(tip.x + 10, 340), top: tip.y - 26 }}>{tip.text}</div>
        )}
        {empty && <div className="river-empty">{connected ? 'watching… blink, wink or clench' : 'waiting for stream'}</div>}
      </div>
    </div>
  )
}
