<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Radio, RefreshCw, ScrollText } from 'lucide-vue-next'
import AppShell from '@/layouts/AppShell.vue'
import { useCapabilityStore } from '@/stores/capabilities'
import { useEventStreamStore } from '@/stores/eventStream'

const route = useRoute()
const capabilities = useCapabilityStore()
const stream = useEventStreamStore()
const groupId = computed(() => String(route.params.groupId ?? ''))
const workflowId = computed(() => String(route.params.workflowId ?? ''))
const typeFilter = ref('')
const taskFilter = ref('')
const visibleEvents = computed(() => stream.orderedEvents.filter((event) =>
  (!typeFilter.value || event.type === typeFilter.value)
  && (!taskFilter.value || event.subject.task_id === taskFilter.value),
))
const eventTypes = computed(() => Array.from(new Set(stream.events.map((event) => event.type))).sort())
const taskIds = computed(() => Array.from(new Set(stream.events.map((event) => event.subject.task_id).filter((value): value is string => Boolean(value)))).sort())

async function start() {
  await capabilities.load(groupId.value)
  if (capabilities.enabled('sse_events')) {
    stream.connect({ groupId: groupId.value, reqId: workflowId.value })
  }
}

onMounted(() => start().catch(() => undefined))
onUnmounted(() => stream.disconnect())
watch([groupId, workflowId], () => start().catch(() => undefined))
</script>

<template>
  <AppShell :group-id="groupId" :workflow-id="workflowId" title="执行日志" eyebrow="Run / Attempt / Session Events">
    <section class="p-4 sm:p-6">
      <div class="mb-4 flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card p-3">
        <div class="inline-flex items-center gap-2 text-xs font-mono"
          :class="stream.state === 'live' ? 'text-emerald-300' : stream.state === 'reset' ? 'text-red-300' : 'text-amber-300'">
          <Radio :size="13" />{{ capabilities.enabled('sse_events') ? stream.state : capabilities.reason('sse_events') }}
        </div>
        <select v-model="typeFilter" class="ml-auto rounded border border-border bg-background px-2 py-1.5 text-xs">
          <option value="">全部事件</option><option v-for="type in eventTypes" :key="type" :value="type">{{ type }}</option>
        </select>
        <select v-model="taskFilter" class="rounded border border-border bg-background px-2 py-1.5 text-xs">
          <option value="">全部任务</option><option v-for="task in taskIds" :key="task" :value="task">{{ task }}</option>
        </select>
        <button v-if="stream.state === 'reset'" class="flex items-center gap-1 rounded bg-blue-500 px-2 py-1.5 text-xs text-white" @click="start"><RefreshCw :size="12" />重新加载</button>
      </div>
      <p v-if="stream.error" role="status" class="mb-3 text-xs text-amber-300">{{ stream.error }}</p>
      <div v-if="!capabilities.enabled('sse_events')" class="rounded-lg border border-border bg-card p-6 text-sm text-muted-foreground">当前部署未启用实时事件；Task Workbench 中仍可查看已有 Session 历史。</div>
      <ol v-else-if="visibleEvents.length" class="space-y-2">
        <li v-for="event in visibleEvents" :key="event.event_id" class="rounded-lg border border-border bg-card p-3">
          <div class="flex flex-wrap items-center gap-2"><ScrollText :size="13" class="text-violet-300" /><strong class="font-mono text-xs">{{ event.type }}</strong><span class="text-[10px] text-muted-foreground">#{{ event.sequence }} · {{ new Date(event.occurred_at).toLocaleString() }}</span></div>
          <div class="mt-2 flex flex-wrap gap-x-3 text-[11px] font-mono text-muted-foreground"><span v-if="event.subject.run_id">run={{ event.subject.run_id }}</span><span v-if="event.subject.task_id">task={{ event.subject.task_id }}</span><span>actor={{ event.actor.id }}</span></div>
          <pre v-if="Object.keys(event.data).length" class="mt-2 overflow-x-auto whitespace-pre-wrap rounded bg-background p-2 text-[11px] text-slate-300">{{ JSON.stringify(event.data, null, 2) }}</pre>
        </li>
      </ol>
      <div v-else class="rounded-lg border border-dashed border-border p-10 text-center text-sm text-muted-foreground">等待匹配的执行事件…</div>
    </section>
  </AppShell>
</template>
