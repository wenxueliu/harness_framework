<script setup lang="ts">
import { AlertCircle } from 'lucide-vue-next'
import type { Workflow } from '@/lib/mockData'
import { PHASE_CONFIG } from '@/lib/mockData'
import { cn } from '@/lib/utils'

const props = defineProps<{
  workflow: Workflow
  selected: boolean
  variant?: 'mobile' | 'desktop'
}>()

const emit = defineEmits<{
  select: [id: string]
}>()

const isMobile = props.variant === 'mobile'
</script>

<template>
  <button
    class="w-full text-left transition-colors border-r-2"
    :class="[
      isMobile ? 'px-4 py-3' : 'px-3 py-2.5',
      selected
        ? 'bg-blue-500/10 border-blue-400'
        : 'hover:bg-accent border-transparent'
    ]"
    @click="emit('select', workflow.id)"
  >
    <div class="flex items-center justify-between mb-1">
      <span
        :class="cn(
          'font-mono text-xs font-medium',
          selected ? 'text-blue-400' : 'text-muted-foreground'
        )"
      >
        {{ workflow.id }}
      </span>
      <AlertCircle
        v-if="Object.values(workflow.tasks).some(t => t.status === 'FAILED' || t.status === 'BLOCKED')"
        :size="isMobile ? 11 : 10"
        class="text-red-400"
      />
    </div>
    <div class="text-xs font-medium leading-tight mb-1.5 truncate">{{ workflow.title }}</div>
    <div class="flex items-center justify-between">
      <span
        :class="cn('text-xs font-mono px-1.5 py-0.5 rounded', PHASE_CONFIG[workflow.phase].bgColor, PHASE_CONFIG[workflow.phase].color)"
      >
        {{ PHASE_CONFIG[workflow.phase].label }}
      </span>
      <span class="text-xs text-muted-foreground font-mono">
        {{ Object.values(workflow.tasks).filter(t => t.status === 'DONE').length }}/{{ Object.values(workflow.tasks).length }}
      </span>
    </div>
  </button>
</template>
