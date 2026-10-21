<script setup lang="ts">
/**
 * ACP 对话页面 — 浏览器直连 ACP Agent 的 ChatGPT 式界面。
 *
 * 数据链路：浏览器 WebSocket → server/acpBridge.ts → ACP Agent stdio。
 * 页面只消费 `useAcpChat` 暴露的 items 列表，不直接接触协议细节。
 */
import { Plug, Send, Square, Unplug } from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppShell from '@/layouts/AppShell.vue'
import AcpConversation from '@/components/AcpConversation.vue'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useAcpChat } from '@/lib/acp/useAcpChat'

const DEFAULT_BRIDGE = 'ws://127.0.0.1:8787/acp'
const SETTINGS_KEY = 'acp-chat:settings'
const SESSION_KEY = 'acp-chat:session-key'

/** 桥接 /health 返回的单个 Agent 探测结果。 */
type AgentHealth = {
  name: string
  available: boolean
  missing: string | null
  install: string | null
}

const FALLBACK_AGENTS = ['opencode', 'claude', 'codex']

const {
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
  disconnect,
  answerPermission,
} = useAcpChat()

const draft = ref('')
const bridgeUrl = ref(DEFAULT_BRIDGE)
const agentName = ref('opencode')
const cwd = ref('')
const sessionKey = ref('')

/** 桥接主机上各 Agent 的依赖探测结果，用于提前提示安装。 */
const agentHealth = ref<AgentHealth[]>([])
const healthError = ref('')
const healthChecking = ref(false)

/** ws://…/acp → http://…/health，同一个桥接进程。 */
function healthUrl(target: string): string | null {
  try {
    const url = new URL(target)
    url.protocol = url.protocol === 'wss:' ? 'https:' : 'http:'
    url.pathname = '/health'
    url.search = ''
    return url.toString()
  } catch {
    return null
  }
}

/**
 * 探测桥接主机是否装好了各 Agent 的依赖 CLI。
 *
 * 装好了才允许连接；没装就地把安装命令显示出来，省得用户只能看到
 * 一句 `spawn ENOENT`。
 */
async function loadAgentHealth(): Promise<void> {
  const target = healthUrl(bridgeUrl.value.trim())
  if (!target) {
    healthError.value = '桥接地址无法解析，应以 ws:// 或 wss:// 开头。'
    return
  }
  healthChecking.value = true
  try {
    const response = await fetch(target)
    const payload = (await response.json()) as { agents?: AgentHealth[] }
    agentHealth.value = Array.isArray(payload.agents) ? payload.agents : []
    healthError.value = ''
    // 选中的 Agent 不见了（或不可用）时，退回到第一个可用的。
    if (!agentHealth.value.some((agent) => agent.name === agentName.value && agent.available)) {
      const firstAvailable = agentHealth.value.find((agent) => agent.available)
      if (firstAvailable) agentName.value = firstAvailable.name
    }
  } catch (error) {
    agentHealth.value = []
    healthError.value = `无法读取桥接 /health：${error instanceof Error ? error.message : String(error)}`
  } finally {
    healthChecking.value = false
  }
}

/** 探测成功则用真实结果，探测失败则退回静态候选列表。 */
const agentOptions = computed<AgentHealth[]>(() =>
  agentHealth.value.length
    ? agentHealth.value
    : FALLBACK_AGENTS.map((name) => ({ name, available: true, missing: null, install: null })),
)

const selectedAgent = computed(() => agentHealth.value.find((agent) => agent.name === agentName.value) ?? null)
const agentUnavailable = computed(() => selectedAgent.value !== null && !selectedAgent.value.available)

onMounted(() => {
  const saved = localStorage.getItem(SETTINGS_KEY)
  if (saved) {
    try {
      const parsed = JSON.parse(saved) as Partial<{ bridgeUrl: string; agentName: string; cwd: string }>
      if (parsed.bridgeUrl) bridgeUrl.value = parsed.bridgeUrl
      if (parsed.agentName) agentName.value = parsed.agentName
      if (parsed.cwd) cwd.value = parsed.cwd
    } catch {
      localStorage.removeItem(SETTINGS_KEY)
    }
  }
  let key = sessionStorage.getItem(SESSION_KEY)
  if (!key) {
    key = `web-${Math.random().toString(36).slice(2, 10)}`
    sessionStorage.setItem(SESSION_KEY, key)
  }
  sessionKey.value = key
  void loadAgentHealth()
})

// 桥接地址变了就重新探测，避免用旧的探测结果禁用连接按钮。
watch(bridgeUrl, () => {
  void loadAgentHealth()
})

