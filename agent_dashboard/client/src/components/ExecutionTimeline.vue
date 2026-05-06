<script setup lang="ts">
import type { SessionEvent } from '@/lib/mockData'
import {
  Clock,
  AlertCircle,
  Info,
  AlertTriangle,
  Terminal,
  FileText,
  Edit3,
  FolderOpen,
  Globe,
  Play,
  Square,
} from 'lucide-vue-next'
import { cn } from '@/lib/utils'

const props = defineProps<{
  events: SessionEvent[]
  loading?: boolean
}>()

const STEP_TYPE_CONFIG: Record<string, { icon: any; color: string; label: string }> = {
  SESSION_START: { icon: Play, color: 'text-blue-400', label: 'START' },
  SESSION_END:   { icon: Square, color: 'text-blue-400', label: 'END' },
  TOOL_CALL:     { icon: Terminal, color: 'text-slate-400', label: 'TOOL' },
  TOOL_RESULT:   { icon: Info, color: 'text-slate-400', label: 'RESULT' },
  TOOL_ERROR:    { icon: AlertCircle, color: 'text-red-400', label: 'ERROR' },
  FILE_READ:     { icon: FolderOpen, color: 'text-violet-400', label: 'READ' },
  FILE_EDIT:     { icon: Edit3, color: 'text-amber-400', label: 'EDIT' },
  BASH:          { icon: Terminal, color: 'text-emerald-400', label: 'BASH' },
  ASSISTANT_MSG: { icon: FileText, color: 'text-slate-500', label: 'MSG' },
  WEB:           { icon: Globe, color: 'text-cyan-400', label: 'WEB' },
  EXEC_START:    { icon: Play, color: 'text-emerald-400', label: 'START' },
  EXEC_END:      { icon: Square, color: 'text-emerald-400', label: 'END' },
  EXEC_ERROR:    { icon: AlertCircle, color: 'text-red-400', label: 'ERROR' },
  EXEC_TIMEOUT:  { icon: AlertTriangle, color: 'text-amber-400', label: 'TIMEOUT' },
}

function getStepConfig(stepType: string) {
  return STEP_TYPE_CONFIG[stepType] ?? { icon: Info, color: 'text-slate-500', label: stepType }
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
  } catch {
    return iso
  }
}

function levelDotColor(level: string): string {
  switch (level) {
    case 'error': return 'bg-red-400 ring-red-400/30'
    case 'warn':  return 'bg-amber-400 ring-amber-400/30'
    case 'debug': return 'bg-slate-600 ring-slate-600/30'
    default:      return 'bg-slate-500 ring-slate-500/30'
  }
}
</script>

<template>
  <div class="text-xs">
    <!-- Loading -->
    <div v-if="loading" class="py-6 flex items-center justify-center">
      <div class="w-4 h-4 border border-blue-400 border-t-transparent rounded-full animate-spin" />
      <span class="ml-2 text-muted-foreground">加载执行记录...</span>
    </div>

    <!-- Empty -->
    <div v-else-if="events.length === 0" class="py-6 text-center text-muted-foreground">
      <Clock :size="16" class="mx-auto mb-1.5 opacity-40" />
      <span>该任务暂无执行记录</span>
    </div>

    <!-- Timeline -->
    <div v-else class="relative pl-5">
      <!-- Vertical line -->
      <div class="absolute left-[9px] top-2 bottom-2 w-px bg-border" />

      <div
        v-for="(event, idx) in events"
        :key="event.seq ?? idx"
        class="relative pb-3 last:pb-0"
      >
        <!-- Dot -->
        <div
          :class="cn(
            'absolute left-[-11px] top-1.5 w-2.5 h-2.5 rounded-full ring-2',
            levelDotColor(event.level),
          )"
        />

        <!-- Content -->
        <div
          :class="cn(
            'rounded px-2 py-1.5 border',
            event.level === 'error'
              ? 'bg-red-500/5 border-red-500/20'
              : 'bg-accent/30 border-border/50',
          )"
        >
          <!-- Header row: step_type badge + timestamp -->
          <div class="flex items-center gap-1.5 mb-0.5">
            <span
              :class="cn(
                'inline-flex items-center gap-0.5 px-1 py-px rounded text-[10px] font-mono font-medium',
                getStepConfig(event.step_type).color,
                'bg-current/10',
              )"
            >
              <component :is="getStepConfig(event.step_type).icon" :size="9" />
              {{ getStepConfig(event.step_type).label }}
            </span>
            <span class="text-[10px] text-muted-foreground font-mono ml-auto flex-shrink-0">
              {{ formatTime(event.ts) }}
            </span>
          </div>
          <!-- Message -->
          <div
            :class="cn(
              'text-[11px] leading-relaxed break-words',
              event.level === 'error' ? 'text-red-300' : 'text-foreground',
            )"
          >
            {{ event.message }}
          </div>
          <!-- Extra data (file path / command) -->
          <div
            v-if="event.data && (event.data.file || event.data.command || event.data.pattern)"
            class="mt-1 text-[10px] text-muted-foreground font-mono truncate"
          >
            <span v-if="event.data.file">{{ event.data.file }}</span>
            <span v-else-if="event.data.command">{{ event.data.command }}</span>
            <span v-else-if="event.data.pattern">{{ event.data.pattern }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
