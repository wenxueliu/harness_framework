<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Archive, ArrowLeft, FolderPlus, ShieldCheck, Trash2, UserPlus } from 'lucide-vue-next'
import AppShell from '@/layouts/AppShell.vue'
import { useCapabilityStore } from '@/stores/capabilities'
import { archiveProjectGroup, createProjectGroup, deleteProjectGroupMember, deleteProjectWorkspace, listProjectGroupMembers, listProjectGroups, listProjectWorkspaces, preflightProjectWorkspace, putProjectGroupMember, registerProjectWorkspace } from '@/api/dashboard'
import { createJobFlowExecutionProfile, getJobFlowExecutionProfile, listAgentRuntimes, listJobFlowExecutionProfiles, listMcpGrants, listSkillBundles, registerAgentRuntime, reportAgentRuntimeHealth, updateJobFlowExecutionProfile } from '@/api/jobFlow'
import type { ProjectGroup, ProjectGroupMember, ProjectWorkspace, WorkspacePreflight } from '@/api/types'
import type { ExecutionProfileSummary } from '@/api/jobFlow'

const capabilities = useCapabilityStore()
const route = useRoute()
const router = useRouter()
const groups = ref<ProjectGroup[]>([])
const selectedId = ref('')
const members = ref<ProjectGroupMember[]>([])
const workspaces = ref<ProjectWorkspace[]>([])
const preflights = ref<Record<string, WorkspacePreflight>>({})
const executionProfiles = ref<ExecutionProfileSummary[]>([])
const agentRuntimes = ref<{ runtime_id: string; name: string; status: string; version?: string; adapter_id?: string }[]>([])
const skillBundles = ref<{ bundle_id: string; name: string; version: string }[]>([])
const mcpGrants = ref<{ grant_id: string; server_id: string; status: string }[]>([])
const profileName = ref('')
const profileRuntimeId = ref('')
const POLICY_FIELDS = [
  { key: 'workspace_policy', label: 'Workspace 策略' },
  { key: 'resource_budget', label: '资源预算' },
  { key: 'network_policy', label: '网络策略' },
  { key: 'secret_policy', label: 'Secret 策略' },
] as const
type PolicyKey = (typeof POLICY_FIELDS)[number]['key']
const editingProfileId = ref('')
const profileDraftName = ref('')
const profileDraftVersion = ref('')
const profileDraftRuntimeId = ref('')
const profileDraftPolicies = ref<Record<PolicyKey, string>>({
  workspace_policy: '{}', resource_budget: '{}', network_policy: '{}', secret_policy: '{}',
})
const profileDraftSkillIds = ref<string[]>([])
const profileDraftMcpIds = ref<string[]>([])
const profileDraftRevision = ref(0)
const profileError = ref('')
const runtimeName = ref('')
const runtimeVersion = ref('')
const runtimeAdapter = ref('')
const runtimeError = ref('')
const error = ref('')
const workspaceError = ref('')
const busy = ref(false)
const groupName = ref('')
const groupDescription = ref('')
const memberSubject = ref('')
const memberRole = ref<ProjectGroupMember['role']>('EDITOR')
const workspaceName = ref('')
const workspaceSource = ref<'LOCAL_PATH' | 'GIT_CLONE'>('LOCAL_PATH')
const workspacePathType = ref<'RELATIVE_PATH' | 'ABSOLUTE_PATH'>('RELATIVE_PATH')
const rootAlias = ref('default')
const relativePath = ref('')
const absolutePath = ref('')
const gitUrl = ref('')
const defaultRef = ref('main')
const workspaceAccess = ref<'READ_ONLY' | 'READ_WRITE'>('READ_WRITE')
const selectedGroup = computed(() => groups.value.find((item) => item.group_id === selectedId.value))
const workspaceFormValid = computed(() => {
  if (workspaceSource.value === 'GIT_CLONE') {
    return Boolean(workspaceName.value.trim() && gitUrl.value.trim() && defaultRef.value.trim())
  }
  return Boolean(
    workspaceName.value.trim()
    && (workspacePathType.value === 'ABSOLUTE_PATH'
      ? absolutePath.value.trim()
      : rootAlias.value.trim() && relativePath.value.trim()),
  )
})

