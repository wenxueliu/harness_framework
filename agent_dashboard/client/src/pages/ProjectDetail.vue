<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { ArrowLeft } from 'lucide-vue-next'
import AppShell from '@/layouts/AppShell.vue'
import GroupDashboard from '@/components/GroupDashboard.vue'
import ProjectInfoBadge from '@/components/ProjectInfoBadge.vue'
import TemplateListPanel from '@/components/TemplateListPanel.vue'
import InstanceListPanel from '@/components/InstanceListPanel.vue'
import { useCapabilityStore } from '@/stores/capabilities'
import { useProjectGroupStore } from '@/stores/projectGroups'

type ProjectTab = 'board' | 'templates' | 'instances'
const TABS: { key: ProjectTab; label: string }[] = [
  { key: 'templates', label: '模板' },
  { key: 'board', label: '看板' },
  { key: 'instances', label: '实例' },
]

const route = useRoute()
const router = useRouter()
const capabilityStore = useCapabilityStore()
const projectGroupStore = useProjectGroupStore()

const currentGroupId = computed(() => typeof route.params.groupId === 'string' ? route.params.groupId : 'unassigned')
// workflow-dashboard 深链始终是看板 tab 的选中态，忽略 ?tab；
// 点击项目进入（group-dashboard）默认展示模板，全局看板（/dashboard）保持看板默认
const activeTab = computed<ProjectTab>(() => {
  if (route.name === 'workflow-dashboard') return 'board'
  const tab = route.query.tab
  if (tab === 'templates') return 'templates'
  if (tab === 'instances') return 'instances'
  if (tab === 'board') return 'board'
  return route.name === 'board-dashboard' ? 'board' : 'templates'
})

function switchTab(tab: ProjectTab) {
  const fallback: ProjectTab = route.name === 'board-dashboard' ? 'board' : 'templates'
  void router.replace({ query: { ...route.query, tab: tab === fallback ? undefined : tab } })
}

function refreshContext() {
  capabilityStore.load(currentGroupId.value).catch(() => undefined)
  projectGroupStore.load(currentGroupId.value).catch(() => undefined)
}

onMounted(refreshContext)
watch(() => route.params.groupId, refreshContext)
</script>

<template>
  <GroupDashboard v-if="activeTab === 'board'" :group-id="currentGroupId" show-project-tabs />
  <AppShell
    v-else
    :group-id="currentGroupId"
    :title="activeTab === 'templates' ? '作业流模板' : '作业流实例'"
    :eyebrow="activeTab === 'templates' ? 'Templates' : 'Instances'"
  >
    <template #actions><ProjectInfoBadge :group-id="currentGroupId" /></template>
    <div class="mx-auto max-w-6xl p-4 sm:p-6">
      <!-- 从项目列表点击进入（group-dashboard）时提供返回首页入口；全局看板不受影响 -->
      <RouterLink
        v-if="route.name === 'group-dashboard'"
        to="/"
        class="mb-4 inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        <ArrowLeft :size="13" />返回首页
      </RouterLink>
      <div class="mb-4 flex items-center gap-1" data-testid="project-tabs">
        <button
          v-for="tab in TABS"
          :key="tab.key"
          type="button"
          class="rounded-full border px-3 py-1 text-xs transition"
          :class="activeTab === tab.key ? 'border-blue-400 bg-blue-400/10 text-blue-300' : 'border-border text-muted-foreground hover:bg-accent'"
          @click="switchTab(tab.key)"
        >{{ tab.label }}</button>
      </div>
      <TemplateListPanel v-if="activeTab === 'templates'" :group-id="currentGroupId" />
      <InstanceListPanel v-else :group-id="currentGroupId" />
    </div>
  </AppShell>
</template>
