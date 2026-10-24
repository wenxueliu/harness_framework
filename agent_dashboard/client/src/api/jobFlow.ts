import { apiRequest, jsonRequest } from './client'

export interface JobFlowTask {
  id: string
  name?: string
  agent?: string
  type?: string
  depends_on?: string[]
  description?: string
  service_name?: string
}

export interface JobFlowTemplate {
  template_id: string
  name: string
  description?: string
  group_id?: string
  status?: string
  current_version_id?: string | null
  draft?: {
    tasks: JobFlowTask[]
    parameters?: Record<string, unknown>
    parameter_schema?: Record<string, unknown>
    manifest_hash?: string
  }
  draft_revision: number
}

export interface JobFlowVersion {
  version_id: string
  template_id: string
  version: number
  status: string
  manifest_hash: string
  created_at: string
}

export interface JobFlowInstance {
  instance_id: string
  template_id: string
  group_id?: string
  version_id: string
  name: string
  context: {
    parameters: Record<string, unknown>
    git: Record<string, unknown>
    workspace: Record<string, unknown>
    /** 实例级执行目录；未配置时按节点目录 / 全局默认解析。 */
    directory?: string
  }
  status: { state: string; revision: number }
  created_at?: string
  execution_profile_id?: string
  tasks: Record<string, {
    state: string
    attempt_count: number
    current_attempt?: string
    depends_on?: string[]
    error_message?: string
    waiting_reason?: string
    fail_count?: number
    retry_feedback?: RetryFeedback
  }>
  successor_of?: string
}

/** 验证失败回退时由 Aggregator 写给目标节点的反馈。 */
export interface RetryFeedback {
  round?: string
  attempt?: number
  max_attempts?: number
  at?: string
  failed_checks?: Array<{
    task: string
    error?: string
    evidence?: Record<string, unknown>
  }>
}

/** 一次真实执行（一个 attempt）的结果记录。 */
export interface TaskAttemptRecord {
  attempt_id: string
  state: string
  created_at: string
  created_by: string
  started_at: string
  ended_at: string
  duration_ms: number | null
  provider: string
  agent_id: string
  session_id: string
  output_excerpt: string
  waiting_reason: string
  failure_reason?: string
  workspace_binding?: {
    workspace_id: string
    binding_type: string
    writable: boolean | null
    bound_at: string
  } | null
}

export interface JobFlowArtifact {
  artifact_id: string
  type: string
  path: string
  size_bytes: number
  retention_until: string
  status: 'ACTIVE' | 'EXPIRED' | 'PURGED'
}

export interface JobFlowChangeSet {
  changeset_id: string
  instance_id?: string
  state?: string
  status?: string
  created_by?: string
  created_at: string
  applied_at?: string
  changed_tasks: string[]
  impacted_tasks?: string[]
  reuse?: Record<string, boolean>
  changes?: Record<string, unknown>
  successor_instance_id?: string
}

export interface JobFlowEvent {
  event_id: string
  type: string
  actor: string
  created_at: string
  data?: Record<string, unknown>
}

export type JobFlowValidation = {
  valid: boolean
  errors: Array<Record<string, unknown>>
  manifest_hash?: string
  task_count?: number
}

export async function listJobFlowTemplates(groupId?: string): Promise<JobFlowTemplate[]> {
  const query = groupId ? '?group_id=' + encodeURIComponent(groupId) : ''
  const result = await apiRequest<{ templates: JobFlowTemplate[] }>('/api/templates' + query)
  return result.templates
}

export async function deleteJobFlowTemplate(templateId: string): Promise<void> {
  await apiRequest<{ deleted: boolean }>(
    '/api/templates/' + encodeURIComponent(templateId), { method: 'DELETE' },
  )
}

export async function archiveJobFlowTemplate(templateId: string): Promise<JobFlowTemplate> {
  const result = await apiRequest<{ template: JobFlowTemplate }>(
    '/api/templates/' + encodeURIComponent(templateId) + '/archive', jsonRequest('POST', {}),
  )
  return result.template
}

export async function createJobFlowTemplate(input: {
  template_id?: string
  name: string
  description?: string
  tasks: JobFlowTask[]
  parameter_schema?: Record<string, unknown>
  group_id?: string
}): Promise<JobFlowTemplate> {
  const result = await apiRequest<{ template: JobFlowTemplate }>(
    '/api/templates', jsonRequest('POST', input),
  )
  return result.template
}

