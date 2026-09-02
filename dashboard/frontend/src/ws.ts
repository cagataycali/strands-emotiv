// ws.ts: the ONE WebSocket + tiny store. The API is FROZEN:
//   useBrain() -> { state, motion, events, connected }
// Panels depend on this. Extend via src/ws-ext/*.ts, never edit here.
import { useSyncExternalStore } from 'react'

export interface ContactQuality { [ch: string]: number } // 0..4
export interface BrainState {
  connected?: boolean
  headset?: { id?: string; by?: string; status?: string; usable?: boolean }
  battery?: number
  signal?: number
  contact_quality?: ContactQuality
  eeg_quality?: { [k: string]: number }
  metrics?: {
    attention?: number; engagement?: number; excitement?: number
    longExcitement?: number; stress?: number; relaxation?: number; interest?: number
  }
  band_power?: { [ch: string]: { theta: number; alpha: number; betaL: number; betaH: number; gamma: number } }
  facial?: { eye?: string; upper?: string; upper_pow?: number; lower?: string; lower_pow?: number }
  mental_command?: { act?: string; pow?: number }
  motion?: { q?: number[]; acc?: number[]; mag?: number[]; yaw?: number; pitch?: number; roll?: number }
  warning?: { code?: number; message?: string }
  updated?: number
}
export interface MotionMsg { q: number[]; t: number }
export interface BrainEvent { kind: string; t: number; meta?: Record<string, unknown>; label?: string; type?: string }

interface Snapshot {
  state: BrainState | null
  motion: MotionMsg | null
  events: BrainEvent[]
  connected: boolean
}

const MAX_EVENTS = 200

let snap: Snapshot = { state: null, motion: null, events: [], connected: false }
const listeners = new Set<() => void>()
const emit = () => listeners.forEach((l) => l())

function wsUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/ws`
}

let ws: WebSocket | null = null
let retry = 0
let started = false

function connect() {
  try { ws = new WebSocket(wsUrl()) } catch { scheduleReconnect(); return }
  ws.onopen = () => {
    retry = 0
    snap = { ...snap, connected: true }
    emit()
  }
  ws.onmessage = (e) => {
    let msg: any
    try { msg = JSON.parse(e.data) } catch { return }
    if (msg?.type === 'state' && msg.state) {
      snap = { ...snap, state: msg.state }
      // piggyback motion if the state carries it and no fast channel yet
      if (msg.state.motion?.q && !snap.motion) {
        snap = { ...snap, motion: { q: msg.state.motion.q, t: msg.state.updated ?? Date.now() / 1000 } }
      }
      emit()
    } else if (msg?.type === 'motion' && msg.q) {
      snap = { ...snap, motion: { q: msg.q, t: msg.t ?? Date.now() / 1000 } }
      emit()
    } else if (msg?.type === 'event' || msg?.type === 'marker') {
      const ev: BrainEvent = { kind: msg.kind ?? msg.label ?? msg.type, t: msg.t ?? Date.now() / 1000, meta: msg.meta, label: msg.label, type: msg.type }
      snap = { ...snap, events: [...snap.events.slice(-(MAX_EVENTS - 1)), ev] }
      emit()
    }
  }
  ws.onclose = () => {
    snap = { ...snap, connected: false }
    emit()
    scheduleReconnect()
  }
  ws.onerror = () => { try { ws?.close() } catch { /* noop */ } }
}

function scheduleReconnect() {
  const delay = Math.min(500 * 2 ** retry, 5000)
  retry++
  setTimeout(connect, delay)
}

export function startBrain() {
  if (started) return
  started = true
  connect()
}

const subscribe = (cb: () => void) => { listeners.add(cb); return () => { listeners.delete(cb) } }
const getSnapshot = () => snap

export function useBrain(): Snapshot {
  startBrain()
  return useSyncExternalStore(subscribe, getSnapshot)
}

// Non-hook access for rAF render loops (canvas/three): read without re-rendering React.
export function getBrain(): Snapshot { return snap }
