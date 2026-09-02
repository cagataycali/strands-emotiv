// BandHeads.tsx: five heads-from-above topomaps, one per band.
// Gaussian-RBF over the 14 EPOC X sites, inferno colormap, ~10 Hz repaint,
// hover shows nearest channel + value. Canvas only; data read via getBrain(), no re-renders.
import { useEffect, useRef, useState } from 'react'
import { getBrain } from '../../ws'

// top-down 2D: x = right, y = toward nose (up on screen). From the 10-20 sphere.
const SITES2D: Record<string, [number, number]> = {
  AF3: [-0.34, 0.89], AF4: [0.34, 0.89],
  F7: [-0.81, 0.59], F8: [0.81, 0.59],
  F3: [-0.55, 0.67], F4: [0.55, 0.67],
  FC5: [-0.87, 0.3], FC6: [0.87, 0.3],
  T7: [-1.0, 0.0], T8: [1.0, 0.0],
  P7: [-0.81, -0.59], P8: [0.81, -0.59],
  O1: [-0.31, -0.95], O2: [0.31, -0.95],
}
const CHANNELS = Object.keys(SITES2D)
const BANDS = ['theta', 'alpha', 'betaL', 'betaH', 'gamma'] as const
const BAND_LABEL: Record<string, string> = { theta: 'θ theta', alpha: 'α alpha', betaL: 'βL low-beta', betaH: 'βH high-beta', gamma: 'γ gamma' }

// inferno colormap, 9 stops
const INFERNO: [number, number, number][] = [
  [0, 0, 4], [31, 12, 72], [85, 15, 109], [136, 34, 106], [186, 54, 85],
  [227, 89, 51], [249, 140, 10], [249, 201, 50], [252, 255, 164],
]
function inferno(t: number): [number, number, number] {
  t = Math.max(0, Math.min(1, t)) * (INFERNO.length - 1)
  const i = Math.min(INFERNO.length - 2, Math.floor(t))
  const u = t - i
  const a = INFERNO[i], b = INFERNO[i + 1]
  return [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u]
}

const GRID = 64 // offscreen resolution per head
const SIGMA = 0.42

