<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import AppShell from '@/layouts/AppShell.vue'
import { deleteJobFlowInstance, listJobFlowInstances, type JobFlowInstance } from '@/api/jobFlow'

const instances = ref<JobFlowInstance[]>([])
const error = ref('')
const route = useRoute()
const groupId = computed(() => {
  const value = route.query.groupId || route.query.group_id
  return typeof value === 'string' && value ? value : undefined
})
onMounted(async () => {
  try { instances.value = await listJobFlowInstances(undefined, groupId.value) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '实例加载失败' }
})
const deletableStates = new Set(['QUEUED', 'SUCCEEDED', 'FAILED', 'ABORTED', 'SUPERSEDED', 'ARCHIVED'])
async function removeInstance(instance: JobFlowInstance) {
  if (!deletableStates.has(instance.status?.state)) return
  if (!window.confirm(`确定删除实例“${instance.name}”吗？删除后实例日志、任务和执行记录将不可恢复。`)) return
  try {
    await deleteJobFlowInstance(instance.instance_id)
    instances.value = instances.value.filter(item => item.instance_id !== instance.instance_id)
  }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '实例删除失败' }
}
</script>

<template>
  <AppShell title="作业流实例" eyebrow="Instances">
    <template #actions><RouterLink :to="groupId ? { path: '/templates', query: { groupId } } : '/templates'" class="rounded border border-border px-3 py-2 text-xs">模板管理</RouterLink></template>
    <div class="mx-auto max-w-6xl space-y-4 p-4 sm:p-6">
      <p class="text-sm text-muted-foreground">实例拥有独立的参数、Git 分支和 Workspace，可从同一模板并行创建。{{ groupId ? `当前项目组：${groupId}` : '' }}</p>
      <p v-if="error" role="alert" class="rounded bg-red-400/10 p-3 text-sm text-red-300">{{ error }}</p>
      <div v-if="!instances.length && !error" class="rounded border border-dashed border-border p-10 text-center text-sm text-muted-foreground">暂无实例</div>
      <div v-for="instance in instances" :key="instance.instance_id" data-testid="instance-card" class="flex items-center justify-between gap-4 rounded-lg border border-border bg-card p-4">
        <div><RouterLink :to="'/instances/' + encodeURIComponent(instance.instance_id)" class="font-semibold text-blue-300 hover:underline">{{ instance.name }}</RouterLink><p class="mt-1 font-mono text-[11px] text-muted-foreground">{{ instance.instance_id }} · {{ instance.version_id }} · {{ instance.group_id || 'unassigned' }}</p></div>
        <div class="flex items-center gap-2"><span class="rounded border border-border px-2 py-1 text-xs">{{ instance.status?.state }}</span><button data-testid="delete-instance" class="rounded border border-red-400/50 px-2 py-1 text-xs text-red-300 disabled:cursor-not-allowed disabled:opacity-40" :disabled="!deletableStates.has(instance.status?.state)" :title="deletableStates.has(instance.status?.state) ? '删除实例' : '只有排队中或终态实例可以删除'" @click="removeInstance(instance)">删除</button></div>
      </div>
    </div>
  </AppShell>
</template>
