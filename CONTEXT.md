# Harness Orchestration

Harness Orchestration coordinates task-scoped Agent executions in a dependency
workflow while preserving execution ownership, history, and human intervention.

## Language

**DAG**:
The versioned dependency structure whose nodes are Tasks and whose directed edges are prerequisite relationships.
_Avoid_: Workflow, run, task list

**Workflow Template**:
A reusable execution definition with a stable identity. A template contains versioned planning resources but does not itself execute work.
_Avoid_: Workflow Instance, Run, Task

**Template Version**:
An immutable published snapshot of a Workflow Template's execution resources. New instances select one specific version; publishing a later version does not change existing instances.
_Avoid_: Draft, Run, ChangeSet

**Template Parameter Schema**:
The declaration of inputs a Workflow Template accepts, including type, requiredness, default, allowed values, and whether a value is sensitive.
_Avoid_: Instance Parameter Value, environment, secret value

**Instance Parameter Value**:
The validated value supplied for one Workflow Instance under a Template Parameter Schema. Values are snapshotted on the instance and do not change when the template evolves.
_Avoid_: Template Parameter Schema, secret value

**Secret Reference**:
A non-secret reference to protected credential material used by a Workflow Instance. The reference may be resolved during execution but the secret value is not part of the template, instance history, Agent prompt, or log.
_Avoid_: password, token, instance parameter value

**Workflow Instance**:
A concrete execution-oriented use of one published Template Version, with its own input parameters, Git reference, optional environment context, Workspace, task state, attempts, sessions, and history. Environment is not required for general orchestration. Multiple instances of one Template Version may run independently and concurrently.
_Avoid_: Workflow Template, Template Version, Task Attempt

**Task**:
A node in a DAG that declares one independently executable unit of work and its completion contract.
_Avoid_: Requirement, workflow, task attempt

**Workflow Graph**:
The normalized directed acyclic graph projection of a Template Version's Tasks and dependency relationships. It is a human-readable view of the definition, not a second independent source of truth. A draft graph may change while editing; a published graph is immutable.
_Avoid_: Run graph, instance task status, independent graph document

**Run**:
The execution record of a Workflow Instance, preserving its own lifecycle and history when later template versions or successor instances are created.
_Avoid_: Workflow Template, Workflow Instance, task attempt, agent session

**ChangeSet**:
An audited proposal to change published template resources or a running Workflow Instance, including impact analysis and an explicit approval decision.
_Avoid_: Draft edit, human message, direct instance mutation

**Instance ChangeSet**:
A ChangeSet scoped to one Workflow Instance. It can produce a successor instance without changing the source Workflow Template or its published version.
_Avoid_: Template Version, template-wide ChangeSet

**Artifact Reuse Eligibility**:
The determination that a prior Task result may be reused by a successor instance because its definition, dependencies, inputs, execution context, and validity remain compatible.
_Avoid_: same task name, automatic reuse

**Successor Instance**:
A new Workflow Instance created to replace or continue from a source instance after a whole-instance change or re-execution. The source instance remains auditable and records its successor relationship.
_Avoid_: Task Attempt, in-place mutation, template version

**Instance Drain**:
The controlled transition in which a Workflow Instance stops activating new work and allows currently executing work to reach a safe boundary before termination. Immediate abort is a separate explicitly confirmed action.
_Avoid_: pause, retry, deletion

**Version Availability**:
Whether a Template Version may be selected for new instances. A disabled or archived version is preserved for history but cannot be selected for new instances.
_Avoid_: Run status, instance status

**External Side Effect**:
An operation performed by a Task that changes a system outside the orchestration record, such as deployment, sending a message, or modifying a database. Such operations require idempotency or an explicit compensating action.
_Avoid_: artifact, task output, log entry

**Execution Context**:
The immutable-at-start context of a Workflow Instance, including its Template Version, validated input values, Git reference, Workspace selection, and execution policy. The current product does not include an Environment object.
_Avoid_: template default, live mutable configuration, Agent Session

**Environment**:
A future execution context representing a target risk or runtime boundary. It is not a current product object and must not be introduced as a required field for templates or instances.
_Avoid_: Workspace, Git reference, Template Version, current instance input

**Instance State**:
The lifecycle state of a Workflow Instance, independent of individual Task states and Template Version availability. It expresses whether the instance is queued, running, draining, paused, completed, failed, aborted, superseded, or archived.
_Avoid_: Task status, Template Version status, Run transition

