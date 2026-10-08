<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import AppShell from '@/layouts/AppShell.vue'
import {
  createJobFlowInstance,
  getJobFlowTemplate,
  listJobFlowExecutionProfiles,
  listJobFlowTemplateVersions,
  startJobFlowInstance,
  type ExecutionProfileSummary,
  type JobFlowTemplate,
  type JobFlowVersion,
} from '@/api/jobFlow'

const router = useRouter()
const route = useRoute()
const templateId = computed(() => route.params.templateId as string)

const step = ref(1)
const template = ref<JobFlowTemplate | null>(null)
const versions = ref<JobFlowVersion[]>([])
const selectedVersionId = ref('')
const gitRef = ref('')
const instanceName = ref('')
const workspaceStrategy = ref('ORIGINAL')
const executionProfileId = ref('')
const startMode = ref<'later' | 'now'>('later')
const parametersText = ref('{}')
const loading = ref(true)
const submitting = ref(false)
const error = ref('')

const parsedParameters = computed<Record<string, unknown>>(() => {
  try { return JSON.parse(parametersText.value) } catch { return {} }
})

const availableVersions = computed(() => versions.value.filter(v => v.status === 'PUBLISHED'))
const selectedVersion = computed(() => versions.value.find(v => v.version_id === selectedVersionId.value))
const profiles = ref<ExecutionProfileSummary[]>([])

const workspaceStrategies = [
  { value: 'ORIGINAL', label: '原目录（需要检查脏修改）' },
  { value: 'GIT_WORKTREE', label: 'Git Worktree（推荐）' },
  { value: 'CONTROLLED_COPY', label: '受控副本' },
  { value: 'DEMO_TEMP', label: '临时演示工作区' },
]

async function submit() {
  error.value = ''
  submitting.value = true
  try {
    const body = {
      template_id: templateId.value,
      version_id: selectedVersionId.value,
      name: instanceName.value.trim() || '未命名实例',
      parameters: parsedParameters.value,
      git: { ref: gitRef.value },
      workspace: { strategy: workspaceStrategy.value },
      execution_profile_id: executionProfileId.value,
    }
    const created = await createJobFlowInstance(body)
    if (startMode.value === 'now') {
      await startJobFlowInstance(created.instance_id)
    }
    router.push(`/instances/${created.instance_id}`)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '创建失败'
  } finally { submitting.value = false }
}

onMounted(async () => {
  try {
    template.value = await getJobFlowTemplate(templateId.value)
    versions.value = await listJobFlowTemplateVersions(templateId.value)
    profiles.value = await listJobFlowExecutionProfiles(template.value.group_id || undefined)
    if (availableVersions.value.length) selectedVersionId.value = availableVersions.value[0].version_id
    if (profiles.value.length) executionProfileId.value = profiles.value[0].profile_id
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '加载失败'
  } finally {
    loading.value = false
  }
})

function nextStep() { if (step.value < 3) step.value++ }
function prevStep() { if (step.value > 1) step.value-- }
</script>

