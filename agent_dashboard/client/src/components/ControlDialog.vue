<script setup lang="ts">
import type { ControlSignal } from '@/lib/constants'

const props = defineProps<{
  open: boolean
  signal: ControlSignal | null
  reqId: string
  onConfirm: () => void
  onCancel: () => void
}>()

const SIGNAL_CONFIG: Record<ControlSignal, { title: string; description: string; confirmLabel: string; confirmClass: string }> = {
  PAUSE: {
    title: '暂停需求执行',
    description: '所有 Agent 将在完成当前原子操作后暂停，任务状态保持不变。可通过 RESUME 指令恢复执行。',
    confirmLabel: '确认暂停',
    confirmClass: 'bg-amber-500 hover:bg-amber-600 text-white',
  },
  RESUME: {
    title: '恢复需求执行',
    description: '已暂停的 Agent 将重新开始轮询并继续执行剩余任务。',
    confirmLabel: '确认恢复',
    confirmClass: 'bg-blue-500 hover:bg-blue-600 text-white',
  },
  ABORT: {
    title: '中止并回退需求',
    description: '⚠️ 危险操作：所有 Agent 将执行 cleanup，删除已部署的 K8s 资源并注销 Consul 服务。数据库变更将执行 Migration Down 回滚。此操作不可撤销。',
    confirmLabel: '确认中止',
    confirmClass: 'bg-red-600 hover:bg-red-700 text-white',
  },
  RETRY: {
    title: '重试失败任务',
    description: '将所有 FAILED 状态的任务重置为 PENDING，Agent 将重新尝试执行。',
    confirmLabel: '确认重试',
    confirmClass: 'bg-emerald-600 hover:bg-emerald-700 text-white',
  },
}
</script>

<template>
  <dialog
    :open="open"
    class="fixed inset-0 z-50 bg-black/60"
    @click.self="onCancel"
  >
    <div
      class="fixed inset-0 flex items-center justify-center p-4"
      @click.self="onCancel"
    >
      <div
        v-if="signal"
        class="bg-[oklch(0.16_0.01_264)] border border-[oklch(0.28_0.01_264)] text-foreground rounded-xl max-w-md w-full p-6 space-y-4"
        @click.stop
      >
        <!-- Title -->
        <div class="space-y-1">
          <h2 class="font-display text-base font-semibold text-foreground">
            {{ SIGNAL_CONFIG[signal].title }}
          </h2>
          <p class="font-mono text-xs text-blue-400 block">{{ reqId }}</p>
          <p class="text-sm text-muted-foreground leading-relaxed">
            {{ SIGNAL_CONFIG[signal].description }}
          </p>
        </div>

        <!-- Actions -->
        <div class="flex items-center gap-3 pt-2">
          <button
            class="flex-1 px-4 py-2 rounded-lg border border-border text-muted-foreground hover:bg-accent hover:text-foreground transition-colors text-sm"
            @click="onCancel"
          >
            取消
          </button>
          <button
            class="flex-1 px-4 py-2 rounded-lg text-white text-sm font-medium transition-colors"
            :class="SIGNAL_CONFIG[signal].confirmClass"
            @click="onConfirm"
          >
            {{ SIGNAL_CONFIG[signal].confirmLabel }}
          </button>
        </div>
      </div>
    </div>
  </dialog>
</template>
