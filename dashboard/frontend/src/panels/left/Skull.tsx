// Skull.tsx: the 3D head.
// Translucent head + 14 EPOC X electrodes (10-20), orb color = contact quality,
// orb pulse = log band power, scalp glow = gaussian-RBF of band power onto vertices,
// eye flash on blink/winkL/winkR, jaw flash on clench, smile lifts jaw,
// rotates from motion.q (else idle sway). Band chips select what the glow shows.
// All data reads happen inside rAF via getBrain(): zero React re-renders per sample.
import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { getBrain } from '../../ws'

// 10-20 positions for EPOC X, unit sphere: [x right, y front, z up]
const SITES: Record<string, [number, number, number]> = {
  AF3: [-0.34, 0.89, 0.31], AF4: [0.34, 0.89, 0.31],
  F7: [-0.81, 0.59, 0.0], F8: [0.81, 0.59, 0.0],
  F3: [-0.55, 0.67, 0.5], F4: [0.55, 0.67, 0.5],
  FC5: [-0.87, 0.3, 0.39], FC6: [0.87, 0.3, 0.39],
  T7: [-1.0, 0.0, 0.0], T8: [1.0, 0.0, 0.0],
  P7: [-0.81, -0.59, 0.0], P8: [0.81, -0.59, 0.0],
  O1: [-0.31, -0.95, 0.03], O2: [0.31, -0.95, 0.03],
}
export const CHANNELS = Object.keys(SITES)

const CQ_COLORS = [0xff5470, 0xff8a5c, 0xffb454, 0xa8e05f, 0x4ee16a]
const HEAD_SCALE = new THREE.Vector3(0.82, 1.0, 0.92)

type BandKey = 'total' | 'theta' | 'alpha' | 'betaL' | 'betaH' | 'gamma'
const BANDS: { key: BandKey; label: string }[] = [
  { key: 'total', label: 'Σ total' }, { key: 'theta', label: 'θ' }, { key: 'alpha', label: 'α' },
  { key: 'betaL', label: 'βL' }, { key: 'betaH', label: 'βH' }, { key: 'gamma', label: 'γ' },
]

function toVec3([x, front, up]: [number, number, number], r = 1): THREE.Vector3 {
  return new THREE.Vector3(x * r, up * r, front * r) // three.js: y up, z = front of face
}

// dark-blue → teal → gold heat ramp for the scalp glow
function heat(t: number, out: THREE.Color) {
  t = Math.max(0, Math.min(1, t))
  if (t < 0.5) out.setRGB(0.02 + 0.1 * t, 0.06 + 0.9 * t * t, 0.22 + 0.6 * t)
  else { const u = (t - 0.5) * 2; out.setRGB(0.07 + 0.93 * u, 0.51 + 0.35 * u, 0.52 - 0.3 * u) }
}

