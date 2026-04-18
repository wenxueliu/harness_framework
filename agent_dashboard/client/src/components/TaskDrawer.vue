<script setup lang="ts">
import type { Task } from '@/lib/mockData'
import StatusBadge from './StatusBadge.vue'
import {
  X,
  ExternalLink,
  GitCommit,
  Server,
  FileText,
  Camera,
} from 'lucide-vue-next'
import { cn } from '@/lib/utils'

const props = defineProps<{
  task: Task | null
  onClose: () => void
}>()

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}
</script>

<template>
  <div
    v-if="task"
    class="flex flex-col h-full bg-[oklch(0.135_0.009_264)] border-l border-border"
  >
    <!-- Header -->
    <div class="flex items-start justify-between p-4 border-b border-border">
      <div class="flex-1 min-w-0 pr-3">
        <div class="text-xs text-muted-foreground font-mono mb-1">{{ task.id }}</div>
        <h3 class="font-display font-semibold text-sm text-foreground leading-tight">
          {{ task.name }}
        </h3>
      </div>
      <button
        class="text-muted-foreground hover:text-foreground transition-colors p-1 rounded hover:bg-accent flex-shrink-0"
        @click="onClose"
      >
        <X :size="14" />
      </button>
    </div>

    <!-- Status -->
    <div class="px-4 py-3 border-b border-border">
      <StatusBadge :status="task.status" />
    </div>

    <!-- Meta info -->
    <div class="flex-1 overflow-y-auto px-4 py-2">
      <!-- Agent -->
      <div class="flex items-start gap-3 py-2.5 border-b border-border/50">
        <span class="text-muted-foreground mt-0.5 flex-shrink-0"><Server :size="13" /></span>
        <div class="flex-1 min-w-0">
          <div class="text-xs text-muted-foreground mb-0.5">执行 Agent</div>
          <span class="font-mono text-xs text-foreground truncate">{{ task.assigned_agent }}</span>
        </div>
      </div>

      <!-- Last updated -->
      <div class="flex items-start gap-3 py-2.5 border-b border-border/50">
        <span class="text-muted-foreground mt-0.5 flex-shrink-0"><FileText :size="13" /></span>
        <div class="flex-1 min-w-0">
          <div class="text-xs text-muted-foreground mb-0.5">最后更新</div>
          <span class="text-sm text-foreground">{{ formatDate(task.last_updated) }}</span>
        </div>
      </div>

      <!-- Git commit -->
      <div v-if="task.git_commit" class="flex items-start gap-3 py-2.5 border-b border-border/50">
        <span class="text-muted-foreground mt-0.5 flex-shrink-0"><GitCommit :size="13" /></span>
        <div class="flex-1 min-w-0">
          <div class="text-xs text-muted-foreground mb-0.5">Git Commit</div>
          <span class="font-mono text-xs text-foreground">{{ task.git_commit }}</span>
        </div>
      </div>

      <!-- Deployed version -->
      <div v-if="task.deployed_version" class="flex items-start gap-3 py-2.5 border-b border-border/50">
        <span class="text-muted-foreground mt-0.5 flex-shrink-0"><Server :size="13" /></span>
        <div class="flex-1 min-w-0">
          <div class="text-xs text-muted-foreground mb-0.5">部署版本</div>
          <span class="font-mono text-xs text-foreground">{{ task.deployed_version }}</span>
        </div>
      </div>

      <!-- Health check -->
      <div v-if="task.health_check_url" class="flex items-start gap-3 py-2.5 border-b border-border/50">
        <span class="text-muted-foreground mt-0.5 flex-shrink-0"><Server :size="13" /></span>
        <div class="flex-1 min-w-0">
          <div class="text-xs text-muted-foreground mb-0.5">健康检查端点</div>
          <a
            :href="task.health_check_url"
            target="_blank"
            rel="noopener noreferrer"
            class="text-sm text-blue-400 hover:text-blue-300 flex items-center gap-1 truncate"
          >
            <span class="truncate">{{ task.health_check_url }}</span>
            <ExternalLink :size="10" />
          </a>
        </div>
      </div>

      <!-- Error log -->
      <div v-if="task.error_log_url" class="flex items-start gap-3 py-2.5 border-b border-border/50">
        <span class="text-muted-foreground mt-0.5 flex-shrink-0"><FileText :size="13" /></span>
        <div class="flex-1 min-w-0">
          <div class="text-xs text-muted-foreground mb-0.5">错误日志</div>
          <a
            :href="task.error_log_url"
            target="_blank"
            rel="noopener noreferrer"
            class="text-sm text-blue-400 hover:text-blue-300 flex items-center gap-1"
          >
            查看错误日志 →
          </a>
        </div>
      </div>

      <!-- Screenshot -->
      <div v-if="task.screenshot_url" class="flex items-start gap-3 py-2.5 border-b border-border/50">
        <span class="text-muted-foreground mt-0.5 flex-shrink-0"><Camera :size="13" /></span>
        <div class="flex-1 min-w-0">
          <div class="text-xs text-muted-foreground mb-0.5">失败截图</div>
          <a
            :href="task.screenshot_url"
            target="_blank"
            rel="noopener noreferrer"
            class="text-sm text-blue-400 hover:text-blue-300 flex items-center gap-1"
          >
            查看截图 →
          </a>
        </div>
      </div>

      <!-- Dependencies -->
      <div v-if="task.depends_on.length > 0" class="mt-3 py-2.5">
        <div class="text-xs text-muted-foreground mb-2">前置依赖</div>
        <div class="flex flex-wrap gap-1.5">
          <span
            v-for="dep in task.depends_on"
            :key="dep"
            class="font-mono text-xs px-2 py-0.5 rounded bg-accent text-accent-foreground border border-border/50"
          >
            {{ dep }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>