export async function importRalphFlowTemplate(input: {
  yaml: string
  name?: string
  group_id?: string
  agent?: string
  check_agent?: string
}): Promise<{ template: JobFlowTemplate; tasks: JobFlowTask[] }> {
  return apiRequest<{ template: JobFlowTemplate; tasks: JobFlowTask[] }>(
    '/api/templates/import-ralph-flow', jsonRequest('POST', input),
  )
}

export async function getJobFlowTemplate(templateId: string): Promise<JobFlowTemplate> {
  const result = await apiRequest<{ template: JobFlowTemplate }>(
    '/api/templates/' + encodeURIComponent(templateId),
  )
  return result.template
}

export async function updateJobFlowDraft(templateId: string, input: {
  expected_revision: number
  name?: string
  description?: string
  tasks?: JobFlowTask[]
  parameter_schema?: Record<string, unknown>
}): Promise<JobFlowTemplate> {
  const result = await apiRequest<{ template: JobFlowTemplate }>(
    '/api/templates/' + encodeURIComponent(templateId) + '/draft',
    jsonRequest('PATCH', input),
  )
  return result.template
}

export async function validateJobFlowTemplate(templateId: string) {
  const result = await apiRequest<{ validation: JobFlowValidation }>(
    '/api/templates/' + encodeURIComponent(templateId) + '/validate',
    jsonRequest('POST', {}),
  )
  return result.validation
}

export async function publishJobFlowTemplate(templateId: string, expectedRevision: number) {
  const result = await apiRequest<{ template: JobFlowTemplate; version: JobFlowVersion }>(
    '/api/templates/' + encodeURIComponent(templateId) + '/publish',
    jsonRequest('POST', { expected_revision: expectedRevision }, { 'Idempotency-Key': crypto.randomUUID() }),
  )
  return result
}

export async function listJobFlowInstances(templateId?: string, groupId?: string): Promise<JobFlowInstance[]> {
  const params = new URLSearchParams()
  if (templateId) params.set('template_id', templateId)
  if (groupId) params.set('group_id', groupId)
  const query = params.toString() ? '?' + params.toString() : ''
  const result = await apiRequest<{ instances: JobFlowInstance[] }>('/api/instances' + query)
  return result.instances
}

export type ExecutionProfileSummary = {
  profile_id: string
  group_id?: string
  name: string
  version: string
  agent_runtime_id: string
  status: string
  revision?: number
  workspace_policy?: Record<string, unknown>
  resource_budget?: Record<string, unknown>
  network_policy?: Record<string, unknown>
  secret_policy?: Record<string, unknown>
  skill_bundle_ids?: string[]
  mcp_grant_ids?: string[]
}

export async function listJobFlowExecutionProfiles(groupId?: string): Promise<ExecutionProfileSummary[]> {
  const query = groupId ? '?group_id=' + encodeURIComponent(groupId) : ''
  const result = await apiRequest<{ execution_profiles: ExecutionProfileSummary[] }>(
    '/api/execution-profiles' + query,
  )
  return result.execution_profiles
}

export async function getJobFlowExecutionProfile(profileId: string): Promise<ExecutionProfileSummary> {
  const result = await apiRequest<{ execution_profile: ExecutionProfileSummary }>(
    '/api/execution-profiles/' + encodeURIComponent(profileId),
  )
  return result.execution_profile
}

export async function createJobFlowExecutionProfile(input: {
  group_id: string
  name: string
  version?: string
  agent_runtime_id: string
}): Promise<ExecutionProfileSummary> {
  // 后端 POST 创建返回裸记录，GET 返回 {execution_profile} 包装，两者都兼容。
  const result = await apiRequest<ExecutionProfileSummary & { execution_profile?: ExecutionProfileSummary }>(
    '/api/execution-profiles', jsonRequest('POST', input),
  )
  return result.execution_profile ?? result
}

