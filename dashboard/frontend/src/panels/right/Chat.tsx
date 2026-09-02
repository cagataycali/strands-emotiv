// Chat: full-width bottom band (neon-the-g1 parity). Markdown, slash commands, ↑/↓
// history, welcome chips, localStorage persistence, `C` focuses input.
// Under every message you send: the AgentEye line the agent had at that moment.
// Backend (:8765):
//   POST /api/agent/ask {message} → {q, a, ambient, events_during:[{kind,time}]}
//   GET  /api/agent/status        → {ready, model, ambient, turns}
import { useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { getEyeLine } from './lib/eyeline'
import './right.css'

interface ChatMsg {
  role: 'user' | 'agent' | 'system' | 'error'
  text: string
  ts: number
  eye?: string // AgentEye line at send time (user) or server ambient (agent)
  events?: string[] // events_during, already labelled
  tools?: string[] // tool chips seen while streaming
  streaming?: boolean
}

const LS_MSGS = 'emotiv.chat.msgs'
const LS_HIST = 'emotiv.chat.history'

const EVENT_GLYPH: Record<string, string> = {
  blink: '👁', wink_left: '😉', wink_right: '😉', clench: '🤏',
  turn_left: '↩', turn_right: '↪', focus_up: '🎯', stress_up: '😬',
  nod: '🙆', shake: '🙅', push: '🫸', pull: '🫷',
}
const glyph = (label: string) => {
  const kind = label.split(' ')[0]
  return EVENT_GLYPH[kind] ? `${EVENT_GLYPH[kind]} ${label}` : label
}

// events_during carries {kind, time}: a blink that landed mid-turn is the
// whole point of this panel, so name it and say how late in the turn it hit.
const labelEvents = (raw: unknown, t0: number): string[] | undefined => {
  if (!Array.isArray(raw) || raw.length === 0) return undefined
  return raw.map((e: any) => {
    if (typeof e === 'string') return e
    const kind = e?.kind ?? 'event'
    const at = typeof e?.time === 'number' ? e.time * 1000 - t0 : NaN
    return Number.isFinite(at) && at > 0 ? `${kind} +${(at / 1000).toFixed(1)}s` : kind
  })
}

interface SlashCmd { cmd: string; desc: string; prompt?: string; action?: 'clear' | 'help' }
const SLASH: SlashCmd[] = [
  { cmd: '/clear',  desc: 'Clear conversation', action: 'clear' },
  { cmd: '/help',   desc: 'Show available commands', action: 'help' },
  { cmd: '/brain',  desc: 'Full brain snapshot', prompt: 'give me a full brain_snapshot' },
  { cmd: '/events', desc: 'Recent brain events', prompt: 'what brain events happened in the last minute?' },
  { cmd: '/pose',   desc: 'Head pose', prompt: 'where is my head pointing?' },
]

const WELCOME_CHIPS = [
  'what am I feeling right now?',
  'am I focused?',
  'what did I do with my face in the last minute?',
  'explain the band topomaps',
]

function CodeBlock({ children, className }: { children?: any; className?: string }) {
  const [copied, setCopied] = useState(false)
  const text = String(children ?? '')
  return (
    <pre className="md-pre">
      <button
        className="md-copy"
        onClick={() => {
          navigator.clipboard.writeText(text).then(() => {
            setCopied(true); setTimeout(() => setCopied(false), 1200)
          }).catch(() => {})
        }}
      >{copied ? '✓' : 'copy'}</button>
      <code className={className}>{text}</code>
    </pre>
  )
}

const MD_COMPONENTS = {
  a: (p: any) => <a {...p} className="md-link" target="_blank" rel="noreferrer" />,
  code: (p: any) => {
    const { inline, className, children } = p
    if (inline) return <code className="md-code">{children}</code>
    return <code className={className}>{children}</code>
  },
  pre: (p: any) => {
    const child = Array.isArray(p.children) ? p.children[0] : p.children
    return <CodeBlock className={child?.props?.className}>{child?.props?.children}</CodeBlock>
  },
  table: (p: any) => <div className="md-table-wrap"><table {...p} /></div>,
}

function Markdown({ text }: { text: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS as any}>
      {text}
    </ReactMarkdown>
  )
}

