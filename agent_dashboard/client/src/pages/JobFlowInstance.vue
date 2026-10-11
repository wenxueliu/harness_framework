<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import AppShell from '@/layouts/AppShell.vue'
import DagGraph from '@/components/DagGraph.vue'
import PreflightPanel, { type PreflightResult } from '@/components/PreflightPanel.vue'
import ManifestViewer from '@/components/ManifestViewer.vue'
import ChangeSetPanel from '@/components/ChangeSetPanel.vue'
import WorkspaceCard from '@/components/WorkspaceCard.vue'
import ArtifactList from '@/components/ArtifactList.vue'
import { HarnessApiError } from '@/api/client'
import type { Task } from '@/api/types'
import { useCapabilityStore } from '@/stores/capabilities'
import { getWorkspaceDetail, updateWorkspace, type WorkspaceRecord } from '@/api/dashboard'
import {
  controlJobFlowInstance,
  deleteJobFlowInstance,
  getJobFlowAttemptManifest,
  getJobFlowInstance,
  getJobFlowPreflight,
  listJobFlowInstanceArtifacts,
  rerunJobFlowInstance,
  runJobFlowPreflight,
  retryJobFlowPreflightCheck,
  startJobFlowInstance,
  getJobFlowTemplate,
  listJobFlowExecutionProfiles,
  updateInstanceProfile,
  listJobFlowChangesets,
  applyJobFlowChangeset,
  listJobFlowInstanceEvents,
  type JobFlowInstance,
  type JobFlowArtifact,
  type JobFlowChangeSet,
  type JobFlowEvent,
} from '@/api/jobFlow'

const route = useRoute()
const router = useRouter()
const instance = ref<JobFlowInstance | null>(null)
const templateName = ref('')
const editingProfile = ref(false)
const selectedProfileId = ref('')
const profileOptions = ref<{ profile_id: string; name: string; version: string }[]>([])
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
const artifactList = ref<JobFlowArtifact[]>([])
const changesets = ref<JobFlowChangeSet[]>([])
const changesetsLoading = ref(false)
const changesetError = ref('')
const applyingChangesetId = ref('')
const events = ref<JobFlowEvent[]>([])
const eventsLoading = ref(false)
const eventFilter = ref('')
const activeTab = ref('preflight')
const deleteDialogOpen = ref(false)
const deleting = ref(false)
const capabilities = useCapabilityStore()
const retryingCheckId = ref('')
const workspaceDetail = ref<WorkspaceRecord | null>(null)
const extendingRetention = ref(false)

const TAB_ITEMS = [
  { key: 'preflight', label: '预检' },
  { key: 'manifest', label: 'Manifest' },
  { key: 'artifacts', label: '产物' },
  { key: 'changeset', label: 'ChangeSet' },
  { key: 'events', label: '审计时间线' },
]
const TAB_KEYS = TAB_ITEMS.map((item) => item.key)

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
    if (instance.value?.group_id) {
      profileOptions.value = await listJobFlowExecutionProfiles(instance.value.group_id)
    }
    const latest = await getJobFlowPreflight(instanceId)
    preflight.value = latest.status === 'IDLE'
      ? await runJobFlowPreflight(instanceId)
      : latest
    if (preflight.value?.attempt_id) {
      manifest.value = await getJobFlowAttemptManifest(preflight.value.attempt_id)
    }
  } catch { /* offline */ }
  try {
    artifactList.value = await listJobFlowInstanceArtifacts(instanceId)
  } catch { /* offline */ }
  void loadWorkspaceDetail()
}

async function loadWorkspaceDetail() {
  const workspaceId = String(instance.value?.context?.workspace?.workspace_id || '')
  if (!workspaceId || workspaceId === '未指定') { workspaceDetail.value = null; return }
  try { workspaceDetail.value = await getWorkspaceDetail(workspaceId) }
  catch { workspaceDetail.value = null }
}

async function retryCheck(checkId: string) {
  retryingCheckId.value = checkId
  try {
    preflight.value = await retryJobFlowPreflightCheck(instanceId, checkId)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '预检重试失败'
  } finally { retryingCheckId.value = '' }
}

