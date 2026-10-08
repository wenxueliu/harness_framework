<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import AppShell from '@/layouts/AppShell.vue'
import DagGraph from '@/components/DagGraph.vue'
import PreflightPanel, { type PreflightResult } from '@/components/PreflightPanel.vue'
import ManifestViewer from '@/components/ManifestViewer.vue'
import ChangeSetPanel from '@/components/ChangeSetPanel.vue'
import WorkspaceCard from '@/components/WorkspaceCard.vue'
import ArtifactList, { type ArtifactItem } from '@/components/ArtifactList.vue'
import type { Task } from '@/api/types'
import {
  controlJobFlowInstance,
  deleteJobFlowInstance,
  getJobFlowInstance,
  getJobFlowPreflight,
  rerunJobFlowInstance,
  runJobFlowPreflight,
  startJobFlowInstance,
  type JobFlowInstance,
} from '@/api/jobFlow'

const route = useRoute()
const router = useRouter()
const instance = ref<JobFlowInstance | null>(null)
const error = ref('')
const rerunning = ref(false)
const controlling = ref(false)
const editing = ref(false)
const gitRef = ref('')
const parameterText = ref('{}')
const instanceId = String(route.params.instanceId || '')
const deletableStates = new Set(['QUEUED', 'SUCCEEDED', 'FAILED', 'ABORTED', 'SUPERSEDED', 'ARCHIVED'])

const TASK_STATE_MAP: Record<string, Task['status']> = {
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

const dagTasks = ref<Record<string, Task>>({})
const preflight = ref<PreflightResult | null>(null)
const manifest = ref<Record<string, unknown> | null>(null)
const artifactList = ref<ArtifactItem[]>([])
const activeTab = ref('preflight')

function buildDagTasks() {
  const record: Record<string, Task> = {}
  for (const [taskId, task] of Object.entries(instance.value?.tasks ?? {})) {
    record[taskId] = {
      id: taskId,
      name: taskId,
      status: TASK_STATE_MAP[task.state] ?? 'UNKNOWN',
      raw_status: task.state,
      assigned_agent: '',
      depends_on: task.depends_on ?? [],
      last_updated: '',
    }
  }
  dagTasks.value = record
}

async function loadCapability() {
  try {
    const latest = await getJobFlowPreflight(instanceId)
    preflight.value = latest.status === 'IDLE'
      ? await runJobFlowPreflight(instanceId)
      : latest
    if (preflight.value?.attempt_id) {
      const response = await fetch(`/api/attempts/${encodeURIComponent(preflight.value.attempt_id)}/manifest`)
      if (response.ok) manifest.value = await response.json()
    }
  } catch { /* offline */ }
  try {
    const res = await fetch(`/api/instances/${instanceId}/artifacts`)
    if (res.ok) { const data = await res.json(); artifactList.value = data.artifacts ?? [] }
  } catch { /* offline */ }
}

async function start() {
  controlling.value = true
  try {
    await startJobFlowInstance(instanceId)
    await Promise.all([load(), loadCapability()])
  }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '实例启动失败' }
  finally { controlling.value = false }
}

async function control(action: 'pause' | 'abort' | 'drain' | 'archive') {
  controlling.value = true
  try {
    await controlJobFlowInstance(instanceId, action)
    await load()
  }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '实例操作失败' }
  finally { controlling.value = false }
}

async function load() {
  try {
    instance.value = await getJobFlowInstance(instanceId)
    buildDagTasks()
    gitRef.value = String(instance.value.context.git?.ref || '')
    parameterText.value = JSON.stringify(instance.value.context.parameters || {}, null, 2)
  }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '实例加载失败' }
}
async function rerun() {
  rerunning.value = true
  try {
    const parameters = JSON.parse(parameterText.value) as Record<string, unknown>
    await rerunJobFlowInstance(instanceId, { parameters, git: { ...instance.value?.context.git, ref: gitRef.value } })
    editing.value = false
    await load()
  }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '重执行失败' }
  finally { rerunning.value = false }
}
async function removeInstance() {
  if (!instance.value || !deletableStates.has(instance.value.status.state)) return
  if (!window.confirm(`确定删除实例“${instance.value.name}”吗？删除后实例日志、任务和执行记录将不可恢复。`)) return
  try {
    await deleteJobFlowInstance(instanceId)
    await router.push('/instances')
  }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '实例删除失败' }
}
onMounted(() => { load(); loadCapability() })
</script>