async function load() {
  error.value = ''
  await capabilities.load(selectedId.value || undefined)
  groups.value = (await listProjectGroups()).filter((group) => !group.virtual)
  // 支持 ?group=<groupId> 预选（项目页徽标跳转入口），组不存在时维持默认行为
  const preselect = typeof route.query.group === 'string' ? route.query.group : ''
  if (!selectedId.value && preselect && groups.value.some((group) => group.group_id === preselect)) {
    selectedId.value = preselect
  }
  if (!selectedId.value && groups.value.length) {
    // 优先选中 ACTIVE 组，避免落在归档组上导致各配置表单不可见
    selectedId.value = (groups.value.find((group) => group.status === 'ACTIVE') ?? groups.value[0]).group_id
  }
  if (selectedId.value) await selectGroup(selectedId.value)
}

async function selectGroup(groupId: string) {
  selectedId.value = groupId
  await capabilities.load(groupId)
  ;[members.value, workspaces.value, executionProfiles.value, agentRuntimes.value] = await Promise.all([
    listProjectGroupMembers(groupId), listProjectWorkspaces(groupId),
    listJobFlowExecutionProfiles(groupId), listAgentRuntimes(),
  ])
  if (editingProfileId.value) cancelEditProfile()
}

async function startEditProfile(profileId: string) {
  profileError.value = ''
  try {
    const profile = await getJobFlowExecutionProfile(profileId)
    editingProfileId.value = profileId
    profileDraftName.value = profile.name
    profileDraftVersion.value = profile.version
    profileDraftRuntimeId.value = profile.agent_runtime_id
    profileDraftRevision.value = profile.revision ?? 0
    for (const field of POLICY_FIELDS) {
      const value = (profile[field.key] as Record<string, unknown> | undefined) ?? {}
      profileDraftPolicies.value[field.key] = JSON.stringify(value, null, 2)
    }
    profileDraftSkillIds.value = [...(profile.skill_bundle_ids ?? [])]
    profileDraftMcpIds.value = [...(profile.mcp_grant_ids ?? [])]
    if (!skillBundles.value.length) skillBundles.value = await listSkillBundles()
    if (!mcpGrants.value.length) mcpGrants.value = await listMcpGrants(profile.group_id || selectedId.value)
  } catch (cause) { profileError.value = cause instanceof Error ? cause.message : 'Profile 加载失败' }
}

function cancelEditProfile() {
  editingProfileId.value = ''
  profileError.value = ''
}

async function saveProfile() {
  if (!editingProfileId.value) return
  const policies: Partial<Record<PolicyKey, Record<string, unknown>>> = {}
  for (const field of POLICY_FIELDS) {
    try { policies[field.key] = JSON.parse(profileDraftPolicies.value[field.key] || '{}') }
    catch { profileError.value = `${field.label}不是合法 JSON`; return }
  }
  try {
    await updateJobFlowExecutionProfile(editingProfileId.value, {
      expected_revision: profileDraftRevision.value,
      name: profileDraftName.value.trim(),
      version: profileDraftVersion.value.trim() || '1.0.0',
      agent_runtime_id: profileDraftRuntimeId.value,
      ...policies,
      skill_bundle_ids: [...profileDraftSkillIds.value],
      mcp_grant_ids: [...profileDraftMcpIds.value],
    })
    cancelEditProfile()
    if (selectedId.value) executionProfiles.value = await listJobFlowExecutionProfiles(selectedId.value)
  } catch (cause) {
    profileError.value = cause instanceof Error ? cause.message : '保存 Execution Profile 失败'
  }
}

async function createGroup() {
  if (!groupName.value.trim()) return
  busy.value = true; error.value = ''
  try {
    workspaceError.value = ''
    const group = await createProjectGroup(groupName.value.trim(), groupDescription.value.trim())
    groupName.value = ''; groupDescription.value = ''
    groups.value.push(group); await selectGroup(group.group_id)
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '项目组创建失败' }
  finally { busy.value = false }
}

