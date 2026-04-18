import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './index.css'
import Toast from 'vue-toastification'

const app = createApp(App)
app.use(router)
app.use(Toast, {
  theme: 'dark',
  style: {
    background: 'oklch(0.16 0.01 264)',
    border: '1px solid oklch(0.28 0.01 264)',
    color: 'oklch(0.92 0.005 264)',
  },
})
app.mount('#app')
