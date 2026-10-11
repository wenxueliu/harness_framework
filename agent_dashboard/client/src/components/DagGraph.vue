<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  Handle,
  Panel,
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

// ui-ux-design.md §4.2：WAITING_FOR_CAPABILITY / UNROUTABLE 使用橙色告警色。
// 这两个状态目前不在 TaskStatus 联合类型中（实例级状态），但任务数据可能透传。
const EXTRA_STATUS_COLORS: Record<string, string> = {
  WAITING_FOR_CAPABILITY: '#d29922',
  UNROUTABLE: '#d29922',
}

// §4.2 要求状态不只靠颜色区分：每种状态附带图标与中文文字标签。
const STATUS_META: Record<string, { icon: string; label: string }> = {
  PENDING: { icon: '◌', label: '待执行' },
  IN_PROGRESS: { icon: '◐', label: '进行中' },
  DONE: { icon: '●', label: '完成' },
  FAILED: { icon: '✕', label: '失败' },
  BLOCKED: { icon: '⊘', label: '阻塞' },
  ABORTED: { icon: '■', label: '已终止' },
  AWAITING_REVIEW: { icon: '◇', label: '待审查' },
  WAITING_FOR_HUMAN: { icon: '☛', label: '待人工' },
  WAITING_FOR_CAPABILITY: { icon: '⚠', label: '待能力' },
  UNROUTABLE: { icon: '⚠', label: '无法路由' },
  SKIPPED_UPSTREAM_FAILED: { icon: '⤼', label: '上游失败' },
  UNKNOWN: { icon: '?', label: '未知' },
}

// §4.2 连线样式：按源任务状态区分（实线/虚线/流动动画/颜色）。
const EDGE_STYLES: Record<string, { stroke: string; animated: boolean; dashed: boolean }> = {
  DONE: { stroke: '#34d399', animated: false, dashed: false },
  IN_PROGRESS: { stroke: '#60a5fa', animated: true, dashed: false },
  WAITING_FOR_HUMAN: { stroke: '#c084fc', animated: true, dashed: false },
  WAITING_FOR_CAPABILITY: { stroke: '#d29922', animated: false, dashed: true },
  UNROUTABLE: { stroke: '#d29922', animated: false, dashed: true },
  FAILED: { stroke: '#f87171', animated: false, dashed: false },
  BLOCKED: { stroke: '#94a3b8', animated: false, dashed: true },
  ABORTED: { stroke: '#94a3b8', animated: false, dashed: true },
  PENDING: { stroke: '#64748b', animated: false, dashed: true },
  AWAITING_REVIEW: { stroke: '#a78bfa', animated: false, dashed: false },
  SKIPPED_UPSTREAM_FAILED: { stroke: '#fda4af', animated: false, dashed: true },
  UNKNOWN: { stroke: '#64748b', animated: false, dashed: true },
}

type LayoutDirection = 'LR' | 'TB'
const layoutDirection = ref<LayoutDirection>('LR')
const layoutNonce = ref(0)

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
  void layoutNonce.value
  const horizontal = layoutDirection.value === 'LR'
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
    // LR：层沿 x 展开，层内沿 y 展开；TB：交换两轴。
    const position = horizontal
      ? { x: level * (NODE_W + COL_GAP), y: row * (NODE_H + ROW_GAP) }
      : { x: row * (NODE_W + COL_GAP), y: level * (NODE_H + ROW_GAP) }
    return {
      id,
      type: 'task',
      position,
      data: { task },
      width: NODE_W,
      height: NODE_H,
      draggable: false,
      connectable: false,
      deletable: false,
      focusable: true,
      selectable: true,
      sourcePosition: horizontal ? Position.Right : Position.Bottom,
      targetPosition: horizontal ? Position.Left : Position.Top,
      ariaLabel: `${task.name}，状态 ${task.status}，点击查看详情`,
    }
  })
})

function edgeStyle(status: unknown): { stroke: string; animated: boolean; dashed: boolean } {
  const key = typeof status === 'string' && status in EDGE_STYLES ? status : 'UNKNOWN'
  return EDGE_STYLES[key]
}

const edges = computed<Edge[]>(() => Object.entries(props.tasks).flatMap(([target, task]) =>
  task.depends_on
    .filter((source) => Boolean(props.tasks[source]))
    .map((source) => {
      const style = edgeStyle(props.tasks[source].status)
      return {
        id: `${source}--${target}`,
        source,
        target,
        type: 'smoothstep',
        animated: style.animated,
        selectable: false,
        focusable: false,
        style: {
          stroke: style.stroke,
          strokeWidth: style.animated ? 2 : 1.5,
          strokeDasharray: style.dashed ? '6 4' : undefined,
          opacity: style.animated ? 0.85 : 0.55,
        },
      }
    }),
))

