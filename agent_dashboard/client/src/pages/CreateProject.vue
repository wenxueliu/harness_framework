<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { ArrowLeft, FolderPlus } from 'lucide-vue-next'
import AppShell from '@/layouts/AppShell.vue'
import { useCapabilityStore } from '@/stores/capabilities'
import { createProjectGroup } from '@/api/dashboard'

const router = useRouter()
const capabilityStore = useCapabilityStore()

const name = ref('')
const description = ref('')
const busy = ref(false)
const error = ref('')
const canManage = computed(() => capabilityStore.permitted('group:manage'))

async function submit() {
  if (!name.value.trim() || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await createProjectGroup(name.value.trim(), description.value.trim())
    // 创建成功后返回首页，新项目会出现在项目列表中
    void router.push('/')
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '项目创建失败'
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  capabilityStore.load().catch(() => {})
})
</script>

<template>
  <AppShell title="新建项目" eyebrow="Create Project">
    <main class="mx-auto max-w-xl p-4 sm:p-6">
      <RouterLink
        to="/"
        class="mb-4 inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        <ArrowLeft :size="13" />返回首页
      </RouterLink>

      <div v-if="error" role="alert" class="mb-4 rounded border border-red-400/30 bg-red-400/10 p-3 text-xs text-red-200">{{ error }}</div>

      <form
        v-if="canManage"
        class="space-y-4 rounded-lg border border-border bg-card p-4"
        @submit.prevent="submit"
      >
        <div class="space-y-1">
          <label for="project-name" class="text-xs text-muted-foreground">项目名称</label>
          <input
            id="project-name"
            v-model="name"
            required
            data-testid="create-project-name"
            class="w-full rounded border border-border bg-background p-2 text-sm"
            placeholder="项目名称"
          />
        </div>
        <div class="space-y-1">
          <label for="project-description" class="text-xs text-muted-foreground">说明（可选）</label>
          <input
            id="project-description"
            v-model="description"
            data-testid="create-project-description"
            class="w-full rounded border border-border bg-background p-2 text-sm"
            placeholder="项目说明"
          />
        </div>
        <button
          type="submit"
          data-testid="create-project-submit"
          class="flex items-center gap-1.5 rounded bg-blue-500 px-4 py-2 text-xs text-white transition-colors hover:bg-blue-400 disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="busy || !name.trim()"
        >
          <FolderPlus :size="13" />创建项目
        </button>
      </form>
      <p v-else class="rounded border border-border bg-card p-4 text-sm text-muted-foreground">
        创建项目需要 group:manage 权限（ADMIN 角色）。
      </p>
    </main>
  </AppShell>
</template>
