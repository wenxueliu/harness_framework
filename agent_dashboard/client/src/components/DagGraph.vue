<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import {
  Handle,
  Position,
  VueFlow,
  type Edge,
  type Node,
  type NodeMouseEvent,
  type VueFlowStore,
} from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'
import type { Task } from '@/api/types'
import { TASK_TYPE_ICON, TASK_TYPE_ORDER } from '@/lib/mockData'

const props = defineProps<{
  tasks: Record<string, Task>
  onTaskClick?: (task: Task) => void
}>()

interface TaskNodeData extends Record<string, unknown> {
  task: Task
}

const NODE_W = 184
const NODE_H = 72
const COL_GAP = 104
const ROW_GAP = 36

const STATUS_COLORS: Record<Task['status'], string> = {
  DONE: '#34d399',
  IN_PROGRESS: '#60a5fa',
  PENDING: '#fbbf24',
  FAILED: '#f87171',
  BLOCKED: '#f87171',
  ABORTED: '#94a3b8',
  AWAITING_REVIEW: '#a78bfa',
  WAITING_FOR_HUMAN: '#c084fc',
  SKIPPED_UPSTREAM_FAILED: '#fda4af',
  UNKNOWN: '#64748b',
}

function taskOrder(task: Task | undefined): number {
  const index = TASK_TYPE_ORDER.indexOf((task?.type ?? '') as typeof TASK_TYPE_ORDER[number])
  return index < 0 ? TASK_TYPE_ORDER.length : index
}

function computeLevels(tasks: Record<string, Task>): Record<string, number> {
  const levels: Record<string, number> = {}
  const visiting = new Set<string>()

  function visit(id: string): number {
    if (levels[id] !== undefined) return levels[id]
    if (visiting.has(id)) return 0
    visiting.add(id)
    const task = tasks[id]
    const dependencies = (task?.depends_on ?? []).filter((dependency) => tasks[dependency])
    const level = dependencies.length === 0
      ? 0
      : Math.max(...dependencies.map((dependency) => visit(dependency))) + 1
    visiting.delete(id)
    levels[id] = level
    return level
  }

  Object.keys(tasks).forEach(visit)
  return levels
}

const nodes = computed<Node<TaskNodeData>[]>(() => {
  const levels = computeLevels(props.tasks)
  const columns = new Map<number, string[]>()
  Object.entries(levels).forEach(([id, level]) => {
    const column = columns.get(level) ?? []
    column.push(id)
    columns.set(level, column)
  })

  columns.forEach((ids) => {
    ids.sort((left, right) => {
      const typeDifference = taskOrder(props.tasks[left]) - taskOrder(props.tasks[right])
      return typeDifference || left.localeCompare(right)
    })
  })

  return Object.entries(props.tasks).map(([id, task]) => {
    const level = levels[id] ?? 0
    const row = columns.get(level)?.indexOf(id) ?? 0
    return {
      id,
      type: 'task',
      position: { x: level * (NODE_W + COL_GAP), y: row * (NODE_H + ROW_GAP) },
      data: { task },
      width: NODE_W,
      height: NODE_H,
      draggable: false,
      connectable: false,
      deletable: false,
      focusable: true,
      selectable: true,
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      ariaLabel: `${task.name}，状态 ${task.status}，点击查看详情`,
    }
  })
})

const edges = computed<Edge[]>(() => Object.entries(props.tasks).flatMap(([target, task]) =>
  task.depends_on
    .filter((source) => Boolean(props.tasks[source]))
    .map((source) => ({
      id: `${source}--${target}`,
      source,
      target,
      type: 'smoothstep',
      animated: props.tasks[source].status === 'IN_PROGRESS',
      selectable: false,
      focusable: false,
      style: {
        stroke: props.tasks[source].status === 'DONE' ? '#34d399' : '#64748b',
        strokeWidth: props.tasks[source].status === 'DONE' ? 2 : 1.5,
        opacity: props.tasks[source].status === 'DONE' ? 0.8 : 0.45,
      },
    })),
))

const flow = ref<VueFlowStore | null>(null)

function fitGraph() {
  nextTick(() => flow.value?.fitView({ padding: 0.2, duration: 250 }))
}

function onPaneReady(instance: VueFlowStore) {
  flow.value = instance
  fitGraph()
}

function selectTask(task: Task) {
  props.onTaskClick?.(task)
}

function onNodeClick({ node }: NodeMouseEvent) {
  selectTask((node.data as TaskNodeData).task)
}

function minimapNodeColor(node: Node): string {
  const task = (node.data as TaskNodeData | undefined)?.task
  return task ? STATUS_COLORS[task.status] : STATUS_COLORS.UNKNOWN
}

function taskStatusColor(status: unknown): string {
  return typeof status === 'string' && status in STATUS_COLORS
    ? STATUS_COLORS[status as Task['status']]
    : STATUS_COLORS.UNKNOWN
}

watch(() => props.tasks, fitGraph, { deep: true })
</script>