export function BandHeads() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [hover, setHover] = useState<string>('')

  useEffect(() => {
    const canvas = canvasRef.current!
    const ctx = canvas.getContext('2d')!

    // precompute per-pixel RBF weights + inside-circle mask (shared by all heads)
    const nCh = CHANNELS.length
    const weights = new Float32Array(GRID * GRID * nCh)
    const mask = new Uint8Array(GRID * GRID)
    for (let py = 0; py < GRID; py++) {
      for (let px = 0; px < GRID; px++) {
        const x = (px / (GRID - 1)) * 2 - 1
        const y = 1 - (py / (GRID - 1)) * 2
        const idx = py * GRID + px
        if (x * x + y * y > 1.06) continue
        mask[idx] = 1
        for (let c = 0; c < nCh; c++) {
          const [sx, sy] = SITES2D[CHANNELS[c]]
          const d2 = (x - sx) * (x - sx) + (y - sy) * (y - sy)
          weights[idx * nCh + c] = Math.exp(-d2 / (2 * SIGMA * SIGMA))
        }
      }
    }
    const off = document.createElement('canvas')
    off.width = GRID; off.height = GRID
    const offCtx = off.getContext('2d')!
    const img = offCtx.createImageData(GRID, GRID)

    const maxes: Record<string, number> = {}
    for (const b of BANDS) maxes[b] = 1e-6

    let raf = 0
    let last = -1e9 // first frame always draws
    const draw = (now: number) => {
      raf = requestAnimationFrame(draw)
      if (now - last < 100) return // ~10 Hz
      last = now

      const bp = getBrain().state?.band_power
      const W = canvas.clientWidth
      if (W === 0) return
      const headW = Math.floor(W / BANDS.length)
      const R = Math.floor(headW * 0.36)
      const H = headW * 0.98
      if (canvas.width !== W * 2 || canvas.height !== Math.floor(H * 2)) {
        canvas.width = W * 2; canvas.height = Math.floor(H * 2)
        canvas.style.height = `${H}px`
      }
      ctx.setTransform(2, 0, 0, 2, 0, 0)
      ctx.clearRect(0, 0, W, H)

      BANDS.forEach((bandKey, bi) => {
        const cx = bi * headW + headW / 2
        const cy = H * 0.52

        if (bp) {
          const vals = new Float32Array(nCh)
          let fm = 1e-6
          for (let c = 0; c < nCh; c++) {
            const b = bp[CHANNELS[c]]
            const lv = Math.log10(1 + Math.max(0, b ? (b as any)[bandKey] : 0))
            vals[c] = lv
            if (lv > fm) fm = lv
          }
          maxes[bandKey] = Math.max(fm, maxes[bandKey] * 0.995)
          const m = maxes[bandKey]
          const d = img.data
          for (let i = 0; i < GRID * GRID; i++) {
            const o = i * 4
            if (!mask[i]) { d[o + 3] = 0; continue }
            let num = 0, den = 1e-9
            const base = i * nCh
            for (let c = 0; c < nCh; c++) { num += weights[base + c] * vals[c]; den += weights[base + c] }
            const [r, g, bl] = inferno(num / den / m)
            d[o] = r; d[o + 1] = g; d[o + 2] = bl; d[o + 3] = 235
          }
          offCtx.putImageData(img, 0, 0)
        }

        // scalp disc (clipped), ears, nose
        ctx.save()
        ctx.beginPath()
        ctx.arc(cx, cy, R, 0, Math.PI * 2)
        ctx.clip()
        ctx.imageSmoothingEnabled = true
        ctx.drawImage(off, cx - R, cy - R, R * 2, R * 2)
        ctx.restore()

        ctx.strokeStyle = '#2a3b52'
        ctx.lineWidth = 1.5
        ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.stroke()
        // nose
        ctx.beginPath()
        ctx.moveTo(cx - R * 0.14, cy - R * 0.99)
        ctx.lineTo(cx, cy - R * 1.14)
        ctx.lineTo(cx + R * 0.14, cy - R * 0.99)
        ctx.stroke()
        // ears
        for (const s of [-1, 1]) {
          ctx.beginPath()
          ctx.ellipse(cx + s * R * 1.04, cy, R * 0.07, R * 0.2, 0, 0, Math.PI * 2)
          ctx.stroke()
        }
        // electrode dots
        ctx.fillStyle = 'rgba(219,228,238,0.75)'
        for (const ch of CHANNELS) {
          const [sx, sy] = SITES2D[ch]
          ctx.beginPath()
          ctx.arc(cx + sx * R * 0.92, cy - sy * R * 0.92, 1.6, 0, Math.PI * 2)
          ctx.fill()
        }
        // label
        ctx.fillStyle = '#6b7a8c'
        ctx.font = '600 11px JetBrains Mono, monospace'
        ctx.textAlign = 'center'
        ctx.fillText(BAND_LABEL[bandKey], cx, H - 4)
      })
    }
    raf = requestAnimationFrame(draw)

    // hover: nearest channel within any head → channel + live value
    const onMove = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect()
      const W = rect.width
      const headW = W / BANDS.length
      const R = headW * 0.36
      const H = rect.height
      const mx = e.clientX - rect.left
      const my = e.clientY - rect.top
      const bi = Math.max(0, Math.min(BANDS.length - 1, Math.floor(mx / headW)))
      const cx = bi * headW + headW / 2
      const cy = H * 0.52 * (H / H)
      const x = (mx - cx) / (R * 0.92)
      const y = -(my - cy) / (R * 0.92)
      let best = ''; let bd = 0.12
      for (const ch of CHANNELS) {
        const [sx, sy] = SITES2D[ch]
        const d2 = (x - sx) ** 2 + (y - sy) ** 2
        if (d2 < bd) { bd = d2; best = ch }
      }
      if (!best) { setHover(''); return }
      const b = getBrain().state?.band_power?.[best]
      const val = b ? (b as any)[BANDS[bi]] : undefined
      setHover(val !== undefined ? `${best} · ${BAND_LABEL[BANDS[bi]]} = ${val.toFixed(2)}` : best)
    }
    canvas.addEventListener('mousemove', onMove)
    canvas.addEventListener('mouseleave', () => setHover(''))
    return () => {
      cancelAnimationFrame(raf)
      canvas.removeEventListener('mousemove', onMove)
    }
  }, [])

  return (
    <div className="panel">
      <h2>
        <span className="dot" /> band topomaps · 14-site RBF
        <span style={{ marginLeft: 'auto', fontFamily: 'var(--mono)', color: 'var(--accent)', textTransform: 'none', letterSpacing: 0 }}>{hover}</span>
      </h2>
      <canvas ref={canvasRef} style={{ width: '100%', display: 'block' }} />
    </div>
  )
}
