<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '@/layouts/AppShell.vue'
import DagGraph from '@/components/DagGraph.vue'
import PublishDialog from '@/components/PublishDialog.vue'
import { useCapabilityStore } from '@/stores/capabilities'
import type { Task } from '@/api/types'
import {
  diffJobFlowVersions, getJobFlowTemplate, getJobFlowTemplateVersion,
  listJobFlowTemplateVersions, listJobFlowInstances,
  archiveJobFlowTemplate, deleteJobFlowTemplate, publishJobFlowTemplate,
  validateJobFlowTemplate,
  type JobFlowTemplate, type JobFlowVersion, type JobFlowTask, type JobFlowInstance, type JobFlowValidation, type VersionDiff,
} from '@/api/jobFlow'

const capabilityStore = useCapabilityStore()
const canEdit = computed(() => capabilityStore.permitted('workflow:draft'))
const canPublish = computed(() => capabilityStore.permitted('workflow:publish'))
const canRun = computed(() => capabilityStore.permitted('instance:create'))

const route = useRoute()
const router = useRouter()
const templateId = computed(() => route.params.templateId as string)

const template = ref<JobFlowTemplate | null>(null)
const versions = ref<JobFlowVersion[]>([])
const instances = ref<JobFlowInstance[]>([])
const selectedVersionId = ref<string>('')
const selectedManifest = ref<{ tasks: JobFlowTask[] } | null>(null)
const versionDiff = ref<VersionDiff | null>(null)
const diffBaseVersionId = ref('')
const loading = ref(true)
const error = ref('')
const actionError = ref('')
const actionMessage = ref('')
const validation = ref<JobFlowValidation | null>(null)
const validationLoading = ref(false)
const publishing = ref(false)
const publishDialogVisible = ref(false)

const selectedVersion = computed(() =>
  versions.value.find((v) => v.version_id === selectedVersionId.value) ?? null
)
const activeInstances = computed(() =>
  instances.value.filter(i => !['SUCCEEDED','FAILED','ABORTED','SUPERSEDED','ARCHIVED'].includes(i.status?.state ?? ''))
)
const hasPublishedVersion = computed(() => versions.value.length > 0)
const isArchived = computed(() => template.value?.status === 'ARCHIVED')

const dagTasks = computed<Record<string, Task>>(() => {
  const raw: JobFlowTask[] = selectedManifest.value?.tasks ?? template.value?.draft?.tasks ?? []
  const record: Record<string, Task> = {}
  for (const task of raw) {
    record[task.id] = {
      id: task.id,
      name: task.name ?? task.id,
      status: 'PENDING',
      assigned_agent: '',
      depends_on: task.depends_on ?? [],
      last_updated: '',
      type: task.type,
    }
  }
  return record
})

onMounted(async () => {
  await load()
})

async function load() {
  loading.value = true
  try {
    template.value = await getJobFlowTemplate(templateId.value)
    capabilityStore.load(template.value.group_id).catch(() => undefined)
    versions.value = await listJobFlowTemplateVersions(templateId.value)
    instances.value = await listJobFlowInstances(templateId.value)
    if (versions.value.length) {
      selectedVersionId.value = versions.value[0].version_id
    }
    runValidation()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '模板加载失败'
  } finally {
    loading.value = false
  }
}

async function runValidation() {
  validationLoading.value = true
  try {
    validation.value = await validateJobFlowTemplate(templateId.value)
  } catch {
    validation.value = null
  } finally {
    validationLoading.value = false
  }
}

const draftTasks = computed<JobFlowTask[]>(() =>
  template.value?.draft?.tasks ?? []
)
const draftEdgeCount = computed(() =>
  draftTasks.value.reduce(
    (total, task) => total + (task.depends_on ?? []).filter((dependency) =>
      draftTasks.value.some((item) => item.id === dependency)).length,
    0,
  )
)
const draftRootCount = computed(() =>
  draftTasks.value.filter((task) => !(task.depends_on ?? []).some((dependency) =>
    draftTasks.value.some((item) => item.id === dependency))).length
)
const draftLeafCount = computed(() =>
  draftTasks.value.filter((task) =>
    !draftTasks.value.some((item) => (item.depends_on ?? []).includes(task.id))).length
)
const validationErrorCount = computed(() => validation.value?.errors.length ?? 0)

async function loadVersion() {
  if (!selectedVersionId.value) {
    selectedManifest.value = null
    versionDiff.value = null
    return
  }
  const result = await getJobFlowTemplateVersion(templateId.value, selectedVersionId.value)
  selectedManifest.value = result.manifest
  const index = versions.value.findIndex(item => item.version_id === selectedVersionId.value)
  const baseId = diffBaseVersionId.value
    && diffBaseVersionId.value !== selectedVersionId.value
    ? diffBaseVersionId.value
    : (index > 0 ? versions.value[index - 1].version_id : '')
  versionDiff.value = baseId
    ? await diffJobFlowVersions(templateId.value, baseId, selectedVersionId.value)
    : null
}

const diffBaseOptions = computed(() =>
  versions.value.filter((v) => v.version_id !== selectedVersionId.value)
)