<template>
  <div class="dag-flow" aria-label="任务依赖拓扑图">
    <VueFlow
      :nodes="nodes"
      :edges="edges"
      :min-zoom="0.35"
      :max-zoom="1.8"
      :nodes-draggable="false"
      :nodes-connectable="false"
      :elements-selectable="true"
      :fit-view-on-init="true"
      :delete-key-code="null"
      :multi-selection-key-code="null"
      class="dag-flow__canvas"
      @pane-ready="onPaneReady"
      @node-click="onNodeClick"
    >
      <Background :gap="20" :size="1" color="#334155" />
      <Controls position="bottom-left" :show-interactive="false" />
      <MiniMap
        position="bottom-right"
        :pannable="true"
        :zoomable="true"
        :node-color="minimapNodeColor"
        mask-color="rgba(8, 12, 20, 0.72)"
      />

      <template #node-task="{ data, selected }">
        <div
          class="dag-task-node"
          :class="{ 'dag-task-node--active': data.task.status === 'IN_PROGRESS', 'dag-task-node--selected': selected }"
          :style="{ '--task-status-color': taskStatusColor(data.task.status) }"
          role="button"
          tabindex="0"
          :aria-label="`${data.task.name}，状态 ${data.task.status}，按回车查看详情`"
          @keydown.enter.prevent="selectTask(data.task)"
          @keydown.space.prevent="selectTask(data.task)"
        >
          <Handle type="target" :position="Position.Left" :connectable="false" />
          <span class="dag-task-node__icon" aria-hidden="true">{{ TASK_TYPE_ICON[data.task.type ?? ''] }}</span>
          <span class="dag-task-node__copy">
            <span class="dag-task-node__name" :title="data.task.name">{{ data.task.name }}</span>
            <span class="dag-task-node__agent" :title="data.task.assigned_agent">{{ data.task.assigned_agent || '未分配 Agent' }}</span>
          </span>
          <span class="dag-task-node__status" :title="data.task.status" />
          <span v-if="data.task.status === 'FAILED' || data.task.status === 'BLOCKED'" class="dag-task-node__error" aria-hidden="true">×</span>
          <Handle type="source" :position="Position.Right" :connectable="false" />
        </div>
      </template>
    </VueFlow>
  </div>
</template>

<style scoped>
.dag-flow {
  width: 100%;
  height: clamp(22rem, 52vh, 38rem);
  overflow: hidden;
  border-radius: 0.5rem;
  background: oklch(0.115 0.009 264);
}

.dag-flow__canvas { background: transparent; }

.dag-task-node {
  position: relative;
  display: flex;
  width: 184px;
  height: 72px;
  align-items: center;
  gap: 0.65rem;
  padding: 0.75rem 1rem;
  border: 1px solid color-mix(in srgb, var(--task-status-color) 64%, transparent);
  border-radius: 0.5rem;
  background: oklch(0.16 0.01 264);
  color: oklch(0.9 0.005 264);
  box-shadow: 0 8px 24px rgb(0 0 0 / 0.16);
  cursor: pointer;
}

.dag-task-node:focus-visible,
.dag-task-node--selected {
  outline: 2px solid var(--task-status-color);
  outline-offset: 3px;
}

.dag-task-node--active {
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--task-status-color) 16%, transparent), 0 8px 24px rgb(0 0 0 / 0.22);
}

.dag-task-node__icon { flex: 0 0 auto; color: var(--task-status-color); font-size: 0.95rem; }
.dag-task-node__copy { display: flex; min-width: 0; flex: 1; flex-direction: column; gap: 0.25rem; }
.dag-task-node__name, .dag-task-node__agent { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dag-task-node__name { font-family: 'Space Grotesk', sans-serif; font-size: 0.75rem; font-weight: 600; }
.dag-task-node__agent { color: oklch(0.56 0.01 264); font-family: 'JetBrains Mono', monospace; font-size: 0.625rem; }
.dag-task-node__status { width: 0.5rem; height: 0.5rem; flex: 0 0 auto; border-radius: 9999px; background: var(--task-status-color); }
.dag-task-node__error { position: absolute; top: 0.35rem; right: 0.55rem; color: #f87171; font-size: 0.75rem; }

:deep(.vue-flow__handle) { width: 7px; height: 7px; border: 1px solid oklch(0.22 0.01 264); background: var(--task-status-color); }
:deep(.vue-flow__controls), :deep(.vue-flow__minimap) { border: 1px solid oklch(0.25 0.01 264); border-radius: 0.4rem; background: oklch(0.145 0.01 264); box-shadow: 0 8px 24px rgb(0 0 0 / 0.24); }
:deep(.vue-flow__controls-button) { border-color: oklch(0.25 0.01 264); background: oklch(0.16 0.01 264); fill: oklch(0.78 0.005 264); }
:deep(.vue-flow__controls-button:hover) { background: oklch(0.22 0.01 264); }
</style>