async function addMember() {
  if (!selectedId.value || !memberSubject.value.trim()) return
  try {
    await putProjectGroupMember(selectedId.value, memberSubject.value.trim(), memberRole.value)
    memberSubject.value = ''; members.value = await listProjectGroupMembers(selectedId.value)
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '成员保存失败' }
}

async function removeMember(subject: string) {
  if (!selectedId.value) return
  try { await deleteProjectGroupMember(selectedId.value, subject); members.value = await listProjectGroupMembers(selectedId.value) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '成员删除失败' }
}

async function registerWorkspace() {
  if (!selectedId.value || !workspaceFormValid.value) {
    error.value = '请填写完整的 Workspace 配置，并确认目录路径有效'
    return
  }
  try {
    await registerProjectWorkspace(selectedId.value, {
      name: workspaceName.value.trim(), sourceType: workspaceSource.value,
      pathType: workspacePathType.value,
      rootAlias: workspacePathType.value === 'RELATIVE_PATH' ? rootAlias.value.trim() : undefined,
      relativePath: workspacePathType.value === 'RELATIVE_PATH' ? relativePath.value.trim() : undefined,
      absolutePath: workspacePathType.value === 'ABSOLUTE_PATH' ? absolutePath.value.trim() : undefined,
      gitUrl: gitUrl.value.trim(), defaultRef: defaultRef.value.trim(), access: workspaceAccess.value,
    })
    workspaceName.value = ''; relativePath.value = ''; absolutePath.value = ''; gitUrl.value = ''; workspaces.value = await listProjectWorkspaces(selectedId.value)
  } catch (cause) { error.value = cause instanceof Error ? cause.message : 'Workspace 登记失败' }
}

async function preflight(workspaceId: string) {
  try { preflights.value[workspaceId] = await preflightProjectWorkspace(workspaceId) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : 'Preflight 失败' }
}

async function removeWorkspace(workspaceId: string) {
  workspaceError.value = ''
  if (!confirm('确认删除此 Workspace？删除后不可恢复。')) return
  try {
    await deleteProjectWorkspace(workspaceId)
    if (selectedId.value) workspaces.value = await listProjectWorkspaces(selectedId.value)
  } catch (cause) {
    workspaceError.value = cause instanceof Error ? cause.message : '删除 Workspace 失败'
  }
}

async function createProfile() {
  if (!selectedId.value || !profileName.value.trim() || !profileRuntimeId.value) return
  try {
    await createJobFlowExecutionProfile({ group_id: selectedId.value, name: profileName.value.trim(), agent_runtime_id: profileRuntimeId.value })
    profileName.value = ''; profileRuntimeId.value = ''
    executionProfiles.value = await listJobFlowExecutionProfiles(selectedId.value)
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '创建 Execution Profile 失败' }
}

async function createRuntime() {
  if (!runtimeName.value.trim()) return
  runtimeError.value = ''
  try {
    await registerAgentRuntime({
      name: runtimeName.value.trim(),
      version: runtimeVersion.value.trim() || undefined,
      adapter_id: runtimeAdapter.value.trim() || undefined,
    })
    runtimeName.value = ''; runtimeVersion.value = ''; runtimeAdapter.value = ''
    agentRuntimes.value = await listAgentRuntimes()
  } catch (cause) { runtimeError.value = cause instanceof Error ? cause.message : '注册 Agent Runtime 失败' }
}

async function markRuntimeHealth(runtimeId: string, status: 'HEALTHY' | 'OFFLINE') {
  runtimeError.value = ''
  try {
    await reportAgentRuntimeHealth(runtimeId, status)
    agentRuntimes.value = await listAgentRuntimes()
  } catch (cause) { runtimeError.value = cause instanceof Error ? cause.message : '更新 Runtime 健康状态失败' }
}