const flow = ref<VueFlowStore | null>(null)
const graphRoot = ref<HTMLElement | null>(null)

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

function focusTask(id: string) {
  nextTick(() => {
    const node = graphRoot.value
      ?.querySelector<HTMLElement>(`.vue-flow__node[data-id="${CSS.escape(id)}"] .dag-task-node`)
    node?.focus()
  })
}

function navigateFrom(taskId: string, direction: 'left' | 'right' | 'up' | 'down') {
  const current = nodes.value.find((node) => node.id === taskId)
  if (!current) return false
  const horizontal = direction === 'left' || direction === 'right'
  const candidates = nodes.value.filter((node) => {
    if (node.id === taskId) return false
    if (horizontal) return node.position.y === current.position.y
    return node.position.x === current.position.x
  })
  const axis = horizontal ? 'x' : 'y'
  const currentAxis = horizontal ? current.position.x : current.position.y
  const desired = direction === 'left' || direction === 'up'
    ? candidates.filter((node) => node.position[axis] < currentAxis).sort((left, right) => right.position[axis] - left.position[axis])[0]
    : candidates.filter((node) => node.position[axis] > currentAxis).sort((left, right) => left.position[axis] - right.position[axis])[0]
  if (!desired) return false
  focusTask(desired.id)
  flow.value?.updateNode(desired.id, {})
  return true
}

function onNodeKeydown(event: KeyboardEvent, task: Task) {
  const directionMap = {
    ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down',
  } as Record<string, 'left' | 'right' | 'up' | 'down'>
  const direction = directionMap[event.key]
  if (!direction) return
  event.preventDefault()
  navigateFrom(task.id, direction)
}

function onGraphKeydown(event: KeyboardEvent) {
  if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return
  event.preventDefault()
  const first = nodes.value[0]
  if (first) {
    focusTask(first.id)
    flow.value?.updateNode(first.id, {})
  }
}

function onNodeClick({ node }: NodeMouseEvent) {
  selectTask((node.data as TaskNodeData).task)
}

function minimapNodeColor(node: Node): string {
  const task = (node.data as TaskNodeData | undefined)?.task
  return task ? STATUS_COLORS[task.status] : STATUS_COLORS.UNKNOWN
}

function taskStatusColor(status: unknown): string {
  if (typeof status === 'string') {
    if (status in EXTRA_STATUS_COLORS) return EXTRA_STATUS_COLORS[status]
    if (status in STATUS_COLORS) return STATUS_COLORS[status as Task['status']]
  }
  return STATUS_COLORS.UNKNOWN
}

function taskStatusMeta(status: unknown): { icon: string; label: string } {
  const key = typeof status === 'string' ? status : ''
  return STATUS_META[key] ?? { icon: '?', label: key || '未知' }
}

// ---- §4.1 工具栏 ----

function toggleLayoutDirection() {
  layoutDirection.value = layoutDirection.value === 'LR' ? 'TB' : 'LR'
  fitGraph()
}

function autoLayout() {
  layoutNonce.value += 1
  fitGraph()
}

const isFullscreen = ref(false)
const fullscreenSupported = computed(() =>
  typeof document !== 'undefined' && typeof document.fullscreenEnabled === 'boolean'
    ? document.fullscreenEnabled
    : typeof document !== 'undefined' && 'fullscreenEnabled' in document,
)

function syncFullscreenState() {
  isFullscreen.value = Boolean(document.fullscreenElement)
}

function toggleFullscreen() {
  const root = graphRoot.value
  if (!root) return
  if (document.fullscreenElement) {
    document.exitFullscreen().catch(() => {})
    return
  }
  if (!document.fullscreenEnabled) {
    // 降级：浏览器或权限策略不允许全屏时静默跳过。
    console.warn('[DagGraph] Fullscreen API 不可用，已跳过全屏请求')
    return
  }
  root.requestFullscreen().catch(() => {
    console.warn('[DagGraph] 进入全屏失败')
  })
}

const hasGraphData = computed(() => Object.keys(props.tasks).length > 0)
const exporting = ref(false)

function xmlEscape(value: string): string {
  return value.replace(/[&<>"']/g, (ch) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' }[ch] ?? ch
  ))
}

