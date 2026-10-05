<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '@/layouts/AppShell.vue'
import DagGraph from '@/components/DagGraph.vue'
import type { Task } from '@/api/types'
import {
  getJobFlowTemplate, listJobFlowTemplateVersions, listJobFlowInstances,
  archiveJobFlowTemplate, deleteJobFlowTemplate,
  type JobFlowTemplate, type JobFlowVersion, type JobFlowTask, type JobFlowInstance,
} from '@/api/jobFlow'

const route = useRoute()
const router = useRouter()
const templateId = computed(() => route.params.templateId as string)

const template = ref<JobFlowTemplate | null>(null)
const versions = ref<JobFlowVersion[]>([])
const instances = ref<JobFlowInstance[]>([])
const selectedVersionId = ref<string>('')
const loading = ref(true)
const error = ref('')
const actionError = ref('')

const selectedVersion = computed(() =>
  versions.value.find((v) => v.version_id === selectedVersionId.value) ?? null
)
const activeInstances = computed(() =>
  instances.value.filter(i => !['SUCCEEDED','FAILED','ABORTED','SUPERSEDED','ARCHIVED'].includes(i.status?.state ?? ''))
)
const hasPublishedVersion = computed(() => versions.value.length > 0)
const isArchived = computed(() => template.value?.status === 'ARCHIVED')

const dagTasks = computed<Record<string, Task>>(() => {
  const raw: JobFlowTask[] = template.value?.draft?.tasks ?? []
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
    versions.value = await listJobFlowTemplateVersions(templateId.value)
    instances.value = await listJobFlowInstances(templateId.value)
    if (versions.value.length) {
      selectedVersionId.value = versions.value[0].version_id
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '模板加载失败'
  } finally {
    loading.value = false
  }
}

function versionLabel(v: JobFlowVersion): string {
  return `V${v.version} · ${v.status}`
}

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
</script>

<template>
  <AppShell :title="template?.name ?? '模板详情'" eyebrow="Template Detail">
    <div class="mx-auto max-w-6xl space-y-4 p-4 sm:p-6">
      <div v-if="loading" class="rounded border border-border p-6 text-sm text-muted-foreground">加载中…</div>
      <div v-else-if="error" role="alert" class="rounded border border-red-400/30 bg-red-400/10 p-4 text-sm text-red-200">{{ error }}</div>
      <template v-else-if="template">
        <div v-if="actionError" role="alert" class="rounded bg-red-400/10 p-3 text-sm text-red-300">{{ actionError }}</div>

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
            <RouterLink v-if="!isArchived" :to="`/templates/${templateId}/edit`" data-testid="edit-template"
              class="rounded bg-blue-500 px-3 py-1.5 text-xs text-white">编辑草稿</RouterLink>
            <button v-if="!isArchived" data-testid="archive-template"
              class="rounded border border-amber-400/50 px-3 py-1.5 text-xs text-amber-300 hover:bg-amber-500/10"
              @click="handleArchive">归档</button>
            <button data-testid="delete-template"
              class="rounded border border-red-400/50 px-3 py-1.5 text-xs text-red-300 hover:bg-red-500/10"
              @click="handleDelete">删除</button>
          </div>
        </div>

        <div v-if="versions.length" class="flex gap-1 border-b border-border" data-testid="version-tabs">
          <button v-for="v in versions" :key="v.version_id"
            :data-testid="`version-tab-${v.version_id}`"
            class="px-3 py-1.5 text-xs transition"
            :class="selectedVersionId === v.version_id
              ? 'border-b-2 border-blue-400 text-blue-300 font-medium'
              : 'text-muted-foreground hover:text-foreground'"
            @click="selectedVersionId = v.version_id">
            {{ versionLabel(v) }}
          </button>
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
            <RouterLink v-if="hasPublishedVersion && !isArchived"
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
  </AppShell>
</template>
