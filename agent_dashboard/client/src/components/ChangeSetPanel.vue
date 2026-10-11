<script setup lang="ts">
import { computed, ref } from 'vue'
import type { JobFlowChangeSet } from '@/api/jobFlow'

const props = defineProps<{
  changesets: JobFlowChangeSet[]
  loading?: boolean
  applyingId?: string
  error?: string
}>()

const emit = defineEmits<{ (e: 'confirm', changeset: JobFlowChangeSet): void }>()
const selectedId = ref('')

const selected = computed(() => props.changesets.find(
  (changeset) => changeset.changeset_id === selectedId.value,
) ?? props.changesets[0] ?? null)

const reusableTasks = computed(() => selected.value
  ? Object.entries(selected.value.reuse ?? {})
      .filter(([, reusable]) => reusable)
      .map(([taskId]) => taskId)
  : [])
</script>

<template>
  <div data-testid="changeset-panel" class="rounded-lg border border-border bg-card p-4">
    <div class="flex items-center justify-between">
      <h4 class="text-sm font-medium">ChangeSet</h4>
      <select
        v-if="changesets.length > 1"
        v-model="selectedId"
        data-testid="changeset-select"
        class="rounded border border-border bg-background px-2 py-1 text-xs"
        aria-label="选择 ChangeSet"
      >
        <option v-for="changeset in changesets" :key="changeset.changeset_id" :value="changeset.changeset_id">
          {{ changeset.changeset_id }}
        </option>
      </select>
    </div>

    <div v-if="loading" class="mt-3 animate-pulse text-xs text-muted-foreground">正在加载 ChangeSet…</div>
    <div v-else-if="error" role="alert" class="mt-3 rounded bg-red-400/10 p-3 text-xs text-red-300">{{ error }}</div>
    <div v-else-if="!changesets.length" class="mt-3 text-xs text-muted-foreground">当前实例没有 ChangeSet</div>

    <div v-else-if="selected" class="mt-3 space-y-3">
      <div class="text-xs text-muted-foreground">
        {{ selected.created_by || 'system' }} · {{ selected.created_at }} ·
        <span data-testid="changeset-state">{{ selected.state || selected.status || 'UNKNOWN' }}</span>
      </div>
      <div class="space-y-1.5 text-xs">
        <p data-testid="changeset-changed"><span class="text-red-300">直接修改:</span> {{ selected.changed_tasks.join(', ') || '无' }}</p>
        <p><span class="text-orange-300">间接受影响:</span> {{ (selected.impacted_tasks ?? []).join(', ') || '无' }}</p>
        <p><span class="text-yellow-300">需重跑:</span> {{ selected.changed_tasks.join(', ') || '无' }}</p>
        <p data-testid="changeset-reusable"><span class="text-green-300">可复用:</span> {{ reusableTasks.join(', ') || '无' }}</p>
        <p v-if="selected.applied_at" class="text-muted-foreground">应用时间: {{ selected.applied_at }}</p>
      </div>
      <div class="flex gap-2">
        <button
          v-if="selected.state !== 'APPLIED' && selected.state !== 'SUPERSEDED'"
          data-testid="changeset-confirm"
          class="rounded bg-orange-500 px-3 py-1.5 text-xs text-white disabled:opacity-50"
          :disabled="Boolean(applyingId)"
          @click="emit('confirm', selected)"
        >确认执行</button>
        <RouterLink
          v-if="selected.successor_instance_id"
          :to="'/instances/' + encodeURIComponent(selected.successor_instance_id)"
          class="rounded border border-border px-3 py-1.5 text-xs text-blue-300 hover:bg-accent"
        >查看后继实例</RouterLink>
      </div>
    </div>
  </div>
</template>