function versionLabel(v: JobFlowVersion): string {
  return `V${v.version} · ${v.status}`
}

watch(selectedVersionId, () => {
  if (diffBaseVersionId.value && diffBaseVersionId.value === selectedVersionId.value) {
    diffBaseVersionId.value = ''
  }
  loadVersion()
})
watch(diffBaseVersionId, () => { loadVersion() })

async function handleArchive() {
  if (!template.value) return
  if (!window.confirm(`确定归档模板「${template.value.name}」？归档后不能再创建新实例。`)) return
  actionError.value = ''
  try {
    await archiveJobFlowTemplate(templateId.value)
    await load()
  } catch (cause) {
    actionError.value = cause instanceof Error ? cause.message : '归档失败'
  }
}

async function handleDelete() {
  if (!template.value) return
  if (!window.confirm(`确定删除模板「${template.value.name}」？此操作不可恢复。`)) return
  actionError.value = ''
  try {
    await deleteJobFlowTemplate(templateId.value)
    router.push('/templates')
  } catch (cause) {
    actionError.value = cause instanceof Error ? cause.message : '删除失败'
  }
}

const publishChangeSummary = computed(() => {
  const nodeCount = draftTasks.value.length
  if (!template.value?.current_version_id) {
    return `将基于当前草稿（revision #${template.value?.draft_revision ?? 0}，${nodeCount} 个节点）创建首个发布版本。`
  }
  return `将基于当前草稿（revision #${template.value?.draft_revision ?? 0}，${nodeCount} 个节点）生成新版本，替代当前最新发布版本 ${template.value.current_version_id}。`
})

function openPublishDialog() {
  actionError.value = ''
  actionMessage.value = ''
  publishDialogVisible.value = true
}

async function confirmPublish(createInstance: boolean) {
  if (!template.value) return
  publishing.value = true
  actionError.value = ''
  try {
    const result = await publishJobFlowTemplate(templateId.value, template.value.draft_revision)
    publishDialogVisible.value = false
    actionMessage.value = `已发布 ${result.version.version_id}`
    await load()
    if (createInstance) {
      await router.push(`/templates/${encodeURIComponent(templateId.value)}/instances/new`)
    }
  } catch (cause) {
    publishDialogVisible.value = false
    actionError.value = cause instanceof Error ? cause.message : '发布失败'
  } finally {
    publishing.value = false
  }
}
</script>

