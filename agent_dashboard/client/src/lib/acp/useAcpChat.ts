/**
 * Drives one ACP session from the browser.
 *
 * Transport: `@agentclientprotocol/sdk`'s WebSocket stream (one JSON-RPC
 * message per text frame) against the local bridge in `server/acpBridge.ts`,
 * which owns the agent subprocess and the stdio transport.
 *
 * Everything the agent reports arrives as `session/update` notifications,
 * drained through `ActiveSession.nextUpdate()` so nothing buffers server-side.
 */
import {
  AGENT_METHODS,
  CLIENT_METHODS,
  PROTOCOL_VERSION,
  client,
  type ActiveSession,
  type ClientConnection,
  type ClientContext,
  type InitializeResponse,
  type RequestPermissionRequest,
  type RequestPermissionResponse,
  type SessionUpdate,
  type Stream,
} from '@agentclientprotocol/sdk'
import { createWebSocketStream } from '@agentclientprotocol/sdk/experimental/ws-client'
import { computed, ref, shallowRef } from 'vue'
import {
  contentText,
  normalizeToolStatus,
  nextItemId,
  toolCallText,
  type ChatItem,
  type ContextUsage,
  type PendingPermission,
} from './chatModel'

export type ChatStatus = 'idle' | 'connecting' | 'ready' | 'busy' | 'error'

export type ConnectOptions = {
  /** Bridge base URL, e.g. `ws://127.0.0.1:8787/acp`. */
  bridgeUrl: string
  /** Agent key the bridge knows about (`codex`, `claude`, ...). */
  agent: string
  /** Absolute path interpreted on the bridge host, not in the browser. */
  cwd: string
  /** Stable key so a reload can reattach to the same agent process. */
  sessionKey?: string
  /** Existing ACP session to load (history is replayed by the agent). */
  loadSessionId?: string
}

/** Client capabilities we advertise: no filesystem, no terminal — we are a browser. */
const CLIENT_CAPABILITIES = {
  fs: { readTextFile: false, writeTextFile: false },
  terminal: false,
} as const

/** Bound on `initialize` + `session/new`; see the guard in `connect`. */
const CONNECT_TIMEOUT_MS = 30_000