const load = <T,>(k: string, d: T): T => {
  try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d } catch { return d }
}

export function Chat() {
  const [msgs, setMsgs] = useState<ChatMsg[]>(() => load<ChatMsg[]>(LS_MSGS, []))
  const [history, setHistory] = useState<string[]>(() => load<string[]>(LS_HIST, []))
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [ready, setReady] = useState<boolean | null>(null)
  const [model, setModel] = useState('')
  const [histIdx, setHistIdx] = useState(-1)
  const feedRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const pinnedRef = useRef(true) // auto-scroll unless the user scrolled up

  useEffect(() => { try { localStorage.setItem(LS_MSGS, JSON.stringify(msgs.slice(-80))) } catch {} }, [msgs])
  useEffect(() => { try { localStorage.setItem(LS_HIST, JSON.stringify(history.slice(-50))) } catch {} }, [history])

  // status poll
  useEffect(() => {
    let alive = true
    const poll = () =>
      fetch('/api/agent/status')
        .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
        .then((j) => {
          if (!alive) return
          // built:false + no error = warming up (badge '…'), error = offline
          setReady(j.error ? false : j.built === false ? null : j.ready !== false)
          if (typeof j.model === 'string') setModel(j.model.split('.').pop() ?? j.model)
        })
        .catch(() => alive && setReady(false))
    void poll()
    const iv = setInterval(poll, 10000)
    return () => { alive = false; clearInterval(iv) }
  }, [])

  // auto-scroll (unless user scrolled up)
  useEffect(() => {
    const el = feedRef.current
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight
  }, [msgs, busy])
  const onScroll = () => {
    const el = feedRef.current
    if (!el) return
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60
  }

  // global "C" focuses the input
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.key === 'c' || e.key === 'C') && !e.metaKey && !e.ctrlKey && !e.altKey) {
        const tag = (document.activeElement?.tagName ?? '').toLowerCase()
        if (tag !== 'input' && tag !== 'textarea') { e.preventDefault(); inputRef.current?.focus() }
      }
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [])

  const slashMatch = useMemo(() => {
    if (!input.startsWith('/')) return []
    const q = input.toLowerCase()
    return SLASH.filter((s) => s.cmd.startsWith(q))
  }, [input])

  const add = (m: ChatMsg) => setMsgs((arr) => [...arr, m])
  const clear = () => { setMsgs([]); try { localStorage.removeItem(LS_MSGS) } catch {} }

  // patch the last message in place (the in-flight streaming bubble)
  const patchLast = (fn: (m: ChatMsg) => ChatMsg) =>
    setMsgs((arr) => arr.length ? [...arr.slice(0, -1), fn(arr[arr.length - 1])] : arr)
  // patch the user message that started this turn (ambient subline)
  const patchUserAt = (ts: number, fn: (m: ChatMsg) => ChatMsg) =>
    setMsgs((arr) => arr.map((m) => (m.role === 'user' && m.ts === ts ? fn(m) : m)))

  const ask = async (text: string) => {
    const eye = getEyeLine()
    const t0 = Date.now()
    add({ role: 'user', text, ts: t0, eye })
    setBusy(true)
    try {
      const r = await fetch('/api/agent/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      })
      if (!r.ok || !r.body) throw new Error(`stream ${r.status}`)
      add({ role: 'agent', text: '', ts: Date.now(), streaming: true })
      const reader = r.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      let sawDone = false
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const frames = buf.split('\n\n')
        buf = frames.pop() ?? ''
        for (const frame of frames) {
          const line = frame.split('\n').find((l) => l.startsWith('data: '))
          if (!line) continue
          let ev: any
          try { ev = JSON.parse(line.slice(6)) } catch { continue }
          if (ev.type === 'ambient' && typeof ev.line === 'string') {
            patchUserAt(t0, (m) => ({ ...m, eye: ev.line })) // what the agent actually saw
          } else if (ev.type === 'delta' && typeof ev.text === 'string') {
            patchLast((m) => ({ ...m, text: m.text + ev.text }))
          } else if (ev.type === 'tool' && typeof ev.name === 'string') {
            patchLast((m) => ({ ...m, tools: [...(m.tools ?? []), ev.name] }))
          } else if (ev.type === 'event') {
            const label = labelEvents([ev], t0)?.[0]
            if (label) patchLast((m) => ({ ...m, events: [...(m.events ?? []), label] }))
          } else if (ev.type === 'done') {
            sawDone = true
            setReady(true)
            patchLast((m) => ({
              ...m,
              streaming: false,
              text: String(ev.full ?? m.text).trim() || '(empty reply)',
              events: labelEvents(ev.events_during, t0) ?? m.events,
            }))
          } else if (ev.type === 'error') {
            sawDone = true
            setReady(false)
            patchLast((m) => ({
              ...m, role: 'error', streaming: false,
              text: ev.detail ? `${ev.error}: ${ev.detail}` : String(ev.error ?? 'agent error'),
            }))
          }
        }
      }
      if (!sawDone) patchLast((m) => (m.streaming ? { ...m, streaming: false, text: m.text || '(stream ended early)' } : m))
    } catch {
      // stream route unavailable → fall back to the blocking endpoint
      setMsgs((arr) => (arr[arr.length - 1]?.streaming ? arr.slice(0, -1) : arr))
      try {
        const r = await fetch('/api/agent/ask', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: text }),
        })
        const j = await r.json().catch(() => null)
        if (!r.ok || j?.error) {
          setReady(false)
          add({ role: 'error', text: j?.error ? `agent offline: ${j.error}` : 'agent offline', ts: Date.now() })
        } else {
          setReady(true)
          add({
            role: 'agent',
            text: String(j.a ?? j.answer ?? '(empty reply)').trim() || '(empty reply)',
            ts: Date.now(),
            eye: j.ambient,
            events: labelEvents(j.events_during, t0),
          })
        }
      } catch {
        setReady(false)
        add({ role: 'error', text: 'agent offline: no answer from :8765', ts: Date.now() })
      }
    }
    setBusy(false)
  }

  const send = async (raw?: string) => {
    const text = (raw ?? input).trim()
    if (!text || busy) return
    setInput(''); setHistIdx(-1)
    if (inputRef.current) inputRef.current.style.height = 'auto'
    setHistory((h) => (h[h.length - 1] === text ? h : [...h, text]).slice(-50))
    pinnedRef.current = true

    if (text.startsWith('/')) {
      const cmd = SLASH.find((s) => s.cmd === text.split(' ')[0])
      if (cmd) {
        if (cmd.action === 'clear') { clear(); return }
        if (cmd.action === 'help') {
          add({ role: 'system', ts: Date.now(), text: SLASH.map((s) => `\`${s.cmd}\`: ${s.desc}`).join('\n') })
          return
        }
        if (cmd.prompt) { await ask(cmd.prompt); return }
      }
    }
    await ask(text)
  }

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send() }
    else if (e.key === 'ArrowUp' && !input) {
      if (!history.length) return
      e.preventDefault()
      const next = histIdx < 0 ? history.length - 1 : Math.max(0, histIdx - 1)
      setHistIdx(next); setInput(history[next] ?? '')
    } else if (e.key === 'ArrowDown' && histIdx >= 0) {
      e.preventDefault()
      const next = histIdx + 1
      if (next >= history.length) { setHistIdx(-1); setInput('') }
      else { setHistIdx(next); setInput(history[next] ?? '') }
    } else if (e.key === 'Tab' && slashMatch.length > 0) {
      e.preventDefault(); setInput(slashMatch[0].cmd + ' ')
    } else if (e.key === 'Escape') { setInput(''); setHistIdx(-1) }
  }

  const grow = (el: HTMLTextAreaElement) => {
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 96) + 'px' // ~4 lines
  }

  const fmtTs = (ts: number) =>
    new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })

  const roleName = (r: ChatMsg['role']) =>
    r === 'user' ? 'you' : r === 'error' ? 'error' : r === 'system' ? 'sys' : 'emotiv'

  return (
    <div className="chat-band panel">
      <div className="chat-header">
        <div className="chat-title"><span className="brand-mark">🧠</span> EMOTIV Agent
          <span className="hint">it feels you while it answers</span>
        </div>
        <div className="chat-status-row">
          <span className={`badge ${ready ? 'ok' : ready === false ? 'err' : 'warn'}`}>
            {ready ? (model || 'ready') : ready === false ? 'offline' : '…'}
          </span>
          <button className="mini-btn" onClick={clear} title="clear conversation">⟲</button>
        </div>
      </div>
      <div className="chat-feed" ref={feedRef} onScroll={onScroll}>
        {msgs.length === 0 && (
          <div className="chat-welcome">
            <div className="cw-title">Talk to the agent that feels you</div>
            <div className="cw-sub">
              It answers knowing your focus, stress and stillness, live from the headset.<br />
              Type <code className="md-code">/help</code> for slash commands · <code className="md-code">↑</code> for history · <code className="md-code">C</code> to focus.
            </div>
            <div className="cw-chips">
              {WELCOME_CHIPS.map((q) => (
                <button key={q} className="cw-chip" onClick={() => void send(q)}>{q}</button>
              ))}
            </div>
          </div>
        )}
        {msgs.map((m, i) => (
          <div className={`chat-msg ${m.role}`} key={i}>
            <div className="chat-role">
              <span>{roleName(m.role)}</span>
              <span className="chat-ts">{fmtTs(m.ts)}</span>
            </div>
            <div className={`chat-text ${m.streaming ? 'streaming' : ''}`}>
              {m.role === 'user'
                ? <span className="chat-plain">{m.text}</span>
                : <Markdown text={m.text} />}
              {m.streaming && <span className="caret">▊</span>}
            </div>
            {m.tools && m.tools.length > 0 && (
              <div className="chat-tools">{m.tools.map((t, j) => <span className="tool-chip" key={j}>🔧 {t}</span>)}</div>
            )}
            {m.eye && (
              <div className="chat-eye" title={m.role === 'user' ? 'what the agent saw when you sent this' : "the agent's ambient read during its reply"}>
                {m.eye}
              </div>
            )}
            {m.events && m.events.length > 0 && (
              <div className="chat-events" title="brain events that landed while the agent was thinking">
                {m.events.map((e, j) => <span className="event-chip" key={j}>{glyph(e)}</span>)}
              </div>
            )}
          </div>
        ))}
        {busy && !msgs[msgs.length - 1]?.streaming && (
          <div className="chat-msg agent">
            <div className="chat-role"><span>emotiv</span></div>
            <div className="chat-text typing">▊ thinking…</div>
          </div>
        )}
      </div>
      <div className="chat-input-row">
        {slashMatch.length > 0 && input.startsWith('/') && (
          <div className="slash-suggest">
            {slashMatch.map((s) => (
              <div className="slash-item" key={s.cmd}
                   onClick={() => { setInput(s.cmd + ' '); inputRef.current?.focus() }}>
                <span className="slash-cmd">{s.cmd}</span>
                <span className="slash-desc">{s.desc}</span>
              </div>
            ))}
          </div>
        )}
        <textarea
          ref={inputRef}
          className="chat-input"
          rows={1}
          value={input}
          placeholder="message the agent  (try /help · C to focus)"
          onChange={(e) => { setInput(e.target.value); grow(e.target) }}
          onKeyDown={onKey}
          disabled={ready === false}
        />
        <button className="chat-send" onClick={() => void send()} disabled={busy || !input.trim()}>
          {busy ? '…' : '➤'}
        </button>
      </div>
    </div>
  )
}
