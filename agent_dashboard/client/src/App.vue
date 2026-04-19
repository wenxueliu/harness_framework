<script setup lang="ts">
import { onErrorCaptured } from 'vue'
import { RouterView } from 'vue-router'

// Error boundary via onErrorCaptured
const error = defineModel<Error | null>('appError', { default: null })

onErrorCaptured((err: Error) => {
  error.value = err
  console.error('[Vue ErrorBoundary]', err)
  return false
})
</script>

<template>
  <div class="min-h-screen bg-background text-foreground">
    <RouterView />
  </div>
</template>
