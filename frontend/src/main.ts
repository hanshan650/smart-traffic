import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
import { useThemeStore } from './stores/theme'
import './styles/global.css'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(router)

// 主题在挂载前初始化：首屏色调由 index.html 的内联脚本先定好，
// 这里负责启动定时判定（跨过 18:00 / 06:00 自动切换）并补齐 store 状态。
useThemeStore(pinia).init()

app.mount('#app')
