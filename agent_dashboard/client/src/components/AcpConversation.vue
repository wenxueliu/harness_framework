<script setup lang="ts">
/**
 * ACP 会话渲染：消息、思考、工具调用、计划与权限审批。
 *
 * `/acp` 独立对话页和实例节点页共用同一个组件，保证两处的外观和行为一致。
 * 数据来自 `useAcpChat`，组件本身不接触 ACP 协议。
 */
import type { ToolUIPart } from 'ai'
import { MessageSquare, ShieldAlert, Sparkles } from 'lucide-vue-next'
import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from '@/components/ai-elements/conversation'
import { Message, MessageAvatar, MessageContent, MessageResponse } from '@/components/ai-elements/message'
import { Reasoning, ReasoningContent, ReasoningTrigger } from '@/components/ai-elements/reasoning'
import { Tool, ToolContent, ToolHeader, ToolOutput } from '@/components/ai-elements/tool'
import { Button } from '@/components/ui/button'
import type { ChatItem, PendingPermission, ToolStatus } from '@/lib/acp/chatModel'

const props = withDefaults(defineProps<{
  items: ChatItem[]
  permission?: PendingPermission | null
  emptyTitle?: string
  emptyDescription?: string
}>(), {
  permission: null,
  emptyTitle: '还没有消息',
  emptyDescription: '连接后，Agent 的历史消息和实时输出会显示在这里。',
})

const emit = defineEmits<{
  (event: 'answer-permission', optionId: string | null): void
}>()

function toolState(status: ToolStatus): ToolUIPart['state'] {
  if (status === 'in_progress') return 'input-available'
  if (status === 'completed') return 'output-available'
  if (status === 'failed') return 'output-error'
  return 'input-streaming'
}

function toolType(item: Extract<ChatItem, { kind: 'tool' }>): ToolUIPart['type'] {
  return `tool-${item.name || item.toolKind || 'tool'}` as ToolUIPart['type']
}

function permissionVariant(kind: string): 'default' | 'outline' {
  return kind.startsWith('allow') ? 'default' : 'outline'
}
</script>

<template>
  <Conversation class="min-h-0 flex-1">
    <ConversationContent class="gap-4 p-3">
      <ConversationEmptyState
        v-if="props.items.length === 0"
        :title="props.emptyTitle"
        :description="props.emptyDescription"
      >
        <template #icon>
          <MessageSquare :size="32" />
        </template>
      </ConversationEmptyState>

      <template v-for="item in props.items" :key="item.id">
        <Message v-if="item.role === 'user'" from="user">
          <MessageContent>
            <MessageResponse :content="item.text" />
          </MessageContent>
        </Message>

        <Message v-else-if="item.kind === 'text'" from="assistant">
          <MessageAvatar src="" name="AI" />
          <MessageContent>
            <MessageResponse :content="item.text" />
          </MessageContent>
        </Message>

        <Message v-else-if="item.kind === 'thought'" from="assistant">
          <MessageAvatar src="" name="AI" />
          <MessageContent>
            <Reasoning :is-streaming="item.streaming">
              <ReasoningTrigger />
              <ReasoningContent :content="item.text" />
            </Reasoning>
          </MessageContent>
        </Message>

        <Message v-else-if="item.kind === 'tool'" from="assistant">
          <MessageAvatar src="" name="AI" />
          <MessageContent>
            <Tool>
              <ToolHeader :title="item.title" :type="toolType(item)" :state="toolState(item.status)" />
              <ToolContent>
                <ToolOutput
                  :output="item.output || null"
                  :error-text="item.status === 'failed' ? item.output : undefined"
                />
              </ToolContent>
            </Tool>
          </MessageContent>
        </Message>

        <Message v-else from="assistant">
          <MessageAvatar src="" name="AI" />
          <MessageContent>
            <div class="flex items-start gap-2 rounded-md border border-border bg-muted/40 px-3 py-2">
              <Sparkles :size="14" class="mt-0.5 shrink-0 text-muted-foreground" />
              <pre class="whitespace-pre-wrap font-mono text-[11px]">{{ item.text }}</pre>
            </div>
          </MessageContent>
        </Message>
      </template>
    </ConversationContent>
    <ConversationScrollButton />
  </Conversation>

  <!-- 权限审批：ACP 的 session/request_permission 必须由客户端回应 -->
  <div v-if="props.permission" class="shrink-0 border-t border-amber-500/30 bg-amber-500/5 px-3 py-3">
    <div class="flex items-start gap-2">
      <ShieldAlert :size="16" class="mt-0.5 shrink-0 text-amber-400" />
      <div class="min-w-0 flex-1 space-y-2">
        <p class="text-sm font-medium">{{ props.permission.title }}</p>
        <pre
          v-if="props.permission.detail"
          class="max-h-40 overflow-auto whitespace-pre-wrap rounded border border-border bg-background/60 p-2 font-mono text-[11px]"
        >{{ props.permission.detail }}</pre>
        <div class="flex flex-wrap gap-2">
          <Button
            v-for="option in props.permission.options"
            :key="option.optionId"
            size="sm"
            :variant="permissionVariant(option.kind)"
            @click="emit('answer-permission', option.optionId)"
          >
            {{ option.name }}
          </Button>
          <Button size="sm" variant="ghost" @click="emit('answer-permission', null)">拒绝</Button>
        </div>
      </div>
    </div>
  </div>
</template>
