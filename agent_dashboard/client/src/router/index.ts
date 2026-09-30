import { createRouter, createWebHashHistory } from 'vue-router'
import Home from '@/pages/Home.vue'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: Home },
    { path: '/workflows/new', name: 'workflow-builder', component: () => import('@/pages/WorkflowBuilder.vue') },
    { path: '/templates', name: 'job-flow-templates', component: () => import('@/pages/JobFlowTemplates.vue') },
    { path: '/templates/new', name: 'job-flow-builder-new', component: () => import('@/pages/JobFlowBuilder.vue') },
    { path: '/templates/:templateId/edit', name: 'job-flow-builder', component: () => import('@/pages/JobFlowBuilder.vue') },
    { path: '/instances', name: 'job-flow-instances', component: () => import('@/pages/JobFlowInstances.vue') },
    { path: '/instances/:instanceId', name: 'job-flow-instance', component: () => import('@/pages/JobFlowInstance.vue') },
    { path: '/instances/:instanceId/tasks/:taskId', name: 'job-flow-task', component: () => import('@/pages/JobFlowTask.vue') },
    {
      path: '/groups/:groupId/workflows/:workflowId',
      name: 'workflow-dashboard',
      component: Home,
    },
    {
      path: '/groups/:groupId/workflows/:workflowId/tasks/:taskId',
      name: 'task-workbench',
      component: () => import('@/pages/TaskWorkbench.vue'),
    },
    {
      path: '/groups/:groupId/workflows/:workflowId/logs',
      name: 'workflow-logs',
      component: () => import('@/pages/WorkflowLogs.vue'),
    },
    { path: '/settings', name: 'settings', component: () => import('@/pages/Settings.vue') },
    { path: '/:pathMatch(.*)*', component: () => import('@/pages/NotFound.vue') },
  ],
})

export default router
