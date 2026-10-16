import { defineStore } from 'pinia'
import { ref } from 'vue'
import { listGroupWorkflows, listProjectGroups, listProjectWorkspaces } from '@/api/dashboard'
import type { ProjectGroup, ProjectWorkspace } from '@/api/types'

export const useProjectGroupStore = defineStore('project-groups', () => {
  const groups = ref<ProjectGroup[]>([])
  const selectedId = ref('unassigned')
  const workflowIds = ref<string[]>([])
  const workspaces = ref<ProjectWorkspace[]>([])
  const error = ref('')
  const workflowCounts = ref<Record<string, number>>({})
  async function load(selected = selectedId.value) {
    error.value = ''
    try {
      groups.value = await listProjectGroups()
      await select(selected)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : '项目组加载失败'
      throw cause
    }
  }
  async function select(groupId: string) {
    selectedId.value = groupId
    const [ids, registered] = await Promise.all([
      listGroupWorkflows(groupId), listProjectWorkspaces(groupId),
    ])
    workflowIds.value = ids; workspaces.value = registered
  }
  // 项目列表首页用：拉全部组并并行统计各组 workflow 数（含 unassigned）
  async function loadOverview() {
    error.value = ''
    try {
      groups.value = await listProjectGroups()
      const targets = [...groups.value.filter((group) => !group.virtual && group.status === 'ACTIVE').map((group) => group.group_id), 'unassigned']
      const entries = await Promise.all(targets.map(async (groupId) => {
        try {
          return [groupId, (await listGroupWorkflows(groupId)).length] as const
        } catch {
          return [groupId, 0] as const
        }
      }))
      workflowCounts.value = Object.fromEntries(entries)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : '项目组加载失败'
      throw cause
    }
  }
  return { groups, selectedId, workflowIds, workspaces, error, workflowCounts, load, select, loadOverview }
})
