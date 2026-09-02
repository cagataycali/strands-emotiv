# Tools

```python
from strands import Agent
from strands_emotiv.tools import BRAIN_TOOLS
agent = Agent(tools=BRAIN_TOOLS)
agent("wait until I blink twice, then tell me what my alpha did")
```

| tool | returns |
|---|---|
| `brain_line()` | the one line, as the agent sees it |
| `brain_snapshot()` | the line + everything behind it |
| `brain_bands(channel?)` | θ α βL βH γ |
| `head_pose()` | yaw / pitch / roll |
| `contact_quality()` | 14 × 0 to 4 |
| `wait_for_brain_event(kind, timeout_s)` | blocks on blink · wink · clench · turn · `command:*` |
| `recent_brain_events(limit)` | the river |
| `brain_status()` | the doctor |
| `mental_approval(question)` | push the box = yes, pull = no, clench = veto |
| `record_start/stop/status` | cut episodes from inside a conversation |
| `record_publish(name?)` | push to the private Hub dataset |

Every agent action calls `injectMarker`; the EEG record carries what the agent did and when.

## Over MCP

```json
{ "mcpServers": { "brain": { "command": "uvx", "args": ["strands-emotiv", "mcp"] } } }
```

```bash
strands-emotiv mcp                 # stdio: Claude Desktop, Cursor, Kiro
strands-emotiv mcp --http 8000     # streamable HTTP at /mcp
strands-emotiv mcp --fake          # no headset
```

Same 13 tools, any client, own Cortex stream. Start with `brain_line`: prepend it to a prompt and that agent can feel the person too.