export async function updateJobFlowExecutionProfile(
  profileId: string,
  input: {
    expected_revision: number
    name?: string
    version?: string
    agent_runtime_id?: string
    workspace_policy?: Record<string, unknown>
    resource_budget?: Record<string, unknown>
    network_policy?: Record<string, unknown>
    secret_policy?: Record<string, unknown>
    skill_bundle_ids?: string[]
    mcp_grant_ids?: string[]
  },
): Promise<ExecutionProfileSummary> {
  const result = await apiRequest<ExecutionProfileSummary & { execution_profile?: ExecutionProfileSummary }>(
    '/api/execution-profiles/' + encodeURIComponent(profileId), jsonRequest('PUT', input),
  )
  return result.execution_profile ?? result
}

export async function listSkillBundles(): Promise<{ bundle_id: string; name: string; version: string }[]> {
  const result = await apiRequest<{ skill_bundles: { bundle_id: string; name: string; version: string }[] }>(
    '/api/skill-bundles',
  )
  return result.skill_bundles
}

export async function listMcpGrants(groupId: string): Promise<{ grant_id: string; server_id: string; status: string }[]> {
  const result = await apiRequest<{ mcp_grants: { grant_id: string; server_id: string; status: string }[] }>(
    '/api/mcp-grants/' + encodeURIComponent(groupId),
  )
  return result.mcp_grants
}

export async function listAgentRuntimes(): Promise<{ runtime_id: string; name: string; status: string; version?: string; adapter_id?: string }[]> {
  const result = await apiRequest<{ agent_runtimes: { runtime_id: string; name: string; status: string; version?: string; adapter_id?: string }[] }>(
    '/api/agent-runtimes',
  )
  return result.agent_runtimes
}

export async function registerAgentRuntime(input: {
  runtime_id?: string
  name: string
  version?: string
  adapter_id?: string
  acp_version?: string
  health_endpoint?: string
}): Promise<{ runtime_id: string; name: string; status: string }> {
  const result = await apiRequest<{ agent_runtime?: { runtime_id: string; name: string; status: string } } & { runtime_id: string; name: string; status: string }>(
    '/api/agent-runtimes', jsonRequest('POST', input),
  )
  return result.agent_runtime ?? result
}

export async function reportAgentRuntimeHealth(runtimeId: string, status: 'HEALTHY' | 'OFFLINE'): Promise<{ agent_runtime: { runtime_id: string; status: string } }> {
  return apiRequest<{ agent_runtime: { runtime_id: string; status: string } }>(
    '/api/agent-runtimes/' + encodeURIComponent(runtimeId) + '/health', jsonRequest('POST', { status }),
  )
}

export async function updateInstanceProfile(instanceId: string, profileId: string): Promise<JobFlowInstance> {
  const result = await apiRequest<{ instance: JobFlowInstance }>(
    '/api/instances/' + encodeURIComponent(instanceId), jsonRequest('PATCH', { execution_profile_id: profileId }),
  )
  return result.instance
}

export async function createJobFlowInstance(input: {
  template_id: string
  version_id?: string
  group_id?: string
  name: string
  parameters: Record<string, unknown>
  git: Record<string, unknown>
  workspace: Record<string, unknown>
  execution_profile_id?: string
  /** 实例级执行目录（绝对路径）；节点 acp.cwd / repo_path 可覆盖。 */
  directory?: string
}): Promise<JobFlowInstance> {
  const result = await apiRequest<{ instance: JobFlowInstance }>(
    '/api/instances', jsonRequest('POST', input, { 'Idempotency-Key': crypto.randomUUID() }),
  )
  return result.instance
}

export type PreflightCheck = {
  check_id: string
  status: 'PASS' | 'FAIL'
  detail: string
  reason?: string
  remediation?: string
}

export type PreflightSummary = {
  instance_id: string
  status: 'IDLE' | 'PASSED' | 'BLOCKED'
  checks: PreflightCheck[]
  manifest_id?: string | null
  attempt_id?: string
}

export async function getJobFlowPreflight(instanceId: string): Promise<PreflightSummary> {
  const result = await apiRequest<PreflightSummary>(
    '/api/instances/' + encodeURIComponent(instanceId) + '/preflight/latest',
  )
  return result
}

export async function runJobFlowPreflight(instanceId: string): Promise<PreflightSummary> {
  return apiRequest<PreflightSummary>(
    '/api/instances/' + encodeURIComponent(instanceId) + '/preflight',
    jsonRequest('POST', {}),
  )
}