onBeforeUnmount(() => {
  void disconnect()
})

async function openConnection(): Promise<void> {
  localStorage.setItem(
    SETTINGS_KEY,
    JSON.stringify({ bridgeUrl: bridgeUrl.value, agentName: agentName.value, cwd: cwd.value }),
  )
  await connect({
    bridgeUrl: bridgeUrl.value.trim(),
    agent: agentName.value,
    cwd: cwd.value.trim(),
    sessionKey: sessionKey.value,
  })
}

async function submit(): Promise<void> {
  const text = draft.value
  if (!text.trim() || isBusy.value) return
  draft.value = ''
  await send(text)
}

const connected = computed(() => status.value !== 'idle' && status.value !== 'error')

const statusLabel = computed(() => {
  switch (status.value) {
    case 'connecting': return '连接中'
    case 'ready': return '就绪'
    case 'busy': return '执行中'
    case 'error': return '异常'
    default: return '未连接'
  }
})

const statusTone = computed(() => {
  switch (status.value) {
    case 'ready': return 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'
    case 'busy': return 'bg-blue-500/15 text-blue-300 border-blue-500/30'
    case 'error': return 'bg-red-500/15 text-red-300 border-red-500/30'
    case 'connecting': return 'bg-amber-500/15 text-amber-300 border-amber-500/30'
    default: return 'bg-muted text-muted-foreground border-border'
  }
})

/** ACP 工具状态 → ai-elements 期望的 AI SDK 状态。 */
</script>