async function archive() {
  if (!selectedGroup.value || !confirm(`确认归档项目组 ${selectedGroup.value.name}？`)) return
  try {
    const updated = await archiveProjectGroup(selectedGroup.value)
    groups.value = groups.value.map((item) => item.group_id === updated.group_id ? updated : item)
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '归档失败' }
}

interface PermissionMatrixRow {
  operation: string
  admin: boolean
  editor: boolean
  viewer: boolean
}

const PERMISSION_MATRIX: PermissionMatrixRow[] = [
  { operation: '管理项目组成员', admin: true, editor: false, viewer: false },
  { operation: '删除项目组', admin: true, editor: false, viewer: false },
  { operation: '配置 Workspace 保留策略', admin: true, editor: false, viewer: false },
  { operation: '配置 Execution Profile', admin: true, editor: false, viewer: false },
  { operation: '创建/编辑模板', admin: false, editor: true, viewer: false },
  { operation: '发布版本', admin: false, editor: true, viewer: false },
  { operation: '创建/启动/暂停/中止实例', admin: false, editor: true, viewer: false },
  { operation: '发起变更重执行', admin: false, editor: true, viewer: false },
  { operation: '查看模板/版本/实例/日志/产物', admin: true, editor: true, viewer: true },
]

const currentRole = computed<'ADMIN' | 'EDITOR' | 'VIEWER'>(() => {
  const permissions = capabilities.snapshot?.permissions ?? []
  if (permissions.includes('member:manage') || permissions.includes('group:manage')) return 'ADMIN'
  if (permissions.includes('run:create') || permissions.includes('workflow:draft')) return 'EDITOR'
  return 'VIEWER'
})

function roleCan(row: PermissionMatrixRow, role: 'ADMIN' | 'EDITOR' | 'VIEWER'): boolean {
  if (role === 'ADMIN') return row.admin
  if (role === 'EDITOR') return row.editor
  return row.viewer
}

onMounted(() => load().catch((cause) => { error.value = cause instanceof Error ? cause.message : '配置加载失败' }))
</script>

