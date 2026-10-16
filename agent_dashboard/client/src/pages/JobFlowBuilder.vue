<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '@/layouts/AppShell.vue'
import DagGraph from '@/components/DagGraph.vue'
import ConflictDialog from '@/components/ConflictDialog.vue'
import PublishDialog from '@/components/PublishDialog.vue'
import { useCapabilityStore } from '@/stores/capabilities'
import type { Task } from '@/api/types'
import {
  createJobFlowInstance,
  createJobFlowTemplate,
  getJobFlowTemplate,
  getJobFlowTemplateVersion,
  publishJobFlowTemplate,
  updateJobFlowDraft,
  validateJobFlowTemplate,
  type JobFlowTask,
  type JobFlowTemplate,
  type JobFlowValidation,
} from '@/api/jobFlow'
import { HarnessApiError } from '@/api/client'

const MonacoEditor = defineAsyncComponent(async () => {
  const module = await import('@guolao/vue-monaco-editor')
  const monaco = await import('monaco-editor')
  module.loader.config({ monaco })
  return module.VueMonacoEditor
})

// 测试钩子：暴露 Monaco editor 实例，供 UI 自动化通过模型 API 写入内容
type MonacoEditorLike = { setValue: (value: string) => void; layout?: () => void }
type HarnessMonacoWindow = Window & { __harnessMonacoEditors?: Record<string, MonacoEditorLike> }
function trackEditor(key: string) {
  return (editor: MonacoEditorLike) => {
    const store = (window as HarnessMonacoWindow).__harnessMonacoEditors ?? {}
    store[key] = editor
    ;(window as HarnessMonacoWindow).__harnessMonacoEditors = store
    // Monaco 在异步组件挂载时可能以 5x5 兜底尺寸初始化且 automaticLayout 不恢复，
    // 等容器完成布局后显式重排版
    requestAnimationFrame(() => editor.layout?.())
    setTimeout(() => editor.layout?.(), 200)
  }
}
const onTasksEditorMounted = trackEditor('tasks')
const onSchemaEditorMounted = trackEditor('schema')

const route = useRoute()
const router = useRouter()
const template = ref<JobFlowTemplate | null>(null)
const name = ref('未命名作业流')
const description = ref('')
const tasksText = ref('[{"id":"build","name":"构建","type":"backend","agent":"codex","depends_on":[]}]')
const parameterSchemaText = ref('{\n  "type": "object",\n  "properties": {},\n  "required": []\n}')
const message = ref('')
const error = ref('')
const saving = ref(false)
const parseError = ref('')
const validationErrors = ref<JobFlowValidation['errors']>([])
const conflictVisible = ref(false)
const serverRevision = ref(0)
const publishing = ref(false)
const publishDialogVisible = ref(false)
const publishChangeSummary = ref('')
const parameterValues = ref<Record<string, unknown>>({})
const capabilityStore = useCapabilityStore()
const canEdit = computed(() => capabilityStore.permitted('workflow:draft'))
const canPublish = computed(() => capabilityStore.permitted('workflow:publish'))

type EditorSnapshot = { name: string; description: string; tasksText: string; parameterSchemaText: string }
const history = ref<EditorSnapshot[]>([])
const historyIndex = ref(-1)
let historyTimer: ReturnType<typeof setTimeout> | undefined

function currentSnapshot(): EditorSnapshot {
  return {
    name: name.value, description: description.value,
    tasksText: tasksText.value, parameterSchemaText: parameterSchemaText.value,
  }
}

function resetHistory() {
  history.value = [currentSnapshot()]
  historyIndex.value = 0
}

watch([name, description, tasksText, parameterSchemaText], () => {
  if (historyTimer) clearTimeout(historyTimer)
  historyTimer = setTimeout(() => {
    const snapshot = currentSnapshot()
    if (JSON.stringify(history.value[historyIndex.value]) === JSON.stringify(snapshot)) return
    history.value = [...history.value.slice(0, historyIndex.value + 1), snapshot]
    historyIndex.value = history.value.length - 1
  }, 250)
})

function restore(snapshot: EditorSnapshot) {
  name.value = snapshot.name
  description.value = snapshot.description
  tasksText.value = snapshot.tasksText
  parameterSchemaText.value = snapshot.parameterSchemaText
}

function undo() {
  if (historyIndex.value <= 0) return
  historyIndex.value -= 1
  restore(history.value[historyIndex.value])
}