export function useAcpChat() {
  const status = ref<ChatStatus>('idle')
  const errorMessage = ref('')
  const items = ref<ChatItem[]>([])
  const permission = ref<PendingPermission | null>(null)
  const usage = ref<ContextUsage | null>(null)
  const agentLabel = ref('')
  const sessionId = ref('')
  const canLoadSession = ref(false)

  const connection = shallowRef<ClientConnection | null>(null)
  const activeSession = shallowRef<ActiveSession | null>(null)
  const context = shallowRef<ClientContext | null>(null)
  const stream = shallowRef<Stream | null>(null)

  let runToken = 0
  let pendingPermissionResolve: ((response: RequestPermissionResponse) => void) | null = null
  let openText: { id: string; messageId: string | null } | null = null
  let openThought: { id: string; messageId: string | null } | null = null
  let openUser: { id: string; messageId: string | null } | null = null
  /** True while `session/load` is replaying history: user chunks are real messages. */
  let historyLoading = false
  const toolItems = new Map<string, string>()

  const isBusy = computed(() => status.value === 'busy')

  function pushItem(item: ChatItem): void {
    items.value = [...items.value, item]
  }

  function patchItem(id: string, patch: Partial<ChatItem>): void {
    items.value = items.value.map((item) =>
      item.id === id ? ({ ...item, ...patch } as ChatItem) : item,
    )
  }

  function finishStreaming(): void {
    for (const item of items.value) {
      if (item.role === 'assistant' && (item.kind === 'text' || item.kind === 'thought') && item.streaming) {
        patchItem(item.id, { streaming: false } as Partial<ChatItem>)
      }
    }
    openText = null
    openThought = null
    openUser = null
  }

  /** 历史回放里的用户消息（`user_message_chunk`），按 messageId 合并。 */
  function appendUserChunk(text: string, messageId: string | null): void {
    if (!text) return
    const current = openUser
    const sameMessage = current && (!messageId || !current.messageId || current.messageId === messageId)
    if (sameMessage && current) {
      const existing = items.value.find((item) => item.id === current.id)
      if (existing && existing.role === 'user') {
        patchItem(current.id, { text: existing.text + text } as Partial<ChatItem>)
        return
      }
    }
    const item: ChatItem = {
      id: nextItemId('user'),
      role: 'user',
      kind: 'text',
      text,
    }
    pushItem(item)
    openUser = { id: item.id, messageId: messageId ?? null }
  }

  function appendChunk(kind: 'text' | 'thought', text: string, messageId: string | null): void {
    if (!text) return
    openUser = null
    const current = kind === 'text' ? openText : openThought
    const sameMessage = current && (!messageId || !current.messageId || current.messageId === messageId)
    if (sameMessage && current) {
      const existing = items.value.find((item) => item.id === current.id)
      if (existing && (existing.kind === 'text' || existing.kind === 'thought')) {
        patchItem(current.id, { text: existing.text + text } as Partial<ChatItem>)
        return
      }
    }
    const item: ChatItem = {
      id: nextItemId(kind),
      role: 'assistant',
      kind,
      text,
      streaming: true,
      messageId: messageId ?? null,
    }
    pushItem(item)
    if (kind === 'text') openText = { id: item.id, messageId: messageId ?? null }
    else openThought = { id: item.id, messageId: messageId ?? null }
  }

  function upsertTool(
    toolCallId: string,
    patch: { title?: string; name?: string | null; toolKind?: string | null; status?: unknown; content?: unknown },
  ): void {
    const existingId = toolItems.get(toolCallId)
    if (existingId) {
      const existing = items.value.find((item) => item.id === existingId)
      if (existing && existing.kind === 'tool') {
        patchItem(existingId, {
          title: patch.title ?? existing.title,
          name: patch.name ?? existing.name,
          toolKind: patch.toolKind ?? existing.toolKind,
          status: patch.status ? normalizeToolStatus(patch.status) : existing.status,
          output: patch.content !== undefined ? toolCallText(patch.content) : existing.output,
        } as Partial<ChatItem>)
        return
      }
    }
    const id = nextItemId('tool')
    toolItems.set(toolCallId, id)
    pushItem({
      id,
      role: 'assistant',
      kind: 'tool',
      toolCallId,
      title: patch.title || toolCallId,
      name: patch.name ?? null,
      toolKind: patch.toolKind ?? null,
      status: normalizeToolStatus(patch.status),
      output: toolCallText(patch.content),
    })
  }

  function applyUpdate(update: SessionUpdate, options: { userMessages?: boolean } = {}): void {
    const value = update as unknown as Record<string, unknown>
    switch (value.sessionUpdate) {
      case 'user_message_chunk':
        // 自己刚发出的 prompt 会被 Agent 回显；只有历史回放时才是真实消息。
        if (historyLoading || options.userMessages) {
          appendUserChunk(contentText(value.content), (value.messageId as string | undefined) ?? null)
        }
        return
      case 'agent_message_chunk':
        appendChunk('text', contentText(value.content), (value.messageId as string | undefined) ?? null)
        return
      case 'agent_thought_chunk':
        appendChunk('thought', contentText(value.content), (value.messageId as string | undefined) ?? null)
        return
      case 'tool_call':
        upsertTool(String(value.toolCallId ?? ''), {
          title: typeof value.title === 'string' ? value.title : undefined,
          name: (value.name as string | null | undefined) ?? null,
          toolKind: (value.kind as string | null | undefined) ?? null,
          status: value.status,
          content: value.content,
        })
        return
      case 'tool_call_update':
        upsertTool(String(value.toolCallId ?? ''), {
          title: typeof value.title === 'string' ? value.title : undefined,
          status: value.status,
          content: value.content,
        })
        return
      case 'plan': {
        const entries = Array.isArray(value.entries) ? value.entries : []
        const text = entries
          .map((entry) => {
            const row = entry as Record<string, unknown>
            const marker = row.status === 'completed' ? 'x' : row.status === 'in_progress' ? '>' : ' '
            return `[${marker}] ${String(row.content ?? '')}`
          })
          .join('\n')
        if (text) pushItem({ id: nextItemId('plan'), role: 'assistant', kind: 'plan', text })
        return
      }
      case 'notice': {
        const title = typeof value.title === 'string' ? value.title : ''
        const description = typeof value.description === 'string' ? value.description : ''
        const text = [title, description].filter(Boolean).join(' — ')
        if (text) pushItem({ id: nextItemId('notice'), role: 'assistant', kind: 'notice', text })
        return
      }
      case 'usage_update':
        usage.value = {
          used: Number(value.used ?? 0),
          size: Number(value.size ?? 0),
        }
        return
      default:
        return
    }
  }

  async function readLoop(session: ActiveSession, token: number): Promise<void> {
    for (;;) {
      let message: Awaited<ReturnType<ActiveSession['nextUpdate']>>
      try {
        message = await session.nextUpdate()
      } catch (error) {
        if (token !== runToken) return
        if (status.value !== 'idle') {
          errorMessage.value = `连接中断：${String(error)}`
          status.value = 'error'
        }
        return
      }
      if (token !== runToken) return
      if (message.kind === 'stop') {
        finishStreaming()
        if (status.value === 'busy') status.value = 'ready'
        continue
      }
      applyUpdate(message.update)
    }
  }

  function handlePermissionRequest(params: RequestPermissionRequest): Promise<RequestPermissionResponse> {
    const toolCall = params.toolCall as unknown as Record<string, unknown>
    const permissionId = nextItemId('perm')
    permission.value = {
      permissionId,
      title: typeof toolCall.title === 'string' ? toolCall.title : '需要授权',
      detail: toolCallText(toolCall.content),
      toolKind: (toolCall.kind as string | null | undefined) ?? null,
      options: (params.options ?? []).map((option) => ({
        optionId: String(option.optionId),
        name: option.name,
        kind: String(option.kind),
      })),
    }
    return new Promise<RequestPermissionResponse>((resolve) => {
      pendingPermissionResolve = (response) => {
        pendingPermissionResolve = null
        permission.value = null
        resolve(response)
      }
    })
  }

  function answerPermission(optionId: string | null): void {
    if (!pendingPermissionResolve) return
    pendingPermissionResolve(
      optionId
        ? { outcome: { outcome: 'selected', optionId } }
        : { outcome: { outcome: 'cancelled' } },
    )
  }

  /** 清空当前渲染的对话（连接新会话或任务重试时使用）。 */
  function resetStream(): void {
    items.value = []
    usage.value = null
    permission.value = null
    toolItems.clear()
    openText = null
    openThought = null
    openUser = null
    historyLoading = false
  }

  async function connect(options: ConnectOptions): Promise<void> {
    await disconnect()
    const token = ++runToken
    status.value = 'connecting'
    errorMessage.value = ''
    resetStream()

    const query = new URLSearchParams({ agent: options.agent, cwd: options.cwd })
    if (options.sessionKey) query.set('session', options.sessionKey)
    const url = `${options.bridgeUrl}?${query.toString()}`

    // A socket that opens and then dies (agent unspawnable at runtime, crash
    // before initialize) leaves the SDK request pending forever, so the page
    // would sit at "连接中" indefinitely. Bound the negotiation.
    const guard = setTimeout(() => {
      if (token !== runToken) return
      errorMessage.value =
        `连接超时：${CONNECT_TIMEOUT_MS / 1000}s 内没有收到 Agent 的初始化响应。` +
        '请检查桥接日志，并确认该 Agent 在桥接主机上可执行。'
      status.value = 'error'
      void disconnect()
    }, CONNECT_TIMEOUT_MS)

    try {
      const wsStream = createWebSocketStream(url)
      stream.value = wsStream

      const app = client({ name: 'harness-dashboard' }).onRequest(
        CLIENT_METHODS.session_request_permission,
        ({ params }) => handlePermissionRequest(params),
      )

      const conn = app.connect(wsStream)
      connection.value = conn
      if (token !== runToken) return

      // `client()` does not auto-negotiate: initialize is an explicit request.
      const init: InitializeResponse = await conn.agent.request(AGENT_METHODS.initialize, {
        protocolVersion: PROTOCOL_VERSION,
        clientCapabilities: CLIENT_CAPABILITIES,
      })
      if (token !== runToken) return
      const info = init.agentInfo as { name?: string; version?: string } | null | undefined
      agentLabel.value = info?.name ? `${info.name}${info.version ? ` ${info.version}` : ''}` : options.agent
      canLoadSession.value = Boolean(
        (init.agentCapabilities as { loadSession?: boolean } | null | undefined)?.loadSession,
      )

      context.value = conn.agent
      const loadSessionId = options.loadSessionId?.trim()
      if (loadSessionId) {
        if (!canLoadSession.value) {
          throw new Error(`Agent ${options.agent} 不支持加载已有会话（loadSession）`)
        }
        // initialize 已成功，后续只是回放历史，不再受连接超时限制。
        clearTimeout(guard)
        historyLoading = true
        // 先挂接会话再 load：history 的 session/update 才会进入 nextUpdate() 队列。
        const attach = (conn.agent as unknown as {
          attachSession: (response: { sessionId: string }) => ActiveSession
        }).attachSession
        const session = attach.call(conn.agent, { sessionId: loadSessionId })
        activeSession.value = session
        sessionId.value = session.sessionId
        void readLoop(session, token)
        const loadGuard = setTimeout(() => {
          if (token !== runToken) return
          errorMessage.value = '加载会话历史超时：Agent 在 60s 内没有完成 session/load。'
          status.value = 'error'
          void disconnect()
        }, 60_000)
        try {
          await conn.agent.request(AGENT_METHODS.session_load, {
            sessionId: loadSessionId,
            cwd: options.cwd,
            mcpServers: [],
          })
        } finally {
          clearTimeout(loadGuard)
        }
        if (token !== runToken) return
        // 回放结束时收尾流式状态；留一点时间给最后几个 chunk。
        await new Promise((resolve) => setTimeout(resolve, 200))
        if (token !== runToken) return
        finishStreaming()
        historyLoading = false
        status.value = 'ready'
        return
      }
      const session = await conn.agent.buildSession(options.cwd).start()
      if (token !== runToken) return
      activeSession.value = session
      sessionId.value = session.sessionId
      status.value = 'ready'
      void readLoop(session, token)
    } catch (error) {
      if (token !== runToken) return
      errorMessage.value = String(error)
      status.value = 'error'
    } finally {
      clearTimeout(guard)
    }
  }

  async function send(text: string): Promise<void> {
    const session = activeSession.value
    const trimmed = text.trim()
    if (!session || !trimmed || status.value === 'busy') return
    pushItem({ id: nextItemId('user'), role: 'user', kind: 'text', text: trimmed })
    openText = null
    status.value = 'busy'
    try {
      await session.prompt(trimmed)
    } catch (error) {
      errorMessage.value = String(error)
      status.value = 'error'
      finishStreaming()
    }
  }

  async function cancel(): Promise<void> {
    const ctx = context.value
    if (!ctx || !sessionId.value) return
    try {
      await ctx.notify(AGENT_METHODS.session_cancel, { sessionId: sessionId.value })
    } catch (error) {
      errorMessage.value = String(error)
    }
  }

  async function disconnect(): Promise<void> {
    runToken += 1
    activeSession.value?.dispose()
    activeSession.value = null
    connection.value?.close()
    connection.value = null
    context.value = null
    try {
      await stream.value?.writable.close()
    } catch {
      // transport already closed
    }
    stream.value = null
    sessionId.value = ''
    historyLoading = false
    openUser = null
    pendingPermissionResolve?.({ outcome: { outcome: 'cancelled' } })
    permission.value = null
    if (status.value !== 'error') status.value = 'idle'
  }

  return {
    status,
    isBusy,
    errorMessage,
    items,
    permission,
    usage,
    agentLabel,
    sessionId,
    canLoadSession,
    connect,
    send,
    cancel,
    applyUpdate,
    finishStreaming,
    resetStream,
    disconnect,
    answerPermission,
  }
}
