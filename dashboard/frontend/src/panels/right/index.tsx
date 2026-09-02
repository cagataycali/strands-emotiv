// RightColumn: what the agent sees, hears, says.
// Panels: AgentEye · EventRiver · MetricRadar · Sonify. Chat lives in App.tsx
// as the full-width bottom band (neon-the-g1 parity).
import { Consent } from './Consent'
import { Recorder } from './Recorder'
import { AgentEye } from './AgentEye'
import { EventRiver } from './EventRiver'
import { MetricRadar } from './MetricRadar'
import { Sonify } from './Sonify'
import './right.css'

export function RightColumn() {
  return (
    <div className="right-col">
      <Consent />
      <Recorder />
      <AgentEye />
      <EventRiver />
      <MetricRadar />
      <Sonify />
    </div>
  )
}
