// auth.ts: passkey (WebAuthn) client for the public door (brain.cagatay.my).
// Hand-rolled base64url <-> ArrayBuffer; no dependency needed for one user.

export interface AuthStatus {
  enabled: boolean
  authed: boolean
  local: boolean
  registered: number
}

const b64uToBuf = (s: string): ArrayBuffer => {
  const b64 = s.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(s.length / 4) * 4, '=')
  const bin = atob(b64)
  const buf = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i)
  return buf.buffer
}

const bufToB64u = (b: ArrayBuffer): string =>
  btoa(String.fromCharCode(...new Uint8Array(b)))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')

export async function authStatus(): Promise<AuthStatus> {
  const r = await fetch('/api/auth/status')
  return r.json()
}

export async function loginWithPasskey(): Promise<void> {
  const r = await fetch('/api/auth/login/begin', { method: 'POST' })
  const opts = await r.json()
  if (!r.ok) throw new Error(opts.error || 'login refused')
  const pk: PublicKeyCredentialRequestOptions = {
    ...opts,
    challenge: b64uToBuf(opts.challenge),
    allowCredentials: (opts.allowCredentials ?? []).map((c: any) => ({
      ...c, id: b64uToBuf(c.id),
    })),
  }
  const cred = (await navigator.credentials.get({ publicKey: pk })) as PublicKeyCredential
  if (!cred) throw new Error('cancelled')
  const resp = cred.response as AuthenticatorAssertionResponse
  const fin = await fetch('/api/auth/login/finish', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      credential: {
        id: cred.id,
        rawId: bufToB64u(cred.rawId),
        type: cred.type,
        response: {
          clientDataJSON: bufToB64u(resp.clientDataJSON),
          authenticatorData: bufToB64u(resp.authenticatorData),
          signature: bufToB64u(resp.signature),
          userHandle: resp.userHandle ? bufToB64u(resp.userHandle) : null,
        },
      },
    }),
  })
  const out = await fin.json()
  if (!fin.ok || !out.ok) throw new Error(out.error || 'verification failed')
}

export async function registerPasskey(name = 'passkey', setupCode = ''): Promise<void> {
  const r = await fetch('/api/auth/register/begin', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, setup_code: setupCode }),
  })
  const opts = await r.json()
  if (!r.ok) throw new Error(opts.error || 'registration refused')
  const pk: PublicKeyCredentialCreationOptions = {
    ...opts,
    challenge: b64uToBuf(opts.challenge),
    user: { ...opts.user, id: b64uToBuf(opts.user.id) },
    excludeCredentials: (opts.excludeCredentials ?? []).map((c: any) => ({
      ...c, id: b64uToBuf(c.id),
    })),
  }
  const cred = (await navigator.credentials.create({ publicKey: pk })) as PublicKeyCredential
  if (!cred) throw new Error('cancelled')
  const resp = cred.response as AuthenticatorAttestationResponse
  const fin = await fetch('/api/auth/register/finish', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name,
      credential: {
        id: cred.id,
        rawId: bufToB64u(cred.rawId),
        type: cred.type,
        response: {
          clientDataJSON: bufToB64u(resp.clientDataJSON),
          attestationObject: bufToB64u(resp.attestationObject),
        },
      },
    }),
  })
  const out = await fin.json()
  if (!fin.ok || !out.ok) throw new Error(out.error || 'verification failed')
}
