<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { useCapabilityStore } from '@/stores/capabilities'
import { archiveJobFlowTemplate, deleteJobFlowTemplate, listJobFlowTemplates, type JobFlowTemplate } from '@/api/jobFlow'

const props = defineProps<{ groupId?: string }>()

const capabilityStore = useCapabilityStore()
const canEdit = computed(() => capabilityStore.permitted('workflow:draft'))
const canRun = computed(() => capabilityStore.permitted('instance:create'))

const templates = ref<JobFlowTemplate[]>([])
const loading = ref(true)
const error = ref('')
const actionError = ref('')
const searchText = ref('')
const statusFilter = ref('ALL')

const STATUS_OPTIONS = [
  { value: 'ALL', label: '全部' },
  { value: 'DRAFT', label: '草稿' },
  { value: 'PUBLISHED', label: '已发布' },
  { value: 'ARCHIVED', label: '已归档' },
] as const

const route = useRoute()
const groupId = computed(() => props.groupId ?? (() => {
  const value = route.query.groupId || route.query.group_id
  return typeof value === 'string' && value ? value : undefined
})())

const filteredTemplates = computed(() => {
  const keyword = searchText.value.trim().toLowerCase()
  return templates.value.filter((template) => {
    if (statusFilter.value !== 'ALL' && (template.status || 'DRAFT') !== statusFilter.value) return false
    if (!keyword) return true
    return [template.name, template.description, template.template_id]
      .some((value) => (value || '').toLowerCase().includes(keyword))
  })
})

function templateStatus(template: JobFlowTemplate): string {
  return template.status || (template.current_version_id ? 'PUBLISHED' : 'DRAFT')
}

onMounted(() => {
  capabilityStore.load(groupId.value).catch(() => undefined)
  loadTemplates()
})

async function loadTemplates() {
  loading.value = true
  error.value = ''
  try {
    templates.value = await listJobFlowTemplates(groupId.value)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '模板加载失败'
  } finally {
    loading.value = false
  }
}

async function removeTemplate(template: JobFlowTemplate) {
  if (!window.confirm(`确认删除模板“${template.name}”？删除后不可恢复。`)) return
  actionError.value = ''
  try {
    await deleteJobFlowTemplate(template.template_id)
    templates.value = templates.value.filter((item) => item.template_id !== template.template_id)
  } catch (cause) {
    actionError.value = cause instanceof Error ? cause.message : '模板删除失败'
  }
}

