import { useBrain } from './ws'
import { LeftColumn } from './panels/left'
import { RightColumn } from './panels/right'
import { Chat } from './panels/right/Chat'

export default function App() {
  const { state, connected } = useBrain()
  const hs = state?.headset
  const cq = state?.contact_quality
  const cqGood = cq ? Object.values(cq).filter((v) => v >= 3).length : 0
  const cqTotal = cq ? Object.keys(cq).length : 0

  return (
    <div className="app">
      <div className="topbar">
        <div className="title">strands-emotiv <span>· the mirror</span></div>
        <div className="spacer" />
        <div className={`pill ${connected ? 'ok' : 'bad'}`}>{connected ? '● live' : '○ reconnecting…'}</div>
        <div className="pill">{hs?.id ?? 'no headset'}</div>
        <div className={`pill ${cqTotal > 0 && cqGood >= cqTotal - 2 ? 'ok' : ''}`}>CQ {cqGood}/{cqTotal || 14}</div>
        <div className="pill">🔋 {state?.battery ?? '?'}%</div>
      </div>
      <div className="columns">
        <div className="col"><LeftColumn /></div>
        <div className="col"><RightColumn /></div>
      </div>
      <Chat />
    </div>
  )
}
