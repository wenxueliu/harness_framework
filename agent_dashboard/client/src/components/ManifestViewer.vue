<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

export interface ManifestDiffChange { field: string; old: unknown; new: unknown }
export interface ManifestDiff { changes: ManifestDiffChange[]; summary: string }
const props = defineProps<{
  manifest: Record<string, unknown> | null
  diff?: ManifestDiff | null
  instanceId?: string
}>()

const router = useRouter()
const copied = ref(false)

/** Manifest 摘要关键字段（运行时、Skill、MCP、Workspace、策略版本和解析来源） */
const SUMMARY_FIELDS = [
  'manifest_id', 'resolved_at', 'resolved_by', 'resolved_from',
  'agent_runtime', 'agent_runtime_id', 'skill_bundle', 'skills', 'mcp_grants', 'mcp',
  'workspace', 'policy_version', 'execution_profile', 'execution_profile_id',
] as const

const summary = computed<Record<string, unknown>>(() => {
  if (!props.manifest) return {}
  const result: Record<string, unknown> = {}
  for (const field of SUMMARY_FIELDS) {
    if (props.manifest[field] !== undefined) result[field] = props.manifest[field]
  }
  // 没命中任何已知字段时退回完整 Manifest 关键信息，保证摘要非空
  if (!Object.keys(result).length && props.manifest.manifest_id) {
    result.manifest_id = props.manifest.manifest_id
  }
  return result
})

const summaryText = computed(() => JSON.stringify(summary.value, null, 2))

async function copySummary() {
  try {
    await navigator.clipboard.writeText(summaryText.value)
    copied.value = true
    window.setTimeout(() => { copied.value = false }, 1500)
  } catch { /* 剪贴板不可用时静默降级 */ }
}

function jumpToAudit() {
  if (!props.instanceId) return
  void router.push(`/instances/${encodeURIComponent(props.instanceId)}?tab=events`)
}
</script>

<template>
  <div data-testid="manifest-viewer" class="rounded-lg border border-border bg-card p-4">
    <div class="flex items-center justify-between">
      <h4 class="text-sm font-medium">Execution Manifest</h4>
      <div class="flex items-center gap-2" v-if="manifest">
        <button data-testid="manifest-copy-summary"
          class="rounded border border-border px-2 py-1 text-[11px] hover:bg-muted"
          @click="copySummary">{{ copied ? '已复制' : '复制摘要' }}</button>
        <button v-if="instanceId" data-testid="manifest-audit-jump"
          class="rounded border border-border px-2 py-1 text-[11px] text-blue-300 hover:bg-accent"
          @click="jumpToAudit">审计跳转</button>
      </div>
    </div>
    <div v-if="!manifest" class="mt-3 text-xs text-muted-foreground">无 Manifest</div>
    <template v-else>
      <div class="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
        <span>{{ manifest.manifest_id }}</span>
        <span>· resolved {{ manifest.resolved_at }}</span>
        <span>· {{ manifest.resolved_by }}</span>
      </div>
      <pre class="mt-2 max-h-64 overflow-auto rounded bg-muted/50 p-2 text-[11px] leading-relaxed">{{ JSON.stringify(manifest, null, 2) }}</pre>
      <div v-if="diff?.changes?.length" class="mt-2 text-xs">
        <p class="font-medium text-orange-400">与前次 Manifest 差异（{{ diff.changes.length }} 项）：</p>
        <ul class="mt-1 space-y-0.5">
          <li v-for="(change, i) in diff.changes" :key="i" class="text-muted-foreground">
            {{ change.field }}: {{ JSON.stringify(change.old) }} → {{ JSON.stringify(change.new) }}
          </li>
        </ul>
      </div>
    </template>
  </div>
</template>