function redo() {
  if (historyIndex.value >= history.value.length - 1) return
  historyIndex.value += 1
  restore(history.value[historyIndex.value])
}

const dagTasks = computed<Record<string, Task>>(() => {
  try {
    const tasks = parseTasks()
    parseError.value = ''
    const record: Record<string, Task> = {}
    for (const task of tasks) {
      record[task.id] = {
        id: task.id,
        name: task.name ?? task.id,
        status: 'PENDING',
        assigned_agent: '',
        depends_on: task.depends_on ?? [],
        last_updated: '',
        type: task.type,
      }
    }
    return record
  } catch {
    parseError.value = 'JSON 格式无效，DAG 预览暂不可用'
    return {}
  }
})
const templateId = computed(() => typeof route.params.templateId === 'string' ? route.params.templateId : '')
const groupId = computed(() => {
  const value = route.query.groupId || route.query.group_id
  return typeof value === 'string' && value ? value : undefined
})

function parseTasks(): JobFlowTask[] {
  const value = JSON.parse(tasksText.value) as unknown
  let source: unknown = value
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const record = value as Record<string, unknown>
    if (Array.isArray(record.tasks) || Array.isArray(record.nodes)) {
      source = record.tasks ?? record.nodes
    } else {
      // Also accept the canonical project DAG format:
      // { "design": { ... }, "backend": { ... } }.
      source = Object.entries(record).map(([id, task]) => {
        if (!task || typeof task !== 'object' || Array.isArray(task)) {
          throw new Error(`节点 ${id} 必须是对象`)
        }
        return { id, ...(task as Record<string, unknown>) }
      })
    }
  }
  if (!Array.isArray(source) || !source.length) throw new Error('至少需要一个节点')
  return source.map((task, index) => {
    if (!task || typeof task !== 'object' || Array.isArray(task)) {
      throw new Error(`第 ${index + 1} 个节点必须是对象`)
    }
    return task as JobFlowTask
  })
}

function parseSchema(): Record<string, unknown> {
  const value = JSON.parse(parameterSchemaText.value) as unknown
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('参数 Schema 必须是 JSON 对象')
  return value as Record<string, unknown>
}

function localValidation(): JobFlowValidation {
  const errors: JobFlowValidation['errors'] = []
  const idLines = new Map<string, number>()
  tasksText.value.split('\n').forEach((line, index) => {
    const match = line.match(/"id"\s*:\s*"([^"]+)"/)
    if (match) idLines.set(match[1], index + 1)
  })
  try {
    const schema = parseSchema()
    if (schema.type !== undefined && schema.type !== 'object') {
      errors.push({ code: 'PARAMETER_SCHEMA_INVALID', message: '参数 Schema 根类型必须是 object', field: 'parameter_schema' })
    }
    if (schema.properties !== undefined && (typeof schema.properties !== 'object' || Array.isArray(schema.properties))) {
      errors.push({ code: 'PARAMETER_SCHEMA_INVALID', message: '参数 Schema properties 必须是对象', field: 'parameter_schema.properties' })
    }
    const required = schema.required
    if (required !== undefined && (!Array.isArray(required) || required.some((item) => typeof item !== 'string'))) {
      errors.push({ code: 'PARAMETER_SCHEMA_INVALID', message: '参数 Schema required 必须是字符串数组', field: 'parameter_schema.required' })
    }
    const values = parameterValues.value
    for (const field of Array.isArray(required) ? required as string[] : []) {
      if (values[field] === undefined || values[field] === '') {
        errors.push({ code: 'PARAMETER_SCHEMA_INVALID', message: `参数 ${field} 必填`, field: `parameters.${field}` })
      }
    }
  } catch (cause) {
    errors.push({ code: 'PARAMETER_SCHEMA_INVALID', message: cause instanceof Error ? cause.message : '参数 Schema 无效' })
  }
  try {
    const tasks = parseTasks()
    const ids = new Set<string>()
    for (const task of tasks) {
      if (!task.id?.trim()) errors.push({ code: 'TASK_FIELD_REQUIRED', message: '节点必填字段 id 不能为空', field: 'id' })
      else if (ids.has(task.id)) errors.push({ code: 'DUPLICATE_TASK_ID', message: `节点 ID 重复: ${task.id}`, node_id: task.id, field: 'id', line: idLines.get(task.id) })
      ids.add(task.id)
      if (task.type === '') errors.push({ code: 'TASK_FIELD_REQUIRED', message: `节点 ${task.id} 必填字段 type 不能为空`, node_id: task.id, field: 'type', line: idLines.get(task.id) })
      if (task.agent === '') errors.push({ code: 'TASK_FIELD_REQUIRED', message: `节点 ${task.id} 必填字段 agent 不能为空`, node_id: task.id, field: 'agent', line: idLines.get(task.id) })
      else if (task.agent && !['claude', 'codex'].includes(task.agent)) errors.push({ code: 'INVALID_AGENT', message: `节点 ${task.id} 的 Agent 不受支持`, node_id: task.id, field: 'agent', line: idLines.get(task.id) })
    }
    for (const task of tasks) {
      for (const dependency of task.depends_on ?? []) {
        if (dependency === task.id) errors.push({ code: 'SELF_DEPENDENCY', message: `节点 ${task.id} 不能自依赖`, node_id: task.id, edge: `${task.id}->${task.id}`, line: idLines.get(task.id) })
        else if (!ids.has(dependency)) errors.push({ code: 'UNKNOWN_DEPENDENCY', message: `依赖节点 ${dependency} 不存在`, node_id: task.id, edge: `${task.id}->${dependency}`, line: idLines.get(dependency) })
      }
    }
    const state = new Set<string>()
    const visit = (taskId: string, from?: string) => {
      if (state.has(taskId)) {
        errors.push({ code: 'TASK_GRAPH_CYCLE', message: '作业流依赖不能形成环', node_id: taskId, edge: from ? `${from}->${taskId}` : undefined, line: idLines.get(taskId) })
        return
      }
      if (taskId !== from) state.add(taskId)
      const task = tasks.find((item) => item.id === taskId)
      for (const dependency of task?.depends_on ?? []) visit(dependency, taskId)
      if (taskId !== from) state.delete(taskId)
    }
    tasks.forEach((task) => { if (!state.has(task.id)) visit(task.id) })
  } catch { /* DAG parse errors are shown beside the editor. */ }
  return { valid: errors.length === 0, errors }
}

