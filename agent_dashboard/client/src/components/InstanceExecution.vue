<script setup lang="ts">
/** 实例级执行过程：每个节点折叠成一条，展开看逐轮 attempt。 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { listJobFlowInstanceAttempts, type InstanceExecutionTask } from '@/api/jobFlow'

const props = defineProps<{ instanceId: string }>()

const tasks = ref<InstanceExecutionTask[]>([])
const loading = ref(false)
const error = ref('')
const expanded = ref<Record<string, boolean>>({})

const totalAttempts = computed(() =>
  tasks.value.reduce((sum, task) => sum + task.attempts.length, 0))
const failedAttempts = computed(() =>
  tasks.value.reduce(
    (sum, task) => sum + task.attempts.filter((item) => item.state === 'FAILED').length,
    0,
  ))

async function load() {
  loading.value = true
  error.value = ''
  try {
    tasks.value = await listJobFlowInstanceAttempts(props.instanceId)
    // 默认展开有多次执行或有失败反馈的节点，其余收起。
    const next: Record<string, boolean> = {}
    for (const task of tasks.value) {
      next[task.task_id] = task.attempts.length > 1
        || Boolean(task.retry_feedback?.failed_checks?.length)
        || task.state === 'FAILED'
    }
    expanded.value = next
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '执行过程加载失败'
  } finally {
    loading.value = false
  }
}

function formatDuration(ms: number | null | undefined) {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60_000)}m${Math.round((ms % 60_000) / 1000)}s`
}

function formatTime(value: string) {
  if (!value) return '—'
  return value.replace('T', ' ').replace('Z', '').slice(0, 19)
}

function stateClass(state: string) {
  if (state === 'DONE' || state === 'SUCCEEDED') return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
  if (state === 'FAILED' || state === 'EXPIRED') return 'border-red-400/30 bg-red-400/10 text-red-300'
  if (state === 'IN_PROGRESS') return 'border-blue-500/30 bg-blue-500/10 text-blue-300'
  if (state === 'WAITING_FOR_HUMAN' || state === 'AWAITING_REVIEW') return 'border-amber-400/30 bg-amber-400/10 text-amber-300'
  if (state === 'SKIPPED_UPSTREAM_FAILED') return 'border-amber-400/30 bg-amber-400/10 text-amber-300'
  return 'border-border text-muted-foreground'
}

onMounted(load)
defineExpose({ load })
</script>

<template>
  <div data-testid="instance-execution" class="rounded-lg border border-border bg-card p-4">
    <div class="flex flex-wrap items-center gap-2">
      <h2 class="text-sm font-semibold">节点执行过程</h2>
      <span class="rounded bg-background px-2 py-0.5 text-xs">{{ tasks.length }} 个节点</span>
      <span class="rounded bg-background px-2 py-0.5 text-xs">{{ totalAttempts }} 次执行</span>
      <span v-if="failedAttempts" class="rounded border border-red-400/30 bg-red-400/10 px-2 py-0.5 text-xs text-red-300">
        失败 {{ failedAttempts }} 次
      </span>
      <button class="ml-auto rounded border border-border px-2 py-1 text-xs" :disabled="loading" @click="load">
        刷新
      </button>
    </div>

    <p v-if="error" role="alert" class="mt-2 rounded border border-red-400/30 bg-red-400/10 px-2 py-1 text-xs text-red-300">{{ error }}</p>
    <p v-else-if="loading" class="mt-3 animate-pulse text-xs text-muted-foreground">正在加载执行记录…</p>
    <p v-else-if="!tasks.length" class="mt-3 text-xs text-muted-foreground">该实例还没有执行记录。</p>

    <div class="mt-3 space-y-2">
      <section v-for="task in tasks" :key="task.task_id" class="rounded border border-border">
        <button class="flex w-full flex-wrap items-center gap-2 px-3 py-2 text-left text-xs hover:bg-accent/40"
          @click="expanded[task.task_id] = !expanded[task.task_id]">
          <span class="font-mono text-blue-300">{{ task.task_id }}</span>
          <span class="rounded border px-1.5 py-0.5" :class="stateClass(task.state)">{{ task.state }}</span>
          <span class="text-muted-foreground">{{ task.attempts.length }} 次执行</span>
          <span v-if="task.fail_count" class="rounded border border-amber-400/30 bg-amber-400/10 px-1.5 py-0.5 text-amber-300">
            回退 {{ task.fail_count }}/{{ task.retry_feedback?.max_attempts ?? '—' }}
          </span>
          <span v-if="task.depends_on?.length" class="font-mono text-[10px] text-muted-foreground">
            依赖 {{ task.depends_on.join(', ') }}
          </span>
          <span class="ml-auto text-muted-foreground">{{ expanded[task.task_id] ? '收起' : '展开' }}</span>
        </button>

        <div v-if="expanded[task.task_id]" class="border-t border-border px-3 py-2 text-xs">
          <div v-if="task.retry_feedback?.failed_checks?.length" class="mb-2 rounded border border-amber-400/30 bg-amber-400/10 p-2 text-[11px] text-amber-100">
            <p class="font-semibold">回退原因（{{ formatTime(task.retry_feedback.at || '') }}）</p>
            <p v-for="check in task.retry_feedback.failed_checks" :key="check.task" class="mt-1 font-mono">
              {{ check.task }}: {{ check.error || '未记录原因' }}
            </p>
          </div>
          <p v-else-if="task.error_message" class="mb-2 text-[11px] text-red-300">{{ task.error_message }}</p>

          <p v-if="!task.attempts.length" class="text-muted-foreground">尚无执行记录。</p>
          <table v-else class="w-full table-fixed border-collapse text-left text-[11px]">
            <thead class="text-muted-foreground">
              <tr>
                <th class="w-12 py-1 font-medium">轮次</th>
                <th class="w-24 py-1 font-medium">状态</th>
                <th class="w-36 py-1 font-medium">开始</th>
                <th class="w-16 py-1 font-medium">耗时</th>
                <th class="w-20 py-1 font-medium">Agent</th>
                <th class="py-1 font-medium">结果</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(attempt, index) in task.attempts" :key="attempt.attempt_id" class="border-t border-border/50 align-top">
                <td class="py-1 font-mono">#{{ index + 1 }}</td>
                <td class="py-1">
                  <span class="rounded border px-1.5 py-0.5" :class="stateClass(attempt.state)">{{ attempt.state }}</span>
                </td>
                <td class="py-1 font-mono text-muted-foreground">{{ formatTime(attempt.started_at || attempt.created_at) }}</td>
                <td class="py-1 font-mono text-muted-foreground">{{ formatDuration(attempt.duration_ms) }}</td>
                <td class="py-1 font-mono text-muted-foreground">{{ attempt.provider || '—' }}</td>
                <td class="py-1">
                  <p v-if="attempt.failure_reason" class="text-red-300">{{ attempt.failure_reason }}</p>
                  <p v-else-if="attempt.output_excerpt" class="max-h-16 overflow-auto whitespace-pre-wrap text-muted-foreground">{{ attempt.output_excerpt }}</p>
                  <p v-else class="text-muted-foreground">—</p>
                  <RouterLink v-if="attempt.session_id"
                    :to="`/instances/${encodeURIComponent(instanceId)}/tasks/${encodeURIComponent(task.task_id)}?attempt=${encodeURIComponent(attempt.attempt_id)}`"
                    class="mt-0.5 inline-block font-mono text-[10px] text-blue-300 hover:underline"
                    title="回放这一轮的 Agent 会话">
                    回放会话 · {{ attempt.session_id }}
                  </RouterLink>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </div>
  </div>
</template>
