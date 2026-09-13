import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { apiUrl } from '@/api/client'
import type { EventEnvelope } from '@/api/types'

export type EventStreamState = 'idle' | 'connecting' | 'live' | 'reconnecting' | 'reset' | 'closed'

export const useEventStreamStore = defineStore('event-stream', () => {
  const events = ref<EventEnvelope[]>([])
  const state = ref<EventStreamState>('idle')
  const error = ref('')
  const lastEventId = ref('')
  let source: EventSource | null = null
  let target = ''

  function disconnect() {
    source?.close()
    source = null
    target = ''
    state.value = 'closed'
  }

  function connect(filters: { groupId?: string; reqId?: string; runId?: string; taskId?: string }) {
    const query = new URLSearchParams()
    if (filters.groupId) query.set('group_id', filters.groupId)
    if (filters.reqId) query.set('req_id', filters.reqId)
    if (filters.runId) query.set('run_id', filters.runId)
    if (filters.taskId) query.set('task_id', filters.taskId)
    const nextTarget = `/api/events?${query.toString()}`
    if (source && target === nextTarget) return
    disconnect()
    events.value = []
    lastEventId.value = ''
    error.value = ''
    target = nextTarget
    state.value = 'connecting'
    source = new EventSource(apiUrl(nextTarget))
    source.onopen = () => { state.value = 'live'; error.value = '' }
    source.onerror = () => {
      if (state.value !== 'reset') state.value = 'reconnecting'
      error.value = '事件流连接中断，正在自动重连'
    }
    const accept = (message: MessageEvent<string>) => {
      try {
        const event = JSON.parse(message.data) as EventEnvelope
        if (!event.event_id || events.value.some((item) => item.event_id === event.event_id)) return
        events.value.push(event)
        if (events.value.length > 1000) events.value.splice(0, events.value.length - 1000)
        lastEventId.value = event.event_id
      } catch { error.value = '服务端返回了无法解析的事件' }
    }
    const eventTypes = [
      'RUN_CREATED', 'RUN_STATUS_CHANGED', 'TASK_STATUS_CHANGED',
      'ATTEMPT_STARTED', 'ATTEMPT_FINISHED', 'SESSION_EVENT',
      'HUMAN_MESSAGE_CREATED', 'WORKSPACE_FILE_CHANGED', 'WORKSPACE_STATUS_CHANGED',
      'WORKSPACE_ACTION_FINISHED', 'WORKSPACE_CHECKPOINT_CREATED',
    ]
    eventTypes.forEach((type) => source?.addEventListener(type, accept as EventListener))
    source.addEventListener('STREAM_RESET_REQUIRED', () => {
      state.value = 'reset'
      error.value = '事件游标已过期，请重新加载服务端快照'
      source?.close()
      source = null
    })
  }

  const orderedEvents = computed(() => [...events.value].sort((a, b) => b.sequence - a.sequence))
  return { events, orderedEvents, state, error, lastEventId, connect, disconnect }
})
