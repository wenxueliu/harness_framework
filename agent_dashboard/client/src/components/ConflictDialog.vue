<script setup lang="ts">
defineProps<{
  visible: boolean
  currentRevision: number
  serverRevision: number
}>()
const emit = defineEmits<{
  (e: 'discard'): void
  (e: 'download'): void
  (e: 'cancel'): void
}>()
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" data-testid="conflict-dialog"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div class="w-full max-w-md rounded-lg border border-yellow-400/40 bg-card p-6 shadow-xl">
        <div class="flex items-center gap-2">
          <span class="text-lg">⚠️</span>
          <h3 class="text-base font-semibold">草稿已被他人修改</h3>
        </div>
        <p class="mt-2 text-sm text-muted-foreground">
          你的修改基于 revision #{{ currentRevision }}，当前为 revision #{{ serverRevision }}。
        </p>
        <div class="mt-6 flex justify-end gap-2">
          <button data-testid="conflict-cancel"
            class="rounded border border-border px-3 py-1.5 text-xs hover:bg-muted"
            @click="emit('cancel')">取消</button>
          <button data-testid="conflict-download"
            class="rounded border border-blue-400/40 px-3 py-1.5 text-xs text-blue-300 hover:bg-blue-400/10"
            @click="emit('download')">下载我的版本</button>
          <button data-testid="conflict-discard"
            class="rounded bg-red-500 px-3 py-1.5 text-xs text-white hover:bg-red-600"
            @click="emit('discard')">放弃修改并刷新</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
