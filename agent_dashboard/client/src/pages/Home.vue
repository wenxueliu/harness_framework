<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import type { Workflow, Task } from '@/api/types'
import {
  fetchWorkflowsFromHarness,
  sendControlSignalToHarness,
  pingHarness,
} from '@/lib/harnessApi'
import { fetchWorkflows as fetchWorkflowsMock, sendControlSignal as sendControlSignalMock, PHASE_CONFIG, TASK_TYPE_ICON } from '@/lib/mockData'
import DagGraph from '@/components/DagGraph.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import PhaseBadge from '@/components/PhaseBadge.vue'
import WorkflowListItem from '@/components/WorkflowListItem.vue'
import ControlDialog from '@/components/ControlDialog.vue'
import TaskDrawer from '@/components/TaskDrawer.vue'
import ProjectGroupSidebar from '@/components/ProjectGroupSidebar.vue'
import RunCreationDialog from '@/components/RunCreationDialog.vue'
import { createRun } from '@/api/dashboard'
import {
  Pause,
  Play,
  XCircle,
  RotateCcw,
  RefreshCw,
  ExternalLink,
  Activity,
  CheckCircle2,
  AlertCircle,
  Clock,
  ChevronRight,
  Menu,
  X,
  LayoutList,
  GitBranch,
  BarChart3,
  FolderPlus,
  Settings,
} from 'lucide-vue-next'
import { cn } from '@/lib/utils'
import { useCapabilityStore } from '@/stores/capabilities'
import { useProjectGroupStore } from '@/stores/projectGroups'
import { useRoute, useRouter } from 'vue-router'
import type { ControlSignal } from '@/lib/constants'
type MobileTab = 'dag' | 'tasks' | 'stats'
const router = useRouter()
const route = useRoute()
const capabilityStore = useCapabilityStore()
const projectGroupStore = useProjectGroupStore()
const demoMode = import.meta.env.VITE_DEMO_MODE === 'true'
const currentGroupId = computed(() => typeof route.params.groupId === 'string' ? route.params.groupId : 'unassigned')

// ─── State ───────────────────────────────────────────────────────────────────
const workflows = ref<Workflow[]>([])
const loading = ref(true)
const selectedId = ref<string | null>(null)
const selectedTask = ref<Task | null>(null)
const pendingSignal = ref<ControlSignal | null>(null)
const pendingTaskName = ref<string | null>(null)
const dataSource = ref<'api' | 'demo' | 'offline'>('offline')
const loadError = ref<string | null>(null)
const dialogOpen = ref(false)
const refreshing = ref(false)
const sheetOpen = ref(false)
const mobileTab = ref<MobileTab>('dag')
const taskDrawerOpen = ref(false)
const runDialogOpen = ref(false)
const runCreating = ref(false)
const runError = ref('')

function syncRouteSelection(data = workflows.value) {
  const workflowId = typeof route.params.workflowId === 'string' ? route.params.workflowId : null
  const taskId = typeof route.params.taskId === 'string' ? route.params.taskId : null
  if (workflowId && data.some((workflow) => workflow.id === workflowId)) {
    selectedId.value = workflowId
  }
  if (taskId && selectedId.value) {
    selectedTask.value = data.find((workflow) => workflow.id === selectedId.value)?.tasks[taskId] ?? null
    taskDrawerOpen.value = selectedTask.value !== null
  } else if (route.name === 'workflow-dashboard') {
    selectedTask.value = null
    taskDrawerOpen.value = false
  }
}

// ─── Derived ────────────────────────────────────────────────────────────────
const selectedWorkflow = computed(() => workflows.value.find((w) => w.id === selectedId.value) ?? null)
const visibleWorkflows = computed(() => {
  if (!projectGroupStore.workflowIds.length && projectGroupStore.selectedId === 'unassigned') {
    return workflows.value
  }
  const allowed = new Set(projectGroupStore.workflowIds)
  return workflows.value.filter((workflow) => allowed.has(workflow.id))
})

const totalDone = computed(() => workflows.value.filter((w) => w.phase === 'DONE').length)
const totalInProgress = computed(() =>
  workflows.value.filter((w) => w.phase === 'DEVELOPMENT' || w.phase === 'TESTING').length
)
const totalBlocked = computed(() =>
  workflows.value.filter((w) => w.phase === 'BLOCKED' || w.phase === 'PAUSED').length
)
const totalAgents = computed(() => {
  return new Set(
    workflows.value.flatMap((w) => Object.values(w.tasks).map((t) => t.assigned_agent))
  ).size
})