const canExtendRetention = computed(() =>
  capabilities.permitted('workspace:policy') && Boolean(workspaceDetail.value?.revision))
const extendDisabledReason = computed(() => {
  if (!capabilities.permitted('workspace:policy')) return '仅 Admin 可延长保留'
  if (!workspaceDetail.value) return 'Workspace 保留数据不可得'
  if (!workspaceDetail.value.revision) return '缺少 Workspace 版本信息'
  return ''
})

async function extendRetention() {
  const detail = workspaceDetail.value
  if (!detail?.revision) return
  extendingRetention.value = true
  try {
    const current = detail.retention_until ? new Date(detail.retention_until).getTime() : 0
    const base = Math.max(current, Date.now())
    const next = new Date(base + 7 * 24 * 3_600_000)
    workspaceDetail.value = await updateWorkspace(detail.workspace_id, {
      expected_revision: detail.revision,
      retention_until: next.toISOString(),
    })
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '延长保留失败'
  } finally { extendingRetention.value = false }
}

async function loadChangesets() {
  changesetsLoading.value = true
  changesetError.value = ''
  try {
    changesets.value = await listJobFlowChangesets(instanceId)
  } catch (cause) {
    changesetError.value = cause instanceof HarnessApiError && cause.status === 404
      ? '后端尚未提供 ChangeSet 列表接口'
      : cause instanceof Error ? cause.message : 'ChangeSet 加载失败'
  } finally {
    changesetsLoading.value = false
  }
}

async function loadEvents() {
  eventsLoading.value = true
  try {
    events.value = await listJobFlowInstanceEvents(instanceId)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '审计记录加载失败'
  } finally {
    eventsLoading.value = false
  }
}

function loadTab(tab: string) {
  activeTab.value = tab
  if (tab === 'changeset') void loadChangesets()
  if (tab === 'events') void loadEvents()
}

async function confirmChangeset(changeset: JobFlowChangeSet) {
  applyingChangesetId.value = changeset.changeset_id
  changesetError.value = ''
  try {
    await applyJobFlowChangeset(instanceId, changeset.changeset_id, {
      parameters: instance.value?.context.parameters ?? {},
      git: instance.value?.context.git ?? {},
      workspace: instance.value?.context.workspace ?? {},
    })
    await Promise.all([load(), loadChangesets(), loadEvents()])
  } catch (cause) {
    changesetError.value = cause instanceof Error ? cause.message : 'ChangeSet 应用失败'
  } finally {
    applyingChangesetId.value = ''
  }
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

async function bindProfile() {
  if (!instance.value || !selectedProfileId.value) return
  controlling.value = true
  try {
    instance.value = await updateInstanceProfile(instanceId, selectedProfileId.value)
    editingProfile.value = false
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '绑定 Profile 失败' }
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
    if (instance.value.template_id) {
      try {
        const tpl = await getJobFlowTemplate(instance.value.template_id)
        templateName.value = tpl.name
      } catch { /* template may have been deleted */ }
    }
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
  deleteDialogOpen.value = true
}

async function confirmRemoveInstance() {
  deleting.value = true
  try {
    await deleteJobFlowInstance(instanceId)
    deleteDialogOpen.value = false
    await router.push('/instances')
  }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '实例删除失败' }
  finally { deleting.value = false }
}

const filteredEvents = computed(() => {
  const keyword = eventFilter.value.trim().toLowerCase()
  if (!keyword) return events.value
  return events.value.filter((event) =>
    [event.type, event.actor, JSON.stringify(event.data ?? {})]
      .join(' ').toLowerCase().includes(keyword),
  )
})

function enterTask(task: Task) {
  void router.push('/instances/' + encodeURIComponent(instanceId) + '/tasks/' + encodeURIComponent(task.id))
}

