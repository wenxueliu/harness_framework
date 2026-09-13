import { apiRequest, jsonRequest } from './client'
import type { AttemptWorkspaceBinding, MergeTask, ProjectGroup, ProjectGroupMember, ProjectWorkspace, RunCreationResult, RunSummary, WorkspaceAction, WorkspaceActionResult, WorkspaceChange, WorkspaceDiff, WorkspaceFile, WorkspaceManifest, WorkspacePreflight, WorkspaceTreeEntry } from './types'

export async function listProjectGroups(): Promise<ProjectGroup[]> {
  const payload = await apiRequest<{ project_groups: ProjectGroup[] }>('/api/project-groups')
  return payload.project_groups
}

export async function createProjectGroup(name: string, description: string): Promise<ProjectGroup> {
  const payload = await apiRequest<{ project_group: ProjectGroup }>('/api/project-groups', jsonRequest('POST', { name, description }))
  return payload.project_group
}

export async function archiveProjectGroup(group: ProjectGroup): Promise<ProjectGroup> {
  const payload = await apiRequest<{ project_group: ProjectGroup }>(`/api/project-groups/${encodeURIComponent(group.group_id)}`,
    jsonRequest('PATCH', { expected_revision: group.revision, status: 'ARCHIVED' }))
  return payload.project_group
}

export async function listProjectGroupMembers(groupId: string): Promise<ProjectGroupMember[]> {
  const payload = await apiRequest<{ members: ProjectGroupMember[] }>(`/api/project-groups/${encodeURIComponent(groupId)}/members`)
  return payload.members
}

export async function putProjectGroupMember(groupId: string, subject: string, role: ProjectGroupMember['role']): Promise<ProjectGroupMember> {
  const payload = await apiRequest<{ member: ProjectGroupMember }>(`/api/project-groups/${encodeURIComponent(groupId)}/members/${encodeURIComponent(subject)}`,
    jsonRequest('PUT', { role }))
  return payload.member
}

export async function deleteProjectGroupMember(groupId: string, subject: string): Promise<void> {
  await apiRequest(`/api/project-groups/${encodeURIComponent(groupId)}/members/${encodeURIComponent(subject)}`, { method: 'DELETE' })
}

export async function listGroupWorkflows(groupId: string): Promise<string[]> {
  const payload = await apiRequest<{ workflows: Array<{ req_id: string }> }>(
    `/api/project-groups/${encodeURIComponent(groupId)}/workflows`,
  )
  return payload.workflows.map((item) => item.req_id)
}

export async function listProjectWorkspaces(groupId: string): Promise<ProjectWorkspace[]> {
  if (groupId === 'unassigned') return []
  const payload = await apiRequest<{ workspaces: ProjectWorkspace[] }>(
    `/api/project-groups/${encodeURIComponent(groupId)}/workspaces`,
  )
  return payload.workspaces
}

export async function registerProjectWorkspace(groupId: string, input: { name: string; sourceType: 'LOCAL_PATH' | 'GIT_CLONE'; rootAlias: string; relativePath: string; gitUrl?: string; defaultRef?: string; access: 'READ_ONLY' | 'READ_WRITE' }): Promise<ProjectWorkspace> {
  const payload = await apiRequest<{ workspace: ProjectWorkspace }>(`/api/project-groups/${encodeURIComponent(groupId)}/workspaces`, jsonRequest('POST', {
    name: input.name, source_type: input.sourceType, root_alias: input.rootAlias,
    relative_path: input.relativePath, git_url: input.gitUrl, default_ref: input.defaultRef,
    access: input.access,
  }))
  return payload.workspace
}

export async function preflightProjectWorkspace(workspaceId: string): Promise<WorkspacePreflight> {
  const payload = await apiRequest<{ preflight: WorkspacePreflight }>(`/api/workspaces/${encodeURIComponent(workspaceId)}/preflight`, jsonRequest('POST', {}))
  return payload.preflight
}

export async function listRuns(reqId: string): Promise<RunSummary[]> {
  const payload = await apiRequest<{ runs: RunSummary[] }>(`/api/workflow/${encodeURIComponent(reqId)}/runs`)
  return payload.runs
}

export async function createRun(reqId: string, workspaceId: string,
  strategy: 'ORIGINAL' | 'GIT_WORKTREE' | 'CONTROLLED_COPY', gitRef?: string,
  acceptDirty = false): Promise<RunCreationResult> {
  return apiRequest(`/api/workflows/${encodeURIComponent(reqId)}/runs`, jsonRequest('POST', {
    workspace: { project_workspace_id: workspaceId, strategy, git_ref: gitRef || undefined, accept_dirty: acceptDirty },
  }, { 'Idempotency-Key': crypto.randomUUID() }))
}

export async function listAttempts(reqId: string, taskId: string, runId: string): Promise<AttemptWorkspaceBinding[]> {
  const payload = await apiRequest<{ attempts: AttemptWorkspaceBinding[] }>(
    `/api/workflows/${encodeURIComponent(reqId)}/tasks/${encodeURIComponent(taskId)}/attempts?run_id=${encodeURIComponent(runId)}`,
  )
  return payload.attempts
}

function contextQuery(binding: AttemptWorkspaceBinding): string {
  return new URLSearchParams({ req_id: binding.req_id, run_id: binding.run_id,
    task_id: binding.task_id, attempt_id: binding.attempt_id }).toString()
}