<template>
  <AppShell title="项目与工作区配置" eyebrow="Project Group Administration">
    <!-- 配置页返回项目首页 -->
    <button type="button" data-testid="settings-back" class="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
      @click="router.push('/')">
      <ArrowLeft :size="13" />返回首页
    </button>
    <section class="grid gap-4 p-4 sm:p-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
      <aside class="rounded-lg border border-border bg-card p-3">
        <h2 class="mb-2 text-xs font-semibold uppercase text-muted-foreground">项目组</h2>
        <button v-for="group in groups" :key="group.group_id" class="mb-1 w-full rounded px-2 py-2 text-left text-sm" :class="selectedId === group.group_id ? 'bg-blue-500/10 text-blue-300' : 'hover:bg-accent'" @click="selectGroup(group.group_id)"><span class="block truncate">{{ group.name }}</span><span class="font-mono text-[10px] text-muted-foreground">{{ group.status }}</span></button>
        <form v-if="capabilities.permitted('group:manage')" class="mt-4 space-y-2 border-t border-border pt-3" @submit.prevent="createGroup"><input v-model="groupName" required class="w-full rounded border border-border bg-background p-2 text-xs" placeholder="新项目组名称" /><input v-model="groupDescription" class="w-full rounded border border-border bg-background p-2 text-xs" placeholder="说明" /><button class="w-full rounded bg-blue-500 px-2 py-2 text-xs text-white disabled:opacity-50" :disabled="busy"><FolderPlus :size="12" class="mr-1 inline" />创建项目组</button></form>
      </aside>
      <div class="space-y-4">
        <div class="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card p-4"><ShieldCheck :size="16" class="text-emerald-300" /><strong class="text-sm">{{ capabilities.snapshot?.actor.display_name || '未知身份' }}</strong><span class="text-xs text-muted-foreground">{{ capabilities.snapshot?.mode }}</span><button v-if="selectedGroup?.status === 'ACTIVE' && capabilities.permitted('group:archive')" class="ml-auto flex items-center gap-1 rounded border border-red-400/30 px-2 py-1 text-xs text-red-300" @click="archive"><Archive :size="12" />归档</button></div>
        <p v-if="error" role="alert" class="rounded border border-red-400/30 bg-red-400/10 p-3 text-xs text-red-200">{{ error }}</p>
        <p v-if="workspaceError" data-testid="workspace-error" role="alert" class="rounded border border-red-400/30 bg-red-400/10 p-3 text-xs text-red-200">{{ workspaceError }}</p>
        <article class="rounded-lg border border-border bg-card p-4"><h2 class="mb-3 font-display text-sm font-semibold">成员与角色</h2><div v-for="member in members" :key="member.subject_id" class="flex items-center gap-2 border-b border-border/60 py-2 text-xs"><span class="min-w-0 flex-1 truncate font-mono">{{ member.subject_id }}</span><span class="text-blue-300">{{ member.role }}</span><button v-if="capabilities.permitted('member:manage')" class="text-red-300" @click="removeMember(member.subject_id)">移除</button></div><form v-if="capabilities.permitted('member:manage') && selectedGroup?.status === 'ACTIVE'" class="mt-3 flex flex-wrap gap-2" @submit.prevent="addMember"><input v-model="memberSubject" required class="min-w-48 flex-1 rounded border border-border bg-background p-2 text-xs" placeholder="subject ID" /><select v-model="memberRole" class="rounded border border-border bg-background p-2 text-xs"><option>ADMIN</option><option>EDITOR</option><option>VIEWER</option></select><button class="rounded bg-blue-500 px-3 text-xs text-white"><UserPlus :size="12" class="mr-1 inline" />添加</button></form></article>
        <article class="rounded-lg border border-border bg-card p-4"><h2 class="mb-3 font-display text-sm font-semibold">Project Workspaces</h2><div v-for="workspace in workspaces" :key="workspace.workspace_id" class="mb-2 rounded border border-border p-3 text-xs"><div class="flex gap-2"><strong>{{ workspace.name }}</strong><span class="font-mono text-muted-foreground">{{ workspace.root_ref }}</span><button class="ml-auto text-blue-300" @click="preflight(workspace.workspace_id)">Preflight</button><button v-if="capabilities.permitted('workspace:register') && selectedGroup?.status === 'ACTIVE'" class="flex items-center gap-1 text-red-300 hover:text-red-200" title="删除 Workspace" @click="removeWorkspace(workspace.workspace_id)"><Trash2 :size="12" /></button></div><p v-if="preflights[workspace.workspace_id]" class="mt-2 font-mono text-muted-foreground">read={{ preflights[workspace.workspace_id].readable }} · write={{ preflights[workspace.workspace_id].writable }} · git={{ preflights[workspace.workspace_id].git }} · dirty={{ preflights[workspace.workspace_id].dirty ?? 'n/a' }}</p></div><form v-if="capabilities.permitted('workspace:register') && selectedGroup?.status === 'ACTIVE'" class="mt-3 grid gap-2 border-t border-border pt-3 sm:grid-cols-2" @submit.prevent="registerWorkspace"><input v-model="workspaceName" required class="rounded border border-border bg-background p-2 text-xs" placeholder="Workspace 名称" /><select v-model="workspaceSource" class="rounded border border-border bg-background p-2 text-xs"><option value="LOCAL_PATH">本地目录</option><option value="GIT_CLONE">Git Clone</option></select><select v-model="workspaceAccess" class="rounded border border-border bg-background p-2 text-xs"><option value="READ_WRITE">读写</option><option value="READ_ONLY">只读</option></select><template v-if="workspaceSource === 'LOCAL_PATH'"><select v-model="workspacePathType" class="rounded border border-border bg-background p-2 text-xs"><option value="RELATIVE_PATH">相对路径（使用服务端根目录）</option><option value="ABSOLUTE_PATH">绝对路径（服务端目录）</option></select><template v-if="workspacePathType === 'RELATIVE_PATH'"><input v-model="rootAlias" required class="rounded border border-border bg-background p-2 font-mono text-xs" placeholder="服务端 root alias" /><input v-model="relativePath" required class="rounded border border-border bg-background p-2 font-mono text-xs" placeholder="相对目录，例如 harness" /></template><input v-else v-model="absolutePath" required class="rounded border border-border bg-background p-2 font-mono text-xs sm:col-span-2" placeholder="服务端绝对目录，例如 /srv/projects/my-app" /></template><template v-else><input v-model="gitUrl" required class="rounded border border-border bg-background p-2 font-mono text-xs sm:col-span-2" placeholder="https://git.example.com/org/repo.git" /><input v-model="defaultRef" required class="rounded border border-border bg-background p-2 font-mono text-xs" placeholder="默认 ref" /></template><p class="text-[11px] text-muted-foreground sm:col-span-2">本地目录必须存在，并且位于 Harness 服务端配置的允许根目录内。</p><button class="rounded bg-blue-500 px-3 py-2 text-xs text-white disabled:cursor-not-allowed disabled:opacity-50 sm:col-span-2" :disabled="!workspaceFormValid">登记 Workspace</button></form></article>
        <article class="rounded-lg border border-border bg-card p-4"><h2 class="mb-3 font-display text-sm font-semibold">Agent Runtimes</h2><div v-for="runtime in agentRuntimes" :key="runtime.runtime_id" class="mb-2 flex items-center gap-2 rounded border border-border p-3 text-xs"><strong>{{ runtime.name }}</strong><span class="font-mono text-muted-foreground">v{{ runtime.version || '0.0.0' }} · {{ runtime.runtime_id }}<template v-if="runtime.adapter_id"> · {{ runtime.adapter_id }}</template></span><span class="ml-auto flex items-center gap-2"><span class="rounded border border-border px-2 py-0.5 text-[10px]" :class="runtime.status === 'HEALTHY' ? 'text-emerald-300' : 'text-muted-foreground'">{{ runtime.status }}</span><button v-if="capabilities.permitted('runtime:manage')" class="text-blue-300 hover:text-blue-200" @click="markRuntimeHealth(runtime.runtime_id, runtime.status === 'HEALTHY' ? 'OFFLINE' : 'HEALTHY')">{{ runtime.status === 'HEALTHY' ? '标记 OFFLINE' : '标记 HEALTHY' }}</button></span></div><p v-if="!agentRuntimes.length" class="text-xs text-muted-foreground">尚无 Agent Runtime，先注册一个（Execution Profile 依赖它）。</p><p v-if="runtimeError" role="alert" class="mt-2 rounded border border-red-400/30 bg-red-400/10 p-2 text-xs text-red-200">{{ runtimeError }}</p><form v-if="capabilities.permitted('runtime:manage')" class="mt-3 grid gap-2 border-t border-border pt-3 sm:grid-cols-3" @submit.prevent="createRuntime"><input v-model="runtimeName" required class="rounded border border-border bg-background p-2 text-xs" placeholder="Runtime 名称，例如 Claude ACP" /><input v-model="runtimeVersion" class="rounded border border-border bg-background p-2 text-xs" placeholder="版本，例如 1.0.0" /><input v-model="runtimeAdapter" class="rounded border border-border bg-background p-2 text-xs" placeholder="adapter，例如 claude-code / codex" /><button class="rounded bg-blue-500 px-3 py-2 text-xs text-white sm:col-span-3" :disabled="!runtimeName.trim()">注册 Agent Runtime</button></form><p v-else class="mt-3 border-t border-border pt-3 text-xs text-muted-foreground">注册 Runtime 需要 runtime:manage 权限（ADMIN 角色），可通过 POST /api/agent-runtimes 完成。</p></article>
        <article class="rounded-lg border border-border bg-card p-4"><h2 class="mb-3 font-display text-sm font-semibold">Execution Profiles</h2><div v-for="profile in executionProfiles" :key="profile.profile_id" class="mb-2 rounded border border-border p-3 text-xs"><div class="flex items-center gap-2"><strong>{{ profile.name }}</strong><span class="font-mono text-muted-foreground">v{{ profile.version }} · {{ profile.agent_runtime_id }}</span><span class="ml-auto rounded border border-border px-2 py-0.5 text-[10px]">{{ profile.status }}</span><button v-if="capabilities.permitted('profile:manage') && selectedGroup?.status === 'ACTIVE'" class="text-blue-300 hover:text-blue-200" data-testid="profile-edit" @click="editingProfileId === profile.profile_id ? cancelEditProfile() : startEditProfile(profile.profile_id)">{{ editingProfileId === profile.profile_id ? '收起' : '编辑' }}</button></div><p v-if="profile.skill_bundle_ids?.length" class="mt-1 text-muted-foreground">Skill Bundles: <span class="font-mono">{{ profile.skill_bundle_ids.join(', ') }}</span></p><p v-if="profile.mcp_grant_ids?.length" class="mt-1 text-muted-foreground">MCP Grants: <span class="font-mono">{{ profile.mcp_grant_ids.join(', ') }}</span></p><div v-if="editingProfileId === profile.profile_id" data-testid="profile-editor" class="mt-3 space-y-3 border-t border-border pt-3"><p v-if="profileError" role="alert" class="rounded border border-red-400/30 bg-red-400/10 p-2 text-red-200">{{ profileError }}</p><div class="grid gap-2 sm:grid-cols-3"><input v-model="profileDraftName" required class="rounded border border-border bg-background p-2 text-xs" placeholder="Profile 名称" /><input v-model="profileDraftVersion" class="rounded border border-border bg-background p-2 text-xs" placeholder="版本，例如 1.0.0" /><select v-model="profileDraftRuntimeId" required class="rounded border border-border bg-background p-2 text-xs"><option value="" disabled>选择 Agent Runtime</option><option v-for="rt in agentRuntimes" :key="rt.runtime_id" :value="rt.runtime_id">{{ rt.name }} ({{ rt.status }})</option></select></div><div class="grid gap-2 sm:grid-cols-2"><label v-for="field in POLICY_FIELDS" :key="field.key" class="block"><span class="mb-1 block text-muted-foreground">{{ field.label }}（JSON）</span><textarea v-model="profileDraftPolicies[field.key]" rows="4" class="w-full rounded border border-border bg-background p-2 font-mono text-xs" :data-testid="'profile-policy-' + field.key"></textarea></label></div><div class="grid gap-2 sm:grid-cols-2"><fieldset class="rounded border border-border p-2"><legend class="px-1 text-muted-foreground">Skill Bundles</legend><p v-if="!skillBundles.length" class="text-muted-foreground">暂无可选 Bundle，可先在平台上注册。</p><label v-for="bundle in skillBundles" :key="bundle.bundle_id" class="flex items-center gap-2 py-0.5"><input type="checkbox" :value="bundle.bundle_id" v-model="profileDraftSkillIds" /><span class="font-mono">{{ bundle.name }} v{{ bundle.version }}</span></label></fieldset><fieldset class="rounded border border-border p-2"><legend class="px-1 text-muted-foreground">MCP Grants</legend><p v-if="!mcpGrants.length" class="text-muted-foreground">该项目组暂无 MCP Grant。</p><label v-for="grant in mcpGrants" :key="grant.grant_id" class="flex items-center gap-2 py-0.5"><input type="checkbox" :value="grant.grant_id" v-model="profileDraftMcpIds" /><span class="font-mono">{{ grant.grant_id }} ({{ grant.server_id }})</span><span v-if="grant.status !== 'ACTIVE'" class="text-[10px] text-red-300">{{ grant.status }}</span></label></fieldset></div><div class="flex gap-2"><button class="rounded bg-blue-500 px-3 py-2 text-xs text-white" data-testid="profile-save" @click="saveProfile">保存配置</button><button class="rounded border border-border px-3 py-2 text-xs" @click="cancelEditProfile">取消</button></div></div></div><p v-if="!executionProfiles.length && capabilities.permitted('profile:manage') && selectedGroup?.status === 'ACTIVE'" class="text-xs text-muted-foreground">暂无 Execution Profile，先创建一个。</p>
