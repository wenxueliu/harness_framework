# Harness Orchestration

Harness Orchestration coordinates task-scoped Agent executions in a dependency
workflow while preserving execution ownership, history, and human intervention.

## Language

**Requirement**:
The user's intended outcome, constraints, and acceptance boundaries, versioned independently from how the work is executed.
_Avoid_: Workflow, task, prompt

**Workflow Spec**:
The execution policy for a Requirement, including defaults, guardrails, and provider choices, versioned independently from the DAG.
_Avoid_: Requirement, DAG, run

**DAG**:
The versioned dependency structure whose nodes are Tasks and whose directed edges are prerequisite relationships.
_Avoid_: Workflow, run, task list

**Plan**:
The versioned execution-facing package derived from a Requirement, Workflow Spec, and DAG for publication.
_Avoid_: Requirement, DAG

**Task**:
A node in a DAG that declares one independently executable unit of work and its completion contract.
_Avoid_: Requirement, workflow, task attempt

**Run**:
One execution of published workflow resources, preserving its own lifecycle and history when later versions supersede it.
_Avoid_: Workflow, task attempt, agent session

**ChangeSet**:
An audited proposal to change published workflow resources, including impact analysis and an explicit approval decision.
_Avoid_: Draft edit, human message, task proposal

**Agent Name**:
A stable logical executor name used only by the registered Worker compatibility path. ACP execution selects a provider from task configuration or task type instead.
_Avoid_: Service name, worker type, capability

**Agent ID**:
A unique runtime identity for one registered Agent instance. Agent ID owns leases, heartbeats, attempts, and audit records but is not a task-routing key.
_Avoid_: Agent name, service name

**Service Name**:
The optional business or repository boundary affected by a Task. Service Name provides context and must not decide which Agent executes the Task.
_Avoid_: Agent name, assignee

**Task Target**:
The Agent Name declared by a Task as its eligible executor in the registered Worker compatibility path.
_Avoid_: Service binding, capability preference

**Task Attempt**:
One fenced execution of a Task, identified by an immutable attempt ID and lease epoch. Retrying or reopening a Task creates a new Task Attempt and preserves earlier history.
_Avoid_: Task, session

**Agent Session**:
A provider-native conversational context attached to a Task Attempt. A later Task Attempt may resume the Agent Session without becoming the same attempt.
_Avoid_: Agent ID, task attempt

**Turn**:
One prompt-response exchange inside an Agent Session, initiated by the task package or a Human Message.
_Avoid_: Task, session

**Human Message**:
A durable instruction from a person to a Task. It is queued or interrupting, audited independently, and consumed by a Turn.
_Avoid_: Task-to-task message, control signal
