<script setup lang="ts">
export interface ChangeSetImpact {
  changeset_id: string
  created_by: string
  created_at: string
  type: 'instance' | 'template'
  direct_modified: string[]
  indirect_affected: string[]
  must_rerun: string[]
  reusable: string[]
  artifact_reuse_reason: string
}
defineProps<{ changeset: ChangeSetImpact | null }>()
const emit = defineEmits<{ (e: 'confirm'): void; (e: 'cancel'): void }>()
</script>

<template>
  <div v-if="changeset" data-testid="changeset-panel" class="rounded-lg border border-border bg-card p-4">
    <h4 class="text-sm font-medium">ChangeSet {{ changeset.changeset_id }}</h4>
    <p class="mt-1 text-xs text-muted-foreground">{{ changeset.created_by }} · {{ changeset.created_at }} · {{ changeset.type }}</p>
    <div class="mt-3 space-y-1.5 text-xs">
      <p><span class="text-red-300">直接修改:</span> {{ changeset.direct_modified.join(', ') }}</p>
      <p><span class="text-orange-300">间接受影响:</span> {{ changeset.indirect_affected.join(', ') }}</p>
      <p><span class="text-yellow-300">需重跑:</span> {{ changeset.must_rerun.join(', ') }}</p>
      <p><span class="text-green-300">可复用:</span> {{ changeset.reusable.join(', ') }}</p>
      <p v-if="changeset.artifact_reuse_reason" class="text-muted-foreground">{{ changeset.artifact_reuse_reason }}</p>
    </div>
    <div class="mt-4 flex gap-2">
      <button data-testid="changeset-confirm" class="rounded bg-orange-500 px-3 py-1.5 text-xs text-white"
        @click="emit('confirm')">确认执行</button>
      <button data-testid="changeset-cancel" class="rounded border border-border px-3 py-1.5 text-xs"
        @click="emit('cancel')">取消</button>
    </div>
  </div>
</template>
