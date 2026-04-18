<script setup lang="ts">
import { computed } from 'vue'
import type { Task } from '@/lib/mockData'
import { TASK_TYPE_ICON } from '@/lib/mockData'

const props = defineProps<{
  tasks: Record<string, Task>
  onTaskClick?: (task: Task) => void
}>()

interface NodePosition {
  x: number
  y: number
  task: Task
}

const NODE_W = 160
const NODE_H = 64
const COL_GAP = 80
const ROW_GAP = 24
const PAD_X = 24
const PAD_Y = 24

// Topological sort to determine column (level) for each node
function computeLevels(tasks: Record<string, Task>): Record<string, number> {
  const levels: Record<string, number> = {}
  const visited = new Set<string>()

  function dfs(id: string): number {
    if (visited.has(id)) return levels[id] ?? 0
    visited.add(id)
    const task = tasks[id]
    if (!task || task.depends_on.length === 0) {
      levels[id] = 0
      return 0
    }
    const maxDep = Math.max(...task.depends_on.map((dep) => dfs(dep)))
    levels[id] = maxDep + 1
    return levels[id]
  }

  Object.keys(tasks).forEach((id) => dfs(id))
  return levels
}

const { positions, svgWidth, svgHeight } = computed(() => {
  const levels = computeLevels(props.tasks)
  const maxLevel = Math.max(...Object.values(levels), 0)

  const byLevel: Record<number, string[]> = {}
  Object.entries(levels).forEach(([id, lvl]) => {
    if (!byLevel[lvl]) byLevel[lvl] = []
    byLevel[lvl].push(id)
  })

  const typeOrder = ['design', 'backend', 'frontend', 'test']
  Object.values(byLevel).forEach((ids) => {
    ids.sort((a, b) => {
      const ta = typeOrder.indexOf(props.tasks[a]?.type ?? '')
      const tb = typeOrder.indexOf(props.tasks[b]?.type ?? '')
      return ta - tb
    })
  })

  const positions: Record<string, NodePosition> = {}
  for (let lvl = 0; lvl <= maxLevel; lvl++) {
    const ids = byLevel[lvl] ?? []
    ids.forEach((id, i) => {
      positions[id] = {
        x: PAD_X + lvl * (NODE_W + COL_GAP),
        y: PAD_Y + i * (NODE_H + ROW_GAP),
        task: props.tasks[id],
      }
    })
  }

  const maxRows = Math.max(...Object.values(byLevel).map((ids) => ids.length), 0)
  const w = PAD_X * 2 + (maxLevel + 1) * (NODE_W + COL_GAP) - COL_GAP
  const h = PAD_Y * 2 + maxRows * (NODE_H + ROW_GAP) - ROW_GAP
  return { positions, svgWidth: w, svgHeight: h }
}).value

const edges = computed(() => {
  const result: { from: string; to: string }[] = []
  Object.entries(props.tasks).forEach(([id, task]) => {
    task.depends_on.forEach((dep) => {
      if (positions[dep] && positions[id]) {
        result.push({ from: dep, to: id })
      }
    })
  })
  return result
})

function getNodeBorderColor(status: Task['status']): string {
  const map: Record<string, string> = {
    DONE: '#34d399',
    IN_PROGRESS: '#60a5fa',
    PENDING: '#fbbf24',
    FAILED: '#f87171',
    BLOCKED: '#f87171',
  }
  return map[status] ?? '#4b5563'
}
</script>

<template>
  <div class="overflow-x-auto overflow-y-auto">
    <svg
      :width="svgWidth"
      :height="svgHeight"
      :viewBox="`0 0 ${svgWidth} ${svgHeight}`"
      class="block"
    >
      <!-- Edges -->
      <path
        v-for="({ from, to }) in edges"
        :key="`${from}-${to}`"
        :d="`M ${positions[from].x + NODE_W} ${positions[from].y + NODE_H / 2} C ${
          (positions[from].x + NODE_W + positions[to].x) / 2
        } ${positions[from].y + NODE_H / 2}, ${
          (positions[from].x + NODE_W + positions[to].x) / 2
        } ${positions[to].y + NODE_H / 2}, ${positions[to].x} ${positions[to].y + NODE_H / 2}`"
        fill="none"
        :stroke="positions[from].task.status === 'DONE' ? '#34d399' : '#6b7280'"
        :stroke-opacity="positions[from].task.status === 'DONE' ? 0.8 : 0.4"
        stroke-width="1.5"
      />

      <!-- Nodes -->
      <g
        v-for="(pos, id) in positions"
        :key="id"
        :transform="`translate(${pos.x}, ${pos.y})`"
        class="cursor-pointer"
        @click="onTaskClick?.(pos.task)"
      >
        <!-- Node background -->
        <rect
          :width="NODE_W"
          :height="NODE_H"
          rx="6"
          fill="oklch(0.16 0.01 264)"
          :stroke="getNodeBorderColor(pos.task.status)"
          :stroke-width="pos.task.status === 'IN_PROGRESS' ? 1.5 : 1"
          :stroke-opacity="pos.task.status === 'IN_PROGRESS' ? 1 : 0.6"
        />
        <!-- Active glow -->
        <rect
          v-if="pos.task.status === 'IN_PROGRESS'"
          :width="NODE_W"
          :height="NODE_H"
          rx="6"
          fill="none"
          :stroke="getNodeBorderColor(pos.task.status)"
          stroke-width="4"
          stroke-opacity="0.15"
        />
        <!-- Type icon -->
        <text
          x="14"
          :y="NODE_H / 2 + 1"
          dominant-baseline="middle"
          font-size="14"
          :fill="getNodeBorderColor(pos.task.status)"
          opacity="0.9"
        >
          {{ TASK_TYPE_ICON[pos.task.type ?? ''] ?? '◇' }}
        </text>
        <!-- Task name -->
        <text
          x="32"
          :y="NODE_H / 2 - 8"
          dominant-baseline="middle"
          font-size="11"
          font-family="'Space Grotesk', sans-serif"
          font-weight="500"
          fill="oklch(0.88 0.005 264)"
        >
          {{ pos.task.name.length > 12 ? pos.task.name.slice(0, 12) + '…' : pos.task.name }}
        </text>
        <!-- Agent ID -->
        <text
          x="32"
          :y="NODE_H / 2 + 8"
          dominant-baseline="middle"
          font-size="9"
          font-family="'JetBrains Mono', monospace"
          fill="oklch(0.5 0.01 264)"
        >
          {{ pos.task.assigned_agent.length > 20 ? pos.task.assigned_agent.slice(0, 20) + '…' : pos.task.assigned_agent }}
        </text>
        <!-- Status dot -->
        <circle
          :cx="NODE_W - 12"
          :cy="NODE_H / 2"
          r="4"
          :fill="getNodeBorderColor(pos.task.status)"
          opacity="0.9"
        />
        <!-- Failed indicator -->
        <text
          v-if="pos.task.status === 'FAILED' || pos.task.status === 'BLOCKED'"
          :x="NODE_W - 12"
          :y="NODE_H / 2 - 12"
          text-anchor="middle"
          dominant-baseline="middle"
          font-size="10"
          fill="#f87171"
        >
          ✕
        </text>
      </g>
    </svg>
  </div>
</template>
