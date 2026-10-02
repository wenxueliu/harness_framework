<script setup lang="ts">
export interface ArtifactItem {
  artifact_id: string
  type: string
  path: string
  size_bytes: number
  retention_until: string
  status: 'ACTIVE' | 'EXPIRED' | 'PURGED'
}
defineProps<{ artifacts: ArtifactItem[] }>()
</script>

<template>
  <div data-testid="artifact-list" class="rounded-lg border border-border bg-card p-4">
    <h4 class="text-sm font-medium">产物</h4>
    <div v-if="!artifacts.length" class="mt-2 text-xs text-muted-foreground">暂无产物</div>
    <ul v-else class="mt-2 space-y-1.5">
      <li v-for="artifact in artifacts" :key="artifact.artifact_id" class="flex items-center justify-between text-xs">
        <div class="flex items-center gap-2">
          <span>{{ artifact.type === 'log' ? '📋' : artifact.type === 'file' ? '📄' : '📎' }}</span>
          <span :class="artifact.status !== 'ACTIVE' ? 'text-muted-foreground line-through' : ''">{{ artifact.path }}</span>
        </div>
        <div class="flex items-center gap-2">
          <span v-if="artifact.status === 'ACTIVE'" class="text-muted-foreground">保留至 {{ artifact.retention_until }}</span>
          <span v-else-if="artifact.status === 'EXPIRED'" class="text-red-300">已清理 ({{ artifact.retention_until }})</span>
          <span v-else class="text-muted-foreground">已清除</span>
        </div>
      </li>
    </ul>
  </div>
</template>