const taskStats = computed(() => {
  const tasks = Object.values(selectedWorkflow.value?.tasks ?? {})
  return {
    done: tasks.filter((t) => t.status === 'DONE').length,
    inProgress: tasks.filter((t) => t.status === 'IN_PROGRESS').length,
    failed: tasks.filter((t) => t.status === 'FAILED' || t.status === 'BLOCKED').length,
    total: tasks.length,
  }
})

// ─── Data loading ────────────────────────────────────────────────────────────
async function load(silent = false) {
  if (!silent) loading.value = true
  else refreshing.value = true
  try {
    let data: Workflow[]
    loadError.value = null
    try {
      const apiOk = await pingHarness()
      if (!apiOk) throw new Error('Harness API 当前不可用')
      const capabilityGroupId = typeof route.params.groupId === 'string' ? route.params.groupId : undefined
      await capabilityStore.load(capabilityGroupId)
      if (capabilityStore.enabled('project_groups')) {
        const groupId = typeof route.params.groupId === 'string' ? route.params.groupId : 'unassigned'
        await projectGroupStore.load(groupId)
      }
      data = await fetchWorkflowsFromHarness()
      dataSource.value = 'api'
    } catch (e) {
      if (!demoMode) {
        dataSource.value = 'offline'
        loadError.value = e instanceof Error ? e.message : 'Harness API 当前不可用'
        return
      }
      data = await fetchWorkflowsMock()
      dataSource.value = 'demo'
    }
    workflows.value = data
    if (selectedTask.value && selectedId.value) {
      const refreshedWorkflow = data.find((workflow) => workflow.id === selectedId.value)
      selectedTask.value = refreshedWorkflow?.tasks[selectedTask.value.id] ?? null
    }
    if (data.length > 0 && !selectedId.value) {
      selectedId.value = data[0].id
    }
    syncRouteSelection(data)
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

let refreshTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  load()
  refreshTimer = setInterval(() => load(true), 30000)
})

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})

watch(() => route.fullPath, () => syncRouteSelection())

// ─── Actions ─────────────────────────────────────────────────────────────────
function handleControl(signal: ControlSignal, taskName?: string) {
  pendingSignal.value = signal
  pendingTaskName.value = taskName ?? null
  dialogOpen.value = true
}

async function handleConfirm() {
  if (!pendingSignal.value || !selectedId.value) return
  dialogOpen.value = false
  try {
    if (dataSource.value === 'api') {
      await sendControlSignalToHarness(
        selectedId.value,
        pendingSignal.value,
        pendingTaskName.value ?? undefined,
      )
    } else if (dataSource.value === 'demo') {
      await sendControlSignalMock(selectedId.value, pendingSignal.value)
    } else {
      throw new Error('离线只读状态不能发送控制指令')
    }
    console.log(`控制信号已发送: ${pendingSignal.value}`)
  } catch {
    console.error('指令发送失败，请检查 Harness API 连接')
  }
  pendingSignal.value = null
  pendingTaskName.value = null
}

function handleTaskClick(task: Task) {
  if (selectedId.value) {
    router.push({
      name: 'task-workbench',
      params: {
        groupId: typeof route.params.groupId === 'string' ? route.params.groupId : 'unassigned',
        workflowId: selectedId.value,
        taskId: task.id,
      },
    })
    return
  }
  selectedTask.value = task
  taskDrawerOpen.value = true
}

function selectWorkflow(id: string) {
  selectedId.value = id
  selectedTask.value = null
  mobileTab.value = 'dag'
  if (typeof route.params.groupId === 'string') {
    router.push({
      name: 'workflow-dashboard',
      params: { groupId: route.params.groupId, workflowId: id },
    })
  }
}

async function selectProjectGroup(groupId: string) {
  await projectGroupStore.select(groupId)
  await capabilityStore.load(groupId)
  const workflowId = projectGroupStore.workflowIds[0]
  if (workflowId) {
    selectedId.value = workflowId
    await router.push({ name: 'workflow-dashboard', params: { groupId, workflowId } })
  }
}

