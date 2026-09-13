import { defineStore } from 'pinia'
import { ref } from 'vue'
import { apiRequest } from '@/api/client'
import type { CapabilitiesSnapshot } from '@/api/types'

export const useCapabilityStore = defineStore('capabilities', () => {
  const snapshot = ref<CapabilitiesSnapshot | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  async function load(groupId?: string) {
    loading.value = true; error.value = null
    try {
      const query = groupId ? `?group_id=${encodeURIComponent(groupId)}` : ''
      snapshot.value = await apiRequest<CapabilitiesSnapshot>(`/api/capabilities${query}`)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : '无法读取服务端能力'
      throw cause
    } finally { loading.value = false }
  }
  const enabled = (feature: string) => snapshot.value?.features[feature] === true
  const permitted = (permission: string) => snapshot.value?.permissions.includes(permission) === true
  const reason = (feature: string) => snapshot.value?.reasons[feature] || '当前上下文不支持该能力'
  return { snapshot, loading, error, load, enabled, permitted, reason }
})
