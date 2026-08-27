# Harness Orchestration

Harness Orchestration coordinates named Agents that claim and execute tasks in a
dependency workflow while preserving execution ownership and business context.

## Language

**Agent Name**:
A stable logical executor name declared by an Agent and targeted by a Task. Agent Name is the scheduling identity used for task matching.
_Avoid_: Service name, worker type, capability

**Agent ID**:
A unique runtime identity for one registered Agent instance. Agent ID owns leases, heartbeats, attempts, and audit records but is not a task-routing key.
_Avoid_: Agent name, service name

**Service Name**:
The optional business or repository boundary affected by a Task. Service Name provides context and must not decide which Agent executes the Task.
_Avoid_: Agent name, assignee

**Task Target**:
The Agent Name declared by a Task as its eligible executor. A Task can be claimed only by an Agent registered with the same Agent Name.
_Avoid_: Service binding, capability preference

Every executable Task must declare an explicit Agent Name, and every Agent
must register one explicitly. Service Name is never used as a fallback routing
identity.
