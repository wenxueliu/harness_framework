<script setup lang="ts">
import type { TaskStatus, WorkflowPhase } from '@/lib/mockData'
import { STATUS_CONFIG, PHASE_CONFIG } from '@/lib/mockData'
import { cn } from '@/lib/utils'

defineProps<{ status: TaskStatus; class?: string }>()
</script>

<template>
  <span
    :class="cn(
      'inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-medium font-mono',
      STATUS_CONFIG[status].bgColor,
      STATUS_CONFIG[status].color,
      $props.class,
    )"
  >
    <span
      :class="cn(
        'w-1.5 h-1.5 rounded-full',
        STATUS_CONFIG[status].dotColor,
        status === 'IN_PROGRESS' && 'animate-pulse',
      )"
    />
    {{ STATUS_CONFIG[status].label }}
  </span>
</template>
