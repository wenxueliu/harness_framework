<script setup lang="ts">
export interface PreflightCheck {
  check_id: string
  status: 'PASS' | 'FAIL'
  detail: string
  reason?: string
  remediation?: string
}
export interface PreflightResult {
  instance_id: string
  status: 'IDLE' | 'PASSED' | 'BLOCKED'
  checks: PreflightCheck[]
  manifest_id?: string | null
  checked_at?: string
  attempt_id?: string
}
defineProps<{
  result: PreflightResult | null
  loading?: boolean
  retryingCheckId?: string
  canSkip?: boolean
  skipping?: boolean
}>()
const emit = defineEmits<{
  (e: 'rerun'): void
  (e: 'retry-check', checkId: string): void
  (e: 'skip-start'): void
}>()
</script>

<template>
  <div data-testid="preflight-panel" class="rounded-lg border border-border bg-card p-4">
    <div class="flex items-center justify-between">
      <h4 class="text-sm font-medium">能力预检</h4>
      <button v-if="!loading" data-testid="preflight-rerun"
        class="rounded border border-border px-2 py-1 text-xs hover:bg-muted"
        @click="emit('rerun')">重新预检全部</button>
    </div>
    <div v-if="loading" class="mt-3 text-xs text-muted-foreground animate-pulse">正在检查能力…</div>
    <div v-else-if="!result" class="mt-3 text-xs text-muted-foreground">尚未执行预检</div>
    <template v-else>
      <div class="mt-2 text-xs font-medium"
        :class="result.status === 'PASSED' ? 'text-green-400' : 'text-orange-400'">
        {{ result.status === 'PASSED' ? '✅ 全部通过' : `⚠ ${result.checks.filter(c => c.status === 'FAIL').length} 项阻塞` }}
      </div>
      <ul class="mt-2 space-y-1.5" data-testid="preflight-checks">
        <li v-for="check in result.checks" :key="check.check_id" class="flex items-start gap-2 text-xs">
          <span :class="check.status === 'PASS' ? 'text-green-400' : 'text-red-400'">{{ check.status === 'PASS' ? '✅' : '❌' }}</span>
          <div class="min-w-0 flex-1">
            <span class="font-medium">{{ check.check_id }}</span>
            <span class="ml-1 text-muted-foreground">{{ check.detail }}</span>
            <div v-if="check.reason" class="mt-0.5 text-orange-300">{{ check.reason }}</div>
            <div v-if="check.remediation" class="text-muted-foreground">{{ check.remediation }}</div>
            <button v-if="check.status === 'FAIL' && !loading" data-testid="preflight-retry-check"
              class="mt-1 rounded border border-border px-2 py-0.5 text-[11px] text-blue-300 hover:bg-accent disabled:cursor-not-allowed disabled:opacity-40"
              :disabled="retryingCheckId === check.check_id"
              @click="emit('retry-check', check.check_id)">
              {{ retryingCheckId === check.check_id ? '正在重试…' : '重试检查' }}
            </button>
          </div>
        </li>
      </ul>
      <div v-if="result.manifest_id" class="mt-2 text-xs text-muted-foreground">
        Manifest: {{ result.manifest_id }}
      </div>
      <div v-if="canSkip" class="mt-3 flex items-center gap-2 border-t border-border pt-3">
        <button data-testid="preflight-skip-start"
          class="rounded bg-amber-500 px-3 py-1.5 text-xs text-white disabled:cursor-not-allowed disabled:opacity-40"
          :disabled="skipping" @click="emit('skip-start')">跳过并启动</button>
        <span class="text-[11px] text-muted-foreground">能力预检存在阻塞项，跳过启动会记录审计。</span>
      </div>
    </template>
  </div>
</template>
