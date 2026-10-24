<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, onUnmounted, ref } from 'vue'
import type { SessionUpdate } from '@agentclientprotocol/sdk'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, File, Folder, GitBranch, GitCommit, LoaderCircle, Play, Plug, RefreshCw, Save, Search, Unplug } from 'lucide-vue-next'
import AppShell from '@/layouts/AppShell.vue'
import AcpConversation from '@/components/AcpConversation.vue'
import { useAcpChat } from '@/lib/acp/useAcpChat'
import {
  createWorkspaceCheckpoint,
  getRunManifest,
  getWorkspaceDiff,
  listAttempts,
  listRuns,
  listWorkspaceActions,
  listWorkspaceChanges,
  listWorkspaceTree,
  readWorkspaceFile,
  runWorkspaceAction,
  saveWorkspaceFile,
  searchWorkspace,
} from '@/api/dashboard'
import { HarnessApiError } from '@/api/client'
import {
  getJobFlowInstance,
  getJobFlowPreflight,
  getTaskLiveUpdates,
  getTaskSessionMapping,
  approveJobFlowTask,
  listJobFlowTaskAttempts,
  retryJobFlowTask,
  rewindJobFlowTask,
  type JobFlowInstance,
  type TaskAttemptRecord,
  type TaskSessionMapping,
} from '@/api/jobFlow'
import type {
  AttemptWorkspaceBinding,
  WorkspaceAction,
  WorkspaceActionResult,
  WorkspaceChange,
  WorkspaceFile,
  WorkspaceManifest,
  WorkspaceTreeEntry,
} from '@/api/types'

const MonacoEditor = defineAsyncComponent(async () => {
  const module = await import('@guolao/vue-monaco-editor')
  return module.VueMonacoEditor
})

const route = useRoute()
const router = useRouter()
const instanceId = String(route.params.instanceId || '')
const taskId = String(route.params.taskId || '')
// 从实例执行过程跳转过来时，用于回放历史某一轮的会话。
const routeAttemptId = String(route.query.attempt || '')

const loading = ref(true)
const workspaceLoading = ref(false)
const retrying = ref(false)
const rewinding = ref(false)
const saving = ref(false)
const error = ref('')
const workspaceError = ref('')
const instance = ref<JobFlowInstance | null>(null)
const taskState = ref('')
const attemptCount = ref(0)
const attempts = ref<AttemptWorkspaceBinding[]>([])
const selectedAttemptId = ref('')
const binding = ref<AttemptWorkspaceBinding | null>(null)
const entries = ref<WorkspaceTreeEntry[]>([])
const currentPath = ref('')
const file = ref<WorkspaceFile | null>(null)
const openFiles = ref<WorkspaceFile[]>([])
const draft = ref('')
const baseContent = ref('')
const conflict = ref<{ currentSha: string | null; currentContent: string } | null>(null)
const attemptHistory = ref<TaskAttemptRecord[]>([])
const historyOpen = ref(false)
const sessionMapping = ref<TaskSessionMapping | null>(null)
const sessionMappingError = ref('')
const acpDraft = ref('')
const liveCursor = ref(0)
const liveActive = ref(false)
const liveError = ref('')
let liveTimer: number | undefined
let liveStateCheckedAt = 0
const actions = ref<WorkspaceAction[]>([])
const actionResult = ref<WorkspaceActionResult | null>(null)
const actionRunning = ref('')
const changes = ref<WorkspaceChange[]>([])
const searchQuery = ref('')
const searchResults = ref<Array<Record<string, unknown>>>([])
const manifest = ref<WorkspaceManifest | null>(null)
const diffText = ref('')
const checkpointMessage = ref('')
const preflightStatus = ref('')
const instanceState = computed(() => instance.value?.status?.state || '')

const DEFAULT_BRIDGE = 'ws://127.0.0.1:8787/acp'
const {
  status: acpStatus,
  isBusy: acpBusy,
  errorMessage: acpError,
  items: acpItems,
  permission: acpPermission,
  connect: connectAcp,
  send: sendAcp,
  cancel: cancelAcp,
  disconnect: disconnectAcp,
  answerPermission,
  applyUpdate,
  finishStreaming,
  resetStream,
} = useAcpChat()

const capabilityText = computed(() => {
  if (!preflightStatus.value) return 'Preflight 未知'
  if (preflightStatus.value === 'PASSED') return '✅ Preflight 通过'
  if (preflightStatus.value === 'IDLE') return 'Preflight 未执行'
  return '⚠ Preflight 阻塞'
})

