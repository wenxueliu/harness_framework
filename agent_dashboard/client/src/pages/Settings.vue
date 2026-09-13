<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Archive, FolderPlus, ShieldCheck, UserPlus } from 'lucide-vue-next'
import AppShell from '@/layouts/AppShell.vue'
import { useCapabilityStore } from '@/stores/capabilities'
import { archiveProjectGroup, createProjectGroup, deleteProjectGroupMember, listProjectGroupMembers, listProjectGroups, listProjectWorkspaces, preflightProjectWorkspace, putProjectGroupMember, registerProjectWorkspace } from '@/api/dashboard'
import type { ProjectGroup, ProjectGroupMember, ProjectWorkspace, WorkspacePreflight } from '@/api/types'

const capabilities = useCapabilityStore()
const groups = ref<ProjectGroup[]>([])
const selectedId = ref('')
const members = ref<ProjectGroupMember[]>([])
const workspaces = ref<ProjectWorkspace[]>([])
const preflights = ref<Record<string, WorkspacePreflight>>({})
const error = ref('')
const busy = ref(false)
const groupName = ref('')
const groupDescription = ref('')
const memberSubject = ref('')
const memberRole = ref<ProjectGroupMember['role']>('DEVELOPER')
const workspaceName = ref('')
const workspaceSource = ref<'LOCAL_PATH' | 'GIT_CLONE'>('LOCAL_PATH')
const rootAlias = ref('default')
const relativePath = ref('')
const gitUrl = ref('')
const defaultRef = ref('main')
const workspaceAccess = ref<'READ_ONLY' | 'READ_WRITE'>('READ_WRITE')
const selectedGroup = computed(() => groups.value.find((item) => item.group_id === selectedId.value))

async function load() {
  error.value = ''
  await capabilities.load(selectedId.value || undefined)
  groups.value = (await listProjectGroups()).filter((group) => !group.virtual)
  if (!selectedId.value && groups.value.length) selectedId.value = groups.value[0].group_id
  if (selectedId.value) await selectGroup(selectedId.value)
}

async function selectGroup(groupId: string) {
  selectedId.value = groupId
  await capabilities.load(groupId)
  ;[members.value, workspaces.value] = await Promise.all([
    listProjectGroupMembers(groupId), listProjectWorkspaces(groupId),
  ])
}

