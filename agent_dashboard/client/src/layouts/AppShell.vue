<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { Activity, GitBranch, ListTree, Settings } from 'lucide-vue-next'

const props = defineProps<{
  groupId?: string
  workflowId?: string
  title?: string
  eyebrow?: string
}>()

const route = useRoute()
const workflowBase = computed(() => props.groupId && props.workflowId
  ? `/groups/${encodeURIComponent(props.groupId)}/workflows/${encodeURIComponent(props.workflowId)}`
  : null)

const navItems = computed(() => [
  ...(workflowBase.value ? [
    { label: 'DAG', to: workflowBase.value, icon: GitBranch, active: route.path === workflowBase.value },
    { label: '执行日志', to: `${workflowBase.value}/logs`, icon: ListTree, active: route.path.endsWith('/logs') },
  ] : []),
  { label: '全局配置', to: '/settings', icon: Settings, active: route.path === '/settings' },
])
</script>

<template>
  <div class="min-h-screen bg-background text-foreground flex flex-col">
    <header class="h-14 border-b border-border flex items-center gap-3 px-3 sm:px-5 bg-background/95 backdrop-blur-sm sticky top-0 z-30">
      <RouterLink to="/" aria-label="返回 Agent Dashboard" class="flex items-center gap-2 rounded-md focus-visible:outline-2 focus-visible:outline-blue-400">
        <span class="w-7 h-7 rounded-lg bg-blue-500/15 border border-blue-500/30 flex items-center justify-center">
          <Activity :size="14" class="text-blue-400" />
        </span>
        <span class="hidden sm:inline font-display font-semibold text-sm">Agent Dashboard</span>
      </RouterLink>

      <div class="hidden md:flex items-center gap-1 min-w-0 text-xs text-muted-foreground font-mono">
        <span v-if="groupId" class="truncate max-w-32">{{ groupId }}</span>
        <span v-if="groupId && workflowId" aria-hidden="true">/</span>
        <span v-if="workflowId" class="truncate max-w-48 text-foreground">{{ workflowId }}</span>
      </div>

      <nav aria-label="主导航" class="ml-auto flex items-center gap-1">
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="flex items-center gap-1.5 rounded-md px-2.5 py-2 text-xs transition-colors focus-visible:outline-2 focus-visible:outline-blue-400"
          :class="item.active ? 'bg-blue-500/10 text-blue-300' : 'text-muted-foreground hover:bg-accent hover:text-foreground'"
          :aria-current="item.active ? 'page' : undefined"
        >
          <component :is="item.icon" :size="13" />
          <span class="hidden sm:inline">{{ item.label }}</span>
        </RouterLink>
      </nav>
      <slot name="actions" />
    </header>

    <div class="flex flex-1 min-h-0">
      <aside v-if="$slots.sidebar" class="hidden lg:block w-64 shrink-0 border-r border-border bg-sidebar overflow-y-auto">
        <slot name="sidebar" />
      </aside>
      <main class="flex-1 min-w-0 overflow-y-auto">
        <div v-if="title || eyebrow" class="border-b border-border px-4 py-4 sm:px-6">
          <p v-if="eyebrow" class="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{{ eyebrow }}</p>
          <h1 v-if="title" class="mt-1 font-display text-lg font-semibold">{{ title }}</h1>
        </div>
        <slot />
      </main>
    </div>
  </div>
</template>
