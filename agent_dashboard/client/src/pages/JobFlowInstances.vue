<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import AppShell from '@/layouts/AppShell.vue'
import { useCapabilityStore } from '@/stores/capabilities'
import { deleteJobFlowInstance, listJobFlowInstances, startJobFlowInstance, type JobFlowInstance } from '@/api/jobFlow'

const instances = ref<JobFlowInstance[]>([])
const error = ref('')
const actionError = ref('')
const busyInstanceId = ref('')
const route = useRoute()
const router = useRouter()
const groupId = computed(() => {
  const value = route.query.groupId || route.query.group_id
  return typeof value === 'string' && value ? value : undefined
})
const capabilities = useCapabilityStore()

const TERMINAL_STATES = ['SUCCEEDED', 'FAILED', 'ABORTED', 'SUPERSEDED', 'ARCHIVED']
const statusFilters = [
  { key: 'all', label: '全部', test: () => true },
  { key: 'running', label: '运行中', test: (s: string) => s === 'RUNNING' || s === 'DRAINING' },
  { key: 'queued', label: '排队中', test: (s: string) => s === 'QUEUED' },
  { key: 'paused', label: '已暂停', test: (s: string) => s === 'PAUSED' },
  { key: 'terminal', label: '终态', test: (s: string) => TERMINAL_STATES.includes(s) },
] as const
const activeFilter = ref('all')

const filteredInstances = computed(() => {
  const filter = statusFilters.find((item) => item.key === activeFilter.value) ?? statusFilters[0]
  return instances.value.filter((instance) => filter.test(instance.status?.state || ''))
})

const deletableStates = new Set(['QUEUED', 'SUCCEEDED', 'FAILED', 'ABORTED', 'SUPERSEDED', 'ARCHIVED'])
const canControl = computed(() => capabilities.permitted('run:control'))

function instanceProgress(instance: JobFlowInstance): string {
  const tasks = instance.tasks
  if (!tasks || !Object.keys(tasks).length) return '—'
  const total = Object.keys(tasks).length
  const done = Object.values(tasks).filter((task) => task.state === 'DONE' || task.state === 'SUCCEEDED').length
  return `${done}/${total}`
}

function gitRefOf(instance: JobFlowInstance): string {
  const git = instance.context?.git as Record<string, unknown> | undefined
  const ref = git?.ref || git?.commit || git?.commit_sha
  return ref ? String(ref) : '—'
}

function workspaceOf(instance: JobFlowInstance): string {
  const workspace = instance.context?.workspace as Record<string, unknown> | undefined
  const label = workspace?.workspace_id || workspace?.strategy
  return label ? String(label) : '—'
}

function openInstance(instance: JobFlowInstance) {
  void router.push('/instances/' + encodeURIComponent(instance.instance_id))
}

async function startInstance(instance: JobFlowInstance) {
  busyInstanceId.value = instance.instance_id
  actionError.value = ''
  try {
    await startJobFlowInstance(instance.instance_id)
    instances.value = await listJobFlowInstances(undefined, groupId.value)
  } catch (cause) {
    actionError.value = cause instanceof Error ? cause.message : '实例启动失败'
  } finally { busyInstanceId.value = '' }
}

async function removeInstance(instance: JobFlowInstance) {
  if (!deletableStates.has(instance.status?.state)) return
  if (!window.confirm(`确定删除实例“${instance.name}”吗？删除后实例日志、任务和执行记录将不可恢复。`)) return
  busyInstanceId.value = instance.instance_id
  actionError.value = ''
  try {
    await deleteJobFlowInstance(instance.instance_id)
    instances.value = instances.value.filter(item => item.instance_id !== instance.instance_id)
  }
  catch (cause) { actionError.value = cause instanceof Error ? cause.message : '实例删除失败' }
  finally { busyInstanceId.value = '' }
}

onMounted(async () => {
  try { instances.value = await listJobFlowInstances(undefined, groupId.value) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '实例加载失败' }
  capabilities.load(groupId.value).catch(() => { /* 权限快照不可用时按无权限渲染 */ })
})
</script>

