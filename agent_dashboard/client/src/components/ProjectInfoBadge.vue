<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { FolderKanban } from 'lucide-vue-next'
import { useProjectGroupStore } from '@/stores/projectGroups'

const props = defineProps<{ groupId: string }>()

const router = useRouter()
const projectGroupStore = useProjectGroupStore()

const group = computed(() =>
  projectGroupStore.groups.find((item) => item.group_id === props.groupId)
)
const displayName = computed(() => group.value?.name || props.groupId)

function openSettings() {
  void router.push({ path: '/settings', query: { group: props.groupId } })
}
</script>

<template>
  <button
    type="button"
    data-testid="project-info-badge"
    class="flex items-center gap-1.5 rounded border border-border bg-card px-2 py-1 text-xs text-muted-foreground transition-colors hover:border-blue-400/60 hover:text-foreground"
    :title="`项目：${displayName}（点击管理）`"
    @click="openSettings"
  >
    <FolderKanban :size="12" />
    <span class="max-w-32 truncate font-medium text-foreground">{{ displayName }}</span>
    <span
      v-if="group?.status"
      class="rounded px-1.5 py-0.5 text-[10px]"
      :class="group.status === 'ACTIVE'
        ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
        : 'bg-muted text-muted-foreground border border-border'"
    >{{ group.status === 'ACTIVE' ? '活跃' : '已归档' }}</span>
  </button>
</template>
