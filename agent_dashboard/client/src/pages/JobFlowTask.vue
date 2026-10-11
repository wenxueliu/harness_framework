<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, File, Folder, GitBranch, GitCommit, LoaderCircle, Play, RefreshCw, Save, Search } from 'lucide-vue-next'
import AppShell from '@/layouts/AppShell.vue'
import ExecutionTimeline from '@/components/ExecutionTimeline.vue'
import { fetchTaskMessages, fetchTaskSessionEvents, sendTaskMessage, type HumanMessage } from '@/lib/harnessApi'
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
  retryJobFlowTask,
  type JobFlowInstance,
} from '@/api/jobFlow'
import type {
  AttemptWorkspaceBinding,
  SessionEvent,
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

const loading = ref(true)
const workspaceLoading = ref(false)
const retrying = ref(false)
const sending = ref(false)
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
const messages = ref<HumanMessage[]>([])
const events = ref<SessionEvent[]>([])
const actions = ref<WorkspaceAction[]>([])
const actionResult = ref<WorkspaceActionResult | null>(null)
const actionRunning = ref('')
const changes = ref<WorkspaceChange[]>([])
const searchQuery = ref('')
const searchResults = ref<Array<Record<string, unknown>>>([])
const manifest = ref<WorkspaceManifest | null>(null)
const diffText = ref('')
const message = ref('')
const messageMode = ref<'queue' | 'interrupt'>('queue')
const checkpointMessage = ref('')
const preflightStatus = ref('')

const capabilityText = computed(() => {
  if (!preflightStatus.value) return 'Preflight 未知'
  if (preflightStatus.value === 'PASSED') return '✅ Preflight 通过'
  if (preflightStatus.value === 'IDLE') return 'Preflight 未执行'
  return '⚠ Preflight 阻塞'
})

function goBack() {
  if (window.history.length > 1) router.back()
  else router.push('/instances/' + encodeURIComponent(instanceId))
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

async function loadCollaboration() {
  try {
    const [session, human] = await Promise.all([
      fetchTaskSessionEvents(instanceId, taskId, { attemptId: selectedAttemptId.value || undefined }),
      fetchTaskMessages(instanceId, taskId),
    ])
    events.value = session.events
    messages.value = human
  } catch (cause) {
    if (!(cause instanceof HarnessApiError && cause.status === 404)) {
      workspaceError.value = cause instanceof Error ? cause.message : '协作记录加载失败'
    }
  }
}

async function selectAttempt(event: Event) {
  const attemptId = (event.target as HTMLSelectElement).value
  selectedAttemptId.value = attemptId
  await Promise.all([loadWorkspace(attemptId), loadCollaboration()])
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

async function send() {
  if (!message.value.trim()) return
  sending.value = true
  try {
    await sendTaskMessage(instanceId, taskId, { message: message.value.trim(), mode: messageMode.value })
    message.value = ''
    messages.value = await fetchTaskMessages(instanceId, taskId)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '消息发送失败'
  } finally {
    sending.value = false
  }
}

async function retry() {
  retrying.value = true
  try {
    await retryJobFlowTask(instanceId, taskId)
    await Promise.all([loadTask(), loadWorkspace(), loadCollaboration()])
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '任务重试失败'
  } finally {
    retrying.value = false
  }
}

onMounted(async () => {
  try {
    await loadTask()
    try {
      const preflight = await getJobFlowPreflight(instanceId)
      preflightStatus.value = preflight.status
    } catch { preflightStatus.value = '' }
    await Promise.all([loadWorkspace(), loadCollaboration()])
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '任务加载失败'
  } finally {
    loading.value = false
  }
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
      <div data-testid="task-workbench-status" class="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2 text-xs">
        <RouterLink :to="'/instances/' + encodeURIComponent(instanceId)" class="text-blue-300 hover:underline">{{ instance?.name }}</RouterLink>
        <span class="text-muted-foreground">›</span><span class="font-mono">{{ taskId }}</span>
        <span class="rounded bg-background px-2 py-0.5">{{ taskState }}</span>
        <span class="rounded bg-background px-2 py-0.5">Attempt {{ selectedAttemptId || attemptCount }}</span>
        <span class="rounded bg-background px-2 py-0.5">{{ capabilityText }}</span>
        <button class="ml-auto rounded border border-border px-2 py-1 disabled:opacity-50" :disabled="retrying" @click="retry">重试任务</button>
      </div>

      <div data-testid="task-workbench" class="grid flex-1 grid-cols-1 min-h-0 xl:grid-cols-[minmax(20rem,0.9fr)_minmax(16rem,0.65fr)_minmax(28rem,1.25fr)]">
        <section aria-label="Agent 会话" class="flex min-h-0 flex-col border-border p-3 xl:border-r">
          <h2 class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Agent Collaboration</h2>
          <div class="min-h-48 flex-1 overflow-auto"><ExecutionTimeline :events="events" /></div>
          <div class="mt-3 space-y-2 border-t border-border pt-3">
            <textarea v-model="message" data-testid="human-message-input" class="min-h-20 w-full rounded border border-border bg-background p-2 text-sm" placeholder="发送人工消息" />
            <div class="flex gap-2">
              <select v-model="messageMode" class="rounded border border-border bg-background px-2 text-xs" aria-label="消息模式">
                <option value="queue">排队</option><option value="interrupt">立即中断</option>
              </select>
              <button data-testid="send-human-message" class="rounded bg-blue-500 px-3 py-1.5 text-xs text-white disabled:opacity-50" :disabled="sending" @click="send">发送</button>
            </div>
            <p class="text-[11px] text-muted-foreground">已记录 {{ messages.length }} 条人工消息</p>
          </div>
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
            :options="{ readOnly: !file.writable, automaticLayout: true, minimap: { enabled: true } }"
            @change="rememberDraft"
          />
          <div v-else-if="file" class="grid flex-1 place-items-center text-sm text-muted-foreground">{{ file.reason || '二进制文件无法预览' }}</div>
          <div v-else class="grid flex-1 place-items-center p-6 text-center text-sm text-muted-foreground">从文件树选择文件进行预览或编辑</div>
        </section>
      </div>
    </div>
  </AppShell>
</template>
