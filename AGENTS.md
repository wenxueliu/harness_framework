# Harness Framework — Multi-Agent Orchestration Engine

## Overview

`harness-framework` is the **core engine** for multi-Agent collaboration. It solves distributed Agent flow control, state management, and feedback loops. Core capabilities:

- **Flow control:** DAG-based task dependency scheduling — downstream tasks auto-activate when dependencies are satisfied
- **State management:** Unified task state machine (BLOCKED → PENDING → IN_PROGRESS → DONE/FAILED) with human intervention (PAUSE/RESUME/ABORT)
- **Feedback loop:** Test failures trigger FIX messages to services via Message Bus; auto-retest on fix completion
- **Fault recovery:** Agent death or task timeout triggers automatic rollback and retry
- **Human takeover:** Manual intervention at any point (reassign tasks, force state changes)

## Architecture

```
harness_framework/
├── daemon.py          # Main entry: starts Aggregator + Watchdog + WebAPI threads
├── aggregator.py      # DAG scheduler: activates downstream tasks when deps satisfied
├── watchdog.py        # Zombie recovery: detects Agent death and task timeout
├── webapi.py          # HTTP API: dashboard queries and control signals
├── message_bus.py     # Inter-task messaging: send, poll, complete
├── consul_client.py   # Consul HTTP client (stdlib only, zero external deps)
├── kv_store_protocol.py  # KVStore Protocol — storage abstraction
├── local_store.py        # LocalStore — in-memory + embedded Consul HTTP
└── file_store.py         # FileStore — JSON file (pure local, no HTTP)
```

**Three core components:**
- **Aggregator:** Only processes `published=true` workflows. Polls task statuses, activates downstream tasks when all dependencies are DONE.
- **Watchdog:** Only processes `published=true` workflows. Polls Consul Health for Agent liveness, detects task timeout (default 1h). Rolls back to PENDING on death/timeout (max 5 retries, then FAILED).
- **WebAPI:** stdlib `http.server` ThreadingHTTPServer. Endpoints: `/api/workflows`, `/api/workflow/<req_id>`, `/api/agents`, `/api/health`.

## Quick Start

**With Consul:**
1. **Define dependencies:** Write `dependencies.json` describing task topology
2. **Start Consul:** `./scripts/start_consul_dev.sh`
3. **Start framework:** `python -m harness_framework.daemon`
4. **Initialize workflow:** `python scripts/sync_to_consul.py <req_id> dependencies.json --title "Title"`
5. **Monitor:** Access WebAPI or Consul UI
6. **Intervene:** Use API to modify task state or reassign as needed

**Local mode (zero dependencies):**
```bash
# In-memory + embedded HTTP server (agents connect via HTTP)
python -m harness_framework.daemon --local

# Pure file mode (no HTTP, agents use file_kv.py CLI)
python -m harness_framework.daemon --local-file
```

## Execution Flow

1. **Task activation:** Aggregator detects all deps DONE → activates downstream tasks as PENDING
2. **Task execution:** Agent claims PENDING task → writes IN_PROGRESS → completes → writes DONE
3. **Fault recovery:** Watchdog detects timeout/Agent death → rolls back to PENDING (≤5 retries)
4. **Quality gate:** test failure → sends FIX message → polls for fix completion → retests (≤3 retries)
5. **Flow termination:** All tasks DONE → flow complete; retry limit exceeded → FAILED

## Storage Backends

| Mode | Flag | Agent Communication |
|------|------|---------------------|
| **Consul** | (default) | HTTP → Consul server |
| **Local + HTTP** | `--local` | HTTP → embedded Consul API |
| **File Store** | `--local-file` | `scripts/file_kv.py` CLI → JSON file |

## Consul KV Structure

```
workflows/<req_id>/
├── published               # true | false (draft mode, default false)
├── title                   # Requirement title
├── priority                # Integer priority (higher = more urgent)
├── control                 # Control signal: PAUSE | RESUME | ABORT
├── dependencies            # JSON, task dependency topology
├── created_at
├── tasks/<task_name>/
│   ├── status              # PENDING | BLOCKED | IN_PROGRESS | DONE | FAILED | ABORTED | AWAITING_REVIEW
│   ├── type                # design | review | backend | test | deploy
│   ├── service_name        # Associated service name (optional)
│   ├── description
│   ├── assigned_agent
│   ├── started_at / activated_at / retry_count / error_message
│   └── last_recovery_reason / last_recovery_at
└── context/...             # Arbitrary context key-values
```

## Task Types and State Machine

**Task types** (`type` field): `design`, `review`, `backend`, `test`, `deploy`

**State machine:** BLOCKED → PENDING → IN_PROGRESS → DONE/FAILED/AWAITING_REVIEW

## Skills

The framework includes 5 infrastructure skills:
- **stage-bridge:** Agent↔Framework communication via Consul KV (22 scripts + 3 platform prompt templates)
- **design-pipeline:** Design document → dependencies.json conversion
- **doc-to-deps:** Arbitrary document → dependencies.json extraction
- **harness-sync:** Consul KV workflow synchronization
- **task-executor:** Task execution workflow (TDD → review → merge per task type)

## Cross-Platform Support

The stage-bridge skill provides platform-specific prompt templates for:
- Claude Code: `skills/stage-bridge/templates/claude_code_prompt.md`
- Codex: `skills/stage-bridge/templates/codex_prompt.md`
- OpenCode: `skills/stage-bridge/templates/opencode_prompt.md`

All stage-bridge scripts use Python stdlib only — zero external dependencies, cross-platform compatible.

## Multi-Platform Target

| Platform | Skill location | Config | Instruction file |
|----------|---------------|--------|------------------|
| Claude Code | `skills/<name>/SKILL.md` | `.claude/settings.json` | `CLAUDE.md` |
| Codex | `.agents/skills/<name>/SKILL.md` | `~/.codex/config.toml` | `AGENTS.md` (this file) |
| OpenCode | `.opencode/skills/<name>/SKILL.md` | `opencode.json` | `opencode.json` instructions |

## Design Rules

- **Zero external dependencies** — stdlib only for Python; no npm packages needed
- **CAS atomic writes** — all contention-sensitive Consul writes use Check-And-Set
- **Agent-initiated task claiming** — Agents pull PENDING tasks via CAS; framework never pushes
- **No LLM calls** — pure rule engine; behavior is fully deterministic