async function archiveTemplate(template: JobFlowTemplate) {
  if (!window.confirm(`确认归档模板“${template.name}”？归档后不能再创建新实例。`)) return
  actionError.value = ''
  try {
    const updated = await archiveJobFlowTemplate(template.template_id)
    const index = templates.value.findIndex((item) => item.template_id === template.template_id)
    if (index >= 0) templates.value[index] = { ...templates.value[index], ...updated }
  } catch (cause) {
    actionError.value = cause instanceof Error ? cause.message : '模板归档失败'
  }
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <p class="text-sm text-muted-foreground">模板定义 DAG；实例保存每次运行的独立输入上下文。{{ groupId ? `当前项目组：${groupId}` : '' }}</p>
      <RouterLink :to="groupId ? { path: '/instances', query: { groupId } } : '/instances'" class="text-xs text-blue-300 hover:underline">查看实例</RouterLink>
    </div>
    <div class="flex flex-wrap items-center gap-3" data-testid="template-toolbar">
      <input
        v-model="searchText"
        data-testid="template-search"
        type="search"
        placeholder="搜索模板名称、描述或 ID"
        class="w-64 rounded border border-input bg-background px-3 py-2 text-sm"
      />
      <select
        v-model="statusFilter"
        data-testid="template-status-filter"
        class="rounded border border-input bg-background px-3 py-2 text-sm"
      >
        <option v-for="option in STATUS_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
      </select>
      <RouterLink
        v-if="canEdit"
        :to="groupId ? { path: '/templates/new', query: { groupId } } : '/templates/new'"
        data-testid="create-template"
        class="rounded bg-blue-500 px-3 py-2 text-xs text-white"
      >创建模板</RouterLink>
    </div>
    <div v-if="loading" class="rounded border border-border p-6 text-sm text-muted-foreground">加载中…</div>
    <div v-else-if="error" role="alert" class="rounded border border-red-400/30 bg-red-400/10 p-4 text-sm text-red-200">{{ error }}</div>
    <div v-else-if="!filteredTemplates.length" class="rounded border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
      <p>暂无模板，先创建一个人工编排的作业流。</p>
      <RouterLink
        v-if="canEdit"
        :to="groupId ? { path: '/templates/new', query: { groupId } } : '/templates/new'"
        data-testid="create-first-template"
        class="mt-3 inline-block rounded bg-blue-500 px-3 py-2 text-xs text-white"
      >创建第一个模板</RouterLink>
    </div>
    <div v-else class="grid gap-3 md:grid-cols-2">
      <article
        v-for="template in filteredTemplates"
        :key="template.template_id"
        data-testid="template-card"
        class="rounded-lg border border-border bg-card p-4 transition hover:border-blue-400/60"
      >
        <div class="flex items-start justify-between gap-3">
          <RouterLink :to="'/templates/' + encodeURIComponent(template.template_id)" class="min-w-0">
            <h2 class="font-semibold">{{ template.name }}</h2>
            <p class="mt-1 font-mono text-[11px] text-muted-foreground">{{ template.template_id }} · {{ template.group_id || 'unassigned' }}</p>
          </RouterLink>
          <div class="flex shrink-0 items-center gap-2">
            <span class="rounded border border-border px-2 py-1 text-[10px]" data-testid="template-card-status">{{ templateStatus(template) }}</span>
          </div>
        </div>
        <RouterLink :to="'/templates/' + encodeURIComponent(template.template_id)" class="block">
          <p class="mt-3 line-clamp-2 text-sm text-muted-foreground">{{ template.description || '暂无描述' }}</p>
          <div class="mt-4 flex flex-wrap gap-x-4 text-xs text-muted-foreground">
            <span>{{ template.draft?.tasks?.length || 0 }} 个节点</span>
            <span data-testid="template-card-draft-revision">草稿修订 {{ template.draft_revision }}</span>
            <span data-testid="template-card-latest-version">最新发布版本 {{ template.current_version_id || '—' }}</span>
          </div>
        </RouterLink>
        <div class="mt-3 flex flex-wrap items-center justify-end gap-2">
          <RouterLink
            :to="'/templates/' + encodeURIComponent(template.template_id)"
            data-testid="view-template"
            class="rounded border border-border px-2 py-1 text-[10px] text-blue-300 hover:bg-blue-400/10"
          >查看详情</RouterLink>
          <RouterLink
            v-if="canEdit && templateStatus(template) !== 'ARCHIVED'"
            :to="'/templates/' + encodeURIComponent(template.template_id) + '/edit'"
            data-testid="edit-template"
            class="rounded border border-border px-2 py-1 text-[10px] hover:bg-muted"
          >编辑草稿</RouterLink>
          <RouterLink
            v-if="canRun && templateStatus(template) !== 'ARCHIVED' && template.current_version_id"
            :to="'/templates/' + encodeURIComponent(template.template_id) + '/instances/new'"
            data-testid="create-instance"
            class="rounded bg-blue-500 px-2 py-1 text-[10px] text-white"
          >创建实例</RouterLink>
          <button
            v-if="canEdit && templateStatus(template) !== 'ARCHIVED'"
            type="button"
            data-testid="archive-template"
            class="rounded border border-amber-400/30 px-2 py-1 text-[10px] text-amber-300 hover:bg-amber-400/10"
            @click="archiveTemplate(template)"
          >归档</button>
          <button
            v-if="canEdit"
            type="button"
            data-testid="delete-template"
            class="rounded border border-red-400/30 px-2 py-1 text-[10px] text-red-300 hover:bg-red-400/10"
            @click="removeTemplate(template)"
          >删除</button>
        </div>
      </article>
    </div>
    <div v-if="actionError" role="alert" class="rounded border border-red-400/30 bg-red-400/10 p-3 text-sm text-red-200">{{ actionError }}</div>
  </div>
</template>