const approving = ref(false)
const taskRunning = computed(() => ['PENDING', 'IN_PROGRESS'].includes(taskState.value))
const acpConnected = computed(() => acpStatus.value === 'ready' || acpStatus.value === 'busy')
const acpStatusLabel = computed(() => ({
  idle: '未连接',
  connecting: '连接中',
  ready: '已连接',
  busy: '执行中',
  error: '异常',
}[acpStatus.value]))

const connectBlockedReason = computed(() => {
  if (acpConnected.value) return ''
  const mapping = sessionMapping.value
  if (!mapping) return sessionMappingError.value ? '' : '正在读取会话映射…'
  if (!mapping.loadable) {
    if (mapping.missing.includes('session_id')) {
      return replayAttemptId.value
        ? '这一轮没有记录会话 ID（旧版本执行的历史轮次）'
        : '该任务还没有 Agent 会话'
    }
    if (mapping.missing.includes('cwd')) return '缺少执行目录映射，无法连接历史会话'
    return '缺少 Agent 映射，无法连接历史会话'
  }
  if (taskRunning.value && !replayAttemptId.value) return '任务执行中：上方实时推送；完成后可连接查看完整历史'
  return ''
})

const retryFeedback = computed(() => instance.value?.tasks[taskId]?.retry_feedback ?? {})
const failCount = computed(() => instance.value?.tasks[taskId]?.fail_count ?? 0)
const failedAttempts = computed(() => attemptHistory.value.filter((item) => item.state === 'FAILED').length)

function formatDuration(ms: number | null | undefined) {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const minutes = Math.floor(ms / 60_000)
  return `${minutes}m${Math.round((ms % 60_000) / 1000)}s`
}

function formatTime(value: string) {
  if (!value) return '—'
  return value.replace('T', ' ').replace('Z', '').slice(0, 19)
}

function attemptStateClass(state: string) {
  if (state === 'DONE') return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
  if (state === 'FAILED' || state === 'EXPIRED') return 'border-red-400/30 bg-red-400/10 text-red-300'
  if (state === 'IN_PROGRESS') return 'border-blue-500/30 bg-blue-500/10 text-blue-300'
  if (state === 'WAITING_FOR_HUMAN') return 'border-amber-400/30 bg-amber-400/10 text-amber-300'
  return 'border-border text-muted-foreground'
}

function goBack() {
  if (window.history.length > 1) router.back()
  else router.push('/instances/' + encodeURIComponent(instanceId))
}

async function selectAttemptById(attemptId: string) {
  selectedAttemptId.value = attemptId
  await loadWorkspace(attemptId)
}

function draftKey(item: WorkspaceFile) {
  return `harness-draft:${binding.value?.binding_id}:${item.path}:${item.sha256 || 'new'}`
}

async function loadTask() {
  instance.value = await getJobFlowInstance(instanceId)
  const current = instance.value.tasks[taskId]
  if (!current) throw new Error('任务不存在')
  taskState.value = current.state
  attemptCount.value = current.attempt_count
  if (current.current_attempt) selectedAttemptId.value = current.current_attempt
}

async function loadAttemptHistory() {
  try {
    attemptHistory.value = await listJobFlowTaskAttempts(instanceId, taskId)
  } catch (cause) {
    if (!(cause instanceof HarnessApiError && cause.status === 404)) {
      error.value = cause instanceof Error ? cause.message : '执行历史加载失败'
    }
  }
}

async function loadWorkspace(attemptId?: string) {
  workspaceLoading.value = true
  try {
    const runs = await listRuns(instanceId)
    const run = runs.find((item) => item.status === 'RUNNING') || runs.at(-1)
    if (!run) throw new Error('该实例还没有 Run Workspace')
    attempts.value = await listAttempts(instanceId, taskId, run.run_id)
    binding.value = attempts.value.find((item) => item.attempt_id === attemptId)
      ?? attempts.value.find((item) => item.attempt_id === instance.value?.tasks[taskId]?.current_attempt)
      ?? attempts.value.at(-1)
      ?? null
    selectedAttemptId.value = binding.value?.attempt_id ?? ''
    openFiles.value = []
    file.value = null
    diffText.value = ''
    searchResults.value = []
    if (binding.value) {
      ;[entries.value, actions.value, changes.value, manifest.value] = await Promise.all([
        listWorkspaceTree(binding.value),
        listWorkspaceActions(binding.value),
        listWorkspaceChanges(binding.value),
        getRunManifest(instanceId, run.run_id),
      ])
    } else {
      entries.value = []
      actions.value = []
      changes.value = []
      manifest.value = null
    }
  } catch (cause) {
    if (!(cause instanceof HarnessApiError && cause.status === 404)) {
      workspaceError.value = cause instanceof Error ? cause.message : 'Workspace 加载失败'
    }
  } finally {
    workspaceLoading.value = false
  }
}

