// Recorder: REC control for the ECoT dataset rail.
// Red dot + episode/frame counters + start/stop + download + publish.
// Talks to /api/dataset/* (dataset_api.py). Poll only while open/recording, so it stays cheap.
import { useEffect, useRef, useState } from 'react'

type Status = {
  recording: boolean
  in_turn: boolean
  episodes: number
  frames: number
  bytes: number
  name: string | null
  repo_id: string
}

const fmtBytes = (b: number) =>
  b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : b > 1e3 ? `${(b / 1e3).toFixed(0)} kB` : `${b} B`

export function Recorder() {
  const [st, setSt] = useState<Status | null>(null)
  const [busy, setBusy] = useState(false)
  const [pub, setPub] = useState<string | null>(null)
  const timer = useRef<number | null>(null)

  const poll = async () => {
    try {
      const r = await fetch('/api/dataset/status')
      if (r.ok) setSt(await r.json())
    } catch {
      /* server away: pill goes gray */
    }
  }

  useEffect(() => {
    poll()
    timer.current = window.setInterval(poll, 2000)
    return () => {
      if (timer.current) window.clearInterval(timer.current)
    }
  }, [])

  const start = async () => {
    setBusy(true)
    setPub(null)
    await fetch('/api/dataset/record/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    }).catch(() => {})
    await poll()
    setBusy(false)
  }

  const stop = async () => {
    setBusy(true)
    await fetch('/api/dataset/record/stop', { method: 'POST' }).catch(() => {})
    await poll()
    setBusy(false)
  }

  const publish = async () => {
    setBusy(true)
    setPub('publishing…')
    try {
      const r = await fetch('/api/dataset/publish', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
      const j = await r.json()
      setPub(r.ok ? j.url : `error: ${j.error}`)
    } catch {
      setPub('error: server away')
    }
    setBusy(false)
  }

  const rec = st?.recording ?? false
  return (
    <div className="panel recorder-panel">
      <div className="panel-title">
        <span className={`rec-dot ${rec ? 'rec-on' : ''}`} />
        dataset {st?.name ? `· ${st.name}` : ''}
      </div>
      <div className="rec-row">
        <span className="rec-stat">{st ? `${st.episodes} ep · ${st.frames} frames · ${fmtBytes(st.bytes)}` : 'server away'}</span>
        {st?.in_turn && <span className="rec-turn">● turn</span>}
      </div>
      <div className="rec-row">
        {!rec ? (
          <button className="rec-btn rec-start" disabled={busy || !st} onClick={start}>● REC</button>
        ) : (
          <button className="rec-btn rec-stop" disabled={busy} onClick={stop}>■ STOP</button>
        )}
        <a
          className={`rec-btn ${!st || rec || !st.name ? 'rec-disabled' : ''}`}
          href={!st || rec || !st.name ? undefined : '/api/dataset/export'}
        >
          ⬇ tar.gz
        </a>
        <button className="rec-btn" disabled={busy || !st || rec || !st.name} onClick={publish}>
          ⬆ publish
        </button>
      </div>
      {pub && (
        <div className="rec-pub">
          {pub.startsWith('http') ? <a href={pub} target="_blank" rel="noreferrer">{pub.replace('https://', '')}</a> : pub}
        </div>
      )}
    </div>
  )
}