// 导出采用数据驱动的 SVG 近似绘制（节点矩形 + 文字 + 连线），保证节点文字保留且无跨域污染。
function buildExportSvg(nodeList: Node<TaskNodeData>[], edgeList: Edge[]): string | null {
  if (!nodeList.length) return null
  const horizontal = layoutDirection.value === 'LR'
  const EXPORT_PAD = 24
  const minX = Math.min(...nodeList.map((node) => node.position.x)) - EXPORT_PAD
  const minY = Math.min(...nodeList.map((node) => node.position.y)) - EXPORT_PAD
  const maxX = Math.max(...nodeList.map((node) => node.position.x + NODE_W)) + EXPORT_PAD
  const maxY = Math.max(...nodeList.map((node) => node.position.y + NODE_H)) + EXPORT_PAD
  const width = maxX - minX
  const height = maxY - minY

  const positionOf = new Map(nodeList.map((node) => [node.id, node.position]))
  const edgePaths = edgeList.map((edge) => {
    const source = positionOf.get(edge.source)
    const target = positionOf.get(edge.target)
    if (!source || !target) return ''
    const style = edgeStyle(props.tasks[edge.source]?.status)
    const [x1, y1] = horizontal
      ? [source.x + NODE_W, source.y + NODE_H / 2]
      : [source.x + NODE_W / 2, source.y + NODE_H]
    const [x2, y2] = horizontal
      ? [target.x, target.y + NODE_H / 2]
      : [target.x + NODE_W / 2, target.y]
    const dash = style.dashed ? ' stroke-dasharray="6 4"' : ''
    const d = horizontal
      ? `M ${x1} ${y1} C ${(x1 + x2) / 2} ${y1}, ${(x1 + x2) / 2} ${y2}, ${x2} ${y2}`
      : `M ${x1} ${y1} C ${x1} ${(y1 + y2) / 2}, ${x2} ${(y1 + y2) / 2}, ${x2} ${y2}`
    return `<path d="${d}" fill="none" stroke="${style.stroke}" stroke-width="1.5" opacity="0.8"${dash} />`
  }).join('')

  const nodeRects = nodeList.map((node) => {
    const task = node.data?.task
    if (!task) return ''
    const color = taskStatusColor(task.status)
    const meta = taskStatusMeta(task.status)
    const { x, y } = node.position
    const truncate = (value: string) => (value.length > 18 ? `${value.slice(0, 17)}…` : value)
    return [
      `<rect x="${x}" y="${y}" width="${NODE_W}" height="${NODE_H}" rx="8" fill="#161b24" stroke="${color}" stroke-width="1.5" />`,
      `<circle cx="${x + 14}" cy="${y + 20}" r="4" fill="${color}" />`,
      `<text x="${x + 24}" y="${y + 24}" font-size="10" fill="${color}" font-family="monospace">${xmlEscape(`${meta.icon} ${meta.label}`)}</text>`,
      `<text x="${x + 12}" y="${y + 45}" font-size="12" font-weight="600" fill="#e6eaf2" font-family="sans-serif">${xmlEscape(truncate(task.name))}</text>`,
      `<text x="${x + 12}" y="${y + 61}" font-size="9" fill="#8b95a5" font-family="monospace">${xmlEscape(truncate(task.assigned_agent || '未分配 Agent'))}</text>`,
    ].join('')
  }).join('')

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="${minX} ${minY} ${width} ${height}"><rect x="${minX}" y="${minY}" width="${width}" height="${height}" fill="#0d1117" />${edgePaths}${nodeRects}</svg>`
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function exportSvgAsPng(blob: Blob): Promise<void> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(blob)
    const image = new Image()
    const cleanup = () => URL.revokeObjectURL(url)
    image.onload = () => {
      try {
        const scale = 2
        const canvas = document.createElement('canvas')
        canvas.width = image.naturalWidth * scale
        canvas.height = image.naturalHeight * scale
        const context = canvas.getContext('2d')
        if (!context) throw new Error('canvas 2d context unavailable')
        context.scale(scale, scale)
        context.drawImage(image, 0, 0)
        canvas.toBlob((png) => {
          cleanup()
          if (!png) {
            reject(new Error('canvas.toBlob returned null'))
            return
          }
          triggerDownload(png, 'dag.png')
          resolve()
        }, 'image/png')
      } catch (error) {
        cleanup()
        reject(error)
      }
    }
    image.onerror = () => {
      cleanup()
      reject(new Error('SVG image load failed'))
    }
    image.src = url
  })
}

async function exportDagImage() {
  if (exporting.value || !hasGraphData.value) return
  const svg = buildExportSvg(nodes.value, edges.value)
  if (!svg) return
  exporting.value = true
  try {
    const svgBlob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' })
    try {
      await exportSvgAsPng(svgBlob)
    } catch {
      // 降级：PNG 渲染失败（如 canvas 不可用）时退回直接下载 SVG。
      triggerDownload(svgBlob, 'dag.svg')
    }
  } finally {
    exporting.value = false
  }
}

onMounted(() => document.addEventListener('fullscreenchange', syncFullscreenState))
onUnmounted(() => document.removeEventListener('fullscreenchange', syncFullscreenState))

watch(() => props.tasks, fitGraph, { deep: true })
</script>

<template>
  <div ref="graphRoot" class="dag-flow" aria-label="任务依赖拓扑图" role="group" tabindex="0" @keydown="onGraphKeydown">
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
      <Panel position="top-left" class="dag-toolbar" aria-label="DAG 画布工具栏">
        <button
          type="button"
          data-testid="dag-toolbar-layout"
          :title="layoutDirection === 'LR' ? '切换为纵向布局' : '切换为横向布局'"
          :aria-label="layoutDirection === 'LR' ? '切换为纵向布局' : '切换为横向布局'"
          @click="toggleLayoutDirection"
        >{{ layoutDirection === 'LR' ? '⇄' : '⇅' }}</button>
        <button
          type="button"
          data-testid="dag-toolbar-autolayout"
          title="自动布局"
          aria-label="自动布局"
          @click="autoLayout"
        >🔄</button>
        <button
          type="button"
          data-testid="dag-toolbar-fullscreen"
          :title="isFullscreen ? '退出全屏' : '全屏'"
          :aria-label="isFullscreen ? '退出全屏' : '全屏'"
          :disabled="!fullscreenSupported"
          @click="toggleFullscreen"
        >⛶</button>
        <button
          type="button"
          data-testid="dag-toolbar-export"
          title="导出图片"
          aria-label="导出图片"
          :disabled="!hasGraphData"
          @click="exportDagImage"
        >📷</button>
      </Panel>
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
          @keydown="onNodeKeydown($event, data.task)"
        >
          <Handle type="target" :position="Position.Left" :connectable="false" />
          <span class="dag-task-node__icon" aria-hidden="true">{{ TASK_TYPE_ICON[data.task.type ?? ''] }}</span>
          <span class="dag-task-node__copy">
            <span class="dag-task-node__name" :title="data.task.name">{{ data.task.name }}</span>
            <span class="dag-task-node__agent" :title="data.task.assigned_agent">{{ data.task.assigned_agent || '未分配 Agent' }}</span>
          </span>
          <span class="dag-task-node__status" :title="data.task.status">
            <span class="dag-task-node__status-dot" aria-hidden="true" />
            <span class="dag-task-node__status-text">{{ taskStatusMeta(data.task.status).icon }} {{ taskStatusMeta(data.task.status).label }}</span>
          </span>
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

.dag-flow:fullscreen { width: 100vw; height: 100vh; border-radius: 0; }

.dag-flow__canvas { background: transparent; }

.dag-toolbar {
  display: flex;
  gap: 0.25rem;
  padding: 0.25rem;
  border: 1px solid oklch(0.25 0.01 264);
  border-radius: 0.4rem;
  background: oklch(0.145 0.01 264);
  box-shadow: 0 8px 24px rgb(0 0 0 / 0.24);
}

.dag-toolbar button {
  display: flex;
  width: 1.75rem;
  height: 1.75rem;
  align-items: center;
  justify-content: center;
  border-radius: 0.3rem;
  font-size: 0.8rem;
  color: oklch(0.78 0.005 264);
  cursor: pointer;
}

.dag-toolbar button:hover:not(:disabled) { background: oklch(0.22 0.01 264); }
.dag-toolbar button:disabled { cursor: not-allowed; opacity: 0.4; }

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
.dag-task-node__status { display: flex; flex: 0 0 auto; align-items: center; gap: 0.25rem; max-width: 5.5rem; }
.dag-task-node__status-dot { width: 0.5rem; height: 0.5rem; flex: 0 0 auto; border-radius: 9999px; background: var(--task-status-color); }
.dag-task-node__status-text {
  overflow: hidden;
  max-width: 4.5rem;
  color: var(--task-status-color);
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.625rem;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.dag-task-node__error { position: absolute; top: 0.35rem; right: 0.55rem; color: #f87171; font-size: 0.75rem; }

:deep(.vue-flow__handle) { width: 7px; height: 7px; border: 1px solid oklch(0.22 0.01 264); background: var(--task-status-color); }
:deep(.vue-flow__controls), :deep(.vue-flow__minimap) { border: 1px solid oklch(0.25 0.01 264); border-radius: 0.4rem; background: oklch(0.145 0.01 264); box-shadow: 0 8px 24px rgb(0 0 0 / 0.24); }
:deep(.vue-flow__controls-button) { border-color: oklch(0.25 0.01 264); background: oklch(0.16 0.01 264); fill: oklch(0.78 0.005 264); }
:deep(.vue-flow__controls-button:hover) { background: oklch(0.22 0.01 264); }
</style>