const replayAttemptId = computed(() => {
  if (!routeAttemptId) return ''
  const current = instance.value?.tasks[taskId]?.current_attempt
  return routeAttemptId === current ? '' : routeAttemptId
})

async function loadSessionMapping() {
  sessionMappingError.value = ''
  try {
    sessionMapping.value = await getTaskSessionMapping(
      instanceId, taskId, replayAttemptId.value || undefined,
    )
  } catch (cause) {
    sessionMapping.value = null
    sessionMappingError.value = cause instanceof Error ? cause.message : '会话映射加载失败'
  }
}

/** 运行中任务：轮询内存中的 ACP 增量，实时显示工具调用和输出。 */
async function pollLive() {
  liveTimer = undefined
  if (!taskRunning.value) {
    finishStreaming()
    liveActive.value = false
    return
  }
  try {
    const payload = await getTaskLiveUpdates(instanceId, taskId, liveCursor.value)
    if (payload.truncated) {
      // 缓冲已丢弃最早的事件，下一轮从头取。
      liveCursor.value = 0
    } else {
      for (const event of payload.events) {
        liveCursor.value = Math.max(liveCursor.value, event.seq)
        const update = event.payload?.update
        if (update && typeof update === 'object' && 'sessionUpdate' in update) {
          applyUpdate(update as unknown as SessionUpdate, { userMessages: true })
        }
      }
    }
    liveActive.value = payload.active
    liveError.value = ''
    // 流停止但页面仍认为在运行：低频核对任务状态，结束后切回“可连接”。
    if (!payload.active && Date.now() - liveStateCheckedAt > 5000) {
      liveStateCheckedAt = Date.now()
      await loadTask()
      if (!taskRunning.value) {
        finishStreaming()
        liveActive.value = false
        void loadSessionMapping()
        return
      }
    }
  } catch (cause) {
    liveError.value = cause instanceof Error ? cause.message : '实时更新加载失败'
  }
  if (taskRunning.value) liveTimer = window.setTimeout(pollLive, 1200)
}

function startLivePolling() {
  if (liveTimer !== undefined) window.clearTimeout(liveTimer)
  liveTimer = undefined
  if (taskRunning.value) void pollLive()
}

function stopLivePolling() {
  if (liveTimer !== undefined) window.clearTimeout(liveTimer)
  liveTimer = undefined
  liveActive.value = false
}

async function selectAttempt(event: Event) {
  const attemptId = (event.target as HTMLSelectElement).value
  selectedAttemptId.value = attemptId
  await loadWorkspace(attemptId)
}

/** 连接 = 让 Agent 回放自己的会话历史；断开 = 关闭桥接进程，避免长期占用。 */
async function toggleAcpConnection() {
  if (acpConnected.value) {
    await disconnectAcp()
    return
  }
  await loadSessionMapping()
  const mapping = sessionMapping.value
  if (!mapping?.loadable || taskRunning.value) return
  await connectAcp({
    bridgeUrl: DEFAULT_BRIDGE,
    agent: mapping.provider,
    cwd: mapping.cwd,
    loadSessionId: mapping.session_id,
  })
}

async function submitAcp() {
  const text = acpDraft.value.trim()
  if (!text || !acpConnected.value) return
  acpDraft.value = ''
  await sendAcp(text)
}

async function openEntry(entry: WorkspaceTreeEntry) {
  if (!binding.value) return
  if (entry.type === 'directory') {
    currentPath.value = entry.path
    entries.value = await listWorkspaceTree(binding.value, entry.path)
    return
  }
  if (entry.type !== 'file') return
  file.value = await readWorkspaceFile(binding.value, entry.path)
  if (!openFiles.value.some((item) => item.path === file.value?.path)) openFiles.value.push(file.value)
  baseContent.value = file.value.content || ''
  draft.value = sessionStorage.getItem(draftKey(file.value)) ?? baseContent.value
  conflict.value = null
}

