# CTestAgent 前端控制台

本目录是 CTestAgent 多智能体测试系统的可视化前端。

## 技术栈

- Vue 3（Composition API + `<script setup>`）
- Vite（构建工具）
- Element Plus（UI 组件）
- ECharts（图表）
- axios（HTTP 客户端）
- WebSocket（实时事件流）

## 启动方式

```bash
# 安装依赖
npm install

# 开发模式启动（默认 http://localhost:5173）
npm run dev

# 生产构建
npm run build

# 预览生产构建
npm run preview
```

## 与后端的关系

前端通过两种方式与后端通信：

- **REST API**：文件上传、启动/停止测试、获取报告（`http://localhost:8000/api/...`）
- **WebSocket**：实时接收智能体协作事件（`ws://localhost:8000/ws/events`）

后端启动方式见项目根目录 `README.md`。