<template>
  <AppShell :title="template?.name ?? '模板详情'" eyebrow="Template Detail">
    <div class="mx-auto max-w-6xl space-y-4 p-4 sm:p-6">
      <div v-if="loading" class="rounded border border-border p-6 text-sm text-muted-foreground">加载中…</div>
      <div v-else-if="error" role="alert" class="rounded border border-red-400/30 bg-red-400/10 p-4 text-sm text-red-200">{{ error }}</div>
      <template v-else-if="template">
        <div v-if="actionError" role="alert" class="rounded bg-red-400/10 p-3 text-sm text-red-300">{{ actionError }}</div>
        <div v-if="actionMessage" data-testid="template-action-message" class="rounded bg-emerald-400/10 p-3 text-sm text-emerald-300">{{ actionMessage }}</div>

        <div class="flex items-start justify-between gap-3">
          <div>
            <h2 class="text-lg font-semibold">{{ template.name }}</h2>
            <p class="text-xs text-muted-foreground">{{ template.description ?? '暂无描述' }}</p>
            <div class="mt-1 flex items-center gap-2">
              <span class="text-xs font-mono text-muted-foreground">{{ template.group_id || 'unassigned' }}</span>
              <span class="rounded border border-border px-1.5 py-0.5 text-[10px]" data-testid="template-status">{{ template.status }}</span>
            </div>
          </div>
          <div class="flex flex-wrap gap-2">
            <RouterLink v-if="canEdit && !isArchived" :to="`/templates/${templateId}/edit`" data-testid="edit-template"
              class="rounded bg-blue-500 px-3 py-1.5 text-xs text-white">编辑草稿</RouterLink>
            <button v-if="canPublish && !isArchived" data-testid="publish-template"
              class="rounded border border-blue-400/50 px-3 py-1.5 text-xs text-blue-300 hover:bg-blue-500/10"
              :disabled="publishing" @click="openPublishDialog">发布</button>
            <button v-if="canEdit && !isArchived" data-testid="archive-template"
              class="rounded border border-amber-400/50 px-3 py-1.5 text-xs text-amber-300 hover:bg-amber-500/10"
              @click="handleArchive">归档</button>
            <button v-if="canEdit" data-testid="delete-template"
              class="rounded border border-red-400/50 px-3 py-1.5 text-xs text-red-300 hover:bg-red-500/10"
              @click="handleDelete">删除</button>
          </div>
        </div>

        <div class="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-card px-4 py-2 text-xs text-muted-foreground" data-testid="validation-summary">
          <span class="font-medium text-foreground">校验摘要</span>
          <span data-testid="validation-node-count">节点数 {{ draftTasks.length }}</span>
          <span data-testid="validation-edge-count">边数 {{ draftEdgeCount }}</span>
          <span>根节点 {{ draftRootCount }}</span>
          <span>叶节点 {{ draftLeafCount }}</span>
          <span
            data-testid="validation-result"
            class="rounded px-2 py-0.5"
            :class="validationErrorCount ? 'bg-red-400/10 text-red-300' : 'bg-emerald-400/10 text-emerald-300'"
          >{{ validationLoading ? '校验中…' : validationErrorCount ? `${validationErrorCount} ERRORS` : validation ? 'PASS' : '—' }}</span>
          <button type="button" data-testid="revalidate-template" class="ml-auto text-blue-300 hover:underline" @click="runValidation">重新校验</button>
        </div>

        <div v-if="versions.length" class="flex flex-wrap items-center gap-3 border-b border-border" data-testid="version-tabs">
          <button v-for="v in versions" :key="v.version_id"
            :data-testid="`version-tab-${v.version_id}`"
            class="px-3 py-1.5 text-xs transition"
            :class="selectedVersionId === v.version_id
              ? 'border-b-2 border-blue-400 text-blue-300 font-medium'
              : 'text-muted-foreground hover:text-foreground'"
            @click="selectedVersionId = v.version_id">
            {{ versionLabel(v) }}
          </button>
          <label v-if="versions.length > 1" class="ml-auto flex items-center gap-2 pb-1 text-xs text-muted-foreground">
            对比版本
            <select
              v-model="diffBaseVersionId"
              data-testid="version-diff-base"
              class="rounded border border-input bg-background px-2 py-1 text-xs"
            >
              <option value="">与上一版本对比</option>
              <option v-for="v in diffBaseOptions" :key="v.version_id" :value="v.version_id">{{ versionLabel(v) }}</option>
            </select>
          </label>
        </div>

        <div v-if="versionDiff" class="rounded-lg border border-border bg-card p-4" data-testid="version-diff">
          <h3 class="text-sm font-semibold">版本差异 <span class="font-mono text-xs">{{ versionDiff.summary }}</span></h3>
          <div class="mt-2 flex flex-wrap gap-2 text-xs">
            <span v-for="task in versionDiff.added_tasks" :key="task" class="rounded bg-green-500/10 px-2 py-1 text-green-300">+ {{ task }}</span>
            <span v-for="task in versionDiff.removed_tasks" :key="task" class="rounded bg-red-500/10 px-2 py-1 text-red-300">- {{ task }}</span>
            <span v-for="change in versionDiff.changed_tasks" :key="change.task_id" class="rounded bg-amber-500/10 px-2 py-1 text-amber-300">~ {{ change.task_id }}</span>
          </div>
        </div>

        <div class="rounded-lg border border-border bg-card overflow-hidden">
          <div class="border-b border-border px-4 py-2 text-xs text-muted-foreground">
            只读 DAG · {{ Object.keys(dagTasks).length }} 个节点
            <span v-if="selectedVersion" class="ml-2">manifest_hash: {{ selectedVersion.manifest_hash?.slice(0, 12) }}…</span>
          </div>
          <div class="min-h-[320px] p-2">
            <DagGraph :tasks="dagTasks" />
          </div>
        </div>

        <div class="rounded-lg border border-border bg-card p-4" data-testid="instance-area">
          <div class="flex items-center justify-between">
            <div>
              <h3 class="text-sm font-semibold">实例</h3>
              <p class="mt-0.5 text-xs text-muted-foreground">
                活跃 {{ activeInstances.length }} · 历史 {{ instances.length - activeInstances.length }}
              </p>
            </div>
            <RouterLink v-if="canRun && hasPublishedVersion && !isArchived"
              :to="`/templates/${templateId}/instances/new`"
              data-testid="create-instance"
              class="rounded bg-blue-500 px-3 py-1.5 text-xs text-white">创建实例</RouterLink>
          </div>
          <div v-if="instances.length" class="mt-3 space-y-2">
            <div v-for="inst in instances.slice(0, 5)" :key="inst.instance_id"
              class="flex items-center justify-between rounded border border-border px-3 py-2 text-xs">
              <RouterLink :to="`/instances/${inst.instance_id}`" class="text-blue-300 hover:underline">{{ inst.name }}</RouterLink>
              <span class="rounded border border-border px-1.5 py-0.5">{{ inst.status?.state }}</span>
            </div>
            <p v-if="instances.length > 5" class="text-xs text-muted-foreground">
              <RouterLink to="/instances" class="text-blue-300 hover:underline">查看全部实例 →</RouterLink>
            </p>
          </div>
          <p v-else class="mt-3 text-xs text-muted-foreground">暂无实例</p>
        </div>
      </template>
    </div>
    <PublishDialog
      :visible="publishDialogVisible"
      :change-summary="publishChangeSummary"
      @save-draft="publishDialogVisible = false"
      @publish-only="confirmPublish(false)"
      @publish-and-create="confirmPublish(true)"
      @cancel="publishDialogVisible = false"
    />
  </AppShell>
</template>
