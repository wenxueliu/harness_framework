<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import AppShell from '@/layouts/AppShell.vue'
import DagGraph from '@/components/DagGraph.vue'
import type { Task } from '@/api/types'
import { getJobFlowTemplate, listJobFlowTemplateVersions, type JobFlowTemplate, type JobFlowVersion, type JobFlowTask } from '@/api/jobFlow'

const route = useRoute()
const templateId = computed(() => route.params.templateId as string)

const template = ref<JobFlowTemplate | null>(null)
const versions = ref<JobFlowVersion[]>([])
const selectedVersionId = ref<string>('')
const loading = ref(true)
const error = ref('')

const selectedVersion = computed(() =>
  versions.value.find((v) => v.version_id === selectedVersionId.value) ?? null
)

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
  try {
    template.value = await getJobFlowTemplate(templateId.value)
    versions.value = await listJobFlowTemplateVersions(templateId.value)
    if (versions.value.length) {
      selectedVersionId.value = versions.value[0].version_id
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '模板加载失败'
  } finally {
    loading.value = false
  }
})

function versionLabel(v: JobFlowVersion): string {
  return `V${v.version} · ${v.status}`
}
</script>

<template>
  <AppShell :title="template?.name ?? '模板详情'" eyebrow="Template Detail">
    <div class="mx-auto max-w-6xl space-y-4 p-4 sm:p-6">
      <div v-if="loading" class="rounded border border-border p-6 text-sm text-muted-foreground">加载中…</div>
      <div v-else-if="error" role="alert" class="rounded border border-red-400/30 bg-red-400/10 p-4 text-sm text-red-200">{{ error }}</div>
      <template v-else-if="template">
        <div class="flex items-center justify-between">
          <div>
            <h2 class="text-lg font-semibold">{{ template.name }}</h2>
            <p class="text-xs text-muted-foreground">{{ template.description ?? '暂无描述' }}</p>
          </div>
          <div class="flex gap-2">
            <RouterLink :to="`/templates/${templateId}/edit`" data-testid="edit-template"
              class="rounded bg-blue-500 px-3 py-1.5 text-xs text-white">编辑草稿</RouterLink>
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
      </template>
    </div>
  </AppShell>
</template>
