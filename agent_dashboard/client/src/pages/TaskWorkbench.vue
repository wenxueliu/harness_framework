<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ChevronRight, File, Folder, LoaderCircle, Play, Save, GitCommit } from 'lucide-vue-next'
import AppShell from '@/layouts/AppShell.vue'
import ExecutionTimeline from '@/components/ExecutionTimeline.vue'
import { fetchTaskMessages, fetchTaskSessionEvents, sendTaskMessage, type HumanMessage } from '@/lib/harnessApi'
import { createWorkspaceCheckpoint, listAttempts, listRuns, listWorkspaceActions, listWorkspaceTree, readWorkspaceFile, runWorkspaceAction, saveWorkspaceFile } from '@/api/dashboard'
import { HarnessApiError } from '@/api/client'
import type { AttemptWorkspaceBinding, RunSummary, SessionEvent, WorkspaceAction, WorkspaceActionResult, WorkspaceFile, WorkspaceTreeEntry } from '@/api/types'

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
const attempts = ref<AttemptWorkspaceBinding[]>([])
const entries = ref<WorkspaceTreeEntry[]>([])
const currentPath = ref('')
const file = ref<WorkspaceFile | null>(null)
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

function draftKey(item: WorkspaceFile) {
  return `harness-draft:${binding.value?.binding_id}:${item.path}:${item.sha256 || 'new'}`
}

