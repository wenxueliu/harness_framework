<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ChevronRight, File, Folder, LoaderCircle, Play, Save, GitCommit, Search, GitBranch, RefreshCw } from 'lucide-vue-next'
import AppShell from '@/layouts/AppShell.vue'
import ExecutionTimeline from '@/components/ExecutionTimeline.vue'
import { fetchTaskMessages, fetchTaskSessionEvents, sendTaskMessage, type HumanMessage } from '@/lib/harnessApi'
import { applyMergeTask, createMergeTask, createWorkspaceCheckpoint, getRunManifest, getWorkspaceDiff, listAttempts, listRuns, listWorkspaceActions, listWorkspaceChanges, listWorkspaceTree, readWorkspaceFile, runWorkspaceAction, saveWorkspaceFile, searchWorkspace } from '@/api/dashboard'
import { HarnessApiError } from '@/api/client'
import type { AttemptWorkspaceBinding, RunSummary, SessionEvent, WorkspaceAction, WorkspaceActionResult, WorkspaceChange, WorkspaceFile, WorkspaceManifest, WorkspaceTreeEntry } from '@/api/types'

const MonacoEditor = defineAsyncComponent(async () => {
  const module = await import('@guolao/vue-monaco-editor')
  return module.VueMonacoEditor
})
const route = useRoute()
const groupId = computed(() => String(route.params.groupId || 'unassigned'))
const reqId = computed(() => String(route.params.workflowId || ''))
const taskId = computed(() => String(route.params.taskId || ''))
const loading = ref(true)
const error = ref('')
const binding = ref<AttemptWorkspaceBinding | null>(null)
const runs = ref<RunSummary[]>([])
const selectedRunId = ref('')
const attempts = ref<AttemptWorkspaceBinding[]>([])
const entries = ref<WorkspaceTreeEntry[]>([])
const currentPath = ref('')
const file = ref<WorkspaceFile | null>(null)
const openFiles = ref<WorkspaceFile[]>([])
const draft = ref('')
const baseContent = ref('')
const conflict = ref<{ currentSha: string | null; currentContent: string } | null>(null)
const saving = ref(false)
const message = ref('')
const messageMode = ref<'queue' | 'interrupt'>('queue')
const messages = ref<HumanMessage[]>([])
const events = ref<SessionEvent[]>([])
const actions = ref<WorkspaceAction[]>([])
const actionResult = ref<WorkspaceActionResult | null>(null)
const actionRunning = ref('')
const checkpointMessage = ref('')
const changes = ref<WorkspaceChange[]>([])
const searchQuery = ref('')
const searchResults = ref<Array<Record<string, unknown>>>([])
const manifest = ref<WorkspaceManifest | null>(null)
const diffText = ref('')
const mergeSourceAttempt = ref('')
const mergeTask = ref<{ merge_id: string; status: string; diff?: string } | null>(null)

function draftKey(item: WorkspaceFile) {
  return `harness-draft:${binding.value?.binding_id}:${item.path}:${item.sha256 || 'new'}`
}

async function load() {
  loading.value = true; error.value = ''
  try {
    runs.value = await listRuns(reqId.value)
    const run = runs.value.find((item) => item.status === 'RUNNING') || runs.value[0]
    if (!run) throw new Error('该工作流还没有 Run')
    selectedRunId.value = run.run_id
    await loadBinding(run.run_id)
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '工作台加载失败' }
  finally { loading.value = false }
}

async function loadBinding(runId: string, attemptId?: string) {
  attempts.value = await listAttempts(reqId.value, taskId.value, runId)
  binding.value = attempts.value.find((item) => item.attempt_id === attemptId) || attempts.value.at(-1) || null
  if (!binding.value) throw new Error('该任务尚无 Attempt Workspace Binding')
  mergeSourceAttempt.value = attempts.value.find((item) => item.attempt_id !== binding.value?.attempt_id)?.attempt_id || ''
  openFiles.value = []; file.value = null; diffText.value = ''
  ;[entries.value, actions.value, changes.value, manifest.value] = await Promise.all([
    listWorkspaceTree(binding.value), listWorkspaceActions(binding.value), listWorkspaceChanges(binding.value), getRunManifest(reqId.value, runId),
  ])
  const [session, human] = await Promise.all([
    fetchTaskSessionEvents(reqId.value, taskId.value, { runId, attemptId: binding.value.attempt_id }), fetchTaskMessages(reqId.value, taskId.value),
  ])
  events.value = session.events; messages.value = human
}

async function selectAttempt(attemptId: string) {
  await loadBinding(selectedRunId.value, attemptId)
}

function onAttemptChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  void selectAttempt(value)
}

