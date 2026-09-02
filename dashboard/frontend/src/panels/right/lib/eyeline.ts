// eyeline.ts: the current AgentEye line, shared module-level so Chat can stamp
// each message with what the agent saw at send time. AgentEye writes it;
// anyone may read it.
let current = ''
export function setEyeLine(line: string) { current = line }
export function getEyeLine(): string { return current }
