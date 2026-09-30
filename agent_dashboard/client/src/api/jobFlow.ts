import { apiRequest, jsonRequest } from './client'

export interface JobFlowTask {
  id: string
  name?: string
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
  current_version_id?: string | null
  draft?: { tasks: JobFlowTask[]; parameters?: Record<string, unknown>; manifest_hash?: string }
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
  }
  status: { state: string; revision: number }
  tasks: Record<string, { state: string; attempt_count: number; current_attempt?: string }>
  successor_of?: string
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

export async function createJobFlowTemplate(input: {
  template_id?: string
  name: string
  description?: string
  tasks: JobFlowTask[]
  group_id?: string
}): Promise<JobFlowTemplate> {
  const result = await apiRequest<{ template: JobFlowTemplate }>(
    '/api/templates', jsonRequest('POST', input),
  )
  return result.template
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
}): Promise<JobFlowTemplate> {
  const result = await apiRequest<{ template: JobFlowTemplate }>(
    '/api/templates/' + encodeURIComponent(templateId) + '/draft',
    jsonRequest('PATCH', input),
  )
  return result.template
}

export async function validateJobFlowTemplate(templateId: string) {
  const result = await apiRequest<{ validation: { valid: boolean; errors: Array<{ message: string }> } }>(
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

export async function createJobFlowInstance(input: {
  template_id: string
  version_id?: string
  group_id?: string
  name: string
  parameters: Record<string, unknown>
  git: Record<string, unknown>
  workspace: Record<string, unknown>
}): Promise<JobFlowInstance> {
  const result = await apiRequest<{ instance: JobFlowInstance }>(
    '/api/instances', jsonRequest('POST', input, { 'Idempotency-Key': crypto.randomUUID() }),
  )
  return result.instance
}

export async function getJobFlowInstance(instanceId: string): Promise<JobFlowInstance> {
  const result = await apiRequest<{ instance: JobFlowInstance }>(
    '/api/instances/' + encodeURIComponent(instanceId),
  )
  return result.instance
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

export async function rerunJobFlowInstance(instanceId: string, input: Partial<JobFlowInstance['context']> = {}) {
  return apiRequest<{ instance: JobFlowInstance; successor_of: string }>(
    '/api/instances/' + encodeURIComponent(instanceId) + '/rerun',
    jsonRequest('POST', input, { 'Idempotency-Key': crypto.randomUUID() }),
  )
}
