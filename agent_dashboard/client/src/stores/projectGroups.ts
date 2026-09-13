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
  return { groups, selectedId, workflowIds, workspaces, error, load, select }
})
