<script setup lang="ts">
defineProps<{
  visible: boolean
  changeSummary: string
}>()
const emit = defineEmits<{
  (e: 'save-draft'): void
  (e: 'publish-only'): void
  (e: 'publish-and-create'): void
  (e: 'cancel'): void
}>()
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" data-testid="publish-dialog"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div class="w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-xl">
        <h3 class="text-base font-semibold">发布确认</h3>
        <p v-if="changeSummary" class="mt-2 text-xs text-muted-foreground" data-testid="publish-change-summary">
          {{ changeSummary }}
        </p>
        <div class="mt-4 space-y-2">
          <button data-testid="publish-save-draft"
            class="w-full rounded border border-border px-3 py-2 text-sm hover:bg-muted"
            @click="emit('save-draft')">保存为草稿（不产生版本）</button>
          <button data-testid="publish-only"
            class="w-full rounded bg-blue-500 px-3 py-2 text-sm text-white hover:bg-blue-600"
            @click="emit('publish-only')">仅发布（生成不可变版本）</button>
          <button data-testid="publish-and-create"
            class="w-full rounded bg-green-600 px-3 py-2 text-sm text-white hover:bg-green-700"
            @click="emit('publish-and-create')">发布并创建实例</button>
        </div>
        <div class="mt-4 flex justify-end">
          <button data-testid="publish-cancel"
            class="rounded px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground"
            @click="emit('cancel')">取消</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
