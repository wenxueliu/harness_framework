/**
 * Chat model for the ACP chat surface.
 *
 * ACP delivers a flat stream of `session/update` notifications. This module
 * folds that stream into an ordered list of renderable items so the UI can use
 * plain `v-for` instead of re-deriving grouping on every update.
 */

export type ToolStatus = 'pending' | 'in_progress' | 'completed' | 'failed'

export type ChatItem =
  | { id: string; role: 'user'; kind: 'text'; text: string }
  | { id: string; role: 'assistant'; kind: 'text'; text: string; streaming: boolean; messageId: string | null }
  | { id: string; role: 'assistant'; kind: 'thought'; text: string; streaming: boolean; messageId: string | null }
  | {
      id: string
      role: 'assistant'
      kind: 'tool'
      toolCallId: string
      title: string
      name: string | null
      toolKind: string | null
      status: ToolStatus
      output: string
    }
  | { id: string; role: 'assistant'; kind: 'plan'; text: string }
  | { id: string; role: 'assistant'; kind: 'notice'; text: string }

export type ContextUsage = {
  used: number
  size: number
}

export type PendingPermission = {
  permissionId: string
  title: string
  detail: string
  toolKind: string | null
  options: Array<{ optionId: string; name: string; kind: string }>
}

let counter = 0
export function nextItemId(prefix: string): string {
  counter += 1
  return `${prefix}-${Date.now().toString(36)}-${counter}`
}

/** Extracts display text from an ACP `ContentBlock`. */
export function contentText(block: unknown): string {
  if (!block || typeof block !== 'object') return ''
  const value = block as Record<string, unknown>
  switch (value.type) {
    case 'text':
      return typeof value.text === 'string' ? value.text : ''
    case 'resource_link':
      return typeof value.uri === 'string' ? value.uri : '[resource]'
    case 'resource':
      return '[embedded resource]'
    case 'image':
      return '[image]'
    case 'audio':
      return '[audio]'
    default:
      return JSON.stringify(value)
  }
}

/** Renders an ACP `ToolCallContent[]` into readable text. */
export function toolCallText(parts: unknown): string {
  if (!Array.isArray(parts)) return ''
  const rendered: string[] = []
  for (const part of parts) {
    if (!part || typeof part !== 'object') continue
    const value = part as Record<string, unknown>
    if (value.type === 'content') {
      const text = contentText(value.content)
      if (text) rendered.push(text)
    } else if (value.type === 'diff') {
      const path = typeof value.path === 'string' ? value.path : ''
      const oldText = typeof value.oldText === 'string' ? value.oldText : ''
      const newText = typeof value.newText === 'string' ? value.newText : ''
      rendered.push(`--- ${path}\n${oldText}\n+++ ${path}\n${newText}`)
    } else if (value.type === 'terminal') {
      rendered.push(`[terminal ${String(value.terminalId ?? '')}]`)
    }
  }
  return rendered.filter(Boolean).join('\n\n')
}

export function normalizeToolStatus(status: unknown): ToolStatus {
  return status === 'in_progress' || status === 'completed' || status === 'failed' || status === 'pending'
    ? status
    : 'pending'
}
