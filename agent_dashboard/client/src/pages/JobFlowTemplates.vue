<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import AppShell from '@/layouts/AppShell.vue'
import { deleteJobFlowTemplate, listJobFlowTemplates, type JobFlowTemplate } from '@/api/jobFlow'

const templates = ref<JobFlowTemplate[]>([])
const loading = ref(true)
const error = ref('')
const route = useRoute()
const groupId = computed(() => {
  const value = route.query.groupId || route.query.group_id
  return typeof value === 'string' && value ? value : undefined
})

onMounted(async () => {
  try {
    templates.value = await listJobFlowTemplates(groupId.value)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '模板加载失败'
  } finally {
    loading.value = false
  }
})

async function removeTemplate(template: JobFlowTemplate) {
  if (!window.confirm(`确认删除模板“${template.name}”？删除后不可恢复。`)) return
  error.value = ''
  try {
    await deleteJobFlowTemplate(template.template_id)
    templates.value = templates.value.filter((item) => item.template_id !== template.template_id)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '模板删除失败'
  }
}
</script>

<template>
  <AppShell title="作业流模板" eyebrow="Templates">
    <template #actions>
      <RouterLink to="/templates/new" data-testid="create-template" class="rounded bg-blue-500 px-3 py-2 text-xs text-white">
        新建模板
      </RouterLink>
    </template>
    <div class="mx-auto max-w-6xl space-y-4 p-4 sm:p-6">
      <div class="flex items-center justify-between">
        <p class="text-sm text-muted-foreground">模板定义 DAG；实例保存每次运行的独立输入上下文。{{ groupId ? `当前项目组：${groupId}` : '' }}</p>
        <RouterLink :to="groupId ? { path: '/instances', query: { groupId } } : '/instances'" class="text-xs text-blue-300 hover:underline">查看实例</RouterLink>
      </div>
      <div v-if="loading" class="rounded border border-border p-6 text-sm text-muted-foreground">加载中…</div>
      <div v-else-if="error" role="alert" class="rounded border border-red-400/30 bg-red-400/10 p-4 text-sm text-red-200">{{ error }}</div>
      <div v-else-if="!templates.length" class="rounded border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
        暂无模板，先创建一个人工编排的作业流。
      </div>
      <div v-else class="grid gap-3 md:grid-cols-2">
        <article
          v-for="template in templates"
          :key="template.template_id"
          data-testid="template-card"
          class="rounded-lg border border-border bg-card p-4 transition hover:border-blue-400/60"
        >
          <div class="flex items-start justify-between gap-3">
            <RouterLink :to="'/templates/' + encodeURIComponent(template.template_id) + '/edit'" class="min-w-0">
              <h2 class="font-semibold">{{ template.name }}</h2>
              <p class="mt-1 font-mono text-[11px] text-muted-foreground">{{ template.template_id }} · {{ template.group_id || 'unassigned' }}</p>
            </RouterLink>
            <div class="flex shrink-0 items-center gap-2">
              <span class="rounded border border-border px-2 py-1 text-[10px] text-muted-foreground">
                {{ template.current_version_id || '草稿' }}
              </span>
              <button
                type="button"
                data-testid="delete-template"
                class="rounded border border-red-400/30 px-2 py-1 text-[10px] text-red-300 hover:bg-red-400/10"
                @click="removeTemplate(template)"
              >删除</button>
            </div>
          </div>
          <RouterLink :to="'/templates/' + encodeURIComponent(template.template_id) + '/edit'" class="block">
            <p class="mt-3 line-clamp-2 text-sm text-muted-foreground">{{ template.description || '暂无描述' }}</p>
            <div class="mt-4 text-xs text-muted-foreground">{{ template.draft?.tasks?.length || 0 }} 个节点 · 草稿修订 {{ template.draft_revision }}</div>
          </RouterLink>
        </article>
      </div>
    </div>
  </AppShell>
</template>
