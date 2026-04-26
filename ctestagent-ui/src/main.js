import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import 'animate.css'
import './style.css'
import './echarts-setup'
import App from './App.vue'

// 启用 Element Plus 暗色变量
document.documentElement.classList.add('dark')

const app = createApp(App)
app.use(ElementPlus)
app.mount('#app')
