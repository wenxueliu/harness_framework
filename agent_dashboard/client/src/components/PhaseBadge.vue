<script setup lang="ts">
import type { WorkflowPhase } from '@/lib/mockData'
import { PHASE_CONFIG } from '@/lib/mockData'
import { cn } from '@/lib/utils'

defineProps<{ phase: WorkflowPhase; class?: string }>()
</script>

<template>
  <span
    :class="cn(
      'inline-flex items-center px-2.5 py-1 rounded text-xs font-semibold tracking-wide font-mono',
      PHASE_CONFIG[phase].bgColor,
      PHASE_CONFIG[phase].color,
      $props.class,
    )"
  >
    {{ PHASE_CONFIG[phase].label }}
  </span>
</template>
