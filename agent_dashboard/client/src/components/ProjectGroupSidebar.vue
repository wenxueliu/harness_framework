<script setup lang="ts">
import { computed } from 'vue'
import { Archive, FolderKanban, FolderPlus } from 'lucide-vue-next'
import { RouterLink } from 'vue-router'
import { useProjectGroupStore } from '@/stores/projectGroups'
import { useCapabilityStore } from '@/stores/capabilities'

const emit = defineEmits<{ select: [groupId: string] }>()
const store = useProjectGroupStore()
const capabilities = useCapabilityStore()
const active = computed(() => store.groups.filter((group) => group.status === 'ACTIVE'))
</script>

<template>
  <aside class="hidden lg:flex w-52 shrink-0 flex-col border-r border-border bg-sidebar">
    <div class="flex items-center justify-between border-b border-border px-3 py-2.5">
      <span class="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">项目组</span>
      <RouterLink
        v-if="capabilities.permitted('group:manage')"
        to="/settings"
        aria-label="新建项目"
        title="新建项目"
        class="rounded p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
      >
        <FolderPlus :size="13" />
      </RouterLink>
    </div>
    <div class="flex-1 overflow-y-auto p-1.5">
      <button v-for="group in active" :key="group.group_id" class="mb-1 flex w-full items-center gap-2 rounded px-2 py-2 text-left text-xs transition-colors"
        :class="store.selectedId === group.group_id ? 'bg-blue-500/10 text-blue-300' : 'text-muted-foreground hover:bg-accent hover:text-foreground'" @click="emit('select', group.group_id)">
        <FolderKanban :size="14" /><span class="truncate">{{ group.name }}</span>
      </button>
      <p v-if="store.error" class="p-2 text-xs text-red-300">{{ store.error }}</p>
    </div>
    <div class="border-t border-border p-3 text-[10px] text-muted-foreground">
      <div class="flex items-center gap-1"><Archive :size="11" />Workspace</div>
      <div class="mt-1 truncate font-mono text-blue-300">{{ store.workspaces[0]?.name || '未配置' }}</div>
    </div>
  </aside>
</template>