/** 逐项重新预检：按契约调用 POST /api/instances/{id}/preflight，附 check_id */
export async function retryJobFlowPreflightCheck(instanceId: string, checkId: string): Promise<PreflightSummary> {
  return apiRequest<PreflightSummary>(
    '/api/instances/' + encodeURIComponent(instanceId) + '/preflight',
    jsonRequest('POST', { check_id: checkId }),
  )
}

export async function getJobFlowInstance(instanceId: string): Promise<JobFlowInstance> {
  const result = await apiRequest<{ instance: JobFlowInstance }>(
    '/api/instances/' + encodeURIComponent(instanceId),
  )
  return result.instance
}

export interface TaskSessionMapping {
  instance_id: string
  task: string
  provider: string
  session_id: string
  cwd: string
  attempt_id: string
  run_id: string
  state: string
  loadable: boolean
  missing: string[]
}

export async function getTaskSessionMapping(
  instanceId: string,
  taskId: string,
  attemptId?: string,
): Promise<TaskSessionMapping> {
  const query = attemptId ? '?attempt_id=' + encodeURIComponent(attemptId) : ''
  return apiRequest<TaskSessionMapping>(
    '/api/instances/' + encodeURIComponent(instanceId)
      + '/tasks/' + encodeURIComponent(taskId) + '/session' + query,
  )
}

export interface TaskLiveUpdate {
  seq: number
  ts: string
  attempt_id: string
  session_id: string
  payload: { update?: Record<string, unknown> } & Record<string, unknown>
}

export interface TaskLiveUpdates {
  instance_id: string
  task: string
  events: TaskLiveUpdate[]
  cursor: number
  active: boolean
  attempt_id: string
  session_id: string
  truncated: boolean
}

export async function getTaskLiveUpdates(
  instanceId: string,
  taskId: string,
  after = 0,
): Promise<TaskLiveUpdates> {
  return apiRequest<TaskLiveUpdates>(
    '/api/instances/' + encodeURIComponent(instanceId)
      + '/tasks/' + encodeURIComponent(taskId) + '/live?after=' + String(after),
  )
}

export async function listJobFlowChangesets(instanceId: string): Promise<JobFlowChangeSet[]> {
  const result = await apiRequest<{ changesets: JobFlowChangeSet[] }>(
    '/api/instances/' + encodeURIComponent(instanceId) + '/changesets',
  )
  return result.changesets ?? []
}

export async function applyJobFlowChangeset(
  instanceId: string,
  changesetId: string,
  input: Partial<JobFlowInstance['context']> = {},
) {
  return apiRequest<{ instance: JobFlowInstance; changeset: JobFlowChangeSet; successor_of: string }>(
    `/api/instances/${encodeURIComponent(instanceId)}/changesets/${encodeURIComponent(changesetId)}/apply`,
    jsonRequest('POST', input, { 'Idempotency-Key': crypto.randomUUID() }),
  )
}

export async function listJobFlowInstanceEvents(instanceId: string): Promise<JobFlowEvent[]> {
  const result = await apiRequest<{ events: JobFlowEvent[] }>(
    '/api/instances/' + encodeURIComponent(instanceId) + '/events',
  )
  return result.events ?? []
}

export async function getJobFlowAttemptManifest(attemptId: string): Promise<Record<string, unknown>> {
  return apiRequest<Record<string, unknown>>(
    '/api/attempts/' + encodeURIComponent(attemptId) + '/manifest',
  )
}

export async function listJobFlowInstanceArtifacts(instanceId: string): Promise<JobFlowArtifact[]> {
  const result = await apiRequest<{ artifacts: JobFlowArtifact[] }>(
    '/api/instances/' + encodeURIComponent(instanceId) + '/artifacts',
  )
  return result.artifacts ?? []
}

export async function deleteJobFlowInstance(instanceId: string): Promise<void> {
  await apiRequest<{ deleted: boolean }>(
    '/api/instances/' + encodeURIComponent(instanceId), { method: 'DELETE' },
  )
}

export async function startJobFlowInstance(instanceId: string) {
  return apiRequest<{ instance: JobFlowInstance; run?: Record<string, unknown> }>(
    '/api/instances/' + encodeURIComponent(instanceId) + '/start', jsonRequest('POST', {}),
  )
}