export function Skull() {
  const mountRef = useRef<HTMLDivElement>(null)
  const [band, setBand] = useState<BandKey>('total')
  const bandRef = useRef<BandKey>(band)
  bandRef.current = band

  useEffect(() => {
    const mount = mountRef.current!
    const W = mount.clientWidth
    const H = 440

    let renderer: THREE.WebGLRenderer
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    } catch {
      // No WebGL (headless, remote desktop, GPU off): degrade to a note, keep the rest alive.
      mount.innerHTML = '<div style="height:440px;display:flex;align-items:center;justify-content:center;color:#5a6a8a;font-size:12px">3D skull needs WebGL, other panels unaffected</div>'
      return
    }
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2))
    renderer.setSize(W, H)
    mount.appendChild(renderer.domElement)

    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(38, W / H, 0.1, 100)
    camera.position.set(0, 0.6, 4.2)
    camera.lookAt(0, 0, 0)

    scene.add(new THREE.AmbientLight(0x8899bb, 0.7))
    const key = new THREE.DirectionalLight(0x7aa2ff, 1.1)
    key.position.set(2, 3, 4); scene.add(key)
    const rim = new THREE.DirectionalLight(0x4ee1c2, 0.7)
    rim.position.set(-3, 1, -2); scene.add(rim)

    const head = new THREE.Group()
    scene.add(head)

    // head shell
    const headGeo = new THREE.SphereGeometry(1, 48, 32)
    headGeo.scale(HEAD_SCALE.x, HEAD_SCALE.y, HEAD_SCALE.z)
    head.add(new THREE.Mesh(headGeo, new THREE.MeshPhysicalMaterial({
      color: 0x0e1522, transparent: true, opacity: 0.5,
      roughness: 0.35, metalness: 0.1, transmission: 0.4, clearcoat: 0.5,
    })))
    const wire = new THREE.Mesh(headGeo.clone(),
      new THREE.MeshBasicMaterial({ color: 0x2a3b52, wireframe: true, transparent: true, opacity: 0.18 }))
    wire.scale.setScalar(1.002)
    head.add(wire)

    // scalp glow: additive vertex-colored shell, RBF weights precomputed
    const glowGeo = new THREE.SphereGeometry(1, 48, 32)
    const pos = glowGeo.attributes.position
    const nVerts = pos.count
    const siteDirs = CHANNELS.map((ch) => toVec3(SITES[ch]).normalize())
    const weights = new Float32Array(nVerts * CHANNELS.length)
    const v = new THREE.Vector3()
    const SIGMA = 0.5 // radians
    for (let i = 0; i < nVerts; i++) {
      v.fromBufferAttribute(pos, i).normalize()
      for (let c = 0; c < siteDirs.length; c++) {
        const ang = v.angleTo(siteDirs[c])
        weights[i * CHANNELS.length + c] = Math.exp(-(ang * ang) / (2 * SIGMA * SIGMA))
      }
    }
    glowGeo.scale(HEAD_SCALE.x * 1.015, HEAD_SCALE.y * 1.015, HEAD_SCALE.z * 1.015)
    const colors = new Float32Array(nVerts * 3)
    glowGeo.setAttribute('color', new THREE.BufferAttribute(colors, 3))
    const glow = new THREE.Mesh(glowGeo, new THREE.MeshBasicMaterial({
      vertexColors: true, transparent: true, opacity: 0.38,
      blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.FrontSide,
    }))
    head.add(glow)

    // face: nose, eyes, jaw
    const nose = new THREE.Mesh(new THREE.ConeGeometry(0.09, 0.26, 12),
      new THREE.MeshStandardMaterial({ color: 0x22334a, roughness: 0.6 }))
    nose.position.set(0, -0.12, 0.9); nose.rotation.x = Math.PI / 2
    head.add(nose)

    const eyeMatL = new THREE.MeshStandardMaterial({ color: 0x16243a, emissive: 0x4ee1c2, emissiveIntensity: 0.15 })
    const eyeMatR = eyeMatL.clone()
    const eyeGeo = new THREE.SphereGeometry(0.11, 16, 12)
    const eyeL = new THREE.Mesh(eyeGeo, eyeMatL); eyeL.position.set(-0.26, 0.12, 0.78)
    const eyeR = new THREE.Mesh(eyeGeo, eyeMatR); eyeR.position.set(0.26, 0.12, 0.78)
    head.add(eyeL, eyeR)

    const jawMat = new THREE.MeshStandardMaterial({ color: 0x18273d, emissive: 0x000000, roughness: 0.5 })
    const jawGeo = new THREE.SphereGeometry(1, 24, 16)
    jawGeo.scale(0.4, 0.18, 0.3)
    const jaw = new THREE.Mesh(jawGeo, jawMat)
    const JAW_Y = -0.62
    jaw.position.set(0, JAW_Y, 0.55)
    head.add(jaw)

    // electrodes + labels
    const orbs: Record<string, THREE.Mesh> = {}
    const orbGeo = new THREE.SphereGeometry(1, 16, 12)
    for (const ch of CHANNELS) {
      const mat = new THREE.MeshStandardMaterial({ color: 0x445566, emissive: 0x222833, emissiveIntensity: 0.8, roughness: 0.3 })
      const orb = new THREE.Mesh(orbGeo, mat)
      orb.position.copy(toVec3(SITES[ch], 1.02)).multiply(HEAD_SCALE)
      orb.scale.setScalar(0.05)
      head.add(orb)
      orbs[ch] = orb
      const canvas = document.createElement('canvas')
      canvas.width = 96; canvas.height = 40
      const ctx = canvas.getContext('2d')!
      ctx.font = '600 26px JetBrains Mono, monospace'
      ctx.fillStyle = '#8494a8'
      ctx.textAlign = 'center'; ctx.fillText(ch, 48, 30)
      const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(canvas), transparent: true, opacity: 0.9 }))
      sprite.position.copy(orb.position).multiplyScalar(1.17)
      sprite.scale.set(0.24, 0.1, 1)
      head.add(sprite)
    }

    // animation state
    const tmpC = new THREE.Color()
    const chVals = new Float32Array(CHANNELS.length)
    let runningMax = 1e-6
    let runningMin = 1e9
    let eyeFlashL = 0, eyeFlashR = 0, clenchFlash = 0, jawLift = 0
    let refYaw: number | null = null
    let refPitch = 0
    let lastFacialSig = ''

    let raf = 0
    const t0 = performance.now()
    let lastW = W
    const animate = () => {
      raf = requestAnimationFrame(animate)
      const t = (performance.now() - t0) / 1000
      const brain = getBrain()
      const st = brain.state
      const cq = st?.contact_quality
      const bp = st?.band_power
      const sel = bandRef.current

      // orbs: CQ color + band-power pulse
      for (const ch of CHANNELS) {
        const orb = orbs[ch]
        const mat = orb.material as THREE.MeshStandardMaterial
        const q = cq?.[ch]
        if (q !== undefined) {
          const c = CQ_COLORS[Math.max(0, Math.min(4, Math.round(q)))]
          mat.color.setHex(c); mat.emissive.setHex(c)
          mat.emissiveIntensity = 0.35 + q * 0.12
        }
        const b = bp?.[ch]
        if (b) {
          const total = b.theta + b.alpha + b.betaL + b.betaH + b.gamma
          const s = 0.045 + Math.min(0.07, Math.log10(1 + total) * 0.028)
          orb.scale.setScalar(s + Math.sin(t * 3 + ch.length) * 0.004)
        }
      }

      // scalp glow: RBF of selected band
      if (bp) {
        let frameMax = 1e-6
        for (let c = 0; c < CHANNELS.length; c++) {
          const b = bp[CHANNELS[c]]
          const p = !b ? 0 : sel === 'total' ? b.theta + b.alpha + b.betaL + b.betaH + b.gamma : b[sel]
          const lv = Math.log10(1 + Math.max(0, p))
          chVals[c] = lv
          if (lv > frameMax) frameMax = lv
        }
        let frameMin = 1e9
        for (let c = 0; c < CHANNELS.length; c++) if (chVals[c] < frameMin) frameMin = chVals[c]
        runningMax = Math.max(frameMax, runningMax * 0.995) // slow-decay normalizers
        runningMin = Math.min(frameMin, runningMin * 1.005 + 1e-9)
        const span = Math.max(1e-6, runningMax - runningMin)
        for (let i = 0; i < nVerts; i++) {
          let num = 0, den = 1e-9
          const base = i * CHANNELS.length
          for (let c = 0; c < CHANNELS.length; c++) { num += weights[base + c] * chVals[c]; den += weights[base + c] }
          heat((num / den - runningMin) / span, tmpC)
          colors[i * 3] = tmpC.r; colors[i * 3 + 1] = tmpC.g; colors[i * 3 + 2] = tmpC.b
        }
        ;(glowGeo.attributes.color as THREE.BufferAttribute).needsUpdate = true
      }

      // facial: trigger flashes on transitions (state cadence 2 to 8 Hz)
      const f = st?.facial
      const sig = `${f?.eye}|${f?.lower}|${f?.upper}|${st?.updated}`
      if (f && sig !== lastFacialSig) {
        lastFacialSig = sig
        if (f.eye === 'blink') { eyeFlashL = 1; eyeFlashR = 1 }
        else if (f.eye === 'winkL') eyeFlashL = 1
        else if (f.eye === 'winkR') eyeFlashR = 1
        if (f.lower === 'clench') clenchFlash = Math.max(clenchFlash, 0.5 + 0.5 * (f.lower_pow ?? 1))
      }
      const smiling = f?.lower === 'smile'
      jawLift += ((smiling ? 0.1 : 0) - jawLift) * 0.12
      eyeFlashL *= 0.9; eyeFlashR *= 0.9; clenchFlash *= 0.93
      eyeMatL.emissiveIntensity = 0.15 + eyeFlashL * 3.5
      eyeMatR.emissiveIntensity = 0.15 + eyeFlashR * 3.5
      jawMat.emissive.setRGB(clenchFlash, clenchFlash * 0.12, clenchFlash * 0.18)
      jaw.position.y = JAW_Y + jawLift

      // orientation: RELATIVE yaw/pitch (vs slow EMA reference) so the face keeps
      // looking at you and mirrors your turns: raw Cortex quat has an arbitrary
      // mounting tilt that twisted the whole head (seen on-screen iter 6).
      const mq = brain.motion?.q
      if (mq && mq.length === 4) {
        const [qw, qx, qy, qz] = mq
        const yawD = Math.atan2(2 * (qw * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz))
        const pitchD = Math.asin(Math.max(-1, Math.min(1, 2 * (qw * qy - qz * qx))))
        if (refYaw === null) { refYaw = yawD; refPitch = pitchD }
        let dy = yawD - refYaw
        if (dy > Math.PI) dy -= 2 * Math.PI; else if (dy < -Math.PI) dy += 2 * Math.PI
        refYaw += dy * 0.0015
        refPitch += (pitchD - refPitch) * 0.0015
        let relYaw = yawD - refYaw
        if (relYaw > Math.PI) relYaw -= 2 * Math.PI; else if (relYaw < -Math.PI) relYaw += 2 * Math.PI
        // mirror: your left turn moves the head's screen-left
        head.rotation.set(-(pitchD - refPitch) * 0.9, -relYaw * 0.9, 0)
      } else {
        head.rotation.y = Math.sin(t * 0.25) * 0.55
        head.rotation.x = Math.sin(t * 0.17) * 0.08
      }

      // cheap responsive: track container width
      if (mount.clientWidth !== lastW && mount.clientWidth > 0) {
        lastW = mount.clientWidth
        renderer.setSize(lastW, H)
        camera.aspect = lastW / H
        camera.updateProjectionMatrix()
      }
      renderer.render(scene, camera)
    }
    animate()

    return () => {
      cancelAnimationFrame(raf)
      renderer.dispose()
      mount.removeChild(renderer.domElement)
    }
  }, [])

  return (
    <div className="panel">
      <h2><span className="dot" /> skull · contact + band power</h2>
      <div ref={mountRef} style={{ width: '100%', height: 440 }} />
      <div className="chips" style={{ marginTop: 8, justifyContent: 'center' }}>
        {BANDS.map((b) => (
          <button key={b.key} className={`chip ${band === b.key ? 'active' : ''}`} onClick={() => setBand(b.key)}>
            {b.label}
          </button>
        ))}
      </div>
    </div>
  )
}