async function openEntry(entry: WorkspaceTreeEntry) {
  if (!binding.value) return
  if (entry.type === 'directory') {
    entries.value = await listWorkspaceTree(binding.value, entry.path)
    currentPath.value = entry.path
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

async function refreshChanges() {
  if (!binding.value) return
  changes.value = await listWorkspaceChanges(binding.value)
}

async function searchFiles() {
  if (!binding.value || !searchQuery.value.trim()) { searchResults.value = []; return }
  searchResults.value = await searchWorkspace(binding.value, searchQuery.value.trim())
}

async function showDiff() {
  if (!binding.value) return
  const result = await getWorkspaceDiff(binding.value, file.value?.path || '')
  diffText.value = result.diff || '当前没有 Git diff'
}

async function createMerge() {
  if (!binding.value || !mergeSourceAttempt.value || mergeSourceAttempt.value === binding.value.attempt_id) return
  mergeTask.value = await createMergeTask({ reqId: reqId.value, runId: selectedRunId.value, sourceTaskId: taskId.value, sourceAttemptId: mergeSourceAttempt.value, targetTaskId: taskId.value, targetAttemptId: binding.value.attempt_id, message: 'Dashboard explicit merge task' })
}

async function applyMerge() {
  if (!mergeTask.value) return
  mergeTask.value = await applyMergeTask(reqId.value, selectedRunId.value, mergeTask.value.merge_id)
  await refreshChanges()
}

async function save() {
  if (!binding.value || !file.value || !file.value.writable) return
  saving.value = true; error.value = ''
  try {
    const previousKey = draftKey(file.value)
    const result = await saveWorkspaceFile(binding.value, file.value, draft.value, 'Dashboard 人工修改')
    file.value = { ...file.value, content: draft.value, sha256: result.sha256 }
    baseContent.value = draft.value
    conflict.value = null
    sessionStorage.removeItem(previousKey)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '保存失败'
    if (cause instanceof HarnessApiError && cause.code === 'FILE_VERSION_CONFLICT' && binding.value && file.value) {
      const current = await readWorkspaceFile(binding.value, file.value.path)
      conflict.value = { currentSha: current.sha256, currentContent: current.content || '' }
    }
  }
  finally { saving.value = false }
}

function acceptCurrentVersion() {
  if (!file.value || !conflict.value) return
  file.value = { ...file.value, content: conflict.value.currentContent, sha256: conflict.value.currentSha }
  baseContent.value = conflict.value.currentContent
  draft.value = conflict.value.currentContent
  conflict.value = null
}

async function keepDraftAndRetry() {
  if (!file.value || !conflict.value) return
  file.value = { ...file.value, content: conflict.value.currentContent, sha256: conflict.value.currentSha }
  conflict.value = null
  await save()
}

function rememberDraft() {
  if (!file.value || draft.value === baseContent.value) return
  sessionStorage.setItem(draftKey(file.value), draft.value)
}

async function executeAction(actionId: string) {
  if (!binding.value) return
  actionRunning.value = actionId; error.value = ''
  try { actionResult.value = await runWorkspaceAction(binding.value, actionId) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : 'Action 执行失败' }
  finally { actionRunning.value = '' }
}

async function checkpoint() {
  if (!binding.value || !checkpointMessage.value.trim()) return
  try {
    const result = await createWorkspaceCheckpoint(binding.value, checkpointMessage.value.trim())
    checkpointMessage.value = ''
    actionResult.value = { action_id: 'checkpoint', exit_code: 0, stdout: `Created ${result.commit_sha}`, stderr: '', event_id: result.event_id }
  } catch (cause) { error.value = cause instanceof Error ? cause.message : 'Checkpoint 创建失败' }
}

async function send() {
  if (!message.value.trim()) return
  await sendTaskMessage(reqId.value, taskId.value, { message: message.value.trim(), mode: messageMode.value })
  message.value = ''
  messages.value = await fetchTaskMessages(reqId.value, taskId.value)
}

watch(selectedRunId, async (runId, previous) => {
  if (runId && runId !== previous && !loading.value) await loadBinding(runId)
})
onMounted(load)
</script>

<template>
  <AppShell :group-id="groupId" :workflow-id="reqId" :title="taskId" eyebrow="Task Workbench">
    <div v-if="loading" class="grid min-h-[60vh] place-items-center"><LoaderCircle class="animate-spin text-blue-300" /></div>
    <div v-else-if="error && !binding" role="alert" class="m-6 rounded border border-amber-400/30 bg-amber-400/10 p-4 text-sm text-amber-200">{{ error }}</div>
    <div v-else class="grid min-h-[calc(100vh-8rem)] grid-cols-1 xl:grid-cols-[minmax(18rem,0.8fr)_18rem_minmax(28rem,1.2fr)]">
      <section class="flex min-h-0 flex-col border-r border-border p-3">
        <div class="mb-2 flex items-center gap-2">
          <h2 class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Agent Collaboration</h2>
          <span class="ml-auto rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">{{ binding?.attempt_id || 'no attempt' }}</span>
        </div>
        <div class="min-h-48 flex-1 overflow-auto"><ExecutionTimeline :events="events" /></div>
        <div class="mt-3 space-y-2 border-t border-border pt-3">
          <textarea v-model="message" class="w-full rounded border border-border bg-background p-2 text-sm" rows="3" placeholder="补充信息或修复说明" />
          <div class="flex gap-2"><select v-model="messageMode" class="rounded border border-border bg-background px-2 text-xs"><option value="queue">排队</option><option value="interrupt">立即中断</option></select><button class="rounded bg-blue-500 px-3 py-1.5 text-xs text-white" @click="send">发送</button></div>
          <p class="text-[11px] text-muted-foreground">已记录 {{ messages.length }} 条人工消息</p>
        </div>
      </section>
      <section class="min-h-0 border-r border-border p-3">
        <div class="mb-2 flex items-center gap-2">
          <h2 class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Workspace Files</h2>
          <select v-model="selectedRunId" class="ml-auto max-w-40 rounded border border-border bg-background px-1.5 py-1 text-[10px]" aria-label="Run">
            <option v-for="run in runs" :key="run.run_id" :value="run.run_id">{{ run.run_id }} · {{ run.status }}</option>
          </select>
        </div>
        <select v-if="attempts.length" :value="binding?.attempt_id" class="mb-2 w-full rounded border border-border bg-background px-2 py-1 text-[11px]" aria-label="Attempt" @change="onAttemptChange">
          <option v-for="attempt in attempts" :key="attempt.attempt_id" :value="attempt.attempt_id">{{ attempt.attempt_id }} · {{ attempt.binding_type }}{{ attempt.writable ? ' · writable' : ' · read-only' }}</option>
        </select>
        <div class="mb-2 grid grid-cols-[1fr_auto] gap-1">
          <input v-model="searchQuery" class="rounded border border-border bg-background px-2 py-1 text-[11px]" placeholder="搜索工作区文件" @keyup.enter="searchFiles" />
          <button class="rounded border border-border px-2" title="搜索" @click="searchFiles"><Search :size="12" /></button>
        </div>
        <div v-if="searchResults.length" class="mb-2 max-h-24 overflow-auto rounded border border-border p-1 text-[10px]">
          <button v-for="result in searchResults" :key="String(result.path) + String(result.line)" class="block w-full truncate px-1 py-0.5 text-left hover:bg-accent" @click="openEntry({ name: String(result.path).split('/').pop() || '', path: String(result.path), type: 'file', size: 0 })">{{ result.path }}:{{ result.line }}</button>
        </div>
        <div class="my-2 truncate text-[11px] font-mono text-blue-300">/{{ currentPath }}</div>
        <button v-if="currentPath" class="mb-1 text-xs text-muted-foreground" @click="currentPath = ''; binding && listWorkspaceTree(binding).then(value => entries = value)">返回根目录</button>
        <button v-for="entry in entries" :key="entry.path" data-testid="workspace-file-entry" class="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-accent" @click="openEntry(entry)">
          <Folder v-if="entry.type === 'directory'" :size="13" class="text-amber-300" /><File v-else :size="13" class="text-slate-400" /><span class="truncate">{{ entry.name }}</span><ChevronRight v-if="entry.type === 'directory'" :size="11" class="ml-auto" />
        </button>
        <div class="mt-4 border-t border-border pt-3">
          <div class="mb-2 flex items-center"><h3 class="text-[11px] font-semibold uppercase text-muted-foreground">Workspace Runtime</h3><button class="ml-auto text-muted-foreground" title="刷新 changes" @click="refreshChanges"><RefreshCw :size="12" /></button></div>
          <div class="mb-2 text-[10px] text-muted-foreground">run={{ selectedRunId }} · {{ binding?.binding_type }} · {{ manifest?.files?.length || 0 }} manifest files</div>
          <div v-if="changes.length" class="mb-2 max-h-20 overflow-auto rounded border border-border p-1 text-[10px]"><div v-for="change in changes" :key="change.path" class="font-mono"><span class="mr-1 text-amber-300">{{ change.status }}</span>{{ change.path }}</div></div>
          <div v-if="attempts.length > 1" class="mb-3 rounded border border-border p-2"><div class="mb-1 text-[10px] text-muted-foreground">Explicit Merge Task</div><div class="flex gap-1"><select v-model="mergeSourceAttempt" class="min-w-0 flex-1 rounded border border-border bg-background px-1 py-1 text-[10px]"><option v-for="attempt in attempts.filter((item) => item.attempt_id !== binding?.attempt_id)" :key="attempt.attempt_id" :value="attempt.attempt_id">源 {{ attempt.attempt_id }}</option></select><button class="rounded bg-amber-500 px-2 py-1 text-[10px] text-black" @click="createMerge">创建</button></div><div v-if="mergeTask" class="mt-1 text-[10px]">{{ mergeTask.merge_id }} · {{ mergeTask.status }} <button v-if="mergeTask.status !== 'DONE'" class="ml-1 text-amber-300 underline" @click="applyMerge">应用合并</button></div></div>
          <h3 class="mb-2 text-[11px] font-semibold uppercase text-muted-foreground">受控 Actions</h3>
          <button v-for="action in actions" :key="action.action_id" class="mb-1 flex w-full items-center gap-2 rounded border border-border px-2 py-1.5 text-left text-xs hover:bg-accent disabled:opacity-50" :disabled="Boolean(actionRunning)" :title="action.description" @click="executeAction(action.action_id)"><Play :size="11" />{{ action.label }}</button>
          <div class="mt-3 flex gap-1"><input v-model="checkpointMessage" class="min-w-0 flex-1 rounded border border-border bg-background px-2 py-1 text-xs" placeholder="Checkpoint 说明" /><button class="rounded border border-border p-1.5 disabled:opacity-50" :disabled="!checkpointMessage.trim()" title="创建 Git Checkpoint" @click="checkpoint"><GitCommit :size="13" /></button></div>
          <pre v-if="actionResult" class="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-background p-2 text-[10px]" :class="actionResult.exit_code ? 'text-red-300' : 'text-emerald-300'">{{ actionResult.stdout || actionResult.stderr || `exit ${actionResult.exit_code}` }}</pre>
        </div>
      </section>
      <section class="flex min-h-0 flex-col">
        <div class="flex h-10 items-center border-b border-border px-3 text-xs font-mono"><div class="flex min-w-0 flex-1 gap-1 overflow-auto"> <button v-for="tab in openFiles" :key="tab.path" class="max-w-40 truncate rounded px-2 py-1" :class="tab.path === file?.path ? 'bg-accent text-foreground' : 'text-muted-foreground'" @click="selectTab(tab)">{{ tab.path }}</button></div><button class="ml-2 flex items-center gap-1 rounded border border-border px-2 py-1 text-[10px]" @click="showDiff"><GitBranch :size="12" />Diff</button><button v-if="file?.writable" class="ml-2 flex items-center gap-1 rounded bg-blue-500 px-2 py-1 text-white disabled:opacity-50" :disabled="saving" @click="save"><Save :size="12" />保存</button></div>
        <div v-if="error" role="alert" class="border-b border-red-400/20 bg-red-400/10 px-3 py-2 text-xs text-red-200">{{ error }}</div>
        <div v-if="conflict" class="border-b border-amber-400/30 bg-amber-400/10 p-3 text-xs text-amber-100"><strong>检测到并发修改，未覆盖当前文件。</strong><div class="mt-2 flex gap-2"><button class="rounded border border-border px-2 py-1" @click="acceptCurrentVersion">采用当前版本</button><button class="rounded bg-amber-500 px-2 py-1 text-black" @click="keepDraftAndRetry">保留草稿并重试</button></div><div class="mt-2 grid gap-2 md:grid-cols-3"><pre class="overflow-auto rounded bg-background p-2">Base\n{{ baseContent }}</pre><pre class="overflow-auto rounded bg-background p-2">Current\n{{ conflict.currentContent }}</pre><pre class="overflow-auto rounded bg-background p-2">Draft\n{{ draft }}</pre></div></div>
        <pre v-if="diffText" class="max-h-48 overflow-auto border-b border-border bg-background p-3 text-[10px] text-slate-300">{{ diffText }}</pre>
        <MonacoEditor v-if="file?.content !== null && file" v-model:value="draft" class="min-h-[30rem] flex-1" theme="vs-dark" :language="file.path.endsWith('.py') ? 'python' : file.path.endsWith('.ts') ? 'typescript' : 'plaintext'" :options="{ readOnly: !file.writable, automaticLayout: true, minimap: { enabled: true } }" @change="rememberDraft" />
        <div v-else class="grid flex-1 place-items-center text-sm text-muted-foreground">{{ file?.reason || '从文件树选择可预览文件' }}</div>
      </section>
    </div>
  </AppShell>
</template>
