<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import AppShell from '@/layouts/AppShell.vue'
import InstanceListPanel from '@/components/InstanceListPanel.vue'

const route = useRoute()
const groupId = computed(() => {
  const value = route.query.groupId || route.query.group_id
  return typeof value === 'string' && value ? value : undefined
})
</script>

<template>
  <AppShell title="作业流实例" eyebrow="Instances">
    <template #actions><RouterLink :to="groupId ? { path: '/templates', query: { groupId } } : '/templates'" class="rounded bg-blue-500 px-3 py-2 text-xs text-white">创建实例</RouterLink></template>
    <div class="mx-auto max-w-6xl p-4 sm:p-6">
      <InstanceListPanel :group-id="groupId" />
    </div>
  </AppShell>
</template>