<p v-else-if="!executionProfiles.length" class="text-xs text-muted-foreground">暂无 Execution Profile。{{ !selectedGroup ? '请先在左侧选择一个项目组。' : selectedGroup.status !== 'ACTIVE' ? `当前组状态为 ${selectedGroup.status}，请选择 ACTIVE 状态的项目组后创建。` : '需要 profile:manage 权限（ADMIN 角色）。' }}</p><form v-if="capabilities.permitted('profile:manage') && selectedGroup?.status === 'ACTIVE'" class="mt-3 grid gap-2 border-t border-border pt-3 sm:grid-cols-2" @submit.prevent="createProfile"><input v-model="profileName" required class="rounded border border-border bg-background p-2 text-xs" placeholder="Profile 名称" /><select v-model="profileRuntimeId" required class="rounded border border-border bg-background p-2 text-xs"><option value="" disabled>选择 Agent Runtime</option><option v-for="rt in agentRuntimes" :key="rt.runtime_id" :value="rt.runtime_id">{{ rt.name }} ({{ rt.status }})</option></select><button class="rounded bg-blue-500 px-3 py-2 text-xs text-white sm:col-span-2" :disabled="!profileName.trim() || !profileRuntimeId">创建 Execution Profile</button></form></article>
        <article data-testid="permission-matrix" class="rounded-lg border border-border bg-card p-4">
          <div class="mb-3 flex items-center gap-2">
            <h2 class="font-display text-sm font-semibold">权限矩阵</h2>
            <span class="rounded-full border border-border px-2 py-0.5 text-[10px] text-muted-foreground">只读 · 角色固定，成员分配通过项目管理完成</span>
            <span class="ml-auto text-xs text-muted-foreground">当前角色：<strong class="text-blue-300">{{ currentRole }}</strong></span>
          </div>
          <div class="overflow-x-auto">
            <table class="w-full min-w-[28rem] text-left text-xs">
              <thead class="border-b border-border text-muted-foreground">
                <tr>
                  <th class="px-3 py-2 font-medium">操作</th>
                  <th class="px-3 py-2 font-medium" :class="currentRole === 'ADMIN' ? 'text-blue-300' : ''">Admin<sup v-if="currentRole === 'ADMIN'"> ← 当前</sup></th>
                  <th class="px-3 py-2 font-medium" :class="currentRole === 'EDITOR' ? 'text-blue-300' : ''">Editor<sup v-if="currentRole === 'EDITOR'"> ← 当前</sup></th>
                  <th class="px-3 py-2 font-medium" :class="currentRole === 'VIEWER' ? 'text-blue-300' : ''">Viewer<sup v-if="currentRole === 'VIEWER'"> ← 当前</sup></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in PERMISSION_MATRIX" :key="row.operation"
                  class="border-b border-border/60"
                  :class="roleCan(row, currentRole) ? 'bg-blue-400/5' : ''">
                  <td class="px-3 py-2">
                    {{ row.operation }}
                    <span v-if="roleCan(row, currentRole)" class="ml-1.5 rounded bg-blue-400/10 px-1.5 py-0.5 text-[10px] text-blue-300">你有权限</span>
                  </td>
                  <td class="px-3 py-2" :class="row.admin ? 'text-emerald-300' : 'text-muted-foreground'">{{ row.admin ? '✅' : '—' }}</td>
                  <td class="px-3 py-2" :class="row.editor ? 'text-emerald-300' : 'text-muted-foreground'">{{ row.editor ? '✅' : '—' }}</td>
                  <td class="px-3 py-2" :class="row.viewer ? 'text-emerald-300' : 'text-muted-foreground'">{{ row.viewer ? '✅' : '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </article>
      </div>
    </section>
  </AppShell>
</template>
