// Consent: the mental-command rail made visible. When the agent asks, this panel gets LOUD:
// the question, a countdown, the live push/pull needle, then the verdict.
// Quiet the rest of the time: profile chip + trained counts + test button.
import { useEffect, useRef, useState } from 'react'
import { useBrain } from '../../ws'
import './consent.css'

interface Approval {
  prompt?: string; t0?: number; deadline?: number; status?: string
  decision?: string; live?: { act?: string; pow?: number }
  action?: string; power?: number; elapsed?: number; reason?: string
}
interface Training { action?: string; status?: string; t0?: number }

const VERDICT: Record<string, [string, string]> = {
  yes: ['✅ YES (pushed)', 'ok'],
  no: ['⛔ NO (pulled)', 'no'],
  vetoed: ['✊ VETOED (jaw clench)', 'veto'],
  timeout: ['⏳ silence (timed out)', 'dim'],
  refused: ['🚫 refused (signal too poor)', 'veto'],
}

export function Consent() {
  const { state } = useBrain()
  const s = state as any
  const approval: Approval | undefined = s?.approval
  const training: Training | undefined = s?.training
  const profile: string | undefined = s?.mental_profile
  const com = state?.mental_command
  const [, tick] = useState(0)
  const [status, setStatus] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const decidedAt = useRef<number>(0)

  // 4 Hz repaint while a question or training round is live (countdown/needle)
  const waiting = approval?.status === 'waiting'
  const trainingLive = training && training.status !== 'done' && training.status !== 'failed'
  useEffect(() => {
    if (!waiting && !trainingLive) return
    const id = setInterval(() => tick((n) => n + 1), 250)
    return () => clearInterval(id)
  }, [waiting, trainingLive])

  useEffect(() => {
    if (approval?.status === 'decided') decidedAt.current = Date.now()
  }, [approval?.status, approval?.decision])

  useEffect(() => {
    const load = () => fetch('/api/mental/status').then((r) => r.json()).then(setStatus).catch(() => {})
    load()
    const id = setInterval(load, 30000)
    return () => clearInterval(id)
  }, [])

  const ask = async () => {
    setBusy(true)
    try {
      await fetch('/api/mental/approval', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: 'test: PUSH to say yes, PULL to say no', timeout: 30 }),
      })
    } catch { /* verdict arrives via WS state anyway */ }
    setBusy(false)
  }

  const now = Date.now() / 1000
  const left = waiting && approval?.deadline ? Math.max(0, approval.deadline - now) : 0
  const total = waiting && approval?.deadline && approval?.t0 ? approval.deadline - approval.t0 : 1
  const livePow = approval?.live?.pow ?? 0
  const liveAct = approval?.live?.act
  const showVerdict = approval?.status === 'decided' && Date.now() - decidedAt.current < 8000
  const verdict = showVerdict ? VERDICT[approval?.decision ?? ''] ?? [approval?.decision ?? '', 'dim'] : null
  const trained: Record<string, number> = {}
  for (const a of status?.trained?.trainedActions ?? []) trained[a.action] = a.times

  return (
    <div className={`panel consent ${waiting ? 'asking' : ''}`}>
      <div className="panel-title">
        consent <span className="hint">push = yes · pull = no · clench = veto</span>
      </div>

      {waiting ? (
        <div className="consent-ask">
          <div className="consent-prompt">{approval?.prompt}</div>
          <div className="consent-countdown">
            <div className="consent-bar" style={{ width: `${(left / total) * 100}%` }} />
            <span>{left.toFixed(0)}s</span>
          </div>
          <div className="consent-needle">
            <div className={`needle-fill ${liveAct === 'pull' ? 'pull' : liveAct === 'push' ? 'push' : ''}`}
              style={{ width: `${Math.min(100, livePow * 100)}%` }} />
            <span className="needle-label">{liveAct ?? 'listening…'} {livePow ? livePow.toFixed(2) : ''}</span>
          </div>
        </div>
      ) : verdict ? (
        <div className={`consent-verdict ${verdict[1]}`}>
          {verdict[0]}
          {approval?.reason && <div className="consent-reason">{approval.reason}</div>}
          <div className="consent-reason">{approval?.elapsed}s</div>
        </div>
      ) : trainingLive ? (
        <div className="consent-training">
          🧠 training <b>{training?.action}</b>: {training?.status ?? 'hold the thought…'}
        </div>
      ) : (
        <div className="consent-idle">
          <div className="consent-chips">
            <span className="chip">profile <b>{profile ?? '·'}</b></span>
            {(['neutral', 'push', 'pull'] as const).map((a) => (
              <span key={a} className="chip">{a} ×{trained[a] ?? 0}</span>
            ))}
            {com?.act && com.act !== 'neutral' && (
              <span className="chip live">{com.act} {(com.pow ?? 0).toFixed(2)}</span>
            )}
          </div>
          <button className="consent-btn" disabled={busy} onClick={ask}>
            {busy ? 'asking…' : 'ask me (test)'}
          </button>
        </div>
      )}
    </div>
  )
}
