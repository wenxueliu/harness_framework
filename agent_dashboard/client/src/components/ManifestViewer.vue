<script setup lang="ts">
export interface ManifestDiffChange { field: string; old: unknown; new: unknown }
export interface ManifestDiff { changes: ManifestDiffChange[]; summary: string }
defineProps<{ manifest: Record<string, unknown> | null; diff?: ManifestDiff | null }>()
</script>

<template>
  <div data-testid="manifest-viewer" class="rounded-lg border border-border bg-card p-4">
    <h4 class="text-sm font-medium">Execution Manifest</h4>
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