function selectTab(item: WorkspaceFile) {
  file.value = item
  baseContent.value = item.content || ''
  draft.value = sessionStorage.getItem(draftKey(item)) ?? baseContent.value
  conflict.value = null
}

function rememberDraft() {
  if (!file.value || draft.value === baseContent.value) return
  sessionStorage.setItem(draftKey(file.value), draft.value)
}

async function save() {
  if (!binding.value || !file.value?.writable) return
  saving.value = true
  error.value = ''
  try {
    const previousKey = draftKey(file.value)
    const result = await saveWorkspaceFile(binding.value, file.value, draft.value, 'Job Flow 工作台修改')
    file.value = { ...file.value, content: draft.value, sha256: result.sha256 }
    baseContent.value = draft.value
    sessionStorage.removeItem(previousKey)
    conflict.value = null
    changes.value = await listWorkspaceChanges(binding.value)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '保存失败'
    if (cause instanceof HarnessApiError && cause.code === 'FILE_VERSION_CONFLICT' && binding.value && file.value) {
      const current = await readWorkspaceFile(binding.value, file.value.path)
      conflict.value = { currentSha: current.sha256, currentContent: current.content || '' }
    }
  } finally {
    saving.value = false
  }
}

async function acceptCurrentVersion() {
  if (!file.value || !conflict.value) return
  file.value = { ...file.value, content: conflict.value.currentContent, sha256: conflict.value.currentSha }
  baseContent.value = conflict.value.currentContent
  draft.value = conflict.value.currentContent
  conflict.value = null
  await save()
}

async function searchFiles() {
  if (!binding.value || !searchQuery.value.trim()) {
    searchResults.value = []
    return
  }
  searchResults.value = await searchWorkspace(binding.value, searchQuery.value.trim())
}

async function showDiff() {
  if (!binding.value) return
  const result = await getWorkspaceDiff(binding.value, file.value?.path || '')
  diffText.value = result.diff || '当前没有 Git diff'
}

async function executeAction(actionId: string) {
  if (!binding.value) return
  actionRunning.value = actionId
  try {
    actionResult.value = await runWorkspaceAction(binding.value, actionId)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Action 执行失败'
  } finally {
    actionRunning.value = ''
  }
}

async function checkpoint() {
  if (!binding.value || !checkpointMessage.value.trim()) return
  const result = await createWorkspaceCheckpoint(binding.value, checkpointMessage.value.trim())
  checkpointMessage.value = ''
  actionResult.value = {
    action_id: 'checkpoint',
    exit_code: 0,
    stdout: `Created ${result.commit_sha}`,
    stderr: '',
    event_id: result.event_id,
  }
}

async function approve() {
  approving.value = true
  error.value = ''
  try {
    await approveJobFlowTask(instanceId, taskId)
    await loadTask()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '批准失败'
  } finally {
    approving.value = false
  }
}

async function retry() {
  retrying.value = true
  try {
    await retryJobFlowTask(instanceId, taskId)
    await Promise.all([loadTask(), loadWorkspace(), loadSessionMapping(), loadAttemptHistory()])
    resetStream()
    liveCursor.value = 0
    startLivePolling()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '任务重试失败'
  } finally {
    retrying.value = false
  }
}

async function rewind() {
  const confirmed = window.confirm(
    `确定从「${taskId}」重来吗？\n\n` +
    '该节点及其所有下游任务（包括已完成）都会被重置为未完成并重新执行。\n' +
    '旧产物会归档保留，但需要重新跑完整条下游链路。',
  )
  if (!confirmed) return
  rewinding.value = true
  error.value = ''
  try {
    await rewindJobFlowTask(instanceId, taskId, { reason: '页面手动重跑' })
    await Promise.all([loadTask(), loadWorkspace(), loadSessionMapping(), loadAttemptHistory()])
    resetStream()
    liveCursor.value = 0
    startLivePolling()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '任务重跑失败'
  } finally {
    rewinding.value = false
  }
}

onMounted(async () => {
  try {
    await loadTask()
    try {
      const preflight = await getJobFlowPreflight(instanceId)
      preflightStatus.value = preflight.status
    } catch { preflightStatus.value = '' }
    await Promise.all([loadWorkspace(), loadSessionMapping(), loadAttemptHistory()])
    startLivePolling()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '任务加载失败'
  } finally {
    loading.value = false
  }
})