async function createGroup() {
  if (!groupName.value.trim()) return
  busy.value = true; error.value = ''
  try {
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
  if (!selectedId.value || !workspaceName.value.trim() || !relativePath.value.trim()) return
  try {
    await registerProjectWorkspace(selectedId.value, { name: workspaceName.value.trim(), sourceType: workspaceSource.value, rootAlias: rootAlias.value.trim(), relativePath: relativePath.value.trim(), gitUrl: gitUrl.value.trim(), defaultRef: defaultRef.value.trim(), access: workspaceAccess.value })
    workspaceName.value = ''; relativePath.value = ''; gitUrl.value = ''; workspaces.value = await listProjectWorkspaces(selectedId.value)
  } catch (cause) { error.value = cause instanceof Error ? cause.message : 'Workspace 登记失败' }
}

async function preflight(workspaceId: string) {
  try { preflights.value[workspaceId] = await preflightProjectWorkspace(workspaceId) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : 'Preflight 失败' }
}

async function archive() {
  if (!selectedGroup.value || !confirm(`确认归档项目组 ${selectedGroup.value.name}？`)) return
  try {
    const updated = await archiveProjectGroup(selectedGroup.value)
    groups.value = groups.value.map((item) => item.group_id === updated.group_id ? updated : item)
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '归档失败' }
}

onMounted(() => load().catch((cause) => { error.value = cause instanceof Error ? cause.message : '配置加载失败' }))
</script>

<template>
  <AppShell title="项目与工作区配置" eyebrow="Project Group Administration">
    <section class="grid gap-4 p-4 sm:p-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
      <aside class="rounded-lg border border-border bg-card p-3">
        <h2 class="mb-2 text-xs font-semibold uppercase text-muted-foreground">项目组</h2>
        <button v-for="group in groups" :key="group.group_id" class="mb-1 w-full rounded px-2 py-2 text-left text-sm" :class="selectedId === group.group_id ? 'bg-blue-500/10 text-blue-300' : 'hover:bg-accent'" @click="selectGroup(group.group_id)"><span class="block truncate">{{ group.name }}</span><span class="font-mono text-[10px] text-muted-foreground">{{ group.status }}</span></button>
        <form v-if="capabilities.permitted('group:manage')" class="mt-4 space-y-2 border-t border-border pt-3" @submit.prevent="createGroup"><input v-model="groupName" required class="w-full rounded border border-border bg-background p-2 text-xs" placeholder="新项目组名称" /><input v-model="groupDescription" class="w-full rounded border border-border bg-background p-2 text-xs" placeholder="说明" /><button class="w-full rounded bg-blue-500 px-2 py-2 text-xs text-white disabled:opacity-50" :disabled="busy"><FolderPlus :size="12" class="mr-1 inline" />创建项目组</button></form>
      </aside>
      <div class="space-y-4">
        <div class="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card p-4"><ShieldCheck :size="16" class="text-emerald-300" /><strong class="text-sm">{{ capabilities.snapshot?.actor.display_name || '未知身份' }}</strong><span class="text-xs text-muted-foreground">{{ capabilities.snapshot?.mode }}</span><button v-if="selectedGroup?.status === 'ACTIVE' && capabilities.permitted('group:archive')" class="ml-auto flex items-center gap-1 rounded border border-red-400/30 px-2 py-1 text-xs text-red-300" @click="archive"><Archive :size="12" />归档</button></div>
        <p v-if="error" role="alert" class="rounded border border-red-400/30 bg-red-400/10 p-3 text-xs text-red-200">{{ error }}</p>
        <article class="rounded-lg border border-border bg-card p-4"><h2 class="mb-3 font-display text-sm font-semibold">成员与角色</h2><div v-for="member in members" :key="member.subject_id" class="flex items-center gap-2 border-b border-border/60 py-2 text-xs"><span class="min-w-0 flex-1 truncate font-mono">{{ member.subject_id }}</span><span class="text-blue-300">{{ member.role }}</span><button v-if="capabilities.permitted('member:manage')" class="text-red-300" @click="removeMember(member.subject_id)">移除</button></div><form v-if="capabilities.permitted('member:manage') && selectedGroup?.status === 'ACTIVE'" class="mt-3 flex flex-wrap gap-2" @submit.prevent="addMember"><input v-model="memberSubject" required class="min-w-48 flex-1 rounded border border-border bg-background p-2 text-xs" placeholder="subject ID" /><select v-model="memberRole" class="rounded border border-border bg-background p-2 text-xs"><option>OWNER</option><option>MAINTAINER</option><option>DEVELOPER</option><option>VIEWER</option></select><button class="rounded bg-blue-500 px-3 text-xs text-white"><UserPlus :size="12" class="mr-1 inline" />添加</button></form></article>
        <article class="rounded-lg border border-border bg-card p-4"><h2 class="mb-3 font-display text-sm font-semibold">Project Workspaces</h2><div v-for="workspace in workspaces" :key="workspace.workspace_id" class="mb-2 rounded border border-border p-3 text-xs"><div class="flex gap-2"><strong>{{ workspace.name }}</strong><span class="font-mono text-muted-foreground">{{ workspace.root_ref }}</span><button class="ml-auto text-blue-300" @click="preflight(workspace.workspace_id)">Preflight</button></div><p v-if="preflights[workspace.workspace_id]" class="mt-2 font-mono text-muted-foreground">read={{ preflights[workspace.workspace_id].readable }} · write={{ preflights[workspace.workspace_id].writable }} · git={{ preflights[workspace.workspace_id].git }} · dirty={{ preflights[workspace.workspace_id].dirty ?? 'n/a' }}</p></div><form v-if="capabilities.permitted('workspace:register') && selectedGroup?.status === 'ACTIVE'" class="mt-3 grid gap-2 border-t border-border pt-3 sm:grid-cols-2" @submit.prevent="registerWorkspace"><input v-model="workspaceName" required class="rounded border border-border bg-background p-2 text-xs" placeholder="Workspace 名称" /><select v-model="workspaceSource" class="rounded border border-border bg-background p-2 text-xs"><option value="LOCAL_PATH">本地目录</option><option value="GIT_CLONE">Git Clone</option></select><select v-model="workspaceAccess" class="rounded border border-border bg-background p-2 text-xs"><option value="READ_WRITE">读写</option><option value="READ_ONLY">只读</option></select><template v-if="workspaceSource === 'LOCAL_PATH'"><input v-model="rootAlias" required class="rounded border border-border bg-background p-2 font-mono text-xs" placeholder="服务端 root alias" /><input v-model="relativePath" required class="rounded border border-border bg-background p-2 font-mono text-xs" placeholder="相对目录，例如 harness" /></template><template v-else><input v-model="gitUrl" required class="rounded border border-border bg-background p-2 font-mono text-xs sm:col-span-2" placeholder="https://git.example.com/org/repo.git" /><input v-model="defaultRef" required class="rounded border border-border bg-background p-2 font-mono text-xs" placeholder="默认 ref" /></template><button class="rounded bg-blue-500 px-3 py-2 text-xs text-white sm:col-span-2">登记 Workspace</button></form></article>
      </div>
    </section>
  </AppShell>
</template>
