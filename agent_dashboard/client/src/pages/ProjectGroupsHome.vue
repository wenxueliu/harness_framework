<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { Activity, FolderPlus, FolderKanban, Unplug } from 'lucide-vue-next'
import { useCapabilityStore } from '@/stores/capabilities'
import { useProjectGroupStore } from '@/stores/projectGroups'

const router = useRouter()
const capabilityStore = useCapabilityStore()
const projectGroupStore = useProjectGroupStore()

const loading = ref(true)
const error = ref('')
const canManage = computed(() => capabilityStore.permitted('group:manage'))

const groups = computed(() => projectGroupStore.groups.filter((group) => !group.virtual))

function workflowCount(groupId: string): number {
  return projectGroupStore.workflowCounts[groupId] ?? 0
}

function createdOf(groupId: string): string {
  const created = projectGroupStore.groups.find((group) => group.group_id === groupId)?.created_at
  return created ? created.slice(0, 10) : '—'
}

function openGroup(groupId: string) {
  void router.push({ name: 'group-dashboard', params: { groupId } })
}

onMounted(async () => {
  try {
    await capabilityStore.load()
    if (!capabilityStore.enabled('project_groups')) {
      // 项目组能力未启用：首页回落到全局看板（保持旧行为）
      await router.replace({ name: 'board-dashboard' })
      return
    }
    await projectGroupStore.loadOverview()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '项目列表加载失败'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="min-h-screen bg-background flex flex-col">
    <header class="h-12 border-b border-border flex items-center px-4 gap-3 flex-shrink-0 sticky top-0 z-30 bg-background/95 backdrop-blur-sm">
      <div class="flex items-center gap-2">
        <div class="w-5 h-5 rounded bg-blue-500/20 border border-blue-500/40 flex items-center justify-center">
          <Activity :size="11" class="text-blue-400" />
        </div>
        <span class="font-display font-semibold text-sm text-foreground">Agent Dashboard</span>
      </div>
      <div class="ml-auto flex items-center gap-2">
        <RouterLink
          to="/settings"
          class="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors px-2 py-1.5 rounded hover:bg-accent"
        >配置</RouterLink>
        <RouterLink
          v-if="canManage"
          to="/projects/new"
          aria-label="新建项目"
          class="flex items-center gap-1.5 text-xs bg-blue-500 hover:bg-blue-400 text-white transition-colors px-3 py-1.5 rounded-md"
        >
          <FolderPlus :size="12" /><span>新建项目</span>
        </RouterLink>
      </div>
    </header>

    <main class="flex-1 overflow-y-auto">
      <div class="mx-auto max-w-4xl p-4 sm:p-6 space-y-4">
        <h1 class="font-display font-semibold text-base text-foreground">项目</h1>

        <div v-if="loading" class="rounded border border-border p-10 text-center text-sm text-muted-foreground">加载中…</div>
        <div v-else-if="error" role="alert" class="rounded border border-red-400/30 bg-red-400/10 p-6 text-sm text-red-200">
          <div class="flex items-center gap-2 mb-2"><Unplug :size="14" />{{ error }}</div>
          <RouterLink to="/dashboard" class="text-xs text-blue-300 hover:underline">进入全局看板 →</RouterLink>
        </div>

        <template v-else>
          <div class="overflow-hidden rounded-lg border border-border">
            <table class="w-full text-left text-sm" data-testid="project-group-list">
              <thead class="border-b border-border bg-muted/40 text-xs text-muted-foreground">
                <tr>
                  <th class="px-4 py-2.5 font-medium">名称</th>
                  <th class="px-4 py-2.5 font-medium">状态</th>
                  <th class="px-4 py-2.5 font-medium">工作流</th>
                  <th class="px-4 py-2.5 font-medium">创建时间</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="group in groups"
                  :key="group.group_id"
                  data-testid="project-group-row"
                  class="cursor-pointer border-b border-border/60 transition hover:bg-accent/50 last:border-0"
                  @click="openGroup(group.group_id)"
                >
                  <td class="px-4 py-3">
                    <div class="flex items-center gap-2">
                      <FolderKanban :size="14" class="text-blue-400 flex-shrink-0" />
                      <div class="min-w-0">
                        <div class="font-medium text-foreground truncate">{{ group.name }}</div>
                        <div class="font-mono text-[10px] text-muted-foreground truncate">{{ group.group_id }}</div>
                      </div>
                    </div>
                  </td>
                  <td class="px-4 py-3">
                    <span
                      class="rounded px-2 py-0.5 text-[10px]"
                      :class="group.status === 'ACTIVE'
                        ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                        : 'bg-muted text-muted-foreground border border-border'"
                    >{{ group.status === 'ACTIVE' ? '活跃' : '已归档' }}</span>
                  </td>
                  <td class="px-4 py-3 font-mono text-xs text-muted-foreground" data-testid="project-group-workflow-count">
                    {{ workflowCount(group.group_id) }}
                  </td>
                  <td class="px-4 py-3 text-xs text-muted-foreground">{{ createdOf(group.group_id) }}</td>
                </tr>
                <tr
                  data-testid="project-group-unassigned"
                  class="cursor-pointer transition hover:bg-accent/50"
                  @click="router.push('/dashboard')"
                >
                  <td class="px-4 py-3">
                    <div class="flex items-center gap-2">
                      <FolderKanban :size="14" class="text-muted-foreground flex-shrink-0" />
                      <div class="min-w-0">
                        <div class="font-medium text-muted-foreground">未分配</div>
                        <div class="font-mono text-[10px] text-muted-foreground">unassigned</div>
                      </div>
                    </div>
                  </td>
                  <td class="px-4 py-3 text-xs text-muted-foreground">—</td>
                  <td class="px-4 py-3 font-mono text-xs text-muted-foreground">{{ workflowCount('unassigned') }}</td>
                  <td class="px-4 py-3 text-xs text-muted-foreground">—</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-if="!groups.length" class="text-sm text-muted-foreground">
            还没有项目。点击右上角「新建项目」创建第一个项目组。
          </p>
        </template>
      </div>
    </main>
  </div>
</template>
