<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import AppShell from '@/layouts/AppShell.vue'
import TemplateListPanel from '@/components/TemplateListPanel.vue'

const route = useRoute()
const groupId = computed(() => {
  const value = route.query.groupId || route.query.group_id
  return typeof value === 'string' && value ? value : undefined
})
</script>

<template>
  <AppShell title="作业流模板" eyebrow="Templates">
    <div class="mx-auto max-w-6xl p-4 sm:p-6">
      <TemplateListPanel :group-id="groupId" />
    </div>
  </AppShell>
</template>
