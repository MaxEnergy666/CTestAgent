# CTestAgent

![License](https://img.shields.io/badge/License-MIT-green.svg)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)
![Vue](https://img.shields.io/badge/Vue-3-42b883.svg)

## 1. 项目简介

**CTestAgent** 是一个面向 C 语言程序的多智能体协同软件测试系统。

一句话定位：CTestAgent 将源码分析、用例生成、编译执行、质量评审和报告生成拆分为 6 个协作智能体，并通过 Web 前端把自动化测试过程可视化呈现。

本项目是2026年华南理工大学《软件测试》课程设计性实验项目，重点展示“多智能体协作”在自动化软件测试中的工程实现。用户可以在前端上传 `.c` / `.h` 文件，启动后端多智能体测试流程，并在页面中查看覆盖率报告、缺陷列表、智能体日志和统计图表。系统既支持命令行运行，也支持 FastAPI + Vue 3 的前后端联动运行方式，便于课堂演示、实验复现和源码审阅。

## 2. 核心特性

✨ **六智能体职责分离**：Coordinator、Analyzer、Generator、Executor、Reviewer、Reporter 分别负责调度、分析、生成、执行、评审和报告。

✨ **前端上传通用 C 文件**：用户可上传 `.c` / `.h` 文件，后端自动保存到 `targets/upload_*` 目录并作为本次测试目标。

✨ **多策略测试用例生成**：后端包含等价类、边界值、决策表、状态序列、随机错误猜测和白盒引导等策略。

✨ **Reviewer 质量审查闭环**：Reviewer 在执行前筛选低质量用例，在执行后聚合崩溃并生成覆盖率反馈。

✨ **GCC / gcov 自动执行链路**：Executor 负责编译、运行、超时控制、崩溃捕获和行 / 分支覆盖率采集。

✨ **实时可视化 Dashboard**：Vue 3 前端通过 HTTP 和 WebSocket 展示智能体状态、消息流、覆盖率、缺陷和统计图表。

✨ **HTML / JSON 测试报告**：Reporter 汇总执行结果、缺陷信息、覆盖率和消息统计，生成可提交的测试报告。

## 3. 系统架构

CTestAgent 采用“前端可视化 + FastAPI 运行时 + 进程内消息总线 + 6 个智能体”的架构。前端负责上传源码、启动测试、调整参数和展示结果；后端由 `server.py` 接收请求并启动测试线程；6 个智能体通过 `MessageBus` 以发布 / 订阅方式同步协作，形成“分析 → 生成 → 审查 → 执行 → 反馈 → 报告”的闭环。

```text
                         +----------------------+
                         |     Vue Dashboard    |
                         |  upload / start / UI |
                         +----------+-----------+
                                    |
                            HTTP / WebSocket
                                    |
                                    v
+------------------------------------------------------------------+
|                         FastAPI Runtime                          |
|                    CTestAgent/server.py                          |
+------------------------------------------------------------------+
                                    |
                                    v
+------------------------------------------------------------------+
|                         Message Bus                              |
|                 publish / subscribe / message log                |
+----------+-----------+-----------+-----------+-----------+-------+
           |           |           |           |           |
           v           v           v           v           v
    +-------------+ +---------+ +-----------+ +----------+ +----------+
    | Coordinator | |Analyzer | | Generator | | Executor | | Reviewer |
    +------+------+ +----+----+ +-----+-----+ +----+-----+ +----+-----+
           |             |            |            |            |
           +-------------+------------+------------+------------+
                                      |
                                      v
                               +----------+
                               | Reporter |
                               +----------+
```

| 智能体 | 作用 |
| --- | --- |
| Coordinator | 全局调度者，负责阶段切换、轮次推进、终止判断和最终收尾。 |
| Analyzer | 源码分析者，提取函数、统计源码行数、标注风险函数和静态风险点。 |
| Generator | 测试生成者，按策略权重生成测试用例，并吸收 Reviewer 反馈。 |
| Executor | 执行者，编译目标 C 程序、运行测试输入、捕获异常并收集覆盖率。 |
| Reviewer | 评审者，预筛选测试用例、聚合崩溃、分析覆盖率缺口并反馈策略建议。 |
| Reporter | 报告者，生成 HTML / JSON 报告，汇总覆盖率、缺陷和智能体交互统计。 |

## 4. 技术栈

### 后端

| 类型 | 技术 / 文件 | 说明 |
| --- | --- | --- |
| 主要语言 | Python 3.10+ | 6 个智能体、消息总线、运行时控制均由 Python 实现。 |
| Web 服务 | FastAPI | `CTestAgent/server.py` 提供上传、启动、状态、报告和 WebSocket 接口。 |
| 实时通信 | WebSocket | `/ws/events` 将消息总线事件广播到前端。 |
| 配置解析 | PyYAML | `CTestAgent/config.yaml` 统一配置项目路径、Agent 参数和输出目录。 |
| C 编译执行 | GCC / MinGW GCC | Executor 使用 `gcc` 编译目标程序和测试 harness。 |
| 覆盖率 | gcov | Executor 解析 `.gcov` 文件，统计行覆盖率和分支覆盖率。 |
| 异常检测 | 返回码 / 信号 / ASan | Linux / macOS 可启用 ASan；Windows 下代码默认关闭 ASan 以提高稳定性。 |
| 可选模型增强 | urllib.request + OpenAI / Anthropic 兼容接口 | `core/agent_api.py` 提供可选的 Agent API Advisor，不依赖第三方 SDK。 |

### 前端

| 类型 | 技术 / 依赖 | 说明 |
| --- | --- | --- |
| 框架 | Vue 3 | 使用 Composition API 构建单页 Dashboard。 |
| 构建工具 | Vite | 前端开发、构建和预览工具。 |
| UI 组件 | Element Plus | 上传、按钮、通知、表单等基础交互组件。 |
| 图表 | ECharts / vue-echarts | 覆盖率、统计图表和测试结果可视化。 |
| HTTP 请求 | axios | 调用后端 `/api/*` 接口。 |
| 动效 | animate.css | 辅助前端状态变化和演示效果。 |

## 5. 环境要求

### 系统要求

| 工具 | 推荐版本 | 用途 |
| --- | --- | --- |
| Python | 3.10+ | 运行后端智能体和 FastAPI 服务。 |
| Node.js | 18+ | 运行 Vue 3 前端开发服务。 |
| GCC | 9+ | 编译被测 C 程序并生成覆盖率数据。 |
| Git | 任意现代版本 | 克隆项目和查看版本历史。 |

Windows 环境建议安装 MSYS2 / MinGW，并确保 `gcc` 可在命令行中访问。项目代码会尝试自动识别 `C:\msys64\mingw64\bin\gcc.exe`。

### 后端依赖

从当前 Python 代码的第三方 import 和运行命令汇总，后端主要依赖如下：

| 依赖 | 用途 |
| --- | --- |
| `fastapi` | 后端 HTTP / WebSocket API。 |
| `uvicorn[standard]` | 启动 FastAPI 服务。 |
| `PyYAML` | 读取 `config.yaml` 和 `agent_api_keys.yaml`。 |
| `python-multipart` | 支持 FastAPI 上传 `.c` / `.h` 文件。 |

项目根目录已提供 `requirements.txt`，可直接安装。标准库依赖包括 `asyncio`、`threading`、`subprocess`、`pathlib`、`dataclasses`、`urllib` 等，无需额外安装。

### 前端依赖

前端依赖来自 `ctestagent-ui/package.json`：

| 依赖 | 版本 |
| --- | --- |
| `@element-plus/icons-vue` | `^2.3.2` |
| `animate.css` | `^4.1.1` |
| `axios` | `^1.15.0` |
| `echarts` | `^6.0.0` |
| `element-plus` | `^2.13.7` |
| `vue` | `^3.5.32` |
| `vue-echarts` | `^8.0.1` |
| `@vitejs/plugin-vue` | `^6.0.5` |
| `vite` | `^8.0.4` |

## 6. 安装与启动

### 6.1 克隆项目

```bash
git clone <your-repository-url> CTestAgent
cd CTestAgent
```

其中 `<your-repository-url>` 请替换为实际 Git 仓库地址。

### 6.2 安装后端依赖

建议先创建虚拟环境：

```bash
python -m venv .venv
```

Windows PowerShell：

```bash
.\.venv\Scripts\Activate.ps1
```

macOS / Linux：

```bash
source .venv/bin/activate
```

安装 Python 依赖：

```bash
pip install -r requirements.txt
```

如果需要确认后端核心模块是否可导入：

```bash
cd CTestAgent
python test_import.py
```

### 6.3 安装前端依赖

```bash
cd ctestagent-ui
npm install
```

### 6.4 启动系统

启动后端服务：

```bash
cd CTestAgent
python -m uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

启动前端开发服务：

```bash
cd ctestagent-ui
npm run dev
```

默认访问地址：

```bash
http://localhost:5173
```

后端状态接口：

```bash
http://localhost:8000/api/status
```

Windows 用户也可以在项目根目录使用一键启动脚本：

```bash
.\start_all.bat
```

或：

```bash
powershell -ExecutionPolicy Bypass -File .\start_all.ps1
```

## 7. 使用指南

### 7.1 上传被测代码

打开前端页面后，在源码上传区域选择或拖拽 `.c` / `.h` 文件。前端会调用 `/api/upload`，后端将文件保存到 `CTestAgent/targets/upload_时间戳/` 目录，并把该目录作为本次测试的 `sourceDir`。

### 7.2 启动测试

点击页面顶部的“启动”按钮。前端会调用 `/api/start`，后端创建测试线程，加载配置，初始化消息总线和 6 个智能体，然后由 Coordinator 推动测试流程。

### 7.3 查看测试报告

测试完成后，底部报告区域会展示：

- 覆盖率报告：行覆盖率、分支覆盖率、已覆盖行数和趋势数据。
- 缺陷列表：动态崩溃和静态风险点，包含位置、风险等级、触发输入或证据。
- 智能体日志：消息总线事件、发送者、接收者和主题统计。
- 统计图表：执行用例、通过用例、确认缺陷、误报和交互统计。

后端报告文件会写入：

```bash
CTestAgent/output/reports/run_时间戳/report_时间戳.html
CTestAgent/output/reports/run_时间戳/report_时间戳.json
```

### 7.4 调整测试参数

- 策略权重：随机、边界、语法、覆盖引导权重，后端会映射到具体生成策略。
- 执行参数：批量大小、单用例超时、最大轮次、最大时间等。
- Reviewer 配置：通过阈值、复核次数、多样性 / 覆盖率 / 有效性权重。
- 阶段切换：探索阶段到深度挖掘阶段的覆盖率阈值。

## 8. 项目目录结构

以下目录树基于当前 master 分支读取生成，部分缓存和构建产物只保留目录名，不展开内部海量文件。

```text
.
├── README.md                              # 项目总说明，老师和 GitHub 访问者优先阅读
├── requirements.txt                       # 后端 Python 依赖
├── start_all.bat                          # Windows 一键启动脚本
├── start_all.ps1                          # PowerShell 一键启动脚本
├── 设计文档_多智能体协同软件测试系统.md       # 旧版设计文档，后续新版设计文档的基础材料
├── vuln_calc.c.gcov                       # 实验过程中的 gcov 产物
├── .vscode/                               # 编辑器配置，普通用户一般无需查看
├── output/                                # 根目录实验对照数据
│   └── final_comparison/
│       ├── master_baseline.log            # 单体探索基线日志
│       └── plan_a_with_feedback.log       # Plan A 多智能体协作日志
├── CTestAgent/                            # 后端主项目
│   ├── config.yaml                        # 默认测试目标和 Agent 参数配置
│   ├── main.py                            # 命令行运行入口
│   ├── server.py                          # FastAPI + WebSocket 前后端联动入口
│   ├── quick_test.py                      # GCC / harness 快速自测脚本
│   ├── test_import.py                     # 后端模块导入自测脚本
│   ├── agent_api_keys.yaml                # 可选 API Advisor key 配置
│   ├── agents/                            # 6 个智能体和生成策略
│   │   ├── analyzer.py                    # Analyzer：源码分析与风险识别
│   │   ├── coordinator.py                 # Coordinator：全局调度与阶段管理
│   │   ├── generator.py                   # Generator：测试用例生成
│   │   ├── executor.py                    # Executor：编译执行与覆盖率采集
│   │   ├── reviewer.py                    # Reviewer：质量审查与反馈
│   │   ├── reporter.py                    # Reporter：HTML / JSON 报告生成
│   │   ├── strategies/                    # 等价类、边界值、决策表等生成策略
│   │   └── executor_modules/              # 执行器拆分模块，占位 / 辅助结构
│   ├── core/                              # 核心基础设施
│   │   ├── message_bus.py                 # 发布 / 订阅消息总线
│   │   ├── message_types.py               # Agent 间传输的数据结构
│   │   ├── global_state.py                # 全局轮次、覆盖率和终止状态
│   │   ├── base_agent.py                  # Agent 基类
│   │   └── agent_api.py                   # 可选模型 API 增强封装
│   ├── targets/                           # 示例和上传后的被测 C 项目
│   │   ├── cjson/                         # cJSON 示例目标
│   │   ├── tinyexpr/                      # tinyexpr 示例目标
│   │   ├── fuzzgoat/                      # fuzzgoat 漏洞样例
│   │   ├── calculator/                    # 简易计算器样例
│   │   ├── sort/                          # 排序样例
│   │   ├── Damn Vulnerable C Program/     # 当前默认配置目标
│   │   └── upload_*/                      # 前端上传文件自动生成的测试目标目录
│   ├── seeds/                             # 初始测试种子
│   ├── tests/                             # 后端单元测试 / 回归测试
│   └── output/                            # 后端运行输出，普通用户只需看 reports
│       ├── agent_logs/                    # 智能体运行日志
│       ├── crashes/                       # 崩溃触发输入
│       ├── executor_work/                 # 编译临时目录和 gcov 文件
│       └── reports/                       # HTML / JSON 测试报告
└── ctestagent-ui/                         # Vue 3 前端项目，演示和交互入口
    ├── package.json                       # 前端依赖和 npm 脚本
    ├── vite.config.js                     # Vite 配置
    ├── index.html                         # 前端 HTML 入口
    ├── dist/                              # 前端构建产物，可由后端静态托管
    ├── public/                            # favicon 和图标资源
    └── src/
        ├── App.vue                        # 前端主布局
        ├── api/                           # HTTP / WebSocket / 运行时控制
        ├── components/                    # 上传、主视图、报告、图表、参数面板
        ├── stores/                        # 前端响应式全局状态
        └── style.css                      # 全局样式
```

| 目录 | 作用 | 关键文件 | 老师是否需要重点查看 |
| --- | --- | --- | --- |
| `CTestAgent/agents/` | 6 个智能体的核心业务代码。 | `coordinator.py`、`reviewer.py`、`executor.py` | 是，体现多智能体设计。 |
| `CTestAgent/core/` | 消息总线、数据结构、全局状态和 Agent 基类。 | `message_bus.py`、`message_types.py` | 是，体现协作协议。 |
| `CTestAgent/targets/` | 示例目标和用户上传后的 C 项目。 | `upload_*`、`fuzzgoat/`、`tinyexpr/` | 按需查看。 |
| `CTestAgent/output/` | 后端运行日志、崩溃输入和报告。 | `reports/`、`agent_logs/` | 看实验结果时查看。 |
| `ctestagent-ui/src/` | Vue 前端界面和交互逻辑。 | `App.vue`、`api/runtimeControl.js` | 演示前端时查看。 |
| `output/final_comparison/` | 最终对照实验日志。 | `master_baseline.log`、`plan_a_with_feedback.log` | 是，支撑实验数据。 |


## 9. 已知限制

- 覆盖率受 harness 设计限制，复杂交互式输入、多入口程序或依赖外部环境的代码可能无法完全覆盖。
- ASan 在发现内存错误后可能中断进程，导致部分 gcov 覆盖率数据来不及写回。
- Windows 环境下 Executor 默认关闭 ASan，主要依靠返回码、信号和异常输出来判断崩溃。
- 当前消息总线是进程内同步分发，高频反馈场景下需要通过跨轮调度避免递归调用和性能抖动。
- 通用 C 测试入口依赖静态分析和启发式 harness，遇到复杂结构体、回调或多文件链接关系时仍可能需要人工调整。


## 10. License

本项目采用 **MIT License**。
