<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createWorkflow, type CreateWorkflowTask } from '@/lib/harnessApi'
import {
  ArrowLeft,
  Bot,
  Check,
  CheckCircle2,
  CircleAlert,
  Clock3,
  GitBranch,
  LoaderCircle,
  MessageSquareText,
  Pencil,
  Play,
  Plus,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
  X,
  Zap,
} from 'lucide-vue-next'

type AgentProvider = 'claude' | 'codex'
type BuilderStep = 'describe' | 'planning' | 'review' | 'published'

interface PlanTask {
  id: string
  name: string
  description: string
  type: 'design' | 'backend' | 'frontend' | 'test' | 'review' | 'deploy'
  agent: AgentProvider
  dependsOn: string[]
  acceptance: string
}

interface ChatMessage {
  role: 'assistant' | 'user'
  content: string
}

const router = useRouter()
const route = useRoute()
const step = ref<BuilderStep>('describe')
const requirement = ref('为 Harness 增加工作流创建能力：用户输入自然语言需求，AI 自动拆分为 DAG；用户可以多轮调整任务、依赖和 Agent，确认后发布并通过 ACP 执行。')
const selectedId = ref<string | null>(null)
const chatInput = ref('')
const showPublish = ref(false)
const saved = ref(true)
const planProgress = ref(0)
const publishedWillRun = ref(true)
const publishError = ref('')
const publishing = ref(false)
const selectedGroupId = computed(() => {
  const groupId = route.query.groupId
  return typeof groupId === 'string' && groupId && groupId !== 'unassigned' ? groupId : undefined
})

const tasks = ref<PlanTask[]>([])
const messages = ref<ChatMessage[]>([
  { role: 'assistant', content: '描述你希望完成的目标。我会把范围、依赖和验收条件整理成可执行的任务图。' },
])

const sampleTasks: PlanTask[] = [
  {
    id: 'prd-and-contract',
    name: '定义产品与接口契约',
    description: '补齐用户旅程、草稿状态模型、DAG Schema 与 WebAPI 契约。',
    type: 'design', agent: 'claude', dependsOn: [],
    acceptance: 'PRD 通过评审，接口字段和状态迁移无歧义。',
  },
  {
    id: 'draft-api',
    name: '实现草稿与发布 API',
    description: '实现工作流草稿 CRUD、DAG 校验、乐观锁和幂等发布。',
    type: 'backend', agent: 'codex', dependsOn: ['prd-and-contract'],
    acceptance: 'API 测试覆盖创建、更新、删除、冲突和重复发布。',
  },
  {
    id: 'builder-ui',
    name: '实现任务编排工作台',
    description: '实现需求对话、DAG 画布、节点编辑和发布确认体验。',
    type: 'frontend', agent: 'codex', dependsOn: ['prd-and-contract'],
    acceptance: '桌面与移动端均可完成创建、修改和发布主流程。',
  },
  {
    id: 'acp-integration',
    name: '接入 ACP 执行链路',
    description: '将发布快照写入执行模型，创建 Run 并激活根任务。',
    type: 'backend', agent: 'codex', dependsOn: ['draft-api'],
    acceptance: '发布后每个节点按配置启动 Claude Code 或 Codex。',
  },
  {
    id: 'journey-test',
    name: '验证端到端用户旅程',
    description: '覆盖自然语言建图、修改、发布、执行和人工介入。',
    type: 'test', agent: 'codex', dependsOn: ['builder-ui', 'acp-integration'],
    acceptance: '核心旅程 E2E 通过，刷新后草稿和运行状态保持一致。',
  },
  {
    id: 'release-review',
    name: '发布前质量审查',
    description: '审查实现与 PRD 一致性、安全边界和回滚方案。',
    type: 'review', agent: 'claude', dependsOn: ['journey-test'],
    acceptance: '无阻断问题，发布检查清单全部通过。',
  },
]

