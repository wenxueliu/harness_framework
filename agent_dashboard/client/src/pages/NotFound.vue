<script setup lang="ts">
import { RouterLink } from 'vue-router'
</script>

<template>
  <div class="min-h-screen bg-background flex items-center justify-center">
    <div class="text-center space-y-4">
      <div class="text-6xl font-display font-bold text-muted-foreground">404</div>
      <h1 class="text-xl font-display font-semibold text-foreground">Page not found</h1>
      <p class="text-sm text-muted-foreground">The page you are looking for does not exist.</p>
      <RouterLink
        to="/"
        class="inline-flex items-center gap-2 text-sm text-blue-400 hover:text-blue-300 transition-colors"
      >
        ← Back to Dashboard
      </RouterLink>
    </div>
  </div>
</template>
