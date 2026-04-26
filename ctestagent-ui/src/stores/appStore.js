import { reactive } from 'vue'

// 轻量状态管理（后续可拆分为多个 store）
export const appStore = reactive({
  projectName: '多智能体协同软件测试系统',
  status: 'idle',
})
