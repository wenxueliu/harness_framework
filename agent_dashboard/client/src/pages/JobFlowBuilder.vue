<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '@/layouts/AppShell.vue'
import DagGraph from '@/components/DagGraph.vue'
import type { Task } from '@/api/types'
import {
  createJobFlowInstance,
  createJobFlowTemplate,
  getJobFlowTemplate,
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
const conflict = ref('')
const parameterValues = ref<Record<string, unknown>>({})

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
  conflict.value = ''
  await nextTick(); resetHistory()
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
      conflict.value = '草稿已被他人修改，请刷新后重试'
      error.value = conflict.value
    } else error.value = cause instanceof Error ? cause.message : '保存失败'
  } finally { saving.value = false }
}

async function publish(run: boolean) {
  const local = localValidation()
  if (!local.valid) {
    validationErrors.value = local.errors
    error.value = '存在校验错误，已阻止发布'
    return
  }
  await saveDraft()
  if (!template.value || error.value) return
  try {
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
    if (cause instanceof HarnessApiError && cause.status === 409) conflict.value = '草稿已被他人修改，请刷新后重试'
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
  if (!templateId.value) return
  try {
    template.value = await getJobFlowTemplate(templateId.value)
    name.value = template.value.name
    description.value = template.value.description || ''
    tasksText.value = JSON.stringify(template.value.draft?.tasks || [], null, 2)
    parameterSchemaText.value = JSON.stringify(template.value.draft?.parameter_schema || { type: 'object', properties: {}, required: [] }, null, 2)
    await nextTick(); resetHistory()
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '模板加载失败' }
})
</script>

<template>
  <AppShell :title="template ? '编辑作业流模板' : '新建作业流模板'" eyebrow="Manual DAG Builder">
    <template #actions>
      <button data-testid="undo-editor" class="rounded border border-border px-3 py-2 text-xs" :disabled="historyIndex <= 0" @click="undo">撤销</button>
      <button data-testid="redo-editor" class="rounded border border-border px-3 py-2 text-xs" :disabled="historyIndex >= history.length - 1" @click="redo">重做</button>
      <button v-if="template" data-testid="restore-draft" class="rounded border border-border px-3 py-2 text-xs" @click="refreshDraft">恢复草稿</button>
      <button data-testid="save-draft" class="rounded border border-border px-3 py-2 text-xs" :disabled="saving" @click="saveDraft">保存草稿</button>
      <button data-testid="publish-only" class="rounded border border-blue-400/50 px-3 py-2 text-xs text-blue-300" :disabled="saving" @click="publish(false)">仅发布</button>
      <button data-testid="publish-and-run" class="rounded bg-blue-500 px-3 py-2 text-xs text-white" :disabled="saving" @click="publish(true)">发布并执行</button>
    </template>
    <div class="mx-auto grid max-w-6xl gap-4 p-4 sm:p-6 lg:grid-cols-[minmax(0,1fr)_minmax(20rem,0.8fr)]">
      <section class="space-y-4">
        <div class="rounded-lg border border-border bg-card p-4">
          <label class="block text-xs font-medium text-muted-foreground">模板名称<input v-model="name" data-testid="template-name" class="mt-2 w-full rounded border border-input bg-background px-3 py-2 text-sm" /></label>
          <label class="mt-4 block text-xs font-medium text-muted-foreground">描述<textarea v-model="description" class="mt-2 w-full rounded border border-input bg-background px-3 py-2 text-sm" rows="3" /></label>
        </div>
        <div class="rounded-lg border border-border bg-card p-4">
          <div class="mb-2 flex items-center justify-between"><h2 class="text-sm font-semibold">节点定义 JSON</h2><button class="text-xs text-blue-300" @click="validate">校验 DAG</button></div>
          <div data-testid="tasks-editor" class="min-h-80 overflow-hidden rounded border border-input">
            <MonacoEditor v-model:value="tasksText" language="json" theme="vs-dark" :options="{ automaticLayout: true, minimap: { enabled: false }, fontSize: 12 }" />
          </div>
          <p class="mt-2 text-[11px] text-muted-foreground">支持节点数组或“节点 ID → 配置”对象；节点可使用 id、depends_on、type、service_name 及 ACP、资源预算等扩展字段。</p>
        </div>
        <div class="rounded-lg border border-border bg-card p-4">
          <div class="mb-2 flex items-center justify-between"><h2 class="text-sm font-semibold">参数 Schema</h2><button class="text-xs text-blue-300" data-testid="schema-editor-toggle" @click="parameterSchemaText = JSON.stringify(parseSchema(), null, 2)">格式化</button></div>
          <div data-testid="schema-editor" class="min-h-48 overflow-hidden rounded border border-input">
            <MonacoEditor v-model:value="parameterSchemaText" language="json" theme="vs-dark" :options="{ automaticLayout: true, minimap: { enabled: false }, fontSize: 12 }" />
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
            <DagGraph v-if="Object.keys(dagTasks).length" :tasks="dagTasks" />
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
    <div v-if="conflict" data-testid="revision-conflict" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div class="w-full max-w-md rounded-lg border border-border bg-card p-5">
        <h2 class="text-base font-semibold">保存冲突</h2>
        <p class="mt-2 text-sm text-muted-foreground">{{ conflict }}</p>
        <div class="mt-4 flex justify-end gap-2">
          <button data-testid="conflict-refresh" class="rounded bg-blue-500 px-3 py-2 text-xs text-white" @click="refreshDraft">刷新后重试</button>
        </div>
      </div>
    </div>
  </AppShell>
</template>