**Approval Decision**:
An explicit, auditable acceptance or rejection by an authorized person for a ChangeSet or high-risk operation. Template publication and destructive control actions may each require an Approval Decision.
_Avoid_: validation result, draft save

**Permission Boundary**:
The set of actions a person may perform on a Template, Template Version, or Workflow Instance, including editing, publishing, creating instances, and controlling execution.
_Avoid_: Agent Name, Project Workspace, environment

**Agent Name**:
A human-readable logical name for an Agent Runtime or provider adapter. It identifies an execution option but does not by itself grant tools, select a Workspace, or replace capability validation.
_Avoid_: Service name, worker identity, capability requirement

**Agent ID**:
A unique runtime identity for one registered Agent instance. Agent ID owns leases, heartbeats, attempts, and audit records but is not a task-routing key.
_Avoid_: Agent name, service name

**Service Name**:
An optional business or repository boundary affected by a Task. Service Name provides context and must not decide which Agent executes the Task. It is not a required Run input; when the same template targets different services, the target service is modeled separately as an instance parameter.
_Avoid_: Agent name, assignee

**Task Target**:
An optional execution preference declared by a Task. The target is resolved through capability requirements, project-group policy, and an Agent Runtime adapter; it is not a direct process-routing key.
_Avoid_: Service binding, guaranteed Agent assignment

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

**Project Group**:
A human-facing ownership and authorization boundary for members, Workflow Templates, Project Workspaces, and default execution policies. This product does not currently model a separate Project entity. Each Workflow Template has exactly one primary Project Group; a Workflow Instance inherits that group and cannot move to another group. A Project Group never selects the Agent that executes a Task.
_Avoid_: Project, Workflow Instance ownership override, service name, agent routing group

**Project**:
No independent domain object in the current product. When users say “项目” in the job-flow area, the canonical term is Project Group unless a future product decision introduces a separate Project layer.
_Avoid_: Project Group, Workflow Template

**Project Workspace**:
A registered repository or directory available to a Project Group as a source workspace, together with its access policy and display metadata.
_Avoid_: Run workspace, task attempt, file tree

**Run Workspace**:
The concrete filesystem root selected for one Run. Tasks share it by default; an execution policy may create an isolated Workspace for a Task that cannot safely share it.
_Avoid_: Project workspace, task attempt, artifact

**Attempt Workspace Binding**:
The immutable record of which Run Workspace or isolated Workspace one Task Attempt actually used. The Agent, file browser, editor, and execution history resolve files through this binding.
_Avoid_: Workspace, project group, agent session

**Task Capability Requirements**:
The abstract abilities, resources, safety level, completion gates, and external integrations required by a Task. They express what the Task needs rather than which concrete Agent, Skill, or MCP implementation must be used.
_Avoid_: Agent assignment, Skill implementation, MCP grant

**Execution Profile**:
A versioned execution policy that determines the permitted Agent Runtime, Workspace behavior, resource budget, network boundary, secret policy, and other runtime constraints for an Attempt.
_Avoid_: Task capability requirement, live mutable defaults

**Skill Bundle**:
A versioned package of Agent instructions, rules, and supporting resources that can be attached to an execution. A Skill Bundle is not itself a tool permission or an Agent identity.
_Avoid_: MCP Server, Agent Runtime, prompt-only text

**MCP Server**:
An external tool or data provider exposed through the Model Context Protocol. Its registration, version, health, and ownership are separate from the Task and the Agent Runtime.
_Avoid_: Skill, Agent, Task output

**MCP Grant**:
An auditable permission that determines whether a Task Attempt may expose or invoke an MCP Server or specific tool. A server being registered does not imply that every Agent may use it.
_Avoid_: MCP Server registration, Skill Bundle

**Agent Runtime**:
A provider-specific execution runtime that starts an Agent and communicates with Harness through an adapter, normally using ACP. It is an execution option governed by policy, not a Task or a Project Group.
_Avoid_: Agent Session, Agent ID, capability requirement

**Execution Manifest**:
The immutable Attempt snapshot of the resolved Agent Runtime, Execution Profile, Skill Bundles, MCP Grants, Workspace binding, secret references, policy versions, and source decisions used to start execution.
_Avoid_: template definition, mutable configuration, Agent transcript

**Capability Preflight**:
The execution-before-start verification that checks whether the resolved runtime, Skills, MCP permissions, Workspace, resources, and policies are available and compatible. A failed preflight is a diagnosable capability state, not an ordinary Task failure.
_Avoid_: Task execution, health dashboard, validation-only draft check