<template>
  <AppShell :title="instance?.name || '作业流实例'" eyebrow="Instance Detail">
    <template #actions>
      <div class="flex flex-wrap gap-2">
        <button data-testid="start-instance" class="rounded bg-emerald-500 px-3 py-2 text-xs text-white disabled:cursor-not-allowed disabled:opacity-40" :disabled="controlling || !['QUEUED','PAUSED','WAITING_FOR_CAPABILITY'].includes(instance?.status.state || '')" @click="start">启动</button>
        <button data-testid="pause-instance" class="rounded border border-border px-3 py-2 text-xs disabled:opacity-40" :disabled="controlling || instance?.status.state !== 'RUNNING'" @click="control('pause')">暂停</button>
        <button data-testid="abort-instance" class="rounded border border-red-400/50 px-3 py-2 text-xs text-red-300 disabled:opacity-40" :disabled="controlling || !['QUEUED','RUNNING','PAUSED','DRAINING'].includes(instance?.status.state || '')" @click="control('abort')">中止</button>
        <button data-testid="drain-instance" class="rounded border border-border px-3 py-2 text-xs disabled:opacity-40" :disabled="controlling || !['QUEUED','RUNNING','PAUSED'].includes(instance?.status.state || '')" @click="control('drain')">Drain</button>
        <button data-testid="rerun-instance" class="rounded bg-blue-500 px-3 py-2 text-xs text-white" :disabled="rerunning" @click="editing = !editing">修改并重新执行</button>
        <button data-testid="delete-instance" class="rounded border border-red-400/50 px-3 py-2 text-xs text-red-300 disabled:cursor-not-allowed disabled:opacity-40" :disabled="!deletableStates.has(instance?.status.state || '')" title="只有排队中或终态实例可以删除" @click="removeInstance">删除实例</button>
      </div>
    </template>
    <div v-if="error" role="alert" class="m-6 rounded bg-red-400/10 p-4 text-sm text-red-300">{{ error }}</div>
    <div v-else-if="instance" class="mx-auto max-w-6xl space-y-4 p-4 sm:p-6">
      <nav data-testid="instance-breadcrumb" class="flex items-center gap-1.5 text-xs text-muted-foreground">
        <RouterLink to="/templates" class="hover:text-foreground">{{ instance.group_id || 'unassigned' }}</RouterLink>
        <span>›</span>
        <RouterLink :to="`/templates/${instance.template_id}`" class="hover:text-foreground">{{ instance.template_id }}</RouterLink>
        <span>›</span>
        <span class="font-mono">{{ instance.version_id }}</span>
        <span>›</span>
        <span class="text-foreground">{{ instance.name }}</span>
      </nav>
      <section class="grid gap-3 md:grid-cols-5">
        <div class="rounded border border-border bg-card p-3"><p class="text-[11px] text-muted-foreground">状态</p><p class="mt-1 font-semibold">{{ instance.status.state }}</p></div>
        <div class="rounded border border-border bg-card p-3"><p class="text-[11px] text-muted-foreground">模板版本</p><p class="mt-1 font-mono text-xs">{{ instance.version_id }}</p></div>
        <div class="rounded border border-border bg-card p-3"><p class="text-[11px] text-muted-foreground">所属项目组</p><p class="mt-1 font-mono text-xs">{{ instance.group_id || 'unassigned' }}</p></div>
        <div class="rounded border border-border bg-card p-3"><p class="text-[11px] text-muted-foreground">Git</p><p class="mt-1 font-mono text-xs">{{ instance.context.git?.ref || '未指定' }}</p></div>
        <div class="rounded border border-border bg-card p-3"><p class="text-[11px] text-muted-foreground">Workspace</p><p class="mt-1 font-mono text-xs">{{ instance.context.workspace?.workspace_id || '未指定' }}</p></div>
      </section>
      <section class="rounded-lg border border-border bg-card p-4">
        <h2 class="text-sm font-semibold mb-2">运行 DAG</h2>
        <div class="rounded border border-border overflow-hidden min-h-[300px]" data-testid="instance-dag">
          <DagGraph v-if="Object.keys(dagTasks).length" :tasks="dagTasks" />
          <p v-else class="p-6 text-center text-xs text-muted-foreground">暂无任务节点</p>
        </div>
      </section>
      <section class="rounded-lg border border-border bg-card p-4">
        <h2 class="text-sm font-semibold mb-2">Workspace</h2>
        <WorkspaceCard
          :name="String(instance.context.workspace?.workspace_id ?? '未指定')"
          :isolation-level="String(instance.context.workspace?.strategy ?? 'ORIGINAL')"
          :status="['SUCCEEDED','FAILED','ABORTED','SUPERSEDED'].includes(instance.status.state) ? 'RETAINED' : 'ACTIVE'"
          :created-at="String(instance.context.workspace?.created_at ?? '')"
          :retention-days="7"
          :is-terminal="['SUCCEEDED','FAILED','ABORTED','SUPERSEDED'].includes(instance.status.state)"
        />
      </section>
      <section class="rounded-lg border border-border bg-card p-4">
        <div class="flex gap-2 border-b border-border mb-3">
          <button v-for="tab in ['preflight','manifest','artifacts','changeset']" :key="tab"
            class="px-3 py-1.5 text-xs transition"
            :class="activeTab === tab ? 'border-b-2 border-blue-400 text-blue-300 font-medium' : 'text-muted-foreground'"
            @click="activeTab = tab">{{ tab }}</button>
        </div>
        <PreflightPanel v-if="activeTab === 'preflight'" :result="preflight" @rerun="loadCapability" />
        <ManifestViewer v-if="activeTab === 'manifest'" :manifest="manifest" />
        <ArtifactList v-if="activeTab === 'artifacts'" :artifacts="artifactList" />
        <ChangeSetPanel v-if="activeTab === 'changeset'" :changeset="null" />
      </section>
      <section class="rounded-lg border border-border bg-card p-4">
        <h2 class="text-sm font-semibold">实例任务</h2>
        <div class="mt-3 divide-y divide-border">
          <RouterLink v-for="(task, taskId) in instance.tasks" :key="taskId" :to="'/instances/' + encodeURIComponent(instance.instance_id) + '/tasks/' + encodeURIComponent(String(taskId))" data-testid="instance-task" class="flex items-center justify-between py-3 hover:text-blue-300">
            <span class="font-mono text-xs">{{ taskId }}</span><span class="text-xs text-muted-foreground">{{ task.state }} · {{ task.attempt_count }} 次尝试</span>
          </RouterLink>
        </div>
      </section>
      <section v-if="editing" data-testid="instance-edit-panel" class="rounded-lg border border-blue-400/40 bg-card p-4">
        <h2 class="text-sm font-semibold">创建后继实例</h2>
        <p class="mt-1 text-xs text-muted-foreground">修改只影响后继实例；当前实例会先进入排空状态。</p>
        <label class="mt-3 block text-xs text-muted-foreground">Git 分支 / ref<input v-model="gitRef" data-testid="instance-git-ref" class="mt-1 w-full rounded border border-input bg-background px-3 py-2 font-mono text-xs" /></label>
        <label class="mt-3 block text-xs text-muted-foreground">输入参数 JSON<textarea v-model="parameterText" data-testid="instance-parameters" class="mt-1 min-h-28 w-full rounded border border-input bg-background p-2 font-mono text-xs" /></label>
        <button data-testid="confirm-rerun" class="mt-3 rounded bg-blue-500 px-3 py-2 text-xs text-white" :disabled="rerunning" @click="rerun">创建后继并执行</button>
      </section>
    </div>
  </AppShell>
</template>
