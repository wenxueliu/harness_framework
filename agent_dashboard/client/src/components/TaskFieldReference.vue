<script setup lang="ts">
/** 节点定义 JSON 支持的字段说明。 */
const fields: { name: string; type: string; required: string; desc: string }[] = [
  { name: 'id', type: 'string', required: '必填', desc: '节点唯一标识，依赖里用它引用' },
  { name: 'name', type: 'string', required: '可选', desc: '看板显示名' },
  { name: 'type', type: 'string', required: '可选', desc: 'design / review / backend / test / deploy，决定默认 agent 路由' },
  { name: 'agent', type: 'string', required: '可选', desc: 'claude / codex / opencode，优先于 type 路由' },
  { name: 'depends_on', type: 'string[]', required: '可选', desc: '前置节点 id 数组；全部 DONE 后本节点才会被调度' },
  { name: 'do', type: 'string', required: '与 description 二选一', desc: '这一步要做什么（发给 Agent 的执行指令）' },
  { name: 'description', type: 'string', required: '与 do 二选一', desc: '人类可读说明；do 存在时以 do 为准' },
  { name: 'input', type: 'string', required: '可选', desc: '这一步消费什么；声明后上游产物会自动带进提示词' },
  { name: 'output', type: 'string', required: '可选', desc: '这一步要产出什么；声明后才会带给下游' },
  { name: 'output_format', type: 'object', required: '可选', desc: '产物格式校验（框架侧）：file / json / marker / text；不符即视为节点失败' },
  { name: 'fail_group', type: 'string', required: '可选', desc: '共享失败预算的验证组（同一组必须用相同 on_fail）' },
  { name: 'on_fail', type: 'object', required: '可选', desc: '{task, max_fail_count}：失败时回退到哪个节点重跑' },
  { name: 'require_approval', type: 'boolean', required: '可选', desc: '需要人工批准才执行（批准只对本次有效）' },
  { name: 'require_verdict', type: 'boolean', required: '可选', desc: '要求 Agent 给出 VERDICT: PASS 结论' },
  { name: 'acp', type: 'object', required: '可选', desc: '{agent, cwd, permission_policy, session}：覆盖 agent、工作目录、权限与会话策略' },
  { name: 'context_inputs', type: 'string[]', required: '可选', desc: '允许注入的上下文 selector：facts / artifacts / summaries / working_memory' },
]

const formats = [
  { kind: 'file', example: '{"kind":"file","path":"result.md"}', desc: '产物文件必须存在且非空' },
  { kind: 'json', example: '{"kind":"json","required":["summary"],"types":{"summary":"string"}}', desc: '输出里的 JSON 按字段校验' },
  { kind: 'marker', example: '{"kind":"marker","pattern":"VERDICT:\\\\s*PASS"}', desc: '输出必须匹配正则' },
  { kind: 'text', example: '{"kind":"text","min_length":200}', desc: '输出长度下限' },
]

const rules = [
  'input / output 都是可选字段，只影响提示词内容，不影响调度',
  '配置 on_fail → 必须声明 output_format，且 on_fail.task 必须存在',
  '格式校验不通过 = 该节点失败 → 触发 on_fail 回退上游重跑（最多 max_fail_count 次）',
]
</script>

<template>
  <details class="mt-2 rounded border border-border bg-background/40 px-3 py-2 text-xs">
    <summary class="cursor-pointer select-none text-muted-foreground">节点字段说明（可配置字段与约束）</summary>
    <div class="mt-3 space-y-3">
      <table class="w-full table-fixed border-collapse text-left">
        <thead>
          <tr class="text-muted-foreground">
            <th class="w-32 py-1 font-medium">字段</th>
            <th class="w-36 py-1 font-medium">类型</th>
            <th class="w-40 py-1 font-medium">必填</th>
            <th class="py-1 font-medium">说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="field in fields" :key="field.name" class="align-top">
            <td class="py-1 font-mono text-[11px] text-blue-300">{{ field.name }}</td>
            <td class="py-1 font-mono text-[11px] text-muted-foreground">{{ field.type }}</td>
            <td class="py-1 text-[11px]" :class="field.required === '可选' ? 'text-muted-foreground' : 'text-amber-300'">{{ field.required }}</td>
            <td class="py-1 text-[11px] text-muted-foreground">{{ field.desc }}</td>
          </tr>
        </tbody>
      </table>

      <div>
        <h3 class="mb-1 font-medium">output_format 四种格式</h3>
        <ul class="space-y-1 text-[11px] text-muted-foreground">
          <li v-for="format in formats" :key="format.kind">
            <span class="font-mono text-blue-300">{{ format.kind }}</span>
            · {{ format.desc }}
            <code class="ml-1 font-mono text-[10px]">{{ format.example }}</code>
          </li>
        </ul>
      </div>

      <div>
        <h3 class="mb-1 font-medium">校验规则</h3>
        <ul class="list-disc space-y-0.5 pl-4 text-[11px] text-muted-foreground">
          <li v-for="rule in rules" :key="rule">{{ rule }}</li>
        </ul>
      </div>
    </div>
  </details>
</template>
