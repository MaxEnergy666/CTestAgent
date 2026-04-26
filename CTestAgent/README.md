# CTestAgent - 多智能体协同软件测试系统

> 基于 **6 个专职智能体（Agent）+ 进程内消息总线（MessageBus）** 的 C 语言项目自动化测试框架。  
> 实现 **分析 → 生成 → 评审 → 执行 → 反馈 → 报告** 闭环。

---

## 1. 项目定位与核心思想

传统测试流水线常是单程序串行执行，难以形成“自我审查 + 策略反馈”。  
CTestAgent 将流程拆分为 6 个职责清晰的智能体，彼此通过消息主题协作：

- 职责分离，降低复杂度
- Reviewer 独立质检，减少误判传播
- 覆盖率和崩溃反馈驱动策略迭代
- 新目标可通过 target 适配快速接入

---

## 2. 主要被测对象（C 代码）

当前 `targets/` 下包含：

1. **cJSON**（`targets/cjson`）
   - 输入：JSON 字符串
   - 特点：真实开源库、稳定度较高、覆盖率提升有代表性

2. **tinyexpr**（`targets/tinyexpr`）
   - 输入：数学表达式字符串（如 `sqrt(2+3*4)`）
   - 特点：解析 + 数值运算，边界值丰富（除零、超大数、非法表达式）

3. **fuzzgoat**（`targets/fuzzgoat`）
   - 输入：JSON 字符串（并提供 `input-files/` 漏洞触发样本）
   - 特点：故意植入内存破坏漏洞，适合验证崩溃发现、去重、分类、报告能力

4. 预留：`targets/sort`、`targets/calculator`

---

## 3. 六个智能体职责（含 2026-04 最新策略）

### 3.1 Coordinator Agent（协调者）
- 阶段状态机：`INIT → EXPLORE → DEEP → FINALIZE`
- 负责轮次调度、策略下发、终止判定、收尾触发
- 汇总执行统计并驱动下一轮
- 无进展策略已升级：默认 **不因 no-progress 直接停机**（优先继续探索）
- 引入无进展救援冷却（`no_progress_rescue_cooldown_rounds`），避免连续重复触发导致过早结束
- 支持自适应批量调度：停滞/救援轮次自动放大 `batch_size`（受 `max_batch_size` 限制）

### 3.2 Analyzer Agent（分析者）
- 解析 `.h/.c` 提取函数签名
- 计算风险分（指针参数、复杂度、危险调用等）
- 推断参数约束、构建调用图
- 轻量静态漏洞扫描会输出可定位问题（文件、行号、风险、建议）
- 扫描前会剥离注释与字符串，降低 `UNGUARDED_DIVISION` 等规则误报

### 3.3 Generator Agent（生成者）
- 多策略生成：等价类、边界值、决策表、状态序列、随机错误猜测、白盒引导
- 接收 Reviewer 反馈后自适应调整权重
- 支持目标相关默认入口：
  - cjson→`cJSON_Parse`
  - tinyexpr→`te_interp`
  - fuzzgoat→`json_parse`
- 支持从 target 的 `input-files` 预加载种子（如 fuzzgoat）

### 3.4 Executor Agent（执行者）
- 编译被测源码 + 自动生成 harness
- 执行测试用例，捕获 PASS/CRASH/TIMEOUT/ERROR
- 采集覆盖率（gcov）并发布覆盖增量
- 已内置目标特化 harness：cjson / tinyexpr / fuzzgoat
- 对含 `main` 的通用 C 程序可自动识别输入模式（`stdin` / `argv_file`）
- 覆盖率改为批次采集（非逐用例），提升吞吐并降低 gcov 频率开销

### 3.5 Reviewer Agent（评审者）
- 执行前：用例质量筛选（去重、有效性、覆盖估计、多样性）
- 执行后：崩溃交叉验证与去重
- 去重已增强为**组合指纹**（类型/信号/返回码/函数/定位/输入哈希）
- 发布策略反馈与验证统计（confirmed / false positives）
- 对缺陷聚合输出 `occurrence_count`、风险等级、定位信息，减少重复缺陷刷屏

