<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft } from 'lucide-vue-next'
import AppShell from '@/layouts/AppShell.vue'
import { getJobFlowInstance, type JobFlowInstance } from '@/api/jobFlow'

const route = useRoute()
const router = useRouter()
const instance = ref<JobFlowInstance | null>(null)
const error = ref('')
const instanceId = String(route.params.instanceId || '')
const taskId = String(route.params.taskId || '')
onMounted(async () => {
  try { instance.value = await getJobFlowInstance(instanceId) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '任务加载失败' }
})
function goBack() {
  if (window.history.length > 1) router.back()
  else router.push('/instances/' + encodeURIComponent(instanceId))
}
</script>

<template>
  <AppShell :title="taskId" eyebrow="Task Detail">
    <template #actions><button data-testid="task-detail-back" class="flex items-center gap-1 rounded border border-border px-3 py-2 text-xs" @click="goBack"><ArrowLeft :size="13" />返回实例</button></template>
    <div v-if="error" role="alert" class="m-6 rounded bg-red-400/10 p-4 text-sm text-red-300">{{ error }}</div>
    <div v-else-if="instance" class="mx-auto max-w-4xl space-y-4 p-4 sm:p-6">
      <div class="rounded-lg border border-border bg-card p-4">
        <p class="text-xs text-muted-foreground">所属实例</p>
        <RouterLink :to="'/instances/' + encodeURIComponent(instanceId)" class="mt-1 inline-block text-sm text-blue-300 hover:underline">{{ instance.name }}</RouterLink>
      </div>
      <div class="rounded-lg border border-border bg-card p-4">
        <h2 class="text-sm font-semibold">任务尝试</h2>
        <p class="mt-3 text-sm text-muted-foreground">当前状态：{{ instance.tasks[taskId]?.state }}；尝试次数：{{ instance.tasks[taskId]?.attempt_count }}</p>
      </div>
    </div>
  </AppShell>
</template>