<template>
  <AppShell title="作业流实例" eyebrow="Instances">
    <template #actions><RouterLink :to="groupId ? { path: '/templates', query: { groupId } } : '/templates'" class="rounded bg-blue-500 px-3 py-2 text-xs text-white">创建实例</RouterLink></template>
    <div class="mx-auto max-w-6xl space-y-4 p-4 sm:p-6">
      <p class="text-sm text-muted-foreground">实例拥有独立的参数、Git 分支和 Workspace，可从同一模板并行创建。{{ groupId ? `当前项目组：${groupId}` : '' }}</p>
      <div class="flex flex-wrap gap-2" data-testid="instance-status-filter">
        <button v-for="filter in statusFilters" :key="filter.key" class="rounded-full border px-3 py-1 text-xs transition"
          :class="activeFilter === filter.key ? 'border-blue-400 bg-blue-400/10 text-blue-300' : 'border-border text-muted-foreground hover:bg-accent'"
          @click="activeFilter = filter.key">{{ filter.label }}</button>
      </div>
      <p v-if="error" role="alert" class="rounded bg-red-400/10 p-3 text-sm text-red-300">{{ error }}</p>
      <p v-if="actionError" role="alert" class="rounded bg-red-400/10 p-3 text-sm text-red-300">{{ actionError }}</p>
      <div v-if="!instances.length && !error" class="rounded border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
        <p>暂无实例</p>
        <p class="mt-2">从模板列表选择一个模板，点击"创建实例"即可开始。</p>
        <RouterLink :to="groupId ? { path: '/templates', query: { groupId } } : '/templates'" class="mt-3 inline-block rounded bg-blue-500 px-4 py-2 text-xs text-white">前往模板列表</RouterLink>
      </div>
      <div v-else-if="!filteredInstances.length && !error" class="rounded border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
        <p>当前筛选条件下暂无实例</p>
      </div>
      <div class="overflow-x-auto rounded-lg border border-border" v-if="instances.length">
        <table class="w-full min-w-[52rem] text-left text-xs">
          <thead class="border-b border-border bg-muted/40 text-muted-foreground">
            <tr>
              <th class="px-3 py-2 font-medium">实例名称</th>
              <th class="px-3 py-2 font-medium">模板/版本</th>
              <th class="px-3 py-2 font-medium">状态</th>
              <th class="px-3 py-2 font-medium">进度</th>
              <th class="px-3 py-2 font-medium">Git Commit</th>
              <th class="px-3 py-2 font-medium">Workspace</th>
              <th class="px-3 py-2 font-medium">创建时间</th>
              <th class="px-3 py-2 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="instance in filteredInstances" :key="instance.instance_id" data-testid="instance-card"
              class="cursor-pointer border-b border-border/60 transition hover:bg-accent/50"
              @click="openInstance(instance)">
              <td class="px-3 py-2.5">
                <RouterLink :to="'/instances/' + encodeURIComponent(instance.instance_id)" class="font-semibold text-blue-300 hover:underline" @click.stop>{{ instance.name }}</RouterLink>
                <p class="mt-0.5 font-mono text-[10px] text-muted-foreground">{{ instance.instance_id }}</p>
              </td>
              <td class="px-3 py-2.5 font-mono text-[11px] text-muted-foreground">
                <div>{{ instance.template_id }}</div>
                <div>{{ instance.version_id }}</div>
              </td>
              <td class="px-3 py-2.5"><span class="rounded border border-border px-2 py-1">{{ instance.status?.state }}</span></td>
              <td class="px-3 py-2.5 font-mono">{{ instanceProgress(instance) }}</td>
              <td class="px-3 py-2.5 font-mono max-w-36 truncate" :title="gitRefOf(instance)">{{ gitRefOf(instance) }}</td>
              <td class="px-3 py-2.5 font-mono max-w-36 truncate" :title="workspaceOf(instance)">{{ workspaceOf(instance) }}</td>
              <td class="px-3 py-2.5 text-muted-foreground">{{ instance.created_at || '—' }}</td>
              <td class="px-3 py-2.5">
                <div class="flex items-center gap-1.5" @click.stop>
                  <button v-if="canControl && instance.status?.state === 'QUEUED'" data-testid="start-instance"
                    class="rounded bg-emerald-500 px-2 py-1 text-[11px] text-white disabled:cursor-not-allowed disabled:opacity-40"
                    :disabled="busyInstanceId === instance.instance_id" @click="startInstance(instance)">启动</button>
                  <button data-testid="delete-instance" v-if="canControl"
                    class="rounded border border-red-400/50 px-2 py-1 text-[11px] text-red-300 disabled:cursor-not-allowed disabled:opacity-40"
                    :disabled="!deletableStates.has(instance.status?.state) || busyInstanceId === instance.instance_id"
                    :title="deletableStates.has(instance.status?.state) ? '删除实例' : '只有排队中或终态实例可以删除'"
                    @click="removeInstance(instance)">删除</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </AppShell>
</template>
