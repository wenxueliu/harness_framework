<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '@/layouts/AppShell.vue'
import {
  createJobFlowInstance,
  createJobFlowTemplate,
  getJobFlowTemplate,
  publishJobFlowTemplate,
  updateJobFlowDraft,
  validateJobFlowTemplate,
  type JobFlowTask,
  type JobFlowTemplate,
} from '@/api/jobFlow'

const route = useRoute()
const router = useRouter()
const template = ref<JobFlowTemplate | null>(null)
const name = ref('未命名作业流')
const description = ref('')
const tasksText = ref('[{"id":"build","name":"构建","type":"backend","depends_on":[]}]')
const message = ref('')
const error = ref('')
const saving = ref(false)
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

async function saveDraft() {
  saving.value = true; error.value = ''; message.value = ''
  try {
    const tasks = parseTasks()
    if (!template.value) {
      template.value = await createJobFlowTemplate({
        name: name.value, description: description.value, tasks, group_id: groupId.value,
      })
      await router.replace('/templates/' + encodeURIComponent(template.value.template_id) + '/edit')
    } else {
      template.value = await updateJobFlowDraft(template.value.template_id, {
        expected_revision: template.value.draft_revision, name: name.value,
        description: description.value, tasks,
      })
    }
    message.value = '草稿已保存'
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '保存失败'
  } finally { saving.value = false }
}

async function publish(run: boolean) {
  await saveDraft()
  if (!template.value || error.value) return
  try {
    const result = await publishJobFlowTemplate(template.value.template_id, template.value.draft_revision)
    template.value = result.template
    if (run) {
      const instance = await createJobFlowInstance({
        template_id: template.value.template_id, version_id: result.version.version_id,
        group_id: groupId.value || template.value.group_id,
        name: name.value + ' 实例', parameters: {}, git: {}, workspace: {},
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
    const result = await validateJobFlowTemplate(template.value.template_id)
    message.value = result.valid ? '校验通过' : result.errors.map((item) => item.message).join('；')
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '校验失败' }
}

onMounted(async () => {
  if (!templateId.value) return
  try {
    template.value = await getJobFlowTemplate(templateId.value)
    name.value = template.value.name
    description.value = template.value.description || ''
    tasksText.value = JSON.stringify(template.value.draft?.tasks || [], null, 2)
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '模板加载失败' }
})
</script>

<template>
  <AppShell :title="template ? '编辑作业流模板' : '新建作业流模板'" eyebrow="Manual DAG Builder">
    <template #actions>
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
          <textarea v-model="tasksText" data-testid="tasks-editor" class="min-h-80 w-full rounded border border-input bg-background p-3 font-mono text-xs" spellcheck="false" />
          <p class="mt-2 text-[11px] text-muted-foreground">支持节点数组或“节点 ID → 配置”对象；节点可使用 id、depends_on、type、service_name 及 ACP、资源预算等扩展字段。</p>
        </div>
      </section>
      <aside class="h-fit rounded-lg border border-border bg-card p-4">
        <h2 class="text-sm font-semibold">发布流程</h2>
        <ol class="mt-3 space-y-3 text-xs text-muted-foreground">
          <li>1. 保存草稿：不创建版本和实例</li>
          <li>2. 仅发布：生成不可变版本</li>
          <li>3. 发布并执行：生成版本、创建实例</li>
        </ol>
        <p v-if="message" data-testid="builder-message" class="mt-4 rounded bg-emerald-400/10 p-3 text-xs text-emerald-300">{{ message }}</p>
        <p v-if="error" role="alert" class="mt-4 rounded bg-red-400/10 p-3 text-xs text-red-300">{{ error }}</p>
      </aside>
    </div>
  </AppShell>
</template>