async function startRun(input: { workspaceId: string; strategy: 'ORIGINAL' | 'GIT_WORKTREE' | 'CONTROLLED_COPY'; gitRef: string; acceptDirty: boolean }) {
  if (!selectedId.value) return
  runCreating.value = true; runError.value = ''
  try {
    await createRun(selectedId.value, input.workspaceId, input.strategy, input.gitRef, input.acceptDirty)
    runDialogOpen.value = false
    await load(true)
  } catch (cause) { runError.value = cause instanceof Error ? cause.message : 'Run 启动失败' }
  finally { runCreating.value = false }
}

function closeTaskDetail() {
  selectedTask.value = null
  taskDrawerOpen.value = false
  if (typeof route.params.groupId === 'string' && selectedId.value) {
    router.push({
      name: 'workflow-dashboard',
      params: { groupId: route.params.groupId, workflowId: selectedId.value },
    })
  }
}
</script>

<template>
  <!-- Loading -->
  <div v-if="loading" class="min-h-screen bg-background flex items-center justify-center">
    <div class="text-center space-y-3">
      <div class="w-8 h-8 border-2 border-blue-400 border-t-transparent rounded-full animate-spin mx-auto" />
      <p class="text-sm text-muted-foreground font-mono">连接 Consul KV...</p>
    </div>
  </div>

  <!-- Main app -->
  <div v-else class="min-h-screen bg-background flex flex-col">
    <!-- Top Header -->
    <header class="h-12 border-b border-border flex items-center px-3 gap-3 flex-shrink-0 sticky top-0 z-30 bg-background/95 backdrop-blur-sm">
      <button
        class="md:hidden text-muted-foreground hover:text-foreground p-1.5 rounded hover:bg-accent transition-colors"
        @click="sheetOpen = true"
      >
        <Menu :size="16" />
      </button>

      <div class="flex items-center gap-2">
        <div class="w-5 h-5 rounded bg-blue-500/20 border border-blue-500/40 flex items-center justify-center">
          <Activity :size="11" class="text-blue-400" />
        </div>
        <span class="font-display font-semibold text-sm text-foreground">Agent Dashboard</span>
      </div>

      <div class="hidden sm:flex items-center gap-1.5 ml-1">
        <span
          :class="cn(
            'w-1.5 h-1.5 rounded-full pulse-dot',
            dataSource === 'api' ? 'bg-emerald-400' : dataSource === 'demo' ? 'bg-amber-400' : 'bg-red-400',
          )"
        />
        <span class="text-xs text-muted-foreground font-mono">
          {{ dataSource === 'api' ? 'Harness API · 已连接' : dataSource === 'demo' ? 'Demo · 模拟数据' : '离线 · 只读' }}
        </span>
      </div>

      <div class="ml-auto flex items-center gap-2">
        <button
          v-if="typeof route.params.groupId === 'string' && selectedId"
          aria-label="查看执行日志"
          class="hidden md:flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors px-2 py-1.5 rounded hover:bg-accent"
          @click="router.push({ name: 'workflow-logs', params: { groupId: route.params.groupId, workflowId: selectedId } })"
        >
          <LayoutList :size="12" />执行日志
        </button>
        <button
          aria-label="打开全局配置"
          class="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors px-2 py-1.5 rounded hover:bg-accent"
          @click="router.push('/settings')"
        >
          <Settings :size="12" /><span class="hidden lg:inline">配置</span>
        </button>
        <button
          v-if="capabilityStore.permitted('group:manage')"
          aria-label="新建项目"
          class="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors px-2 py-1.5 rounded hover:bg-accent"
          @click="router.push('/settings')"
        >
          <FolderPlus :size="12" /><span class="hidden sm:inline">新建项目</span>
        </button>
        <button
          class="flex items-center gap-1.5 text-xs bg-blue-500 hover:bg-blue-400 text-white transition-colors px-3 py-1.5 rounded-md"
          @click="router.push('/workflows/new')"
        >
          <span class="text-sm leading-none">+</span>
          <span class="hidden sm:inline">新建任务</span>
        </button>
        <button
          class="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors px-2 py-1 rounded hover:bg-accent"
          :disabled="refreshing"
          @click="load(true)"
        >
          <RefreshCw :size="11" :class="refreshing ? 'animate-spin' : ''" />
          <span class="hidden sm:inline">刷新</span>
        </button>
        <span class="text-xs text-muted-foreground font-mono hidden sm:inline ms-2">
          {{ new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) }}
        </span>
      </div>
    </header>

    <div
      v-if="loadError"
      role="alert"
      class="mx-3 mt-3 flex items-center justify-between gap-3 rounded-md border border-red-400/30 bg-red-400/10 px-3 py-2 text-sm text-red-200"
    >
      <span>{{ loadError }}。当前不会自动切换到模拟数据。</span>
      <button class="rounded border border-red-300/30 px-2 py-1 text-xs hover:bg-red-300/10" @click="load()">重试</button>
    </div>

    <!-- Mobile Requirement Sheet -->
    <Teleport to="body">
      <div v-if="sheetOpen" class="fixed inset-0 bg-black/60 z-40 md:hidden" @click="sheetOpen = false" />
      <div
        class="fixed top-0 left-0 h-full w-72 bg-sidebar border-r border-border z-50 flex flex-col transition-transform duration-300 ease-in-out md:hidden"
        :class="sheetOpen ? 'translate-x-0' : '-translate-x-full'"
      >
        <div class="flex items-center justify-between px-4 py-3 border-b border-border">
          <span class="text-sm font-display font-semibold text-foreground">需求列表</span>
          <button
            class="text-muted-foreground hover:text-foreground p-1 rounded hover:bg-accent transition-colors"
            @click="sheetOpen = false"
          >
            <X :size="16" />
          </button>
        </div>
        <div class="flex-1 overflow-y-auto py-1">
          <WorkflowListItem
            v-for="wf in workflows"
            :key="wf.id"
            :workflow="wf"
            :selected="wf.id === selectedId"
            variant="mobile"
            @select="(id) => { selectWorkflow(id); sheetOpen = false }"
          />
        </div>
      </div>
    </Teleport>

    <!-- Body -->
    <div class="flex flex-1 overflow-hidden">
      <ProjectGroupSidebar v-if="capabilityStore.enabled('project_groups')" @select="selectProjectGroup" />
      <!-- Desktop sidebar -->
      <aside class="hidden md:flex w-56 border-r border-border flex-col flex-shrink-0 overflow-hidden">
        <div class="px-3 py-2.5 border-b border-border">
          <span class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">需求列表</span>
          <span class="ml-2 text-xs font-mono text-muted-foreground">{{ workflows.length }}</span>
        </div>
        <div class="flex-1 overflow-y-auto py-1">
          <WorkflowListItem
            v-for="wf in visibleWorkflows"
            :key="wf.id"
            :workflow="wf"
            :selected="wf.id === selectedId"
            variant="desktop"
            @select="selectWorkflow"
          />
        </div>
      </aside>

      <!-- Main Content -->
      <main class="flex-1 flex flex-col overflow-hidden min-w-0">
        <template v-if="selectedWorkflow">
          <!-- Workflow header -->
          <div class="border-b border-border px-3 sm:px-5 py-2.5 flex-shrink-0">
            <div class="flex items-start gap-2 mb-2">
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2 mb-0.5 flex-wrap">
                  <span class="font-mono text-xs text-muted-foreground">{{ selectedWorkflow.id }}</span>
                  <ChevronRight :size="12" class="text-border hidden sm:block" />
                  <PhaseBadge :phase="selectedWorkflow.phase" />
                </div>
                <h1 class="font-display font-semibold text-sm sm:text-base text-foreground truncate">
                  {{ selectedWorkflow.title }}
                </h1>
              </div>
            </div>

            <!-- Control buttons -->
            <div class="flex items-center gap-2 overflow-x-auto pb-0.5 scrollbar-hide">
              <button
                v-if="capabilityStore.permitted('run:create') && projectGroupStore.workspaces.length"
                class="flex-shrink-0 flex items-center gap-1.5 text-xs px-3 py-1.5 rounded bg-blue-500/10 border border-blue-500/30 text-blue-300 hover:bg-blue-500/20 transition-colors whitespace-nowrap"
                @click="runError = ''; runDialogOpen = true"
              >
                <Play :size="11" />启动 Run
              </button>
              <button
                v-if="selectedWorkflow.phase === 'BLOCKED'"
                class="flex-shrink-0 flex items-center gap-1.5 text-xs px-3 py-1.5 rounded bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/20 transition-colors whitespace-nowrap"
                @click="handleControl('RETRY')"
              >
                <RotateCcw :size="11" />重试
              </button>
              <button
                v-if="selectedWorkflow.phase === 'PAUSED'"
                class="flex-shrink-0 flex items-center gap-1.5 text-xs px-3 py-1.5 rounded bg-blue-500/10 border border-blue-500/30 text-blue-400 hover:bg-blue-500/20 transition-colors whitespace-nowrap"
                @click="handleControl('RESUME')"
              >
                <Play :size="11" />恢复
              </button>
              <button
                v-else-if="selectedWorkflow.phase !== 'DONE'"
                class="flex-shrink-0 flex items-center gap-1.5 text-xs px-3 py-1.5 rounded bg-amber-500/10 border border-amber-500/30 text-amber-400 hover:bg-amber-500/20 transition-colors whitespace-nowrap"
                @click="handleControl('PAUSE')"
              >
                <Pause :size="11" />暂停
              </button>
              <button
                v-if="selectedWorkflow.phase !== 'DONE'"
                class="flex-shrink-0 flex items-center gap-1.5 text-xs px-3 py-1.5 rounded bg-red-500/10 border border-red-500/30 text-red-400 hover:bg-red-500/20 transition-colors whitespace-nowrap"
                @click="handleControl('ABORT')"
              >
                <XCircle :size="11" />中止
              </button>
              <a
                v-if="selectedWorkflow.artifacts.api_spec"
                :href="selectedWorkflow.artifacts.api_spec"
                target="_blank"
                rel="noopener noreferrer"
                class="flex-shrink-0 flex items-center gap-1.5 text-xs px-3 py-1.5 rounded bg-accent border border-border text-muted-foreground hover:text-foreground transition-colors whitespace-nowrap"
              >
                <ExternalLink :size="11" />API 文档
              </a>
              <a
                v-if="selectedWorkflow.artifacts.test_report"
                :href="selectedWorkflow.artifacts.test_report"
                target="_blank"
                rel="noopener noreferrer"
                class="flex-shrink-0 flex items-center gap-1.5 text-xs px-3 py-1.5 rounded bg-violet-500/10 border border-violet-500/30 text-violet-400 hover:bg-violet-500/20 transition-colors whitespace-nowrap"
              >
                <ExternalLink :size="11" />测试报告
              </a>
            </div>
          </div>

          <!-- Mobile Tab Bar -->
          <div class="md:hidden flex border-b border-border flex-shrink-0">
            <button
              v-for="tab in ([
                { key: 'dag' as MobileTab, label: 'DAG 图', icon: GitBranch },
                { key: 'tasks' as MobileTab, label: '任务列表', icon: LayoutList },
                { key: 'stats' as MobileTab, label: '概览', icon: BarChart3 },
              ])"
              :key="tab.key"
              class="flex-1 flex items-center justify-center gap-1.5 py-2.5 text-xs font-medium transition-colors border-b-2"
              :class="mobileTab === tab.key ? 'text-blue-400 border-blue-400' : 'text-muted-foreground border-transparent hover:text-foreground'"
              @click="mobileTab = tab.key"
            >
              <component :is="tab.icon" :size="13" />
              {{ tab.label }}
            </button>
          </div>

          <!-- Scrollable Content -->
          <div class="flex-1 overflow-y-auto">
            <div class="flex h-full">
              <!-- Left main area -->
              <div class="flex-1 overflow-y-auto min-w-0">

                <!-- Progress bar -->
                <div :class="cn('p-3 sm:p-5', mobileTab !== 'dag' && mobileTab !== 'tasks' ? 'hidden md:block' : '')">
                  <div class="bg-card border border-border rounded-lg p-4">
                    <div class="space-y-1.5">
                      <div class="flex items-center justify-between text-xs">
                        <span class="text-muted-foreground font-mono">
                          {{ taskStats.done }}/{{ taskStats.total }} 任务完成
                        </span>
                        <span class="text-muted-foreground font-mono">
                          {{ taskStats.total > 0 ? Math.round(taskStats.done / taskStats.total * 100) : 0 }}%
                        </span>
                      </div>
                      <div class="h-1.5 bg-[oklch(0.22_0.01_264)] rounded-full overflow-hidden">
                        <div
                          class="h-full bg-emerald-500 rounded-full transition-all duration-700"
                          :style="{ width: `${taskStats.total > 0 ? Math.round(taskStats.done / taskStats.total * 100) : 0}%` }"
                        />
                      </div>
                      <div class="flex gap-3 text-xs text-muted-foreground">
                        <span v-if="taskStats.inProgress > 0" class="flex items-center gap-1 text-blue-400">
                          <span class="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
                          {{ taskStats.inProgress }} 进行中
                        </span>
                        <span v-if="taskStats.failed > 0" class="flex items-center gap-1 text-red-400">
                          <span class="w-1.5 h-1.5 rounded-full bg-red-400" />
                          {{ taskStats.failed }} 失败
                        </span>
                      </div>
                    </div>
                  </div>
                </div>

                <!-- DAG section -->
                <div :class="cn('px-3 sm:px-5 pb-3 sm:pb-5', mobileTab === 'dag' ? 'block' : 'hidden md:block')">
                  <div class="flex items-center gap-2 mb-3">
                    <h2 class="font-display font-semibold text-sm text-foreground">任务依赖拓扑</h2>
                    <div class="hidden sm:flex items-center gap-3 ml-auto text-xs text-muted-foreground">
                      <span v-for="(icon, type) in TASK_TYPE_ICON" :key="type" class="flex items-center gap-1">
                        <span>{{ icon }}</span>
                        <span class="capitalize">{{ type }}</span>
                      </span>
                    </div>
                  </div>
                  <div class="bg-card border border-border rounded-lg p-3 sm:p-4 overflow-x-auto">
                    <DagGraph
                      :tasks="selectedWorkflow.tasks"
                      @task-click="handleTaskClick"
                    />
                  </div>
                  <p class="text-xs text-muted-foreground mt-2 text-center md:hidden">
                    左右滑动查看完整图表 · 点击节点查看详情
                  </p>
                </div>

                <!-- Task list section -->
                <div :class="cn('px-3 sm:px-5 pb-3 sm:pb-5', mobileTab === 'tasks' ? 'block' : 'hidden md:block')">
                  <h2 class="font-display font-semibold text-sm text-foreground mb-3">任务列表</h2>

                  <!-- Mobile card layout -->
                  <div class="md:hidden space-y-2">
                    <button
                      v-for="task in Object.values(selectedWorkflow.tasks)"
                      :key="task.id"
                      class="w-full text-left bg-card border border-border rounded-lg p-3 hover:border-blue-500/40 transition-colors active:bg-accent"
                      @click="handleTaskClick(task)"
                    >
                      <div class="flex items-start justify-between gap-2">
                        <div class="flex items-center gap-2 min-w-0">
                          <span class="text-muted-foreground text-sm flex-shrink-0">{{ TASK_TYPE_ICON[task.type ?? ''] }}</span>
                          <div class="min-w-0">
                            <div class="text-xs font-medium text-foreground truncate">{{ task.name }}</div>
                            <div class="font-mono text-xs text-muted-foreground truncate">{{ task.assigned_agent }}</div>
                          </div>
                        </div>
                        <StatusBadge :status="task.status" class="flex-shrink-0" />
                      </div>
                      <div v-if="task.depends_on.length > 0" class="mt-2 flex flex-wrap gap-1">
                        <span
                          v-for="dep in task.depends_on"
                          :key="dep"
                          class="font-mono text-xs px-1.5 py-0.5 rounded bg-accent text-muted-foreground border border-border/50"
                        >
                          {{ dep }}
                        </span>
                      </div>
                    </button>
                  </div>

                  <!-- Desktop table layout -->
                  <div class="hidden md:block bg-card border border-border rounded-lg overflow-hidden">
                    <table class="w-full text-sm">
                      <thead>
                        <tr class="border-b border-border">
                          <th class="text-left px-4 py-2.5 text-xs font-medium text-muted-foreground">任务</th>
                          <th class="text-left px-4 py-2.5 text-xs font-medium text-muted-foreground">状态</th>
                          <th class="text-left px-4 py-2.5 text-xs font-medium text-muted-foreground hidden lg:table-cell">执行 Agent</th>
                          <th class="text-left px-4 py-2.5 text-xs font-medium text-muted-foreground hidden xl:table-cell">版本 / Commit</th>
                          <th class="text-left px-4 py-2.5 text-xs font-medium text-muted-foreground hidden xl:table-cell">更新时间</th>
                          <th class="px-4 py-2.5 w-8" />
                        </tr>
                      </thead>
                      <tbody>
                        <tr
                          v-for="task in Object.values(selectedWorkflow.tasks)"
                          :key="task.id"
                          class="border-b border-border/50 last:border-0 cursor-pointer transition-colors"
                          :class="selectedTask?.id === task.id ? 'bg-blue-500/5' : 'hover:bg-accent/50'"
                          @click="handleTaskClick(task)"
                        >
                          <td class="px-4 py-3">
                            <div class="flex items-center gap-2">
                              <span class="text-muted-foreground text-xs">{{ TASK_TYPE_ICON[task.type ?? ''] }}</span>
                              <div>
                                <div class="font-medium text-xs text-foreground">{{ task.name }}</div>
                                <div class="font-mono text-xs text-muted-foreground">{{ task.id }}</div>
                              </div>
                            </div>
                          </td>
                          <td class="px-4 py-3"><StatusBadge :status="task.status" /></td>
                          <td class="px-4 py-3 hidden lg:table-cell">
                            <span class="font-mono text-xs text-muted-foreground">{{ task.assigned_agent }}</span>
                          </td>
                          <td class="px-4 py-3 hidden xl:table-cell">
                            <span class="font-mono text-xs text-muted-foreground">
                              {{ task.deployed_version ?? (task.git_commit ? `#${task.git_commit}` : '—') }}
                            </span>
                          </td>
                          <td class="px-4 py-3 hidden xl:table-cell">
                            <span class="font-mono text-xs text-muted-foreground">
                              {{ new Date(task.last_updated).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) }}
                            </span>
                          </td>
                          <td class="px-4 py-3">
                            <AlertCircle v-if="task.error_log_url || task.screenshot_url" :size="12" class="text-red-400" />
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>

                <!-- Stats section — mobile only -->
                <div :class="cn('px-3 pb-6', mobileTab === 'stats' ? 'block md:hidden' : 'hidden')">
                  <h2 class="font-display font-semibold text-sm text-foreground mb-3">全局概览</h2>
                  <div class="grid grid-cols-2 gap-3">
                    <div class="bg-card border border-border rounded-lg p-4 flex items-center gap-3">
                      <div class="bg-emerald-400/10 p-2 rounded-md flex-shrink-0"><CheckCircle2 :size="14" class="text-emerald-400" /></div>
                      <div>
                        <div class="text-2xl font-display font-bold text-foreground leading-none">{{ totalDone }}</div>
                        <div class="text-xs text-muted-foreground mt-0.5">已完成需求</div>
                      </div>
                    </div>
                    <div class="bg-card border border-border rounded-lg p-4 flex items-center gap-3">
                      <div class="bg-blue-400/10 p-2 rounded-md flex-shrink-0"><Activity :size="14" class="text-blue-400" /></div>
                      <div>
                        <div class="text-2xl font-display font-bold text-foreground leading-none">{{ totalInProgress }}</div>
                        <div class="text-xs text-muted-foreground mt-0.5">进行中需求</div>
                      </div>
                    </div>
                    <div class="bg-card border border-border rounded-lg p-4 flex items-center gap-3">
                      <div class="bg-red-400/10 p-2 rounded-md flex-shrink-0"><AlertCircle :size="14" class="text-red-400" /></div>
                      <div>
                        <div class="text-2xl font-display font-bold text-foreground leading-none">{{ totalBlocked }}</div>
                        <div class="text-xs text-muted-foreground mt-0.5">阻塞需求</div>
                      </div>
                    </div>
                    <div class="bg-card border border-border rounded-lg p-4 flex items-center gap-3">
                      <div class="bg-violet-400/10 p-2 rounded-md flex-shrink-0"><Clock :size="14" class="text-violet-400" /></div>
                      <div>
                        <div class="text-2xl font-display font-bold text-foreground leading-none">{{ totalAgents }}</div>
                        <div class="text-xs text-muted-foreground mt-0.5">活跃 Agent</div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <!-- Desktop right task detail -->
              <div v-if="selectedTask" class="hidden md:flex w-64 flex-shrink-0 overflow-hidden border-l border-border">
                <TaskDrawer :task="selectedTask" :req-id="selectedId ?? undefined" :group-id="currentGroupId" @close="closeTaskDetail" @message-sent="load(true)" />
              </div>
            </div>
          </div>
        </template>

        <!-- Empty state -->
        <div v-else class="flex-1 flex items-center justify-center text-muted-foreground text-sm">
          请从左侧选择一个需求
        </div>
      </main>

      <!-- Desktop right stats panel -->
      <aside class="hidden xl:flex w-48 border-l border-border flex-col flex-shrink-0 overflow-y-auto p-3 gap-3">
        <div class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">全局概览</div>
        <div class="bg-card border border-border rounded-lg p-4 flex items-center gap-3">
          <div class="bg-emerald-400/10 p-2 rounded-md flex-shrink-0"><CheckCircle2 :size="14" class="text-emerald-400" /></div>
          <div>
            <div class="text-2xl font-display font-bold text-foreground leading-none">{{ totalDone }}</div>
            <div class="text-xs text-muted-foreground mt-0.5">已完成需求</div>
          </div>
        </div>
        <div class="bg-card border border-border rounded-lg p-4 flex items-center gap-3">
          <div class="bg-blue-400/10 p-2 rounded-md flex-shrink-0"><Activity :size="14" class="text-blue-400" /></div>
          <div>
            <div class="text-2xl font-display font-bold text-foreground leading-none">{{ totalInProgress }}</div>
            <div class="text-xs text-muted-foreground mt-0.5">进行中需求</div>
          </div>
        </div>
        <div class="bg-card border border-border rounded-lg p-4 flex items-center gap-3">
          <div class="bg-red-400/10 p-2 rounded-md flex-shrink-0"><AlertCircle :size="14" class="text-red-400" /></div>
          <div>
            <div class="text-2xl font-display font-bold text-foreground leading-none">{{ totalBlocked }}</div>
            <div class="text-xs text-muted-foreground mt-0.5">阻塞需求</div>
          </div>
        </div>
        <div class="bg-card border border-border rounded-lg p-4 flex items-center gap-3">
          <div class="bg-violet-400/10 p-2 rounded-md flex-shrink-0"><Clock :size="14" class="text-violet-400" /></div>
          <div>
            <div class="text-2xl font-display font-bold text-foreground leading-none">{{ totalAgents }}</div>
            <div class="text-xs text-muted-foreground mt-0.5">活跃 Agent</div>
          </div>
        </div>
      </aside>
    </div>
    <RunCreationDialog :open="runDialogOpen" :req-id="selectedId || ''" :workspaces="projectGroupStore.workspaces" :busy="runCreating" :error="runError" @close="runDialogOpen = false" @create="startRun" />

    <!-- Mobile Task Detail Bottom Sheet -->
    <Teleport to="body">
      <div v-if="taskDrawerOpen && selectedTask" class="fixed inset-0 bg-black/60 z-40 md:hidden" @click="closeTaskDetail" />
      <div
        class="fixed bottom-0 left-0 right-0 z-50 bg-[oklch(0.135_0.009_264)] border-t border-border rounded-t-2xl transition-transform duration-300 ease-in-out md:hidden"
        :class="taskDrawerOpen ? 'translate-y-0' : 'translate-y-full'"
        :style="{ maxHeight: '75vh' }"
      >
        <div class="flex justify-center pt-3 pb-1">
          <div class="w-10 h-1 rounded-full bg-border" />
        </div>
        <div class="overflow-y-auto" :style="{ maxHeight: 'calc(75vh - 32px)' }">
          <TaskDrawer :task="selectedTask" :req-id="selectedId ?? undefined" :group-id="currentGroupId" @close="closeTaskDetail" @message-sent="load(true)" />
        </div>
      </div>
    </Teleport>

    <!-- Control Dialog -->
    <ControlDialog
      :open="dialogOpen"
      :signal="pendingSignal"
      :req-id="selectedId ?? ''"
      @confirm="handleConfirm"
      @cancel="dialogOpen = false; pendingSignal = null; pendingTaskName = null"
    />
  </div>
</template>