export async function retryJobFlowTask(instanceId: string, taskId: string) {
  return apiRequest<{ task: Record<string, unknown> }>(
    `/api/instances/${encodeURIComponent(instanceId)}/tasks/${encodeURIComponent(taskId)}/retry`,
    jsonRequest('POST', {}),
  )
}

/** 某任务的全部 attempt（含已完成/失败状态与失败原因）。 */
export async function listJobFlowTaskAttempts(
  instanceId: string,
  taskId: string,
): Promise<TaskAttemptRecord[]> {
  const result = await apiRequest<{ attempts: TaskAttemptRecord[] }>(
    `/api/instances/${encodeURIComponent(instanceId)}/tasks/${encodeURIComponent(taskId)}/attempts`,
  )
  return result.attempts ?? []
}

/** 整个实例的执行过程：每个节点一条记录，内含逐轮 attempt。 */
export interface InstanceExecutionTask {
  task_id: string
  state: string
  attempt_count: number
  fail_count: number
  retry_feedback?: RetryFeedback
  error_message?: string
  depends_on?: string[]
  attempts: TaskAttemptRecord[]
}

export async function listJobFlowInstanceAttempts(
  instanceId: string,
): Promise<InstanceExecutionTask[]> {
  const result = await apiRequest<{ tasks: InstanceExecutionTask[] }>(
    `/api/instances/${encodeURIComponent(instanceId)}/attempts`,
  )
  return result.tasks ?? []
}

export async function approveJobFlowTask(instanceId: string, taskId: string) {
  return apiRequest<{ instance: JobFlowInstance; approved: boolean }>(
    `/api/instances/${encodeURIComponent(instanceId)}/tasks/${encodeURIComponent(taskId)}/approve`,
    jsonRequest('POST', {}),
  )
}

/**
 * 从当前节点重来：下游闭包（含已完成）全部重置。
 * 与 retry 的区别是 retry 只补跑未完成的下游。
 */
export async function rewindJobFlowTask(
  instanceId: string,
  taskId: string,
  options: { reason?: string; resetDownstream?: boolean } = {},
) {
  return apiRequest<{ instance: JobFlowInstance; rewind_id: string; rewound_tasks: string[] }>(
    `/api/instances/${encodeURIComponent(instanceId)}/tasks/${encodeURIComponent(taskId)}/rewind`,
    jsonRequest('POST', {
      reason: options.reason || '',
      reset_downstream: options.resetDownstream ?? true,
    }),
  )
}

export async function controlJobFlowInstance(instanceId: string, action: 'pause' | 'abort' | 'drain' | 'archive') {
  return apiRequest<{ instance: JobFlowInstance }>(
    '/api/instances/' + encodeURIComponent(instanceId) + '/' + action, jsonRequest('POST', {}),
  )
}

export async function rerunJobFlowInstance(instanceId: string, input: Partial<JobFlowInstance['context']> = {}) {
  return apiRequest<{ instance: JobFlowInstance; successor_of: string }>(
    '/api/instances/' + encodeURIComponent(instanceId) + '/rerun',
    jsonRequest('POST', input, { 'Idempotency-Key': crypto.randomUUID() }),
  )
}

export async function listJobFlowTemplateVersions(templateId: string): Promise<JobFlowVersion[]> {
  const result = await apiRequest<{ versions: JobFlowVersion[] }>(
    '/api/templates/' + encodeURIComponent(templateId) + '/versions',
  )
  return result.versions
}

export async function getJobFlowTemplateVersion(templateId: string, versionId: string) {
  return apiRequest<{ version: JobFlowVersion; manifest: { tasks: JobFlowTask[] } }>(
    `/api/templates/${encodeURIComponent(templateId)}/versions/${encodeURIComponent(versionId)}`,
  )
}

export type VersionDiff = {
  template_id: string
  from_version_id: string
  to_version_id: string
  added_tasks: string[]
  removed_tasks: string[]
  changed_tasks: Array<{ task_id: string; fields: Record<string, { old: unknown; new: unknown }> }>
  summary: string
}

export async function diffJobFlowVersions(
  templateId: string,
  fromVersionId: string,
  toVersionId: string,
): Promise<VersionDiff> {
  return apiRequest<VersionDiff>(
    `/api/templates/${encodeURIComponent(templateId)}/versions/${encodeURIComponent(toVersionId)}/diff` +
      `?from_version_id=${encodeURIComponent(fromVersionId)}`,
  )
}
