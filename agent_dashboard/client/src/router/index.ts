import { createRouter, createWebHashHistory } from 'vue-router'
import Home from '@/pages/Home.vue'
import NotFound from '@/pages/NotFound.vue'
import WorkflowBuilder from '@/pages/WorkflowBuilder.vue'
import WorkflowLogs from '@/pages/WorkflowLogs.vue'
import Settings from '@/pages/Settings.vue'
import TaskWorkbench from '@/pages/TaskWorkbench.vue'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: Home },
    { path: '/workflows/new', name: 'workflow-builder', component: WorkflowBuilder },
    {
      path: '/groups/:groupId/workflows/:workflowId',
      name: 'workflow-dashboard',
      component: Home,
    },
    {
      path: '/groups/:groupId/workflows/:workflowId/tasks/:taskId',
      name: 'task-workbench',
      component: TaskWorkbench,
    },
    {
      path: '/groups/:groupId/workflows/:workflowId/logs',
      name: 'workflow-logs',
      component: WorkflowLogs,
    },
    { path: '/settings', name: 'settings', component: Settings },
    { path: '/:pathMatch(.*)*', component: NotFound },
  ],
})

export default router