const selectedTask = computed(() => tasks.value.find((task) => task.id === selectedId.value) ?? null)
const roots = computed(() => tasks.value.filter((task) => task.dependsOn.length === 0))
const agentStats = computed(() => ({
  claude: tasks.value.filter((task) => task.agent === 'claude').length,
  codex: tasks.value.filter((task) => task.agent === 'codex').length,
}))

function levelOf(task: PlanTask, visiting = new Set<string>()): number {
  if (!task.dependsOn.length || visiting.has(task.id)) return 0
  const next = new Set(visiting).add(task.id)
  return 1 + Math.max(...task.dependsOn.map((id) => {
    const dependency = tasks.value.find((item) => item.id === id)
    return dependency ? levelOf(dependency, next) : 0
  }))
}

const levels = computed(() => {
  const result: PlanTask[][] = []
  tasks.value.forEach((task) => {
    const level = levelOf(task)
    if (!result[level]) result[level] = []
    result[level].push(task)
  })
  return result
})

async function generatePlan() {
  if (!requirement.value.trim()) return
  step.value = 'planning'
  messages.value.push({ role: 'user', content: requirement.value.trim() })
  const stops = [18, 43, 68, 86, 100]
  for (const value of stops) {
    await new Promise((resolve) => setTimeout(resolve, 360))
    planProgress.value = value
  }
  tasks.value = sampleTasks.map((task) => ({ ...task, dependsOn: [...task.dependsOn] }))
  selectedId.value = tasks.value[0].id
  messages.value.push({
    role: 'assistant',
    content: '已生成 6 个任务。我把产品与契约设计作为根节点，前后端可以并行，最终由端到端验证和发布审查收口。',
  })
  step.value = 'review'
}

async function applyChatChange() {
  const input = chatInput.value.trim()
  if (!input || step.value !== 'review') return
  messages.value.push({ role: 'user', content: input })
  chatInput.value = ''
  await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 450))

  if (/安全|审查|review/i.test(input) && !tasks.value.some((task) => task.id === 'security-review')) {
    tasks.value.splice(Math.max(tasks.value.length - 1, 0), 0, {
      id: 'security-review', name: '安全与权限审查',
      description: '检查 Agent 工具权限、秘密信息边界与危险操作审计。',
      type: 'review', agent: 'claude', dependsOn: ['draft-api', 'builder-ui'],
      acceptance: '高风险操作均有确认和审计，敏感信息不会进入 Agent 日志。',
    })
    const release = tasks.value.find((task) => task.id === 'release-review')
    if (release && !release.dependsOn.includes('security-review')) release.dependsOn.push('security-review')
    messages.value.push({ role: 'assistant', content: '已添加“安全与权限审查”，并将它设为发布审查的前置任务。变更未引入环。' })
  } else {
    messages.value.push({ role: 'assistant', content: '我理解了这项调整。原型中已记录你的意见；接入规划 API 后，这里会先展示 DAG Diff，再由你确认应用。' })
  }
  markDirty()
}

function markDirty() {
  saved.value = false
  window.setTimeout(() => { saved.value = true }, 700)
}

function addTask() {
  const id = `new-task-${tasks.value.length + 1}`
  tasks.value.push({
    id, name: '未命名任务', description: '补充这个任务要完成的具体工作。',
    type: 'backend', agent: 'codex', dependsOn: [], acceptance: '补充可验证的完成条件。',
  })
  selectedId.value = id
  markDirty()
}

function removeTask(id: string) {
  tasks.value = tasks.value.filter((task) => task.id !== id)
  tasks.value.forEach((task) => { task.dependsOn = task.dependsOn.filter((dependency) => dependency !== id) })
  selectedId.value = tasks.value[0]?.id ?? null
  markDirty()
}

function toggleDependency(id: string) {
  if (!selectedTask.value || id === selectedTask.value.id) return
  const index = selectedTask.value.dependsOn.indexOf(id)
  if (index >= 0) selectedTask.value.dependsOn.splice(index, 1)
  else selectedTask.value.dependsOn.push(id)
  markDirty()
}

