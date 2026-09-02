# API (`localhost:8765`)

**Live** · `WS /ws` → `state` 2 to 8 Hz · `motion` 30 Hz · `event` · `marker` · `GET /api/state` · `/api/history`

**Agent** · `POST /api/agent/stream {message}` → SSE `ambient → delta* → tool* → event* → done` · `POST /api/agent/ask` · `GET /api/agent/status`

**Consent** · `/api/mental/{status,profile,train,erase,active,approval}`

**Dataset** · `POST /api/dataset/record/{start,stop}` · `GET /api/dataset/{status,episodes,export}` · `POST /api/dataset/publish` → `cagataydev/emotiv-ecot`, private

```bash
curl -N -X POST localhost:8765/api/agent/stream -H 'content-type: application/json' -d '{"message":"am I focused?"}'
```
