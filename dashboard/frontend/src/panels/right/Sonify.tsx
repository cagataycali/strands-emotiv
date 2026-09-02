// Sonify: hear your brain, opt-in.
// OFF by default: Web Audio starts only on the user's explicit toggle.
import { useEffect, useRef, useState } from 'react'
import { useBrain } from '../../ws'
import { SonifyEngine } from './lib/audio'
import './right.css'

const TOOLTIP =
  'alpha (mean over 14 ch) → volume of a warm 110 Hz drone · ' +
  'beta → tempo of a soft click (0.5 to 4 Hz) · blink → tick. ' +
  'Compressed output, so it cannot get loud.'

// running normalization: track a slow-decaying max so bands map honestly to [0,1]
function makeNorm() {
  let peak = 0.5
  return (v: number) => {
    peak = Math.max(v, peak * 0.999)
    return peak > 0 ? v / peak : 0
  }
}

export function Sonify() {
  const { state } = useBrain()
  const [on, setOn] = useState(false)
  const engine = useRef<SonifyEngine | null>(null)
  const alphaNorm = useRef(makeNorm())
  const betaNorm = useRef(makeNorm())
  const lastEye = useRef('neutral')
  const lastUpdated = useRef(0)
  const [levels, setLevels] = useState({ alpha: 0, beta: 0 })

  useEffect(() => () => { engine.current?.stop() }, [])

  useEffect(() => {
    if (!state || state.updated === lastUpdated.current) return
    lastUpdated.current = state.updated ?? 0

    const bp = state.band_power
    let a = 0, b = 0
    if (bp) {
      const chs = Object.keys(bp)
      for (const ch of chs) {
        a += bp[ch]?.alpha ?? 0
        b += ((bp[ch]?.betaL ?? 0) + (bp[ch]?.betaH ?? 0)) / 2
      }
      if (chs.length) { a /= chs.length; b /= chs.length }
    }
    const an = alphaNorm.current(a), bn = betaNorm.current(b)
    setLevels({ alpha: an, beta: bn })
    engine.current?.update(an, bn)

    const eye = state.facial?.eye ?? 'neutral'
    if (eye !== lastEye.current) {
      if (eye === 'blink') engine.current?.blink()
      lastEye.current = eye
    }
  }, [state])

  const toggle = () => {
    if (on) { engine.current?.stop(); engine.current = null; setOn(false) }
    else { engine.current = new SonifyEngine(); engine.current.start(); setOn(true) }
  }

  return (
    <div className="panel sonify">
      <div className="panel-title">sonify <span className="hint" title={TOOLTIP}>hear your brain (hover for the mapping ⓘ)</span></div>
      <div className="sonify-row">
        <button className={`sonify-toggle ${on ? 'on' : ''}`} onClick={toggle} title={TOOLTIP}>
          {on ? '🔊 sound on' : '🔇 sound off'}
        </button>
        <div className="sonify-meters" title={TOOLTIP}>
          <div className="meter">
            <span className="meter-label">α drone</span>
            <div className="meter-track"><div className="meter-fill" style={{ width: `${Math.round(levels.alpha * 100)}%` }} /></div>
          </div>
          <div className="meter">
            <span className="meter-label">β click</span>
            <div className="meter-track"><div className="meter-fill beta" style={{ width: `${Math.round(levels.beta * 100)}%` }} /></div>
          </div>
        </div>
      </div>
    </div>
  )
}