export async function listWorkspaceTree(binding: AttemptWorkspaceBinding, path = ''): Promise<WorkspaceTreeEntry[]> {
  const query = new URLSearchParams({ req_id: binding.req_id, run_id: binding.run_id,
    task_id: binding.task_id, attempt_id: binding.attempt_id, path }).toString()
  const payload = await apiRequest<{ entries: WorkspaceTreeEntry[] }>(
    `/api/workspaces/${encodeURIComponent(binding.workspace_id)}/tree?${query}`,
  )
  return payload.entries
}

export async function readWorkspaceFile(binding: AttemptWorkspaceBinding, path: string): Promise<WorkspaceFile> {
  return apiRequest<WorkspaceFile>(
    `/api/workspaces/${encodeURIComponent(binding.workspace_id)}/file?${contextQuery(binding)}&path=${encodeURIComponent(path)}`,
  )
}

export async function saveWorkspaceFile(binding: AttemptWorkspaceBinding, file: WorkspaceFile,
  content: string, reason: string): Promise<{ sha256: string; event_id: string }> {
  return apiRequest(`/api/workspaces/${encodeURIComponent(binding.workspace_id)}/file`, jsonRequest('PUT', {
    req_id: binding.req_id, run_id: binding.run_id, task_id: binding.task_id,
    attempt_id: binding.attempt_id, path: file.path, content,
    expected_sha256: file.sha256, reason,
  }, { 'Idempotency-Key': crypto.randomUUID() }))
}

export async function listWorkspaceActions(binding: AttemptWorkspaceBinding): Promise<WorkspaceAction[]> {
  const payload = await apiRequest<{ actions: WorkspaceAction[] }>(
    `/api/workspaces/${encodeURIComponent(binding.workspace_id)}/actions?${contextQuery(binding)}`,
  )
  return payload.actions
}

export async function runWorkspaceAction(binding: AttemptWorkspaceBinding, actionId: string): Promise<WorkspaceActionResult> {
  return apiRequest(`/api/workspaces/${encodeURIComponent(binding.workspace_id)}/actions/${encodeURIComponent(actionId)}`,
    jsonRequest('POST', { req_id: binding.req_id, run_id: binding.run_id, task_id: binding.task_id, attempt_id: binding.attempt_id }))
}

export async function createWorkspaceCheckpoint(binding: AttemptWorkspaceBinding, message: string): Promise<{ checkpoint_id: string; commit_sha: string; event_id: string }> {
  return apiRequest(`/api/workspaces/${encodeURIComponent(binding.workspace_id)}/checkpoints`,
    jsonRequest('POST', { req_id: binding.req_id, run_id: binding.run_id, task_id: binding.task_id,
      attempt_id: binding.attempt_id, message }, { 'Idempotency-Key': crypto.randomUUID() }))
}

export async function listWorkspaceChanges(binding: AttemptWorkspaceBinding): Promise<WorkspaceChange[]> {
  const payload = await apiRequest<{ changes: WorkspaceChange[] }>(
    `/api/workspaces/${encodeURIComponent(binding.workspace_id)}/changes?${contextQuery(binding)}`,
  )
  return payload.changes
}

export async function searchWorkspace(binding: AttemptWorkspaceBinding, query: string): Promise<Array<Record<string, unknown>>> {
  const payload = await apiRequest<{ results: Array<Record<string, unknown>> }>(
    `/api/workspaces/${encodeURIComponent(binding.workspace_id)}/search?${contextQuery(binding)}&q=${encodeURIComponent(query)}`,
  )
  return payload.results
}

export async function getWorkspaceDiff(binding: AttemptWorkspaceBinding, path = ''): Promise<WorkspaceDiff> {
  return apiRequest(`/api/workspaces/${encodeURIComponent(binding.workspace_id)}/diff?${contextQuery(binding)}&path=${encodeURIComponent(path)}`)
}

export async function getRunManifest(reqId: string, runId: string): Promise<WorkspaceManifest> {
  const payload = await apiRequest<{ manifest: WorkspaceManifest }>(
    `/api/workflows/${encodeURIComponent(reqId)}/runs/${encodeURIComponent(runId)}/workspace/manifest`,
  )
  return payload.manifest
}

export async function createMergeTask(input: { reqId: string; runId: string; sourceTaskId: string; sourceAttemptId: string; targetTaskId: string; targetAttemptId: string; message?: string }): Promise<MergeTask> {
  const payload = await apiRequest<{ merge_task: MergeTask }>(
    `/api/workflows/${encodeURIComponent(input.reqId)}/runs/${encodeURIComponent(input.runId)}/merge-tasks`,
    jsonRequest('POST', { source_task_id: input.sourceTaskId, source_attempt_id: input.sourceAttemptId, target_task_id: input.targetTaskId, target_attempt_id: input.targetAttemptId, message: input.message || '' }),
  )
  return payload.merge_task
}

export async function previewMergeTask(reqId: string, runId: string, mergeId: string): Promise<MergeTask> {
  const payload = await apiRequest<{ merge_task: MergeTask }>(`/api/workflows/${encodeURIComponent(reqId)}/runs/${encodeURIComponent(runId)}/merge-tasks/${encodeURIComponent(mergeId)}`)
  return payload.merge_task
}

export async function applyMergeTask(reqId: string, runId: string, mergeId: string): Promise<MergeTask> {
  const payload = await apiRequest<{ merge_task: MergeTask }>(`/api/workflows/${encodeURIComponent(reqId)}/runs/${encodeURIComponent(runId)}/merge-tasks/${encodeURIComponent(mergeId)}/apply`, jsonRequest('POST', {}))
  return payload.merge_task
}
