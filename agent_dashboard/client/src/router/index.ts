import { createRouter, createWebHashHistory } from 'vue-router'
import Home from '@/pages/Home.vue'
import NotFound from '@/pages/NotFound.vue'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: Home },
    { path: '/:pathMatch(.*)*', component: NotFound },
  ],
})

export default router
