// Waterfall.tsx: per-channel band×time spectrogram.
// Columns: 14 channels × 5 band sub-columns. Time flows downward, newest row on top.
// 10-minute ring buffer (500 ms rows → 1200 rows) in an offscreen canvas; display
// draws the ring in two slices. Inferno colormap, per-band slow-decay normalizer
// (matches BandHeads). Data via getBrain() inside rAF, zero React re-renders.
import { useEffect, useRef } from 'react'
import { getBrain } from '../../ws'

const CHANNELS = ['AF3', 'F7', 'F3', 'FC5', 'T7', 'P7', 'O1', 'O2', 'P8', 'T8', 'FC6', 'F4', 'F8', 'AF4']
const BANDS = ['theta', 'alpha', 'betaL', 'betaH', 'gamma'] as const

const INFERNO: [number, number, number][] = [
  [0, 0, 4], [31, 12, 72], [85, 15, 109], [136, 34, 106], [186, 54, 85],
  [227, 89, 51], [249, 140, 10], [249, 201, 50], [252, 255, 164],
]
function inferno(t: number): [number, number, number] {
  t = Math.max(0, Math.min(1, t)) * (INFERNO.length - 1)
  const i = Math.min(INFERNO.length - 2, Math.floor(t))
  const u = t - i
  const a = INFERNO[i], b = INFERNO[i + 1]
  return [a[0] + (b[0] - a[0]) * u | 0, a[1] + (b[1] - a[1]) * u | 0, a[2] + (b[2] - a[2]) * u | 0]
}

const ROW_MS = 500          // one row per 500 ms
const ROWS = 1200           // = 10 minutes
const COLS = CHANNELS.length * BANDS.length // 70
const DISPLAY_H = 240

export function Waterfall() {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current!
    const ctx = canvas.getContext('2d')!

    // ring buffer canvas: 1 px per band-column, 1 px per row
    const off = document.createElement('canvas')
    off.width = COLS; off.height = ROWS
    const offCtx = off.getContext('2d')!
    offCtx.fillStyle = '#0d1117'
    offCtx.fillRect(0, 0, COLS, ROWS)
    const rowImg = offCtx.createImageData(COLS, 1)

    let head = 0 // y of the most recent row
    let rowsWritten = 0
    let lastRowT = 0
    let lastUpdated: number | undefined
    const maxes = new Float32Array(BANDS.length).fill(1e-6)

    let raf = 0
    let lastDraw = -1e9
    const draw = (now: number) => {
      raf = requestAnimationFrame(draw)
      const st = getBrain().state
      const bp = st?.band_power

      // append a row at most every ROW_MS, only when fresh data exists
      if (bp && now - lastRowT >= ROW_MS && st?.updated !== lastUpdated) {
        lastRowT = now
        lastUpdated = st?.updated
        // per-band frame max → slow-decay normalizer (shared across channels, per band)
        for (let b = 0; b < BANDS.length; b++) {
          let fm = 1e-6
          for (const ch of CHANNELS) {
            const v = Math.log10(1 + Math.max(0, bp[ch]?.[BANDS[b]] ?? 0))
            if (v > fm) fm = v
          }
          maxes[b] = Math.max(fm, maxes[b] * 0.998)
        }
        const d = rowImg.data
        for (let c = 0; c < CHANNELS.length; c++) {
          const b0 = bp[CHANNELS[c]]
          for (let b = 0; b < BANDS.length; b++) {
            const col = c * BANDS.length + b
            const v = Math.log10(1 + Math.max(0, b0 ? b0[BANDS[b]] : 0)) / maxes[b]
            const [r, g, bl] = inferno(v)
            d[col * 4] = r; d[col * 4 + 1] = g; d[col * 4 + 2] = bl; d[col * 4 + 3] = 255
          }
        }
        head = (head - 1 + ROWS) % ROWS
        offCtx.putImageData(rowImg, 0, head)
        rowsWritten = Math.min(rowsWritten + 1, ROWS)
      }

      // repaint display ~5 Hz
      if (now - lastDraw < 200) return
      lastDraw = now

      const W = canvas.clientWidth
      if (W === 0) return
      const H = DISPLAY_H
      if (canvas.width !== W * 2 || canvas.height !== H * 2) {
        canvas.width = W * 2; canvas.height = H * 2
        canvas.style.height = `${H}px`
      }
      ctx.setTransform(2, 0, 0, 2, 0, 0)
      ctx.imageSmoothingEnabled = false
      ctx.fillStyle = '#0d1117'
      ctx.fillRect(0, 0, W, H)

      const labelH = 16
      const plotH = H - labelH - 14
      const n = Math.max(1, rowsWritten)
      // ring → screen in two slices: head..ROWS, then 0..head
      const slice1 = Math.min(n, ROWS - head)
      const s1h = (slice1 / n) * plotH
      ctx.drawImage(off, 0, head, COLS, slice1, 0, labelH, W, s1h)
      if (n > slice1) {
        ctx.drawImage(off, 0, 0, COLS, n - slice1, 0, labelH + s1h, W, plotH - s1h)
      }

      // channel labels + separators
      const chW = W / CHANNELS.length
      ctx.font = '600 10px JetBrains Mono, monospace'
      ctx.textAlign = 'center'
      ctx.fillStyle = '#6b7a8c'
      for (let c = 0; c < CHANNELS.length; c++) {
        ctx.fillText(CHANNELS[c], c * chW + chW / 2, 11)
        if (c > 0) {
          ctx.strokeStyle = 'rgba(7,9,13,0.9)'
          ctx.lineWidth = 1
          ctx.beginPath()
          ctx.moveTo(c * chW, labelH)
          ctx.lineTo(c * chW, labelH + plotH)
          ctx.stroke()
        }
      }
      // time axis
      ctx.textAlign = 'left'
      ctx.fillStyle = '#4a5866'
      ctx.font = '10px JetBrains Mono, monospace'
      const minutes = (n * ROW_MS) / 60000
      ctx.fillText('now', 2, labelH + 10)
      ctx.fillText(`−${minutes < 10 ? minutes.toFixed(1) : '10'} min`, 2, labelH + plotH - 3)
      // band legend
      ctx.textAlign = 'right'
      ctx.fillText('per channel: θ α βL βH γ →', W - 2, H - 3)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [])

  return (
    <div className="panel">
      <h2><span className="dot" /> waterfall · band power × time</h2>
      <canvas ref={canvasRef} style={{ width: '100%', display: 'block' }} />
    </div>
  )
}
