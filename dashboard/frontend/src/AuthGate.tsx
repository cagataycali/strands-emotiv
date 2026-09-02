// AuthGate: the public door. Local visits pass straight through; the tunnel
// (brain.cagatay.my) demands a passkey before ANY brain data flows (the WS and
// every /api/* are refused server-side too, this gate is UX, not the lock).
import { useEffect, useState } from 'react'
import { authStatus, loginWithPasskey, registerPasskey, type AuthStatus } from './auth'

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [st, setSt] = useState<AuthStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [enroll, setEnroll] = useState(false)
  const [setupCode, setSetupCode] = useState('')

  useEffect(() => { authStatus().then(setSt).catch(() => setErr('server unreachable')) }, [])

  if (!st && !err) return <div className="auth-gate"><div className="auth-card">…</div></div>
  if (st && (!st.enabled || st.authed)) return <>{children}</>

  const go = async (fn: () => Promise<void>) => {
    setBusy(true); setErr('')
    try { await fn(); location.reload() } catch (e: any) { setErr(e?.message || String(e)) }
    setBusy(false)
  }

  return (
    <div className="auth-gate">
      <div className="auth-card">
        <div className="auth-skull">🧠</div>
        <div className="auth-title">strands-emotiv</div>
        <div className="auth-sub">a live human nervous system lives behind this door</div>
        {st && st.registered > 0 && !enroll ? (
          <>
            <button className="auth-btn" disabled={busy} onClick={() => go(loginWithPasskey)}>
              {busy ? 'waiting for passkey…' : '🔑 unlock with passkey'}
            </button>
            <a className="auth-alt" onClick={() => setEnroll(true)}>enroll a new device</a>
          </>
        ) : (
          <>
            <input
              className="auth-input" type="password" placeholder="setup code"
              value={setupCode} onChange={(e) => setSetupCode(e.target.value)}
            />
            <button
              className="auth-btn" disabled={busy}
              onClick={() => go(() => registerPasskey('passkey', setupCode))}
            >
              {busy ? 'waiting for passkey…' : '➕ enroll this device'}
            </button>
            {st && st.registered > 0 && (
              <a className="auth-alt" onClick={() => setEnroll(false)}>back to sign-in</a>
            )}
          </>
        )}
        {err && <div className="auth-err">{err}</div>}
      </div>
    </div>
  )
}
