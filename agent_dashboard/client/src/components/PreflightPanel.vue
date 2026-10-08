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
defineProps<{ result: PreflightResult | null; loading?: boolean }>()
const emit = defineEmits<{ (e: 'rerun'): void }>()
</script>

<template>
  <div data-testid="preflight-panel" class="rounded-lg border border-border bg-card p-4">
    <div class="flex items-center justify-between">
      <h4 class="text-sm font-medium">能力预检</h4>
      <button v-if="!loading" data-testid="preflight-rerun"
        class="rounded border border-border px-2 py-1 text-xs hover:bg-muted"
        @click="emit('rerun')">重新预检</button>
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
          <div>
            <span class="font-medium">{{ check.check_id }}</span>
            <span class="ml-1 text-muted-foreground">{{ check.detail }}</span>
            <div v-if="check.reason" class="mt-0.5 text-orange-300">{{ check.reason }}</div>
            <div v-if="check.remediation" class="text-muted-foreground">{{ check.remediation }}</div>
          </div>
        </li>
      </ul>
      <div v-if="result.manifest_id" class="mt-2 text-xs text-muted-foreground">
        Manifest: {{ result.manifest_id }}
      </div>
    </template>
  </div>
</template>