async function load() {
  loading.value = true; error.value = ''
  try {
    runs.value = await listRuns(reqId.value)
    const run = runs.value.find((item) => item.status === 'RUNNING') || runs.value[0]
    if (!run) throw new Error('该工作流还没有 Run')
    attempts.value = await listAttempts(reqId.value, taskId.value, run.run_id)
    binding.value = attempts.value.at(-1) || null
    if (!binding.value) throw new Error('该任务尚无 Attempt Workspace Binding')
    ;[entries.value, actions.value] = await Promise.all([
      listWorkspaceTree(binding.value), listWorkspaceActions(binding.value),
    ])
    const [session, human] = await Promise.all([
      fetchTaskSessionEvents(reqId.value, taskId.value),
      fetchTaskMessages(reqId.value, taskId.value),
    ])
    events.value = session.events; messages.value = human
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '工作台加载失败' }
  finally { loading.value = false }
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
  baseContent.value = file.value.content || ''
  draft.value = sessionStorage.getItem(draftKey(file.value)) ?? baseContent.value
  conflict.value = null
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

onMounted(load)
</script>

<template>
  <AppShell :group-id="groupId" :workflow-id="reqId" :title="taskId" eyebrow="Task Workbench">
    <div v-if="loading" class="grid min-h-[60vh] place-items-center"><LoaderCircle class="animate-spin text-blue-300" /></div>
    <div v-else-if="error && !binding" role="alert" class="m-6 rounded border border-amber-400/30 bg-amber-400/10 p-4 text-sm text-amber-200">{{ error }}</div>
    <div v-else class="grid min-h-[calc(100vh-8rem)] grid-cols-1 xl:grid-cols-[minmax(18rem,0.8fr)_18rem_minmax(28rem,1.2fr)]">
      <section class="flex min-h-0 flex-col border-r border-border p-3">
        <h2 class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Agent Collaboration</h2>
        <div class="min-h-48 flex-1 overflow-auto"><ExecutionTimeline :events="events" /></div>
        <div class="mt-3 space-y-2 border-t border-border pt-3">
          <textarea v-model="message" class="w-full rounded border border-border bg-background p-2 text-sm" rows="3" placeholder="补充信息或修复说明" />
          <div class="flex gap-2"><select v-model="messageMode" class="rounded border border-border bg-background px-2 text-xs"><option value="queue">排队</option><option value="interrupt">立即中断</option></select><button class="rounded bg-blue-500 px-3 py-1.5 text-xs text-white" @click="send">发送</button></div>
          <p class="text-[11px] text-muted-foreground">已记录 {{ messages.length }} 条人工消息</p>
        </div>
      </section>
      <section class="min-h-0 border-r border-border p-3">
        <h2 class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Workspace Files</h2>
        <div class="my-2 truncate text-[11px] font-mono text-blue-300">/{{ currentPath }}</div>
        <button v-if="currentPath" class="mb-1 text-xs text-muted-foreground" @click="currentPath = ''; binding && listWorkspaceTree(binding).then(value => entries = value)">返回根目录</button>
        <button v-for="entry in entries" :key="entry.path" class="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-accent" @click="openEntry(entry)">
          <Folder v-if="entry.type === 'directory'" :size="13" class="text-amber-300" /><File v-else :size="13" class="text-slate-400" /><span class="truncate">{{ entry.name }}</span><ChevronRight v-if="entry.type === 'directory'" :size="11" class="ml-auto" />
        </button>
        <div class="mt-4 border-t border-border pt-3">
          <h3 class="mb-2 text-[11px] font-semibold uppercase text-muted-foreground">受控 Actions</h3>
          <button v-for="action in actions" :key="action.action_id" class="mb-1 flex w-full items-center gap-2 rounded border border-border px-2 py-1.5 text-left text-xs hover:bg-accent disabled:opacity-50" :disabled="Boolean(actionRunning)" :title="action.description" @click="executeAction(action.action_id)"><Play :size="11" />{{ action.label }}</button>
          <div class="mt-3 flex gap-1"><input v-model="checkpointMessage" class="min-w-0 flex-1 rounded border border-border bg-background px-2 py-1 text-xs" placeholder="Checkpoint 说明" /><button class="rounded border border-border p-1.5 disabled:opacity-50" :disabled="!checkpointMessage.trim()" title="创建 Git Checkpoint" @click="checkpoint"><GitCommit :size="13" /></button></div>
          <pre v-if="actionResult" class="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-background p-2 text-[10px]" :class="actionResult.exit_code ? 'text-red-300' : 'text-emerald-300'">{{ actionResult.stdout || actionResult.stderr || `exit ${actionResult.exit_code}` }}</pre>
        </div>
      </section>
      <section class="flex min-h-0 flex-col">
        <div class="flex h-10 items-center border-b border-border px-3 text-xs font-mono"><span class="truncate">{{ file?.path || '选择文件' }}</span><button v-if="file?.writable" class="ml-auto flex items-center gap-1 rounded bg-blue-500 px-2 py-1 text-white disabled:opacity-50" :disabled="saving" @click="save"><Save :size="12" />保存</button></div>
        <div v-if="error" role="alert" class="border-b border-red-400/20 bg-red-400/10 px-3 py-2 text-xs text-red-200">{{ error }}</div>
        <div v-if="conflict" class="border-b border-amber-400/30 bg-amber-400/10 p-3 text-xs text-amber-100"><strong>检测到并发修改，未覆盖当前文件。</strong><div class="mt-2 grid gap-2 md:grid-cols-3"><pre class="overflow-auto rounded bg-background p-2">Base\n{{ baseContent }}</pre><pre class="overflow-auto rounded bg-background p-2">Current\n{{ conflict.currentContent }}</pre><pre class="overflow-auto rounded bg-background p-2">Draft\n{{ draft }}</pre></div></div>
        <MonacoEditor v-if="file?.content !== null && file" v-model:value="draft" class="min-h-[30rem] flex-1" theme="vs-dark" :language="file.path.endsWith('.py') ? 'python' : file.path.endsWith('.ts') ? 'typescript' : 'plaintext'" :options="{ readOnly: !file.writable, automaticLayout: true, minimap: { enabled: true } }" @change="rememberDraft" />
        <div v-else class="grid flex-1 place-items-center text-sm text-muted-foreground">{{ file?.reason || '从文件树选择可预览文件' }}</div>
      </section>
    </div>
  </AppShell>
</template>