function applyTabFromQuery() {
  const tab = route.query.tab
  if (typeof tab === 'string' && TAB_KEYS.includes(tab) && tab !== activeTab.value) loadTab(tab)
}
onMounted(() => {
  applyTabFromQuery()
  load()
  loadCapability()
})
watch(() => route.query.tab, applyTabFromQuery)
watch(() => instance.value?.group_id, (groupId) => {
  if (groupId) capabilities.load(groupId).catch(() => { /* 权限快照不可用时按钮按无权限隐藏 */ })
}, { immediate: true })
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
        <RouterLink :to="`/templates/${instance.template_id}`" class="text-blue-300 hover:underline">
          {{ templateName || instance.template_id }}
          <span class="font-mono text-[11px] text-muted-foreground">{{ instance.template_id }}</span>
        </RouterLink>
        <span>›</span>
        <span class="font-mono">{{ instance.version_id }}</span>
        <span>›</span>
        <span class="text-foreground">{{ instance.name }}</span>
        <RouterLink v-if="instance.template_id" :to="`/templates/${instance.template_id}`"
          class="ml-3 rounded border border-border px-2 py-1 text-[11px] text-blue-300 hover:bg-accent">查看模板</RouterLink>
      </nav>
      <section class="grid gap-3 md:grid-cols-5">
        <div class="rounded border border-border bg-card p-3"><p class="text-[11px] text-muted-foreground">状态</p><p class="mt-1 font-semibold">{{ instance.status.state }}</p></div>
        <div class="rounded border border-border bg-card p-3"><p class="text-[11px] text-muted-foreground">模板版本</p><p class="mt-1 font-mono text-xs">{{ instance.version_id }}</p></div>
        <div class="rounded border border-border bg-card p-3"><p class="text-[11px] text-muted-foreground">所属项目组</p><p class="mt-1 font-mono text-xs">{{ instance.group_id || 'unassigned' }}</p></div>
        <div class="rounded border border-border bg-card p-3"><p class="text-[11px] text-muted-foreground">Git</p><p class="mt-1 font-mono text-xs">{{ instance.context.git?.ref || '未指定' }}</p></div>
        <div class="rounded border border-border bg-card p-3"><p class="text-[11px] text-muted-foreground">Workspace</p><p class="mt-1 font-mono text-xs">{{ instance.context.workspace?.workspace_id || '未指定' }}</p></div>
      </section>
      <section v-if="['QUEUED', 'WAITING_FOR_CAPABILITY'].includes(instance.status.state)" class="rounded-lg border border-border bg-card p-4">
        <div class="flex items-center justify-between">
          <h2 class="text-sm font-semibold">Execution Profile</h2>
          <button v-if="!editingProfile" class="rounded border border-border px-2 py-1 text-xs text-blue-300 hover:bg-accent" @click="editingProfile = true; selectedProfileId = instance.execution_profile_id || ''">绑定 Profile</button>
        </div>
        <template v-if="editingProfile">
          <select v-model="selectedProfileId" class="mt-2 w-full rounded border border-border bg-background p-2 text-xs">
            <option value="" disabled>选择 Execution Profile</option>
            <option v-for="p in profileOptions" :key="p.profile_id" :value="p.profile_id">{{ p.name }} v{{ p.version }}</option>
          </select>
          <div class="mt-2 flex gap-2">
            <button class="rounded bg-blue-500 px-3 py-1.5 text-xs text-white disabled:opacity-40" :disabled="!selectedProfileId || controlling" @click="bindProfile">确认绑定</button>
            <button class="rounded border border-border px-3 py-1.5 text-xs" @click="editingProfile = false">取消</button>
          </div>
        </template>
        <template v-else>
          <p class="mt-1 text-xs text-muted-foreground">{{ instance.execution_profile_id || '未绑定 Profile' }}</p>
        </template>
      </section>
      <section class="rounded-lg border border-border bg-card p-4">
        <h2 class="text-sm font-semibold mb-2">运行 DAG</h2>
        <div class="rounded border border-border overflow-hidden min-h-[300px]" data-testid="instance-dag">
          <DagGraph v-if="Object.keys(dagTasks).length" :tasks="dagTasks" :on-task-click="enterTask" />
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
          :cleanup-at="workspaceDetail?.retention_until ?? null"
          :can-manage="capabilities.permitted('workspace:policy')"
          :extend-disabled="!canExtendRetention || extendingRetention"
          :extend-disabled-reason="extendDisabledReason"
          @extend="extendRetention"
        />
      </section>
      <section class="rounded-lg border border-border bg-card p-4">
        <div class="flex gap-2 border-b border-border mb-3">
          <button v-for="tab in TAB_ITEMS" :key="tab.key"
            class="px-3 py-1.5 text-xs transition"
            :class="activeTab === tab.key ? 'border-b-2 border-blue-400 text-blue-300 font-medium' : 'text-muted-foreground'"
            @click="loadTab(tab.key)">{{ tab.label }}</button>
        </div>
        <PreflightPanel
          v-if="activeTab === 'preflight'"
          :result="preflight"
          :retrying-check-id="retryingCheckId"
          :can-skip="instance.status.state === 'WAITING_FOR_CAPABILITY' && capabilities.permitted('run:control')"
          :skipping="controlling"
          @rerun="loadCapability"
          @retry-check="retryCheck"
          @skip-start="start"
        />
        <ManifestViewer v-if="activeTab === 'manifest'" :manifest="manifest" :instance-id="instanceId" />
        <ArtifactList v-if="activeTab === 'artifacts'" :artifacts="artifactList" />
        <ChangeSetPanel
          v-if="activeTab === 'changeset'"
          :changesets="changesets"
          :loading="changesetsLoading"
          :applying-id="applyingChangesetId"
          :error="changesetError"
          @confirm="confirmChangeset"
        />
        <div v-if="activeTab === 'events'" data-testid="audit-timeline" class="rounded-lg border border-border bg-card p-4">
          <div class="flex items-center gap-2">
            <h4 class="text-sm font-medium">审计时间线</h4>
            <input v-model="eventFilter" data-testid="audit-filter" class="ml-auto rounded border border-border bg-background px-2 py-1 text-xs" placeholder="按类型/操作人筛选" />
          </div>
          <div v-if="eventsLoading" class="mt-3 animate-pulse text-xs text-muted-foreground">正在加载审计记录…</div>
          <div v-else-if="!filteredEvents.length" class="mt-3 text-xs text-muted-foreground">暂无审计记录</div>
          <ol v-else class="mt-3 space-y-3 border-l border-border pl-4">
            <li v-for="event in filteredEvents" :key="event.event_id" data-testid="audit-event" class="relative text-xs">
              <span class="absolute -left-[1.28rem] top-1 h-2 w-2 rounded-full bg-blue-400" aria-hidden="true" />
              <div class="font-medium">{{ event.type }}</div>
              <div class="text-muted-foreground">{{ event.created_at }} · {{ event.actor }}</div>
              <pre v-if="event.data && Object.keys(event.data).length" class="mt-1 overflow-auto rounded bg-background p-2 text-[10px]">{{ JSON.stringify(event.data, null, 2) }}</pre>
            </li>
          </ol>
        </div>
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
    <dialog
      :open="deleteDialogOpen"
      class="fixed inset-0 z-50 m-0 h-screen w-screen max-h-none max-w-none border-0 bg-black/60 p-0"
      @click.self="deleteDialogOpen = false"
    >
      <div class="fixed inset-0 flex items-center justify-center p-4" @click.self="deleteDialogOpen = false">
        <div v-if="instance" class="w-full max-w-md space-y-4 rounded-xl border border-border bg-card p-6" role="alertdialog" aria-modal="true" aria-labelledby="delete-instance-title">
          <h2 id="delete-instance-title" class="text-base font-semibold text-red-300">确认删除实例</h2>
          <p class="text-sm text-muted-foreground">
            将永久删除实例 <span class="font-mono text-foreground">{{ instance.name }}</span> 及其任务、审计记录和执行数据。此操作不可撤销。
          </p>
          <div class="flex gap-3">
            <button data-testid="cancel-delete-instance" class="flex-1 rounded border border-border px-4 py-2 text-sm hover:bg-accent" @click="deleteDialogOpen = false">取消</button>
            <button data-testid="confirm-delete-instance" class="flex-1 rounded bg-red-600 px-4 py-2 text-sm text-white disabled:opacity-50" :disabled="deleting" @click="confirmRemoveInstance">确认删除</button>
          </div>
        </div>
      </div>
    </dialog>
  </AppShell>
</template>