### 3.6 Reporter Agent（报告者）
- 汇总执行与验证结果
- 输出 HTML + JSON 报告
- 缺陷详情增强：类型、函数、输入、stack hash、signal、return code、描述
- fuzzgoat 场景支持已知漏洞映射描述（如 UAF、invalid free、NULL 解引用）
- 报告中的缺陷证据类型已区分：`evidence=dynamic/static`
- `confirmed_bugs` 仅统计动态确认缺陷；静态发现计入 `static_findings`

---

## 4. 智能体模型是什么？

本项目中的“智能体模型”是：

- **策略驱动 + API 增强** 的混合模型
- 每个 Agent 是一个独立 Python 类（继承 `BaseAgent`）
- 决策来源：配置参数 + 统计数据 + 策略模块 + 消息反馈 + API 语义建议

当前实现中，6 个 Agent（Coordinator/Analyzer/Generator/Executor/Reviewer/Reporter）都可接入 API：

- API 可用于“微调”策略，不替代原有本地策略链路
- API 调用失败时自动回退到原规则逻辑
- 通过统一文件 `agent_api_keys.yaml` 管理 6 个 Agent 的 key

即：这是“可解释、可控、可复现”的多智能体系统，同时具备可选的 LLM 语义增强能力。

---

## 5. 消息总线设计

实现文件：`core/message_bus.py`

### 5.1 架构特征
- 进程内、同步调用
- 发布/订阅（Pub/Sub）
- 全量消息日志（供 Reporter 统计）

### 5.2 主题类别
- `cmd:*`：命令（如 `cmd:start_analysis`, `cmd:generate`, `cmd:finalize`）
- `data:*`：数据（如 `data:test_cases`, `data:execution_results`, `data:report_ready`）
- `feedback:*`：反馈（如 `feedback:review`, `feedback:strategy`）

### 5.3 单轮时序（简化）
1. Coordinator 发 `cmd:generate`
2. Generator 发 `data:test_cases`
3. Reviewer 筛选后发 `data:approved_cases`
4. Executor 执行并发 `data:execution_results`
5. Reviewer 验证崩溃并发反馈
6. Coordinator 汇总进入下一轮或收尾

---

## 6. 环境与配置需求

### 6.1 运行环境
- Python 3.10+
- GCC + gcov
  - Windows 推荐 MSYS2 MinGW-w64（`C:\msys64\mingw64\bin`）
- Python 包：
  - `pyyaml`
  - `jinja2`
  - `pycparser`

安装依赖：

```bash
python -m pip install pyyaml jinja2 pycparser
```

### 6.2 关于 ASan
- Linux/macOS 可启用 `enable_asan: true`
- Windows + MinGW 下通常关闭 ASan（当前默认 `false`）

### 6.3 API 增强配置（opus4.6）

项目根目录新增统一 key 文件：`agent_api_keys.yaml`

```yaml
provider: "anthropic"
endpoint: "https://api.anthropic.com/v1/messages"
model: "opus4.6"
timeout_seconds: 25

keys:
  Coordinator: ""
  Analyzer: ""
  Generator: ""
  Executor: ""
  Reviewer: ""
  Reporter: ""
```

说明：

- 6 个 Agent 默认都会尝试读取该文件中的对应 key
- 某个 Agent 未配置 key 时，仅该 Agent 的 API 增强自动关闭，不影响整体流程
- 可在 `config.yaml` 中对单个 Agent 追加 `api` 配置覆盖默认值

示例（可选）：

```yaml
agents:
  coordinator:
    api:
      enabled: true
      model: "opus4.6"
      # key_file: "./agent_api_keys.yaml"
```

也支持环境变量覆盖（按 Agent 维度）：

- `CTESTAGENT_COORDINATOR_API_KEY`
- `CTESTAGENT_ANALYZER_API_KEY`
- `CTESTAGENT_GENERATOR_API_KEY`
- `CTESTAGENT_EXECUTOR_API_KEY`
- `CTESTAGENT_REVIEWER_API_KEY`
- `CTESTAGENT_REPORTER_API_KEY`