<template>
  <AppShell title="ACP 对话" eyebrow="Agent Client Protocol">
    <template #actions>
      <span class="rounded-md border px-2 py-1 font-mono text-[11px]" :class="statusTone">{{ statusLabel }}</span>
      <Badge v-if="agentLabel" variant="secondary" class="hidden sm:inline-flex">{{ agentLabel }}</Badge>
    </template>

    <template #sidebar>
      <div class="space-y-4 p-4 text-xs">
        <div class="space-y-2">
          <label class="block font-medium">桥接地址</label>
          <input
            v-model="bridgeUrl"
            spellcheck="false"
            class="w-full rounded border border-border bg-background px-2 py-1.5 font-mono text-[11px]"
            placeholder="ws://127.0.0.1:8787/acp"
          />
        </div>

        <div class="space-y-2">
          <div class="flex items-center justify-between gap-2">
            <label class="block font-medium">Agent</label>
            <button
              type="button"
              class="text-[10px] text-muted-foreground underline-offset-2 hover:underline disabled:opacity-60"
              :disabled="healthChecking"
              @click="loadAgentHealth()"
            >
              {{ healthChecking ? '检测中…' : '重新检测' }}
            </button>
          </div>
          <select v-model="agentName" class="w-full rounded border border-border bg-background px-2 py-1.5">
            <option
              v-for="agent in agentOptions"
              :key="agent.name"
              :value="agent.name"
              :disabled="!agent.available"
            >
              {{ agent.name }}{{ agent.available ? '' : '（未安装）' }}
            </option>
          </select>
          <p
            v-if="agentUnavailable && selectedAgent"
            class="rounded border border-amber-500/30 bg-amber-500/5 p-2 text-[10px] leading-relaxed text-amber-300"
          >
            桥接主机上未找到 <code class="font-mono">{{ selectedAgent.missing }}</code>，该 Agent 不可用。安装：
            <code class="mt-1 block font-mono text-amber-200">{{ selectedAgent.install }}</code>
          </p>
          <p v-else-if="healthError" class="text-[10px] leading-relaxed text-amber-400">{{ healthError }}</p>
        </div>

        <div class="space-y-2">
          <label class="block font-medium">工作目录（Agent 主机绝对路径）</label>
          <input
            v-model="cwd"
            spellcheck="false"
            class="w-full rounded border border-border bg-background px-2 py-1.5 font-mono text-[11px]"
            placeholder="/abs/path/to/project"
          />
          <p class="text-[10px] leading-relaxed text-muted-foreground">
            浏览器没有文件系统，该路径在桥接进程所在主机上解析。
          </p>
        </div>

        <Button v-if="!connected" size="sm" class="w-full" :disabled="!cwd.trim() || status === 'connecting' || agentUnavailable" @click="openConnection">
          <Plug :size="13" /> 连接
        </Button>
        <Button v-else size="sm" variant="outline" class="w-full" @click="disconnect()">
          <Unplug :size="13" /> 断开
        </Button>

        <dl class="space-y-1 border-t border-border pt-3 text-[11px]">
          <div class="flex justify-between gap-2">
            <dt class="text-muted-foreground">sessionId</dt>
            <dd class="truncate font-mono">{{ sessionId || '—' }}</dd>
          </div>
          <div class="flex justify-between gap-2">
            <dt class="text-muted-foreground">支持恢复</dt>
            <dd class="font-mono">{{ canLoadSession ? 'yes' : 'no' }}</dd>
          </div>
          <div class="flex justify-between gap-2">
            <dt class="text-muted-foreground">上下文</dt>
            <dd class="font-mono">{{ usage ? `${usage.used}/${usage.size}` : '—' }}</dd>
          </div>
          <div class="flex justify-between gap-2">
            <dt class="text-muted-foreground">桥接会话键</dt>
            <dd class="truncate font-mono">{{ sessionKey }}</dd>
          </div>
        </dl>

        <Alert variant="default" class="text-[11px]">
          <AlertDescription class="text-[11px]">
            先在终端启动桥接：<code class="font-mono">npm run acp</code>，再点连接。
          </AlertDescription>
        </Alert>
      </div>
    </template>

    <div class="flex h-[calc(100vh-3.5rem)] flex-col">
      <Alert v-if="errorMessage" variant="destructive" class="m-3 shrink-0">
        <AlertDescription>{{ errorMessage }}</AlertDescription>
      </Alert>

      <!-- 小屏幕下没有侧栏，连接表单内联展示 -->
      <div v-if="!connected" class="border-b border-border p-4 lg:hidden">
        <div class="mx-auto max-w-md space-y-3 text-xs">
          <div class="space-y-1.5">
            <label class="block font-medium">桥接地址</label>
            <input
              v-model="bridgeUrl"
              spellcheck="false"
              class="w-full rounded border border-border bg-background px-2 py-1.5 font-mono text-[11px]"
            />
          </div>
          <div class="space-y-1.5">
            <label class="block font-medium">Agent</label>
            <select v-model="agentName" class="w-full rounded border border-border bg-background px-2 py-1.5">
              <option
                v-for="agent in agentOptions"
                :key="agent.name"
                :value="agent.name"
                :disabled="!agent.available"
              >
                {{ agent.name }}{{ agent.available ? '' : '（未安装）' }}
              </option>
            </select>
            <p
              v-if="agentUnavailable && selectedAgent"
              class="rounded border border-amber-500/30 bg-amber-500/5 p-2 text-[10px] leading-relaxed text-amber-300"
            >
              桥接主机上未找到 <code class="font-mono">{{ selectedAgent.missing }}</code>，该 Agent 不可用。安装：
              <code class="mt-1 block font-mono text-amber-200">{{ selectedAgent.install }}</code>
            </p>
          </div>
          <div class="space-y-1.5">
            <label class="block font-medium">工作目录（Agent 主机绝对路径）</label>
            <input
              v-model="cwd"
              spellcheck="false"
              class="w-full rounded border border-border bg-background px-2 py-1.5 font-mono text-[11px]"
              placeholder="/abs/path/to/project"
            />
          </div>
          <Button size="sm" class="w-full" :disabled="!cwd.trim() || status === 'connecting' || agentUnavailable" @click="openConnection">
            <Plug :size="13" /> 连接
          </Button>
          <p class="text-[10px] leading-relaxed text-muted-foreground">
            先启动桥接：<code class="font-mono">npm run acp</code>
          </p>
        </div>
      </div>

      <AcpConversation
        v-if="connected"
        :items="items"
        :permission="permission"
        empty-description="在左侧填写工作目录并连接，然后开始对话。"
        @answer-permission="answerPermission"
      />

      <!-- 输入区 -->
      <form class="shrink-0 border-t border-border p-3" @submit.prevent="submit">
        <div class="flex items-end gap-2">
          <Textarea
            v-model="draft"
            :disabled="!connected"
            placeholder="输入指令，Enter 发送，Shift+Enter 换行"
            class="max-h-40 min-h-16 flex-1 resize-none"
            @keydown.enter.exact.prevent="submit"
          />
          <div class="flex shrink-0 flex-col gap-2">
            <Button type="submit" :disabled="isBusy || !draft.trim() || !connected">
              <Send :size="13" /> 发送
            </Button>
            <Button v-if="isBusy" type="button" variant="secondary" @click="cancel()">
              <Square :size="13" /> 停止
            </Button>
          </div>
        </div>
      </form>
    </div>
  </AppShell>
</template>
