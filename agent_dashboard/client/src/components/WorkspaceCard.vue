<script setup lang="ts">
defineProps<{
  name: string
  isolationLevel: string
  status: 'ACTIVE' | 'RETAINED' | 'EXPIRED'
  createdAt: string
  retentionDays: number
  isTerminal: boolean
  canManage?: boolean
}>()
const emit = defineEmits<{ (e: 'extend'): void }>()
</script>

<template>
  <div data-testid="workspace-card" class="rounded-lg border border-border bg-card p-3">
    <div class="flex items-center justify-between">
      <span class="text-xs font-medium">📁 Run Workspace</span>
      <span class="text-xs px-1.5 py-0.5 rounded-full font-medium"
        :class="{
          'bg-green-400/10 text-green-400': status === 'ACTIVE',
          'bg-yellow-400/10 text-yellow-400': status === 'RETAINED',
          'bg-red-400/10 text-red-400': status === 'EXPIRED',
        }">{{ status }}</span>
    </div>
    <dl class="mt-2 space-y-0.5 text-xs">
      <div class="flex justify-between"><dt class="text-muted-foreground">名称</dt><dd>{{ name }}</dd></div>
      <div class="flex justify-between"><dt class="text-muted-foreground">隔离等级</dt><dd class="font-mono">{{ isolationLevel }}</dd></div>
      <div class="flex justify-between"><dt class="text-muted-foreground">创建时间</dt><dd>{{ createdAt }}</dd></div>
      <div class="flex justify-between"><dt class="text-muted-foreground">保留策略</dt><dd>{{ retentionDays }} 天（终态后自动清理）</dd></div>
    </dl>
    <div v-if="isTerminal && status === 'ACTIVE'" data-testid="workspace-cleanup-warning"
      class="mt-2 rounded bg-yellow-400/10 px-2 py-1.5 text-xs text-yellow-300">
      ⚠ 实例已进入终态，Workspace 将于 {{ retentionDays }} 天后自动清理。
      <button v-if="canManage" class="ml-1 underline hover:no-underline" @click="emit('extend')">延长保留</button>
    </div>
  </div>
</template>