---

## 7. 如何开始跑（Quick Start）

### 7.1 进入项目

```bash
cd CTestAgent
```

### 7.2 选择被测目标（编辑 `config.yaml`）

#### 示例：fuzzgoat
```yaml
project:
  source_dir: "./targets/fuzzgoat"
  entry_function: "json_parse"
```

#### 示例：tinyexpr
```yaml
project:
  source_dir: "./targets/tinyexpr"
  entry_function: "te_interp"
```

#### 示例：cjson
```yaml
project:
  source_dir: "./targets/cjson"
  entry_function: "cJSON_Parse"
```

### 7.3 启动

```bash
python main.py
```

### 7.4 启动前后端 UI（推荐）

在工作区根目录执行（Windows）：

```bash
start_all.bat
```

默认会拉起：

- 后端：`http://localhost:8000/api/status`
- 前端：`http://localhost:5173`

可用 PowerShell 自定义端口：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_all.ps1 -PythonExe "D:\Anaconda3\envs\rl_learning\python.exe" -BackendPort 8001 -FrontendPort 5174
```

### 7.5 前端使用流程（用户视角）

1. 打开前端页面后，在左侧「上传被测 C 文件」拖拽或选择 `.c/.h` 文件。
2. 等待自动上传完成，或点击「立即上传到后端」，看到「后端已同步」状态。
3. 在顶部点击「启动」开始测试。
4. 中央主视图实时观察：阶段、轮次、运行时长、覆盖率、缺陷数量。
5. 测试结束后查看顶部「停止原因」字段（如时间上限、覆盖目标、手动停止）。
6. 在底部标签页查看结果：
  - 覆盖率报告：行/分支覆盖曲线与统计
  - 缺陷列表：定位、风险、出现次数、最小输入、调用栈/描述
  - 智能体日志：消息流回放
  - 统计图表：主题与交互统计
7. 点击「暂停」时，当前版本后端会执行停止并标记为 paused（非断点续跑）。
8. 点击「停止」可立即结束本轮测试并进入收尾。

---

## 8. 报告字段解读（重点）

报告路径：`output/reports/run_YYYYMMDD_HHMMSS/`

- `report_*.html`：可视化报告
- `report_*.json`：结构化数据

### 8.1 summary 字段
- `total_generated`：本次总生成用例数
- `total_executed`：实际执行用例数
- `total_passed`：执行通过数
- `total_crashes`：崩溃数
- `confirmed_bugs`：动态执行链路中确认的缺陷数量（`evidence=dynamic`）
- `static_findings`：静态扫描发现数量（`evidence=static`）
- `false_positives`：误报/重复崩溃等无效崩溃计数
- `line_coverage`：目标源码行覆盖率（0~1）

### 8.2 bugs 字段
- `id`：缺陷 ID
- `type`：缺陷类型（增强后可能含定位，如 `null_deref @ fuzzgoat.c:297`）
- `severity`：严重级别
- `risk_level` / `risk`：风险等级与风险说明
- `function`：目标函数
- `input`：触发输入片段
- `description`：缺陷说明（fuzzgoat 下会给出已知漏洞映射）
- `stack_hash`：去重指纹
- `signal` / `return_code`：崩溃证据
- `occurrences`：同类缺陷出现次数
- `evidence`：证据类型（`dynamic` 或 `static`）
- `verified`：是否为动态确认缺陷

### 8.3 agent_interactions 字段
- `total_messages`：总消息数
- `messages_by_topic`：按主题统计
- `messages_by_sender`：按发送者统计

---

## 9. 关键配置项（config.yaml）

- `coordinator.max_rounds`：最大轮数
- `coordinator.max_time_seconds`：最大测试时长
- `coordinator.no_progress_stop_enabled`：是否允许因连续无进展提前停止（默认建议 `false`）
- `coordinator.no_progress_rescue_cooldown_rounds`：无进展救援触发冷却轮次
- `coordinator.max_batch_size`：自适应批量上限
- `coordinator.rescue_batch_multiplier`：救援轮次批量倍率
- `coordinator.stagnation_batch_multiplier`：停滞轮次批量倍率
- `generator.batch_size`：每轮生成数量
- `executor.timeout_per_case`：单用例超时时间
- `executor.enable_gcov`：是否采集覆盖率
- `executor.enable_asan`：是否启用 ASan
- `reviewer.approval_threshold`：审查通过阈值

调参建议：
- 提高覆盖率：增加 `max_rounds` / `max_time_seconds`，并保留 `no_progress_stop_enabled=false`
- 提升漏洞命中：救援轮次提高 `rescue_batch_multiplier`，并加强 `whitebox_guided` / `random_error_guess`
- 提升稳定性：降低 `max_batch_size` 或 `timeout_per_case`，避免目标长时间卡住

---

## 10. 项目详细目录树

```text
CTestAgent/
├── README.md
├── config.yaml
├── main.py
├── quick_test.py
├── run_output.txt
├── test_output.txt
├── test_import.py
│
├── core/
│   ├── __init__.py
│   ├── base_agent.py
│   ├── message_bus.py
│   ├── message_types.py
│   └── global_state.py
│
├── agents/
│   ├── __init__.py
│   ├── coordinator.py
│   ├── analyzer.py
│   ├── generator.py
│   ├── executor.py
│   ├── reviewer.py
│   ├── reporter.py
│   ├── strategies/
│   │   ├── __init__.py
│   │   ├── equivalence_partitioner.py
│   │   ├── boundary_value.py
│   │   ├── decision_table_combiner.py
│   │   ├── state_sequence_generator.py
│   │   ├── random_error_guesser.py
│   │   ├── whitebox_guided.py
│   │   └── mutator.py
│   └── executor_modules/
│       ├── __init__.py
│       ├── compiler.py
│       ├── harness.py
│       ├── runner.py
│       └── coverage.py
│
├── targets/
│   ├── cjson/
│   │   ├── cJSON.c
│   │   ├── cJSON.h
│   │   ├── cJSON_Utils.c
│   │   └── cJSON_Utils.h
│   ├── tinyexpr/
│   │   ├── tinyexpr.c
│   │   └── tinyexpr.h
│   ├── fuzzgoat/
│   │   ├── fuzzgoat.c
│   │   ├── fuzzgoat.h
│   │   └── input-files/
│   │       ├── emptyArray.txt
│   │       ├── emptyString.txt
│   │       ├── oneByteString.txt
│   │       └── validObject.txt
│   ├── sort/
│   └── calculator/
│
├── seeds/
│   └── json/
│
├── output/
│   ├── agent_logs/
│   ├── corpus/
│   ├── coverage/
│   ├── crashes/
│   ├── executor_work/
│   └── reports/
│       └── run_YYYYMMDD_HHMMSS/
│
└── tests/
    ├── __init__.py
    ├── test_message_bus.py
    ├── test_generator.py
    ├── test_reviewer.py
    └── test_executor.py
```

---

## 11. 常见问题（FAQ）

### Q1: 为什么 function 会显示成不匹配目标？
- 确认 `config.yaml` 的 `source_dir` 与 `entry_function` 是否对应。

### Q2: 为什么 confirmed_bugs 比 total_crashes 少很多？
- total_crashes 是执行期崩溃总数；confirmed_bugs 是 Reviewer 去重后的“缺陷簇”。

### Q3: line_coverage 曾经出现 >100% 是什么原因？
- 历史版本统计可能混入 harness 覆盖；当前版本已过滤为目标源码口径。

### Q4: Windows 下看不到 ASan 详情？
- MinGW 常见限制，建议用 Linux/WSL + clang/gcc ASan 复现内存错误细节。

---

## 12. 后续建议

- 引入异步消息总线（降低同步阻塞风险）
- 增加更强的崩溃最小化与聚类算法
- 对 fuzzgoat 自动抽取注释漏洞元数据（替代静态映射）
- 增补 CI 与回归测试矩阵（多 target、多平台）
