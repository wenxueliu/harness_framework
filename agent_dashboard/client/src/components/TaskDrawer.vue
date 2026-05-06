<script setup lang="ts">
import { ref, watch } from 'vue'
import type { Task, SessionEvent } from '@/lib/mockData'
import { fetchTaskSessionEvents } from '@/lib/consulApi'
import StatusBadge from './StatusBadge.vue'
import ExecutionTimeline from './ExecutionTimeline.vue'
import {
  X,
  ExternalLink,
  GitCommit,
  Server,
  FileText,
  Camera,
  History,
  RefreshCw,
} from 'lucide-vue-next'
import { cn } from '@/lib/utils'

const props = defineProps<{
  task: Task | null
  reqId?: string
  onClose: () => void
}>()

const sessionEvents = ref<SessionEvent[]>([])
const sessionLoading = ref(false)
const sessionError = ref('')

async function loadSessions() {
  if (!props.task || !props.reqId) return
  sessionLoading.value = true
  sessionError.value = ''
  try {
    const data = await fetchTaskSessionEvents(props.reqId, props.task.id)
    sessionEvents.value = data.events
  } catch (e) {
    sessionError.value = '加载执行历史失败'
    sessionEvents.value = []
  } finally {
    sessionLoading.value = false
  }
}

// Reload sessions when task changes
watch(() => props.task?.id, () => {
  sessionEvents.value = []
  sessionError.value = ''
  if (props.task && props.reqId) {
    loadSessions()
  }
}, { immediate: true })

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

      <!-- Execution History -->
      <div v-if="reqId" class="mt-3 py-2.5 border-t border-border/50">
        <div class="flex items-center justify-between mb-2">
          <div class="flex items-center gap-1.5">
            <History :size="12" class="text-muted-foreground" />
            <span class="text-xs text-muted-foreground font-medium">执行历史</span>
            <span
              v-if="sessionEvents.length > 0"
              class="text-[10px] font-mono text-muted-foreground bg-accent px-1 rounded"
            >
              {{ sessionEvents.length }}
            </span>
          </div>
          <button
            class="text-muted-foreground hover:text-foreground transition-colors p-0.5 rounded hover:bg-accent"
            :disabled="sessionLoading"
            @click="loadSessions"
          >
            <RefreshCw :size="11" :class="sessionLoading ? 'animate-spin' : ''" />
          </button>
        </div>
        <div v-if="sessionError" class="text-xs text-red-400 mb-2">
          {{ sessionError }}
        </div>
        <ExecutionTimeline
          :events="sessionEvents"
          :loading="sessionLoading"
        />
      </div>
    </div>
  </div>
</template>