onUnmounted(() => {
  stopLivePolling()
  // 离开页面就断开桥接，不把 Agent 进程留在后台。
  void disconnectAcp()
})
</script>

<template>
  <AppShell :title="taskId" eyebrow="Task Workbench">
    <template #actions>
      <button data-testid="task-detail-back" class="flex items-center gap-1 rounded border border-border px-3 py-2 text-xs" @click="goBack">
        <ArrowLeft :size="13" />返回实例
      </button>
    </template>

    <div v-if="loading" class="grid min-h-[60vh] place-items-center">
      <LoaderCircle class="animate-spin text-blue-300" />
    </div>
    <div v-else-if="error && !instance" role="alert" class="m-6 rounded border border-red-400/30 bg-red-400/10 p-4 text-sm text-red-300">
      {{ error }}
    </div>
    <div v-else class="flex min-h-[calc(100vh-9rem)] flex-col">
      <!-- 任务已加载时也要能看到操作失败的原因（例如消息发送被拒绝） -->
      <p v-if="error" role="alert" data-testid="task-action-error" class="border-b border-red-400/30 bg-red-400/10 px-4 py-2 text-xs text-red-300">
        {{ error }}
      </p>
      <div data-testid="task-workbench-status" class="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2 text-xs">
        <RouterLink :to="'/instances/' + encodeURIComponent(instanceId)" class="text-blue-300 hover:underline">{{ instance?.name }}</RouterLink>
        <span class="text-muted-foreground">›</span><span class="font-mono">{{ taskId }}</span>
        <span class="rounded bg-background px-2 py-0.5">{{ taskState }}</span>
        <span data-testid="instance-state" class="rounded bg-background px-2 py-0.5">实例 {{ instanceState }}</span>
        <span class="rounded bg-background px-2 py-0.5">Attempt {{ selectedAttemptId || attemptCount }}</span>
        <span v-if="replayAttemptId" class="rounded border border-blue-500/30 bg-blue-500/10 px-2 py-0.5 text-blue-300">
          回放历史轮次
        </span>
        <span class="rounded bg-background px-2 py-0.5">{{ capabilityText }}</span>
        <button v-if="taskState === 'AWAITING_REVIEW'" data-testid="task-approve" class="rounded border border-amber-400/50 px-2 py-1 text-amber-300 disabled:opacity-50" :disabled="approving" @click="approve">批准验证</button>
        <button data-testid="task-rewind" class="rounded border border-amber-400/50 px-2 py-1 text-amber-300 disabled:opacity-50" :disabled="rewinding" @click="rewind">从此节点重来</button>
        <button class="ml-auto rounded border border-border px-2 py-1 disabled:opacity-50" :disabled="retrying" @click="retry">重试任务</button>
      </div>

      <!-- 执行历史：每一轮（attempt）的结果、耗时与失败原因 -->
      <section data-testid="attempt-history" class="border-b border-border px-4 py-2 text-xs">
        <button class="flex w-full items-center gap-2 text-left" @click="historyOpen = !historyOpen">
          <span class="font-semibold text-muted-foreground">执行历史</span>
          <span class="rounded bg-background px-1.5 py-0.5">{{ attemptHistory.length }} 次</span>
          <span v-if="failedAttempts" class="rounded border border-red-400/30 bg-red-400/10 px-1.5 py-0.5 text-red-300">失败 {{ failedAttempts }} 次</span>
          <span v-if="failCount" class="rounded border border-amber-400/30 bg-amber-400/10 px-1.5 py-0.5 text-amber-300">
            回退 {{ failCount }}/{{ retryFeedback.max_attempts ?? '—' }}
          </span>
          <span v-if="retryFeedback.attempt" class="rounded border border-amber-400/30 bg-amber-400/10 px-1.5 py-0.5 text-amber-300">
            上一轮验证未通过，反馈已注入本轮提示词
          </span>
          <span class="ml-auto text-muted-foreground">{{ historyOpen ? '收起' : '展开' }}</span>
        </button>

        <div v-if="historyOpen" class="mt-2 space-y-2">
          <div v-if="retryFeedback.failed_checks?.length" class="rounded border border-amber-400/30 bg-amber-400/10 p-2 text-[11px] text-amber-100">
            <p class="font-semibold">上一轮回退原因（{{ formatTime(retryFeedback.at || '') }}）</p>
            <p v-for="check in retryFeedback.failed_checks" :key="check.task" class="mt-1 font-mono">
              {{ check.task }}: {{ check.error || '未记录原因' }}
            </p>
          </div>

          <div v-if="!attemptHistory.length" class="text-muted-foreground">还没有执行记录。</div>
          <table v-else class="w-full table-fixed border-collapse text-left text-[11px]">
            <thead class="text-muted-foreground">
              <tr>
                <th class="w-14 py-1 font-medium">轮次</th>
                <th class="w-24 py-1 font-medium">状态</th>
                <th class="w-40 py-1 font-medium">开始</th>
                <th class="w-20 py-1 font-medium">耗时</th>
                <th class="w-20 py-1 font-medium">Agent</th>
                <th class="py-1 font-medium">失败原因 / 输出</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(attempt, index) in attemptHistory" :key="attempt.attempt_id" class="border-t border-border/50 align-top">
                <td class="py-1 font-mono">#{{ index + 1 }}</td>
                <td class="py-1">
                  <span class="rounded border px-1.5 py-0.5" :class="attemptStateClass(attempt.state)">{{ attempt.state }}</span>
                </td>
                <td class="py-1 font-mono text-muted-foreground">{{ formatTime(attempt.started_at || attempt.created_at) }}</td>
                <td class="py-1 font-mono text-muted-foreground">{{ formatDuration(attempt.duration_ms) }}</td>
                <td class="py-1 font-mono text-muted-foreground">{{ attempt.provider || '—' }}</td>
                <td class="py-1">
                  <p v-if="attempt.failure_reason" class="text-red-300">{{ attempt.failure_reason }}</p>
                  <p v-else-if="attempt.output_excerpt" class="max-h-16 overflow-auto whitespace-pre-wrap text-muted-foreground">{{ attempt.output_excerpt }}</p>
                  <p v-else class="text-muted-foreground">—</p>
                  <div v-if="attempt.session_id" class="mt-1 flex flex-wrap items-center gap-2">
                    <RouterLink
                      :to="`/instances/${encodeURIComponent(instanceId)}/tasks/${encodeURIComponent(taskId)}?attempt=${encodeURIComponent(attempt.attempt_id)}`"
                      class="font-mono text-[10px] text-blue-300 hover:underline"
                      title="回放这一轮的 Agent 会话">
                      回放会话 · {{ attempt.session_id }}
                    </RouterLink>
                    <button class="text-[10px] text-muted-foreground hover:underline" @click="selectAttemptById(attempt.attempt_id)">
                      查看工作区
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <div data-testid="task-workbench" class="grid flex-1 grid-cols-1 min-h-0 xl:grid-cols-[minmax(20rem,0.9fr)_minmax(16rem,0.65fr)_minmax(28rem,1.25fr)]">
        <section aria-label="Agent 会话" class="flex min-h-0 flex-col border-border p-3 xl:border-r">
          <div class="mb-2 flex flex-wrap items-center gap-2">
            <h2 class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Agent Conversation</h2>
            <span class="rounded border border-border px-1.5 py-0.5 font-mono text-[10px]">{{ sessionMapping?.provider || '—' }}</span>
            <span class="max-w-40 truncate font-mono text-[10px] text-muted-foreground" :title="sessionMapping?.session_id || ''">{{ sessionMapping?.session_id || '无会话' }}</span>
            <span v-if="taskRunning" class="flex items-center gap-1 rounded border border-blue-500/30 bg-blue-500/10 px-1.5 py-0.5 text-[10px] text-blue-300">
              <span class="size-1.5 animate-pulse rounded-full bg-blue-400" />实时
            </span>
            <span v-else class="rounded border px-1.5 py-0.5 text-[10px]"
              :class="acpConnected ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300' : acpStatus === 'error' ? 'border-red-400/30 bg-red-400/10 text-red-300' : 'border-border text-muted-foreground'">
              {{ acpStatusLabel }}
            </span>
            <button data-testid="task-acp-toggle"
              class="ml-auto flex items-center gap-1 rounded border border-border px-2 py-1 text-[11px] disabled:opacity-50"
              :disabled="!acpConnected && (acpStatus === 'connecting' || Boolean(connectBlockedReason))"
              @click="toggleAcpConnection">
              <Unplug v-if="acpConnected" :size="12" /><Plug v-else :size="12" />
              {{ acpConnected ? '断开' : '连接' }}
            </button>
          </div>
          <p v-if="connectBlockedReason" class="mb-2 rounded border border-amber-400/20 bg-amber-400/10 px-2 py-1 text-[11px] text-amber-200">
            {{ connectBlockedReason }}
          </p>
          <p v-if="acpError" role="alert" class="mb-2 rounded border border-red-400/20 bg-red-400/10 px-2 py-1 text-[11px] text-red-300">{{ acpError }}</p>
          <p v-if="liveError" role="alert" class="mb-2 rounded border border-red-400/20 bg-red-400/10 px-2 py-1 text-[11px] text-red-300">{{ liveError }}</p>

          <AcpConversation :items="acpItems" :permission="acpPermission"
            :empty-title="taskRunning ? '等待 Agent 输出…' : '尚未连接'"
            :empty-description="taskRunning ? '任务执行中的工具调用和消息会实时出现在这里。' : '连接后读取 Agent 自己保存的会话历史，断开即释放进程。'"
            @answer-permission="answerPermission" />

          <form class="mt-3 shrink-0 space-y-2 border-t border-border pt-3" @submit.prevent="submitAcp">
            <textarea v-model="acpDraft" data-testid="task-acp-input" :disabled="!acpConnected"
              class="min-h-16 w-full rounded border border-border bg-background p-2 text-sm disabled:opacity-50"
              placeholder="连接后可继续对话（Enter 发送，Shift+Enter 换行）"
              @keydown.enter.exact.prevent="submitAcp" />
            <div class="flex gap-2">
              <button data-testid="task-acp-send" type="submit"
                class="rounded bg-blue-500 px-3 py-1.5 text-xs text-white disabled:opacity-50"
                :disabled="!acpConnected || acpBusy || !acpDraft.trim()">发送</button>
              <button v-if="acpBusy" type="button" class="rounded border border-border px-3 py-1.5 text-xs" @click="cancelAcp()">停止</button>
            </div>
          </form>
        </section>

        <section aria-label="工作区文件" class="flex min-h-0 flex-col border-border p-3 xl:border-r">
          <div class="mb-2 flex items-center gap-2">
            <h2 class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Workspace</h2>
            <select class="ml-auto max-w-40 rounded border border-border bg-background px-1 py-1 text-[10px]" :value="selectedAttemptId" aria-label="Attempt" @change="selectAttempt">
              <option v-for="attempt in attempts" :key="attempt.attempt_id" :value="attempt.attempt_id">
                {{ attempt.attempt_id }} · {{ attempt.binding_type }}
              </option>
              <option v-if="!attempts.length" value="">无绑定</option>
            </select>
          </div>
          <div v-if="workspaceLoading" class="grid flex-1 place-items-center text-xs text-muted-foreground"><LoaderCircle class="animate-spin" /></div>
          <div v-else-if="!binding" class="rounded border border-amber-400/30 bg-amber-400/10 p-3 text-xs text-amber-200">任务尚无 Workspace Attempt Binding。</div>
          <template v-else>
            <div class="mb-2 grid grid-cols-[1fr_auto] gap-1">
              <input v-model="searchQuery" class="rounded border border-border bg-background px-2 py-1 text-[11px]" placeholder="搜索工作区文件" @keyup.enter="searchFiles" />
              <button class="rounded border border-border px-2" aria-label="搜索文件" @click="searchFiles"><Search :size="12" /></button>
            </div>
            <div v-if="searchResults.length" class="mb-2 max-h-24 overflow-auto rounded border border-border p-1 text-[10px]">
              <button v-for="result in searchResults" :key="String(result.path) + String(result.line)" class="block w-full truncate px-1 py-0.5 text-left hover:bg-accent" @click="openEntry({ name: String(result.path).split('/').pop() || '', path: String(result.path), type: 'file', size: 0 })">
                {{ result.path }}:{{ result.line }}
              </button>
            </div>
            <div class="my-2 truncate text-[11px] font-mono text-blue-300">/{{ currentPath }}</div>
            <button v-if="currentPath" class="mb-1 text-xs text-muted-foreground" @click="currentPath = ''; binding && listWorkspaceTree(binding).then(value => entries = value)">返回根目录</button>
            <div class="flex-1 overflow-auto">
              <button v-for="entry in entries" :key="entry.path" data-testid="workspace-file-entry" class="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-accent" @click="openEntry(entry)">
                <Folder v-if="entry.type === 'directory'" :size="13" class="text-amber-300" /><File v-else :size="13" class="text-slate-400" />
                <span class="truncate">{{ entry.name }}</span>
              </button>
            </div>
            <div class="mt-3 border-t border-border pt-3">
              <h3 class="text-[11px] font-semibold uppercase text-muted-foreground">Runtime</h3>
              <div class="mt-1 text-[10px] text-muted-foreground">{{ binding.binding_type }} · {{ manifest?.files?.length || 0 }} manifest files · {{ changes.length }} changes</div>
              <button v-for="change in changes.slice(0, 6)" :key="change.path" class="mt-1 block w-full truncate text-left text-[10px] font-mono text-amber-300" :title="change.path">{{ change.status }} {{ change.path }}</button>
              <div class="mt-2 space-y-1">
                <button v-for="action in actions" :key="action.action_id" class="flex w-full items-center gap-2 rounded border border-border px-2 py-1.5 text-left text-xs hover:bg-accent disabled:opacity-50" :disabled="Boolean(actionRunning)" :title="action.description" @click="executeAction(action.action_id)">
                  <Play :size="11" />{{ action.label }}
                </button>
                <div class="flex gap-1">
                  <input v-model="checkpointMessage" class="min-w-0 flex-1 rounded border border-border bg-background px-2 py-1 text-xs" placeholder="Checkpoint 说明" />
                  <button class="rounded border border-border p-1.5 disabled:opacity-50" :disabled="!checkpointMessage.trim()" aria-label="创建 Checkpoint" @click="checkpoint"><GitCommit :size="13" /></button>
                  <button class="rounded border border-border p-1.5" aria-label="刷新变更" @click="binding && listWorkspaceChanges(binding).then(value => changes = value)"><RefreshCw :size="13" /></button>
                </div>
              </div>
              <pre v-if="actionResult" class="mt-2 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-background p-2 text-[10px]" :class="actionResult.exit_code ? 'text-red-300' : 'text-emerald-300'">{{ actionResult.stdout || actionResult.stderr || `exit ${actionResult.exit_code}` }}</pre>
            </div>
          </template>
        </section>

        <section aria-label="代码编辑器" class="flex min-h-0 flex-col">
          <div class="flex h-10 items-center border-b border-border px-3 text-xs font-mono">
            <div class="flex min-w-0 flex-1 gap-1 overflow-auto">
              <button v-for="tab in openFiles" :key="tab.path" class="max-w-40 truncate rounded px-2 py-1" :class="tab.path === file?.path ? 'bg-accent text-foreground' : 'text-muted-foreground'" @click="selectTab(tab)">{{ tab.path }}</button>
            </div>
            <button class="ml-2 flex items-center gap-1 rounded border border-border px-2 py-1 text-[10px]" @click="showDiff"><GitBranch :size="12" />Diff</button>
            <button v-if="file?.writable" class="ml-2 flex items-center gap-1 rounded bg-blue-500 px-2 py-1 text-white disabled:opacity-50" :disabled="saving" @click="save"><Save :size="12" />保存</button>
          </div>
          <div v-if="workspaceError" role="alert" class="border-b border-amber-400/20 bg-amber-400/10 px-3 py-2 text-xs text-amber-200">{{ workspaceError }}</div>
          <div v-if="conflict" class="border-b border-amber-400/30 bg-amber-400/10 p-3 text-xs text-amber-100">
            <strong>检测到并发修改，未覆盖当前文件。</strong>
            <button class="mt-2 rounded border border-border px-2 py-1" @click="acceptCurrentVersion">采用当前版本并保存草稿</button>
          </div>
          <pre v-if="diffText" class="max-h-48 overflow-auto border-b border-border bg-background p-3 text-[10px]">{{ diffText }}</pre>
          <MonacoEditor
            v-if="file && file.content !== null"
            v-model:value="draft"
            class="min-h-[30rem] flex-1"
            theme="vs-dark"
            :language="file.path.endsWith('.py') ? 'python' : file.path.endsWith('.ts') ? 'typescript' : 'plaintext'"
            :options="{ readOnly: !file.writable, automaticLayout: true, minimap: { enabled: true }, editContext: false }"
            @change="rememberDraft"
          />
          <div v-else-if="file" class="grid flex-1 place-items-center text-sm text-muted-foreground">{{ file.reason || '二进制文件无法预览' }}</div>
          <div v-else class="grid flex-1 place-items-center p-6 text-center text-sm text-muted-foreground">从文件树选择文件进行预览或编辑</div>
        </section>
      </div>
    </div>
  </AppShell>
</template>