async function refreshDraft() {
  if (!templateId.value) return
  template.value = await getJobFlowTemplate(templateId.value)
  name.value = template.value.name
  description.value = template.value.description || ''
  tasksText.value = JSON.stringify(template.value.draft?.tasks || [], null, 2)
  parameterSchemaText.value = JSON.stringify(template.value.draft?.parameter_schema || { type: 'object', properties: {}, required: [] }, null, 2)
  conflictVisible.value = false
  await nextTick(); resetHistory()
}

async function showRevisionConflict() {
  serverRevision.value = template.value?.draft_revision ?? 0
  conflictVisible.value = true
  if (template.value) {
    try {
      const server = await getJobFlowTemplate(template.value.template_id)
      serverRevision.value = server.draft_revision
    } catch { /* 无法读取服务端修订时保留本地修订号 */ }
  }
}

function downloadDraftSnapshot() {
  let payload: Record<string, unknown>
  try {
    payload = {
      name: name.value, description: description.value,
      tasks: JSON.parse(tasksText.value), parameter_schema: JSON.parse(parameterSchemaText.value),
    }
  } catch {
    payload = {
      name: name.value, description: description.value,
      tasks: tasksText.value, parameter_schema: parameterSchemaText.value,
    }
  }
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${template.value?.template_id || 'draft'}-revision-${template.value?.draft_revision ?? 0}.json`
  anchor.click()
  URL.revokeObjectURL(url)
}

function discardDraftAndRefresh() {
  conflictVisible.value = false
  refreshDraft().catch(() => undefined)
}

async function saveDraft() {
  saving.value = true; error.value = ''; message.value = ''
  try {
    const tasks = parseTasks()
    const parameterSchema = parseSchema()
    if (!template.value) {
      template.value = await createJobFlowTemplate({
        name: name.value, description: description.value, tasks, parameter_schema: parameterSchema,
        group_id: groupId.value,
      })
      await router.replace('/templates/' + encodeURIComponent(template.value.template_id) + '/edit')
    } else {
      template.value = await updateJobFlowDraft(template.value.template_id, {
        expected_revision: template.value.draft_revision, name: name.value,
        description: description.value, tasks, parameter_schema: parameterSchema,
      })
    }
    message.value = '草稿已保存'
  } catch (cause) {
    if (cause instanceof HarnessApiError && cause.status === 409) {
      await showRevisionConflict()
    } else error.value = cause instanceof Error ? cause.message : '保存失败'
  } finally { saving.value = false }
}

async function computeChangeSummary(): Promise<string> {
  try {
    const tasks = parseTasks()
    const currentVersionId = template.value?.current_version_id
    if (!currentVersionId) {
      return `全新模板：将创建包含 ${tasks.length} 个节点的首个发布版本`
    }
    const { manifest } = await getJobFlowTemplateVersion(template.value!.template_id, currentVersionId)
    const previous = new Map((manifest.tasks ?? []).map((task) => [task.id, task]))
    const current = new Map(tasks.map((task) => [task.id, task]))
    const added = tasks.filter((task) => !previous.has(task.id)).length
    const removed = (manifest.tasks ?? []).filter((task) => !current.has(task.id)).length
    let changed = 0
    for (const task of tasks) {
      const before = previous.get(task.id)
      if (before && JSON.stringify(before) !== JSON.stringify(task)) changed += 1
    }
    return `与上一版本对比：新增 ${added} 个节点，删除 ${removed} 个节点，修改 ${changed} 个节点`
  } catch {
    return ''
  }
}

async function openPublishDialog() {
  message.value = ''
  error.value = ''
  publishChangeSummary.value = await computeChangeSummary()
  publishDialogVisible.value = true
}

async function doPublish(run: boolean) {
  const local = localValidation()
  if (!local.valid) {
    validationErrors.value = local.errors
    error.value = '存在校验错误，已阻止发布'
    return
  }
  publishing.value = true
  try {
    await saveDraft()
    if (!template.value || error.value) return
    const result = await publishJobFlowTemplate(template.value.template_id, template.value.draft_revision)
    template.value = result.template
    if (run) {
      const instance = await createJobFlowInstance({
        template_id: template.value.template_id, version_id: result.version.version_id,
        group_id: groupId.value || template.value.group_id,
        name: name.value + ' 实例', parameters: { ...parameterValues.value }, git: {}, workspace: {},
      })
      await router.push('/instances/' + encodeURIComponent(instance.instance_id))
    } else {
      message.value = '已发布 ' + result.version.version_id
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '发布失败'
  } finally { publishing.value = false }
}

function confirmPublishDialog(mode: 'save-draft' | 'publish-only' | 'publish-and-create') {
  publishDialogVisible.value = false
  if (mode === 'save-draft') {
    saveDraft()
  } else {
    doPublish(mode === 'publish-and-create')
  }
}

async function validate() {
  if (!template.value) {
    await saveDraft()
  }
  if (!template.value) return
  try {
    const local = localValidation()
    if (!local.valid) { validationErrors.value = local.errors; return }
    const result = await validateJobFlowTemplate(template.value.template_id)
    validationErrors.value = result.errors
    message.value = result.valid ? '校验通过' : result.errors.map((item) => String(item.message || '')).join('；')
  } catch (cause) {
    if (cause instanceof HarnessApiError && cause.status === 409) await showRevisionConflict()
    else error.value = cause instanceof Error ? cause.message : '校验失败'
  }
}

type SchemaProperty = { key: string; title?: unknown; type?: unknown; enum?: unknown }

const schemaProperties = computed<SchemaProperty[]>(() => {
  try {
    const properties = parseSchema().properties
    return Object.entries(properties as Record<string, Record<string, unknown>>)
      .filter(([, value]) => value && typeof value === 'object')
      .map(([key, value]) => ({ key, ...value }))
  } catch { return [] }
})

const requiredParameters = computed(() => {
  try { return (parseSchema().required as unknown[]).filter((item): item is string => typeof item === 'string') }
  catch { return [] }
})

onMounted(async () => {
  capabilityStore.load(groupId.value).catch(() => undefined)
  if (!templateId.value) return
  try {
    template.value = await getJobFlowTemplate(templateId.value)
    capabilityStore.load(template.value.group_id).catch(() => undefined)
    name.value = template.value.name
    description.value = template.value.description || ''
    tasksText.value = JSON.stringify(template.value.draft?.tasks || [], null, 2)
    parameterSchemaText.value = JSON.stringify(template.value.draft?.parameter_schema || { type: 'object', properties: {}, required: [] }, null, 2)
    await nextTick(); resetHistory()
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '模板加载失败' }
})

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

type MonacoNavigator = MonacoEditorLike & {
  getModel?: () => { getLinesContent?: () => string[] } | null
  revealLineInCenter?: (lineNumber: number) => void
  setPosition?: (position: { lineNumber: number; column: number }) => void
}

// 节点图点击节点时，把 JSON 编辑器滚动到该节点的 id 定义行
function scrollToTaskLine(task: Task) {
  const editor = (window as HarnessMonacoWindow).__harnessMonacoEditors?.tasks as MonacoNavigator | undefined
  if (!editor?.revealLineInCenter) return
  const lines = editor.getModel?.()?.getLinesContent?.() ?? tasksText.value.split('\n')
  const pattern = new RegExp(`"id"\\s*:\\s*"${escapeRegExp(task.id)}"`)
  const index = lines.findIndex((line) => pattern.test(line))
  if (index < 0) return
  const lineNumber = index + 1
  editor.revealLineInCenter(lineNumber)
  editor.setPosition?.({ lineNumber, column: 1 })
}
</script>

<template>
  <AppShell :title="template ? '编辑作业流模板' : '新建作业流模板'" eyebrow="Manual DAG Builder">
    <template #actions>
      <button data-testid="undo-editor" class="rounded border border-border px-3 py-2 text-xs" :disabled="historyIndex <= 0" @click="undo">撤销</button>
      <button data-testid="redo-editor" class="rounded border border-border px-3 py-2 text-xs" :disabled="historyIndex >= history.length - 1" @click="redo">重做</button>
      <button v-if="template" data-testid="restore-draft" class="rounded border border-border px-3 py-2 text-xs" @click="refreshDraft">恢复草稿</button>
      <button v-if="canEdit" data-testid="save-draft" class="rounded border border-border px-3 py-2 text-xs" :disabled="saving" @click="saveDraft">保存草稿</button>
      <button v-if="canPublish" data-testid="publish-only" class="rounded border border-blue-400/50 px-3 py-2 text-xs text-blue-300" :disabled="saving || publishing" @click="openPublishDialog">仅发布</button>
      <button v-if="canPublish" data-testid="publish-and-run" class="rounded bg-blue-500 px-3 py-2 text-xs text-white" :disabled="saving || publishing" @click="openPublishDialog">发布并执行</button>
    </template>
    <div class="mx-auto grid max-w-6xl gap-4 p-4 sm:p-6 lg:grid-cols-[minmax(0,1fr)_minmax(20rem,0.8fr)]">
      <section class="space-y-4">
        <div class="rounded-lg border border-border bg-card p-4">
          <label class="block text-xs font-medium text-muted-foreground">模板名称<input v-model="name" data-testid="template-name" class="mt-2 w-full rounded border border-input bg-background px-3 py-2 text-sm" /></label>
          <label class="mt-4 block text-xs font-medium text-muted-foreground">描述<textarea v-model="description" class="mt-2 w-full rounded border border-input bg-background px-3 py-2 text-sm" rows="3" /></label>
        </div>
        <div class="rounded-lg border border-border bg-card p-4">
          <div class="mb-2 flex items-center justify-between"><h2 class="text-sm font-semibold">节点定义 JSON</h2><button class="text-xs text-blue-300" @click="validate">校验 DAG</button></div>
          <div data-testid="tasks-editor" class="h-80 overflow-hidden rounded border border-input">
            <MonacoEditor v-model:value="tasksText" language="json" theme="vs-dark" :options="{ automaticLayout: true, minimap: { enabled: false }, fontSize: 12 }" @mount="onTasksEditorMounted" />
          </div>
          <p class="mt-2 text-[11px] text-muted-foreground">支持节点数组或“节点 ID → 配置”对象；节点可使用 id、depends_on、type、service_name 及 ACP、资源预算等扩展字段。</p>
        </div>
        <div class="rounded-lg border border-border bg-card p-4">
          <div class="mb-2 flex items-center justify-between"><h2 class="text-sm font-semibold">参数 Schema</h2><button class="text-xs text-blue-300" data-testid="schema-editor-toggle" @click="parameterSchemaText = JSON.stringify(parseSchema(), null, 2)">格式化</button></div>
          <div data-testid="schema-editor" class="h-48 overflow-hidden rounded border border-input">
            <MonacoEditor v-model:value="parameterSchemaText" language="json" theme="vs-dark" :options="{ automaticLayout: true, minimap: { enabled: false }, fontSize: 12 }" @mount="onSchemaEditorMounted" />
          </div>
          <div v-if="schemaProperties.length" class="mt-3 space-y-3">
            <div v-for="property in schemaProperties" :key="property.key" class="text-xs">
              <label class="block text-muted-foreground">{{ String(property.title || property.key) }}
                <input v-if="property.type === 'boolean'" v-model="parameterValues[property.key]" type="checkbox" :data-testid="'parameter-' + property.key" class="mt-2 block" />
                <select v-else-if="Array.isArray(property.enum)" v-model="parameterValues[property.key]" :data-testid="'parameter-' + property.key" class="mt-2 w-full rounded border border-input bg-background px-2 py-2">
                  <option v-for="option in property.enum" :key="String(option)" :value="option">{{ String(option) }}</option>
                </select>
                <input v-else-if="property.type === 'number' || property.type === 'integer'" v-model="parameterValues[property.key]" type="number" :data-testid="'parameter-' + property.key" class="mt-2 w-full rounded border border-input bg-background px-3 py-2" />
                <input v-else v-model="parameterValues[property.key]" type="text" :data-testid="'parameter-' + property.key" class="mt-2 w-full rounded border border-input bg-background px-3 py-2" />
              </label>
            </div>
          </div>
          <p v-else class="mt-3 text-xs text-muted-foreground">在 Schema 中添加 properties 后，将生成动态参数表单。</p>
        </div>
      </section>
      <aside class="h-fit space-y-3">
        <div class="rounded-lg border border-border bg-card overflow-hidden">
          <div class="border-b border-border px-3 py-2 flex items-center justify-between">
            <h2 class="text-xs font-semibold">DAG 预览</h2>
            <span v-if="parseError" class="text-[11px] text-orange-300">{{ parseError }}</span>
            <span v-else class="text-[11px] text-muted-foreground">{{ Object.keys(dagTasks).length }} 个节点</span>
          </div>
          <div class="min-h-[280px]" data-testid="dag-preview">
            <DagGraph v-if="Object.keys(dagTasks).length" :tasks="dagTasks" :on-task-click="scrollToTaskLine" />
            <p v-else class="p-6 text-center text-xs text-muted-foreground">输入有效 JSON 后显示 DAG</p>
          </div>
        </div>
        <div class="rounded-lg border border-border bg-card p-4">
          <h2 class="text-sm font-semibold">发布流程</h2>
          <ol class="mt-3 space-y-3 text-xs text-muted-foreground">
          <li>1. 保存草稿：不创建版本和实例</li>
          <li>2. 仅发布：生成不可变版本</li>
          <li>3. 发布并执行：生成版本、创建实例</li>
        </ol>
          <p v-if="message" data-testid="builder-message" class="mt-4 rounded bg-emerald-400/10 p-3 text-xs text-emerald-300">{{ message }}</p>
          <p v-if="error" role="alert" class="mt-4 rounded bg-red-400/10 p-3 text-xs text-red-300">{{ error }}</p>
          <div v-if="validationErrors.length" data-testid="validation-errors" class="mt-3 space-y-2">
            <div v-for="(item, index) in validationErrors" :key="index" class="rounded border border-red-400/30 bg-red-400/10 p-2 text-xs text-red-300">
              <div>{{ item.message }}</div>
              <div v-if="item.node_id || item.edge || item.line" class="mt-1 text-[11px] opacity-80">
                <span v-if="item.node_id">节点 {{ item.node_id }} </span>
                <span v-if="item.edge">边 {{ item.edge }} </span>
                <span v-if="item.line">第 {{ item.line }} 行</span>
              </div>
            </div>
          </div>
        </div>
      </aside>
    </div>
    <ConflictDialog
      :visible="conflictVisible"
      :current-revision="template?.draft_revision ?? 0"
      :server-revision="serverRevision"
      @discard="discardDraftAndRefresh"
      @download="downloadDraftSnapshot"
      @cancel="conflictVisible = false"
    />
    <PublishDialog
      :visible="publishDialogVisible"
      :change-summary="publishChangeSummary"
      @save-draft="confirmPublishDialog('save-draft')"
      @publish-only="confirmPublishDialog('publish-only')"
      @publish-and-create="confirmPublishDialog('publish-and-create')"
      @cancel="publishDialogVisible = false"
    />
  </AppShell>
</template>
