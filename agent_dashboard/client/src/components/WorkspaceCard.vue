<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  name: string
  isolationLevel: string
  status: 'ACTIVE' | 'RETAINED' | 'EXPIRED'
  createdAt: string
  retentionDays: number
  isTerminal: boolean
  canManage?: boolean
  /** 保留策略到期时间（ISO 字符串）。数据不可得时不显示清理倒计时 */
  cleanupAt?: string | null
  /** Admin「延长保留」是否可执行（如缺少 expected_revision 时禁用） */
  extendDisabled?: boolean
  extendDisabledReason?: string
}>()
const emit = defineEmits<{ (e: 'extend'): void }>()

const CLEANUP_WARNING_WINDOW_HOURS = 24

const cleanupInfo = computed<{ hours: number; showWarning: boolean; overdue: boolean }>(() => {
  if (!props.cleanupAt) return { hours: 0, showWarning: false, overdue: false }
  const target = new Date(props.cleanupAt).getTime()
  if (Number.isNaN(target)) return { hours: 0, showWarning: false, overdue: false }
  const diffHours = (target - Date.now()) / 3_600_000
  return {
    hours: Math.max(0, Math.round(diffHours * 10) / 10),
    showWarning: diffHours <= CLEANUP_WARNING_WINDOW_HOURS,
    overdue: diffHours <= 0,
  }
})

const cleanupText = computed(() => {
  if (!cleanupInfo.value.showWarning) return ''
  if (cleanupInfo.value.overdue) return 'Workspace 保留期已到，即将自动清理。'
  const hours = cleanupInfo.value.hours
  const label = hours >= 1
    ? `${Math.round(hours * 10) / 10} 小时`
    : `${Math.max(1, Math.round(hours * 60))} 分钟`
  return `将于 ${label}后自动清理。`
})
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
      <div v-if="cleanupAt" class="flex justify-between"><dt class="text-muted-foreground">清理时间</dt><dd class="font-mono">{{ cleanupAt }}</dd></div>
    </dl>
    <div v-if="cleanupInfo.showWarning" data-testid="workspace-cleanup-countdown"
      class="mt-2 rounded bg-yellow-400/10 px-2 py-1.5 text-xs text-yellow-300">
      ⚠ {{ cleanupText }}如需保留请联系项目组 Admin。
      <button v-if="canManage" data-testid="workspace-extend-retention"
        class="ml-1 underline hover:no-underline disabled:cursor-not-allowed disabled:opacity-40"
        :disabled="extendDisabled"
        :title="extendDisabled ? (extendDisabledReason || '当前不可延长保留') : '延长保留'"
        @click="emit('extend')">延长保留</button>
      <span v-else-if="extendDisabled && extendDisabledReason" class="ml-1 text-[10px] text-muted-foreground">{{ extendDisabledReason }}</span>
    </div>
    <div v-if="isTerminal && status === 'ACTIVE'" data-testid="workspace-cleanup-warning"
      class="mt-2 rounded bg-yellow-400/10 px-2 py-1.5 text-xs text-yellow-300">
      ⚠ 实例已进入终态，Workspace 将于 {{ retentionDays }} 天后自动清理。
      <button v-if="canManage" class="ml-1 underline hover:no-underline" @click="emit('extend')">延长保留</button>
    </div>
  </div>
</template>