async function publish(startRun: boolean) {
  if (!tasks.value.length || publishing.value) return
  publishing.value = true
  publishError.value = ''
  try {
    const title = requirement.value.trim().split(/\r?\n/, 1)[0].slice(0, 80) || '未命名工作流'
    const workflow = await createWorkflow({
      title,
      requirement: requirement.value.trim(),
      tasks: tasks.value.map((task): CreateWorkflowTask => ({ ...task })),
      published: startRun,
      groupId: selectedGroupId.value,
    })
    showPublish.value = false
    publishedWillRun.value = startRun
    step.value = 'published'
    window.setTimeout(() => router.push({
      name: 'workflow-dashboard',
      params: { groupId: selectedGroupId.value || 'unassigned', workflowId: workflow.req_id },
    }), startRun ? 900 : 300)
  } catch (cause) {
    publishError.value = cause instanceof Error ? cause.message : 'Workflow 创建失败'
  } finally {
    publishing.value = false
  }
}

function startPublishedRun() {
  publishedWillRun.value = true
  window.setTimeout(() => router.push('/'), 500)
}

const typeLabel: Record<PlanTask['type'], string> = {
  design: '设计', backend: '后端', frontend: '前端', test: '测试', review: '审查', deploy: '部署',
}
</script>

<template>
  <div class="min-h-screen bg-background text-foreground flex flex-col">
    <header class="h-14 border-b border-border flex items-center px-4 gap-3 bg-background/95 backdrop-blur-sm sticky top-0 z-30">
      <button aria-label="返回工作流" class="p-2 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent" @click="router.push('/')">
        <ArrowLeft :size="17" />
      </button>
      <div class="w-7 h-7 rounded-lg bg-blue-500/15 border border-blue-500/30 flex items-center justify-center">
        <GitBranch :size="14" class="text-blue-400" />
      </div>
      <div class="min-w-0">
        <div class="font-display text-sm font-semibold">任务编排工作台</div>
        <div class="text-[10px] font-mono text-amber-300">PROTOTYPE · DRAFT · v1</div>
      </div>
      <div class="hidden md:flex items-center gap-1 ml-4 text-xs text-muted-foreground">
        <Check v-if="saved" :size="12" class="text-emerald-400" />
        <LoaderCircle v-else :size="12" class="animate-spin text-blue-400" />
        {{ saved ? '草稿已保存' : '正在保存' }}
      </div>
      <div v-if="selectedGroupId" class="hidden lg:block text-xs text-blue-300">
        项目组：{{ selectedGroupId }}
      </div>
      <div class="ml-auto flex items-center gap-2">
        <div v-if="step === 'review'" class="hidden sm:flex items-center gap-1.5 text-xs text-emerald-400 mr-2">
          <ShieldCheck :size="13" /> DAG 校验通过
        </div>
        <button v-if="step === 'review'" class="px-3 py-2 rounded-md border border-border text-xs hover:bg-accent" @click="addTask">
          <Plus :size="13" class="inline mr-1" />添加任务
        </button>
        <button
          v-if="step === 'review'"
          class="px-4 py-2 rounded-md bg-blue-500 text-white text-xs font-medium hover:bg-blue-400"
          @click="showPublish = true"
        >
          进入发布 <Play :size="12" class="inline ml-1" />
        </button>
      </div>
    </header>

    <main v-if="step === 'describe'" class="flex-1 flex items-center justify-center px-5 py-12 relative overflow-hidden">
      <div class="absolute inset-0 pointer-events-none opacity-40 builder-grid" />
      <section class="relative w-full max-w-3xl">
        <div class="inline-flex items-center gap-2 px-3 py-1.5 rounded-full border border-blue-500/25 bg-blue-500/10 text-blue-300 text-xs mb-6">
          <Sparkles :size="13" /> AI 任务规划
        </div>
        <h1 class="font-display text-3xl sm:text-5xl font-semibold tracking-tight leading-tight mb-4">
          描述目标，得到一张<br><span class="text-blue-400">可以执行的任务图。</span>
        </h1>
        <p class="text-sm sm:text-base text-muted-foreground max-w-2xl leading-7 mb-8">
          Harness 会拆分任务、安排依赖，并为每个节点选择 Claude Code 或 Codex。发布前，一切都只是可编辑的草稿。
        </p>
        <div class="bg-card border border-border rounded-xl p-3 shadow-2xl shadow-black/20 focus-within:border-blue-500/50 transition-colors">
          <textarea
            v-model="requirement"
            rows="7"
            class="w-full bg-transparent resize-none p-3 text-sm leading-6 outline-none placeholder:text-muted-foreground/60"
            placeholder="例如：为现有系统增加基于邮箱验证码的登录方式，需要兼容旧账号……"
          />
          <div class="border-t border-border pt-3 flex items-center gap-2">
            <button class="text-xs text-muted-foreground px-2 py-1.5 rounded hover:bg-accent">+ 添加仓库上下文</button>
            <span class="hidden sm:inline text-[11px] text-muted-foreground">系统会在规划前标出关键假设</span>
            <button class="ml-auto bg-blue-500 hover:bg-blue-400 text-white rounded-lg px-4 py-2.5 text-xs font-medium" @click="generatePlan">
              生成任务图 <Sparkles :size="13" class="inline ml-1" />
            </button>
          </div>
        </div>
        <div class="grid sm:grid-cols-3 gap-3 mt-4 text-xs text-muted-foreground">
          <div class="flex gap-2 p-3"><MessageSquareText :size="15" class="text-blue-400 shrink-0" /> 多轮对话调整计划</div>
          <div class="flex gap-2 p-3"><GitBranch :size="15" class="text-violet-400 shrink-0" /> 实时检查依赖与环</div>
          <div class="flex gap-2 p-3"><ShieldCheck :size="15" class="text-emerald-400 shrink-0" /> 确认后才开始执行</div>
        </div>
      </section>
    </main>

    <main v-else-if="step === 'planning'" class="flex-1 flex items-center justify-center px-5">
      <section class="w-full max-w-lg text-center">
        <div class="w-14 h-14 rounded-2xl bg-blue-500/10 border border-blue-500/25 flex items-center justify-center mx-auto mb-6">
          <Sparkles :size="24" class="text-blue-400 animate-pulse" />
        </div>
        <h1 class="font-display text-2xl font-semibold mb-2">正在构建任务图</h1>
        <p class="text-sm text-muted-foreground mb-7">分析边界、可并行工作和 Agent 能力…</p>
        <div class="h-1.5 bg-accent rounded-full overflow-hidden mb-3">
          <div class="h-full bg-blue-500 rounded-full transition-all duration-300" :style="{ width: `${planProgress}%` }" />
        </div>
        <div class="flex justify-between text-[11px] font-mono text-muted-foreground">
          <span>{{ planProgress < 45 ? '提取验收目标' : planProgress < 85 ? '计算任务依赖' : '验证 DAG' }}</span>
          <span>{{ planProgress }}%</span>
        </div>
      </section>
    </main>

    <main v-else-if="step === 'review'" class="flex-1 grid grid-cols-1 lg:grid-cols-[300px_minmax(500px,1fr)_300px] min-h-0 overflow-hidden">
      <aside class="hidden lg:flex border-r border-border bg-sidebar flex-col min-h-0">
        <div class="p-4 border-b border-border">
          <div class="flex items-center gap-2 text-xs font-semibold mb-1"><Bot :size="14" class="text-blue-400" />规划助手</div>
          <p class="text-[11px] text-muted-foreground leading-5">用自然语言修改任务图。修改会先通过 DAG 校验。</p>
        </div>
        <div class="flex-1 overflow-y-auto p-3 space-y-3">
          <div v-for="(message, index) in messages" :key="index" :class="message.role === 'user' ? 'ml-6 bg-blue-500/10 border-blue-500/20' : 'mr-3 bg-card border-border'" class="border rounded-lg px-3 py-2.5 text-xs leading-5">
            <div class="text-[10px] uppercase tracking-wide mb-1" :class="message.role === 'user' ? 'text-blue-400' : 'text-muted-foreground'">{{ message.role === 'user' ? '你' : 'Planner' }}</div>
            {{ message.content }}
          </div>
        </div>
        <div class="p-3 border-t border-border">
          <div class="relative">
            <textarea v-model="chatInput" rows="3" class="w-full bg-card border border-border rounded-lg p-3 pr-10 text-xs resize-none outline-none focus:border-blue-500/50" placeholder="例如：增加安全审查，并放到发布前…" @keydown.meta.enter="applyChatChange" />
            <button aria-label="发送修改" class="absolute right-2 bottom-2 p-1.5 bg-blue-500 rounded text-white disabled:opacity-40" :disabled="!chatInput.trim()" @click="applyChatChange"><Send :size="12" /></button>
          </div>
          <div class="text-[10px] text-muted-foreground mt-1.5">⌘ Enter 发送 · 试试“增加安全审查”</div>
        </div>
      </aside>

      <section class="flex flex-col min-h-0 overflow-hidden">
        <div class="px-4 py-3 border-b border-border flex items-center gap-3">
          <div>
            <h1 class="font-display text-sm font-semibold">工作流创建与 ACP 执行</h1>
            <p class="text-[11px] text-muted-foreground mt-0.5">{{ tasks.length }} 个任务 · {{ roots.length }} 个根节点 · 最大 {{ Math.max(...levels.map((items) => items.length), 0) }} 路并行</p>
          </div>
          <div class="ml-auto flex items-center gap-3 text-[11px] text-muted-foreground">
            <span><i class="inline-block w-1.5 h-1.5 rounded-full bg-violet-400 mr-1" />Claude {{ agentStats.claude }}</span>
            <span><i class="inline-block w-1.5 h-1.5 rounded-full bg-blue-400 mr-1" />Codex {{ agentStats.codex }}</span>
          </div>
        </div>
        <div class="flex-1 overflow-auto p-5 builder-grid">
          <div class="min-w-max flex items-stretch gap-14 py-4">
            <div v-for="(column, level) in levels" :key="level" class="w-56 flex flex-col justify-center gap-4 relative">
              <div class="absolute -top-4 left-0 text-[10px] font-mono text-muted-foreground">阶段 {{ Number(level) + 1 }}</div>
              <button
                v-for="task in column" :key="task.id"
                class="relative text-left bg-card border rounded-xl p-3.5 transition-all group"
                :class="selectedId === task.id ? 'border-blue-400 ring-2 ring-blue-500/10' : 'border-border hover:border-blue-500/40'"
                @click="selectedId = task.id"
              >
                <span v-if="Number(level) > 0" class="absolute -left-[57px] top-1/2 w-14 border-t border-dashed border-border pointer-events-none" />
                <div class="flex items-center gap-2 mb-3">
                  <span class="text-[10px] px-2 py-1 rounded bg-accent text-muted-foreground">{{ typeLabel[task.type] }}</span>
                  <span class="ml-auto text-[10px] font-mono" :class="task.agent === 'claude' ? 'text-violet-400' : 'text-blue-400'">{{ task.agent }}</span>
                </div>
                <div class="text-xs font-semibold mb-1.5">{{ task.name }}</div>
                <div class="text-[11px] text-muted-foreground leading-4 line-clamp-2">{{ task.description }}</div>
                <div class="mt-3 pt-2.5 border-t border-border flex items-center text-[10px] text-muted-foreground">
                  <GitBranch :size="10" class="mr-1" />{{ task.dependsOn.length ? `${task.dependsOn.length} 个依赖` : '根任务' }}
                  <Pencil :size="10" class="ml-auto opacity-0 group-hover:opacity-100" />
                </div>
              </button>
            </div>
          </div>
        </div>
        <div class="lg:hidden border-t border-border p-3 flex items-center gap-2">
          <input v-model="chatInput" class="flex-1 bg-card border border-border rounded-lg px-3 py-2 text-xs outline-none" placeholder="用自然语言修改任务图…" @keydown.enter="applyChatChange" />
          <button class="p-2 bg-blue-500 rounded-lg" @click="applyChatChange"><Send :size="14" /></button>
        </div>
      </section>

      <aside class="border-t lg:border-t-0 lg:border-l border-border bg-sidebar overflow-y-auto" v-if="selectedTask">
        <div class="p-4 border-b border-border flex items-center">
          <div><div class="text-xs font-semibold">任务详情</div><div class="text-[10px] font-mono text-muted-foreground mt-0.5">{{ selectedTask.id }}</div></div>
          <button aria-label="删除任务" class="ml-auto p-1.5 text-muted-foreground hover:text-red-400" @click="removeTask(selectedTask.id)"><Trash2 :size="14" /></button>
        </div>
        <div class="p-4 space-y-4">
          <label class="block"><span class="field-label">任务名称</span><input v-model="selectedTask.name" class="field-input" @input="markDirty" /></label>
          <label class="block"><span class="field-label">任务描述</span><textarea v-model="selectedTask.description" rows="4" class="field-input resize-none leading-5" @input="markDirty" /></label>
          <div class="grid grid-cols-2 gap-2">
            <label><span class="field-label">任务类型</span><select v-model="selectedTask.type" class="field-input" @change="markDirty"><option v-for="(_, key) in typeLabel" :key="key" :value="key">{{ typeLabel[key] }}</option></select></label>
            <label><span class="field-label">执行 Agent</span><select v-model="selectedTask.agent" class="field-input" @change="markDirty"><option value="claude">Claude</option><option value="codex">Codex</option></select></label>
          </div>
          <div>
            <span class="field-label">前置依赖</span>
            <div class="space-y-1.5">
              <button v-for="task in tasks.filter((item) => item.id !== selectedTask?.id)" :key="task.id" class="w-full flex items-center gap-2 text-left p-2 rounded border text-[11px]" :class="selectedTask.dependsOn.includes(task.id) ? 'border-blue-500/30 bg-blue-500/10' : 'border-border hover:bg-accent'" @click="toggleDependency(task.id)">
                <span class="w-3.5 h-3.5 rounded border flex items-center justify-center" :class="selectedTask.dependsOn.includes(task.id) ? 'bg-blue-500 border-blue-500' : 'border-border'"><Check v-if="selectedTask.dependsOn.includes(task.id)" :size="10" /></span>
                <span class="truncate">{{ task.name }}</span>
              </button>
            </div>
          </div>
          <label class="block"><span class="field-label">完成条件</span><textarea v-model="selectedTask.acceptance" rows="3" class="field-input resize-none leading-5" @input="markDirty" /></label>
        </div>
      </aside>
    </main>

    <main v-else class="flex-1 flex items-center justify-center">
      <div class="text-center">
        <CheckCircle2 :size="46" class="text-emerald-400 mx-auto mb-4" />
        <h1 class="text-xl font-semibold">工作流已发布</h1>
        <p class="text-sm text-muted-foreground mt-2">{{ publishedWillRun ? '正在创建 Run 并进入执行看板…' : 'v1 已冻结，可以随时开始执行。' }}</p>
        <button v-if="!publishedWillRun" class="mt-5 px-5 py-2.5 bg-blue-500 hover:bg-blue-400 rounded-lg text-sm text-white" @click="startPublishedRun">开始执行 <Play :size="13" class="inline ml-1" /></button>
      </div>
    </main>

    <Teleport to="body">
      <div v-if="showPublish" class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4" @click.self="showPublish = false">
        <section class="w-full max-w-lg bg-[oklch(0.135_0.009_264)] border border-border rounded-xl shadow-2xl">
          <div class="p-5 border-b border-border flex items-start gap-3">
            <div class="p-2 rounded-lg bg-blue-500/10"><Zap :size="18" class="text-blue-400" /></div>
            <div><h2 class="font-display text-base font-semibold">发布 v1 并开始执行？</h2><p class="text-xs text-muted-foreground mt-1 leading-5">发布后计划将被冻结。继续修改需要创建新版本。</p></div>
            <button aria-label="关闭" class="ml-auto text-muted-foreground" @click="showPublish = false"><X :size="16" /></button>
          </div>
          <div class="p-5 space-y-4">
            <div v-if="publishError" role="alert" class="rounded-lg border border-red-400/30 bg-red-400/10 px-3 py-2 text-xs text-red-200">
              {{ publishError }}
            </div>
            <div class="grid grid-cols-3 gap-2">
              <div class="summary-card"><strong>{{ tasks.length }}</strong><span>任务</span></div>
              <div class="summary-card"><strong>{{ Math.max(...levels.map((items) => items.length), 0) }}</strong><span>最大并行</span></div>
              <div class="summary-card"><strong>{{ roots.length }}</strong><span>根任务</span></div>
            </div>
            <div class="rounded-lg border border-border divide-y divide-border text-xs">
              <div class="flex p-3"><ShieldCheck :size="14" class="text-emerald-400 mr-2" />DAG 校验通过<span class="ml-auto text-emerald-400">0 个错误</span></div>
              <div class="flex p-3"><Bot :size="14" class="text-violet-400 mr-2" />Agent 分配<span class="ml-auto text-muted-foreground">Claude {{ agentStats.claude }} · Codex {{ agentStats.codex }}</span></div>
              <div class="flex p-3"><Clock3 :size="14" class="text-amber-400 mr-2" />执行策略<span class="ml-auto text-muted-foreground">依赖就绪后自动启动</span></div>
            </div>
            <div class="flex gap-2 text-[11px] text-amber-300 bg-amber-500/10 border border-amber-500/20 p-3 rounded-lg leading-5"><CircleAlert :size="14" class="shrink-0 mt-0.5" />发布后任务会写入 Harness；选择“发布并执行”后，根任务将进入执行队列。</div>
          </div>
            <div class="p-4 border-t border-border flex flex-wrap justify-end gap-2"><button class="px-4 py-2 text-xs border border-border rounded-md hover:bg-accent" :disabled="publishing" @click="showPublish = false">返回检查</button><button class="px-4 py-2 text-xs border border-blue-500/40 text-blue-300 rounded-md hover:bg-blue-500/10 disabled:opacity-50" :disabled="publishing" @click="publish(false)">{{ publishing ? '保存中…' : '仅发布' }}</button><button class="px-4 py-2 text-xs bg-blue-500 text-white rounded-md hover:bg-blue-400 disabled:opacity-50" :disabled="publishing" @click="publish(true)">{{ publishing ? '发布中…' : '发布并执行' }}</button></div>
        </section>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.builder-grid { background-image: linear-gradient(to right, color-mix(in oklch, var(--border) 45%, transparent) 1px, transparent 1px), linear-gradient(to bottom, color-mix(in oklch, var(--border) 45%, transparent) 1px, transparent 1px); background-size: 28px 28px; }
.field-label { display: block; font-size: 10px; color: var(--muted-foreground); margin-bottom: 6px; text-transform: uppercase; letter-spacing: .06em; }
.field-input { width: 100%; border: 1px solid var(--border); background: var(--card); border-radius: 7px; padding: 9px 10px; font-size: 12px; outline: none; }
.field-input:focus { border-color: oklch(0.65 0.18 240 / .7); }
.summary-card { display: flex; flex-direction: column; align-items: center; gap: 4px; background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
.summary-card strong { font: 600 20px 'Space Grotesk', sans-serif; }
.summary-card span { font-size: 10px; color: var(--muted-foreground); }
</style>