<template>
  <AppShell title="创建实例" eyebrow="Instance Create">
    <div class="mx-auto max-w-3xl space-y-4 p-4 sm:p-6">
      <div v-if="loading" class="text-sm text-muted-foreground">加载中…</div>
      <div v-else-if="error" role="alert" class="rounded border border-red-400/30 bg-red-400/10 p-4 text-sm text-red-200">{{ error }}</div>
      <template v-else>
        <div class="flex items-center gap-2 text-xs">
          <span :class="step === 1 ? 'text-blue-300 font-medium' : 'text-muted-foreground'">1 选择版本</span>
          <span class="text-muted-foreground">→</span>
          <span :class="step === 2 ? 'text-blue-300 font-medium' : 'text-muted-foreground'">2 配置实例</span>
          <span class="text-muted-foreground">→</span>
          <span :class="step === 3 ? 'text-blue-300 font-medium' : 'text-muted-foreground'">3 确认</span>
        </div>

        <div v-if="step === 1" data-testid="wizard-step-1" class="rounded-lg border border-border bg-card p-4">
          <h4 class="text-sm font-medium">选择模板版本</h4>
          <div class="mt-3 space-y-2">
            <button v-for="v in availableVersions" :key="v.version_id"
              class="w-full rounded border px-3 py-2 text-left text-sm transition"
              :class="selectedVersionId === v.version_id ? 'border-blue-400 bg-blue-400/10' : 'border-border hover:border-blue-400/40'"
              @click="selectedVersionId = v.version_id">
              V{{ v.version }} · {{ v.status }}
              <span class="ml-2 text-xs text-muted-foreground">{{ v.created_at }}</span>
            </button>
            <p v-if="!availableVersions.length" class="text-xs text-muted-foreground">没有可用版本</p>
          </div>
        </div>

        <div v-if="step === 2" data-testid="wizard-step-2" class="space-y-3">
          <div class="rounded-lg border border-border bg-card p-4">
            <h4 class="text-sm font-medium">实例名称</h4>
            <input v-model="instanceName" data-testid="wizard-name" placeholder="release-main"
              class="mt-2 w-full rounded border border-border bg-transparent p-2 text-xs" />
          </div>
          <div class="rounded-lg border border-border bg-card p-4">
            <h4 class="text-sm font-medium">输入参数</h4>
            <textarea v-model="parametersText" data-testid="wizard-parameters" rows="4"
              class="mt-2 w-full rounded border border-border bg-transparent p-2 font-mono text-xs"
              placeholder='{"key": "value"}' />
          </div>
          <div class="rounded-lg border border-border bg-card p-4">
            <h4 class="text-sm font-medium">Execution Profile</h4>
            <select v-model="executionProfileId" data-testid="wizard-profile"
              class="mt-2 w-full rounded border border-border bg-transparent p-2 text-xs">
              <option value="">选择 Profile</option>
              <option v-for="profile in profiles" :key="profile.profile_id" :value="profile.profile_id">
                {{ profile.name }} v{{ profile.version }} · {{ profile.status }}
              </option>
            </select>
            <p v-if="!profiles.length" class="mt-1 text-xs text-amber-300">
              当前项目组没有可用 Profile，启动时会被能力预检阻塞。
            </p>
          </div>
          <div class="rounded-lg border border-border bg-card p-4">
            <h4 class="text-sm font-medium">Git Branch / Commit</h4>
            <input v-model="gitRef" data-testid="wizard-git-ref" placeholder="main / commit SHA"
              class="mt-2 w-full rounded border border-border bg-transparent p-2 text-xs" />
          </div>
          <div class="rounded-lg border border-border bg-card p-4">
            <h4 class="text-sm font-medium">Run Workspace 隔离等级</h4>
            <div class="mt-2 space-y-1">
              <label v-for="strategy in workspaceStrategies" :key="strategy.value" class="flex items-center gap-2 text-xs">
                <input v-model="workspaceStrategy" type="radio" :value="strategy.value" data-testid="wizard-workspace" />
                {{ strategy.label }}
              </label>
            </div>
          </div>
        </div>

        <div v-if="step === 3" data-testid="wizard-step-3" class="rounded-lg border border-border bg-card p-4">
          <h4 class="text-sm font-medium">确认配置</h4>
          <dl class="mt-3 space-y-1 text-xs">
            <div class="flex justify-between"><dt class="text-muted-foreground">模板</dt><dd>{{ template?.name }}</dd></div>
            <div class="flex justify-between"><dt class="text-muted-foreground">版本</dt><dd>{{ selectedVersion?.version_id }}</dd></div>
            <div class="flex justify-between"><dt class="text-muted-foreground">Profile</dt><dd>{{ profiles.find(p => p.profile_id === executionProfileId)?.name || '未选择' }}</dd></div>
            <div class="flex justify-between"><dt class="text-muted-foreground">Git</dt><dd>{{ gitRef || '未指定' }}</dd></div>
            <div class="flex justify-between"><dt class="text-muted-foreground">Workspace</dt><dd>{{ workspaceStrategy }}</dd></div>
          </dl>
          <div class="mt-4 flex items-center gap-3">
            <label class="flex items-center gap-1.5 text-xs">
              <input v-model="startMode" type="radio" value="later" /> 稍后启动
            </label>
            <label class="flex items-center gap-1.5 text-xs">
              <input v-model="startMode" type="radio" value="now" /> 立即执行
            </label>
          </div>
        </div>

        <div class="flex justify-between">
          <button v-if="step > 1" class="rounded border border-border px-3 py-1.5 text-xs" @click="prevStep">上一步</button>
          <span v-else />
          <button v-if="step < 3" data-testid="wizard-next"
            class="rounded bg-blue-500 px-3 py-1.5 text-xs text-white" @click="nextStep">下一步</button>
          <button v-else data-testid="wizard-submit"
            class="rounded bg-green-600 px-3 py-1.5 text-xs text-white"
            :disabled="submitting || !selectedVersionId || !executionProfileId" @click="submit">创建实例</button>
        </div>
      </template>
    </div>
  </AppShell>
</template>
