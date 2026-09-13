<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { ProjectWorkspace } from '@/api/types'

const props = defineProps<{ open: boolean; reqId: string; workspaces: ProjectWorkspace[]; busy?: boolean; error?: string }>()
const emit = defineEmits<{
  close: []
  create: [input: { workspaceId: string; strategy: 'ORIGINAL' | 'GIT_WORKTREE' | 'CONTROLLED_COPY'; gitRef: string; acceptDirty: boolean }]
}>()
const workspaceId = ref('')
const strategy = ref<'ORIGINAL' | 'GIT_WORKTREE' | 'CONTROLLED_COPY'>('GIT_WORKTREE')
const gitRef = ref('')
const acceptDirty = ref(false)
const selected = computed(() => props.workspaces.find((item) => item.workspace_id === workspaceId.value))
watch(() => props.open, (open) => {
  if (open && !workspaceId.value) workspaceId.value = props.workspaces[0]?.workspace_id || ''
})
</script>

<template>
  <dialog :open="open" class="fixed inset-0 z-50 bg-black/60" @click.self="emit('close')">
    <div class="fixed inset-0 grid place-items-center p-4" @click.self="emit('close')">
      <form class="w-full max-w-lg space-y-4 rounded-xl border border-border bg-card p-6 text-foreground" @submit.prevent="emit('create', { workspaceId, strategy, gitRef, acceptDirty })">
        <div><h2 class="font-display text-base font-semibold">启动 Workspace-first Run</h2><p class="mt-1 font-mono text-xs text-blue-300">{{ reqId }}</p></div>
        <label class="block text-xs text-muted-foreground">Project Workspace<select v-model="workspaceId" required class="mt-1 w-full rounded border border-border bg-background p-2 text-sm text-foreground"><option disabled value="">请选择工作区</option><option v-for="workspace in workspaces" :key="workspace.workspace_id" :value="workspace.workspace_id">{{ workspace.name }} · {{ workspace.access }}</option></select></label>
        <label class="block text-xs text-muted-foreground">隔离策略<select v-model="strategy" class="mt-1 w-full rounded border border-border bg-background p-2 text-sm text-foreground"><option value="GIT_WORKTREE">Git Worktree（推荐）</option><option value="CONTROLLED_COPY">受控副本</option><option value="ORIGINAL">原目录</option></select></label>
        <label v-if="strategy === 'GIT_WORKTREE'" class="block text-xs text-muted-foreground">Git ref（留空使用默认值）<input v-model="gitRef" class="mt-1 w-full rounded border border-border bg-background p-2 font-mono text-sm text-foreground" placeholder="main / feature branch" /></label>
        <label v-if="strategy === 'ORIGINAL'" class="flex items-start gap-2 text-xs text-muted-foreground"><input v-model="acceptDirty" type="checkbox" class="mt-0.5" />允许并记录原目录中已有的未提交修改</label>
        <p v-if="selected?.access === 'READ_ONLY'" class="rounded bg-amber-400/10 p-2 text-xs text-amber-200">此 Workspace 为只读，Attempt 不可编辑文件。</p>
        <p v-if="error" role="alert" class="rounded bg-red-400/10 p-2 text-xs text-red-200">{{ error }}</p>
        <div class="flex gap-2 pt-2"><button type="button" class="flex-1 rounded border border-border px-3 py-2 text-sm" @click="emit('close')">取消</button><button type="submit" class="flex-1 rounded bg-blue-500 px-3 py-2 text-sm text-white disabled:opacity-50" :disabled="busy || !workspaceId">{{ busy ? '准备工作区…' : '启动 Run' }}</button></div>
      </form>
    </div>
  </dialog>
</template>
