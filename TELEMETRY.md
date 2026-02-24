# Telemetry

Unleashed includes optional telemetry (via AssemblyZero's telemetry module) that records structured events about session lifecycle and auto-approval activity.

## What Is Collected

| Event Type | Trigger | Data |
|-----------|---------|------|
| `session.start` | PTY spawns | version, repo name |
| `session.end` | Session exits | duration, approval count, friction prompts |
| `approval.permission` | Each auto-approval | event type, pattern matched, prompt number |

Every event includes: actor (`claude`), repo name, GitHub user, timestamp, machine ID (hashed).

No prompts, responses, file contents, or API keys are ever collected.

## Where It Is Stored

- **Primary**: DynamoDB table `assemblyzero-telemetry` in `us-east-1`
- **Fallback**: Local JSONL files at `~/.assemblyzero/telemetry-buffer/`

## Retention

Events expire automatically after **90 days**.

## How to Disable

```bash
export ASSEMBLYZERO_TELEMETRY=0
```

When disabled, no events are emitted. Unleashed operates identically with or without telemetry — it is never in the critical path.
