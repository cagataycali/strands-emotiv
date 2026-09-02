// HeadTrail.tsx: where your head has been looking.
// Yaw/pitch plane with a heat-fading trail: dwell areas glow, the path cools over
// ~45 s, current gaze is a bright comet head. Centered on a slow EMA reference so
// absolute heading doesn't matter: deliberate turns are what light up.
// Fast path: derives yaw/pitch from {type:"motion",q} (≥30 Hz); falls back to
// state.motion.yaw/pitch. Shows a waiting card until motion arrives.
import { useEffect, useRef } from 'react'
import { getBrain } from '../../ws'

const H = 230
const YAW_RANGE = 70   // ± degrees mapped to plot width
const PITCH_RANGE = 45 // ± degrees mapped to plot height

function quatToYawPitch(q: number[]): [number, number] {
  const [w, x, y, z] = q
  const pitch = Math.asin(Math.max(-1, Math.min(1, 2 * (w * y - z * x)))) * 57.2958
  const yaw = Math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)) * 57.2958
  return [yaw, pitch]
}

export function HeadTrail() {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current!
    const ctx = canvas.getContext('2d')!

    // persistent heat layer (faded a little every frame)
    let heat: HTMLCanvasElement | null = null
    let heatCtx: CanvasRenderingContext2D | null = null

    let refYaw: number | null = null
    let refPitch = 0
    let lastX: number | null = null
    let lastY: number | null = null
    let lastMotionT = 0
    let raf = 0

    const draw = () => {
      raf = requestAnimationFrame(draw)
      const brain = getBrain()
      const W = canvas.clientWidth
      if (W === 0) return
      if (canvas.width !== W * 2 || canvas.height !== H * 2) {
        canvas.width = W * 2; canvas.height = H * 2
        canvas.style.height = `${H}px`
        heat = document.createElement('canvas')
        heat.width = W; heat.height = H
        heatCtx = heat.getContext('2d')!
        lastX = lastY = null
      }
      if (!heat || !heatCtx) return

      // resolve yaw/pitch: fast quaternion first, state fallback
      let yaw: number | null = null, pitch: number | null = null
      const m = brain.motion
      if (m?.q && m.q.length === 4) {
        ;[yaw, pitch] = quatToYawPitch(m.q)
        lastMotionT = m.t
      } else if (brain.state?.motion?.yaw !== undefined) {
        yaw = brain.state.motion.yaw!
        pitch = brain.state.motion.pitch ?? 0
      }

      ctx.setTransform(2, 0, 0, 2, 0, 0)
      ctx.fillStyle = '#0d1117'
      ctx.fillRect(0, 0, W, H)

      if (yaw === null || pitch === null) {
        ctx.fillStyle = '#6b7a8c'
        ctx.font = '12px JetBrains Mono, monospace'
        ctx.textAlign = 'center'
        ctx.fillText('◌ waiting for motion stream…', W / 2, H / 2)
        return
      }

      // slow EMA reference (~30 s) so the plot stays centered on "straight ahead"
      if (refYaw === null) { refYaw = yaw; refPitch = pitch }
      // handle wrap for yaw
      let dy = yaw - refYaw
      if (dy > 180) dy -= 360; else if (dy < -180) dy += 360
      refYaw += dy * 0.0015
      refPitch += (pitch - refPitch) * 0.0015
      let relYaw = yaw - refYaw
      if (relYaw > 180) relYaw -= 360; else if (relYaw < -180) relYaw += 360
      const relPitch = pitch - refPitch

      const px = W / 2 + (relYaw / YAW_RANGE) * (W / 2 - 16)
      const py = H / 2 - (relPitch / PITCH_RANGE) * (H / 2 - 16)

      // heat layer: fade + stamp
      heatCtx.globalCompositeOperation = 'destination-out'
      heatCtx.fillStyle = 'rgba(0,0,0,0.045)' // ~45 s to black at 60 fps... (0.955^n)
      heatCtx.fillRect(0, 0, W, H)
      heatCtx.globalCompositeOperation = 'lighter'
      if (lastX !== null && lastY !== null) {
        const grad = heatCtx.createLinearGradient(lastX, lastY, px, py)
        grad.addColorStop(0, 'rgba(78,225,194,0.25)')
        grad.addColorStop(1, 'rgba(122,162,255,0.5)')
        heatCtx.strokeStyle = grad
        heatCtx.lineWidth = 5
        heatCtx.lineCap = 'round'
        heatCtx.beginPath()
        heatCtx.moveTo(lastX, lastY)
        heatCtx.lineTo(px, py)
        heatCtx.stroke()
      }
      const spot = heatCtx.createRadialGradient(px, py, 0, px, py, 9)
      spot.addColorStop(0, 'rgba(78,225,194,0.5)')
      spot.addColorStop(1, 'rgba(78,225,194,0)')
      heatCtx.fillStyle = spot
      heatCtx.beginPath(); heatCtx.arc(px, py, 9, 0, Math.PI * 2); heatCtx.fill()
      lastX = px; lastY = py

      // compose: grid, heat, head dot
      ctx.strokeStyle = '#1c2530'
      ctx.lineWidth = 1
      for (const f of [0.25, 0.5, 0.75]) {
        ctx.beginPath(); ctx.moveTo(W * f, 0); ctx.lineTo(W * f, H); ctx.stroke()
        ctx.beginPath(); ctx.moveTo(0, H * f); ctx.lineTo(W, H * f); ctx.stroke()
      }
      ctx.strokeStyle = '#2a3b52'
      ctx.beginPath(); ctx.moveTo(W / 2, 0); ctx.lineTo(W / 2, H); ctx.stroke()
      ctx.beginPath(); ctx.moveTo(0, H / 2); ctx.lineTo(W, H / 2); ctx.stroke()

      ctx.drawImage(heat, 0, 0)

      // comet head
      const g2 = ctx.createRadialGradient(px, py, 0, px, py, 12)
      g2.addColorStop(0, 'rgba(219,244,255,0.95)')
      g2.addColorStop(0.4, 'rgba(78,225,194,0.6)')
      g2.addColorStop(1, 'rgba(78,225,194,0)')
      ctx.fillStyle = g2
      ctx.beginPath(); ctx.arc(px, py, 12, 0, Math.PI * 2); ctx.fill()

      // labels + live numbers
      ctx.fillStyle = '#4a5866'
      ctx.font = '10px JetBrains Mono, monospace'
      ctx.textAlign = 'left'
      ctx.fillText('← yaw left', 6, H / 2 - 6)
      ctx.textAlign = 'right'
      ctx.fillText('yaw right →', W - 6, H / 2 - 6)
      ctx.textAlign = 'center'
      ctx.fillText('pitch up', W / 2 + 34, 12)
      ctx.fillText('pitch down', W / 2 + 40, H - 6)
      ctx.textAlign = 'right'
      ctx.fillStyle = '#6b7a8c'
      const stale = m?.q && performance.now() / 1000 - lastMotionT > 5 ? ' · stale' : ''
      ctx.fillText(`yaw ${relYaw.toFixed(0)}° · pitch ${relPitch.toFixed(0)}°${stale}`, W - 6, 14)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [])

  return (
    <div className="panel">
      <h2><span className="dot" /> head trail · yaw × pitch heat</h2>
      <canvas ref={canvasRef} style={{ width: '100%', display: 'block' }} />
    </div>
  )
}
