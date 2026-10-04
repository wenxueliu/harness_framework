import type { Task, Workflow, WorkflowPhase, TaskStatus } from '@/api/types'
import type { JobFlowInstance } from '@/api/jobFlow'

const INSTANCE_STATE_TO_PHASE: Record<string, WorkflowPhase> = {
  QUEUED: 'PENDING',
  PENDING: 'PENDING',
  RUNNING: 'RUNNING',
  IN_PROGRESS: 'DEVELOPMENT',
  SUCCEEDED: 'DONE',
  FAILED: 'FAILED',
  ABORTED: 'BLOCKED',
  SUPERSEDED: 'DONE',
  ARCHIVED: 'DONE',
}

const TASK_STATE_TO_STATUS: Record<string, TaskStatus> = {
  QUEUED: 'PENDING',
  PENDING: 'PENDING',
  BLOCKED: 'BLOCKED',
  RUNNING: 'IN_PROGRESS',
  IN_PROGRESS: 'IN_PROGRESS',
  DONE: 'DONE',
  SUCCEEDED: 'DONE',
  FAILED: 'FAILED',
  ABORTED: 'ABORTED',
  AWAITING_REVIEW: 'AWAITING_REVIEW',
  WAITING_FOR_HUMAN: 'WAITING_FOR_HUMAN',
  WAITING_FOR_CAPABILITY: 'BLOCKED',
  SKIPPED_UPSTREAM_FAILED: 'SKIPPED_UPSTREAM_FAILED',
}

export function instanceToWorkflow(instance: JobFlowInstance): Workflow {
  const tasks: Record<string, Task> = {}
  for (const [taskId, task] of Object.entries(instance.tasks ?? {})) {
    tasks[taskId] = {
      id: taskId,
      name: taskId,
      status: TASK_STATE_TO_STATUS[task.state] ?? 'UNKNOWN',
      raw_status: task.state,
      assigned_agent: '',
      depends_on: task.depends_on ?? [],
      last_updated: '',
    }
  }

  const phase = INSTANCE_STATE_TO_PHASE[instance.status?.state] ?? 'UNKNOWN'
  return {
    id: instance.instance_id,
    title: instance.name,
    phase,
    raw_phase: instance.status?.state,
    created_at: '',
    tasks,
    artifacts: {},
  }
}

export function instancesToWorkflows(instances: JobFlowInstance[]): Workflow[] {
  return instances.map(instanceToWorkflow)
}
