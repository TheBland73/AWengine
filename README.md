# AWEngine

[![tests](https://github.com/TheBland73/AWengine/actions/workflows/ci.yml/badge.svg)](https://github.com/TheBland73/AWengine/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

声明式、可验证、可恢复的多智能体工作流引擎。

AWEngine 把协作流程写在 YAML 里，把节点实现留给 Python 函数、子进程、内置动作或
LLM Agent。一次运行是否成功由**确定性验证命令 + 隐藏测试**判定，而不是由模型
自称完成；运行状态持续落盘，进程中断后可以从断点继续。

- **控制流与实现分离**：分支、循环、收敛条件都在 YAML 中，引擎不包含业务节点名。
- **验证优先**：可见测试、holdout、red-before 三者组合，能识别假通过和空转。
- **崩溃安全**：原子检查点、租约锁、事件日志，支持 `resume` 与完整回放。
- **离线可复现**：录制的 LLM cassette 使全部演示和测试无需 API key。
- **轻量依赖**：运行时只需要 Pydantic 与 PyYAML；OpenAI 兼容客户端是可选依赖。

---

## 快速开始

要求 Python 3.11+，以及 PATH 中可用的 `git`（沙箱基线和 `git apply` 需要）。

```powershell
git clone https://github.com/TheBland73/AWengine.git
cd AWengine
python -m pip install -e ".[dev]"

# 运行全部离线演示并生成报告
awf demo

# 查看运行列表、报告和回放
awf ls
awf report <run_id>
awf replay <run_id>
```

也可以不安装，直接从仓库根目录运行：

```powershell
python -m awengine demo
```

演示默认写入 `runs/`。每次运行都会生成 `state.json`、`events.jsonl`、
`report.md`、`summary.json`、Mermaid 图和单文件 `replay.html`。

---

## 演示

`awf demo` 覆盖六条互不依赖的流程，全部离线执行：

| 工作流 | 说明 | 预期结果 |
|---|---|---|
| `hello` | 最小顺序流程、节点输出、goal 收敛 | `success` / `goal_met` |
| `bugfix` | coder → reviewer → fixer 修复循环，含补丁与评审 artifact | `success`，两轮评审后 0 blocker |
| `docsync` | 完全内联在 YAML 中的三个 Agent，生成 Markdown 文档 | `success`，产出 `NOTES.md` |
| `guard` | 补丁触碰测试文件时的硬拦截 | `blocked` / `F-STOP-EARLY` |
| `falsepass` | 可见测试通过、隐藏测试失败 | `blocked` / `F-VERIFY-FALSEPASS` |
| `recover` | 补丁应用失败后按条件边进入恢复分支 | `success` / `goal_met` |

`guard` 与 `falsepass` 是刻意失败的负向用例。`awf demo` 将它们视为预期结果并以
0 退出；单独运行时会按运行结果返回退出码 1。

完整的安装步骤、八分钟录屏脚本、六十秒短片版本和讲解词见
[`Demonstration.md`](Demonstration.md)。

### 三 Agent 修复循环

`workflows/bugfix.yaml` 定义了 coder、reviewer、fixer 三个 Agent：

```text
plan -> coder -> apply -> review --(blockers == 0)--> END
                            |
                            +--(blockers > 0)--> fix -> apply_fix -> review_final
                                                                         |
                                                        review_final --(0)--> END
                                                        review_final --(>0)--> fix
```

- **coder** 读取仓库并返回 unified diff，产出 `patch` artifact；
- **apply** 在沙箱中应用补丁，失败时保留 stderr 并交给恢复边；
- **reviewer** 只报告带文件、行号和原文证据的问题，产出 `review` artifact；
- **fixer** 读取评审结果修复 blocker，由 `review_final` 复核；
- 复核不通过时沿回边回到 `fix`，直到 blocker 清零或触发收敛上限。

Agent 之间不共享可变内存。它们通过 artifact（补丁、评审）和 message/handoff
事件交换信息，所有交接都记录在 `events.jsonl` 与报告的时间线中。

---

## 核心概念

| 概念 | 含义 |
|---|---|
| Workflow | 一份 YAML，包含 `nodes`、`edges`、`converge`、`verify` |
| Node | 执行单元：`python`、`process`、`builtin` 或 `llm_agent` |
| Artifact | 不可变的协作产物，带类型、生产者、摘要和内容 |
| Edge | 条件转移；第一个求值为真的边生效，`END` 结束流程 |
| Round | 从入口到一条回边的执行区间；回边即循环边界 |
| Converge | 轮数、步数、尝试次数、成本、goal streak 与 progress 停滞规则 |
| Verify | 在沙箱中运行验证命令，可选 holdout 与 red-before 检查 |

### 确定性验证

```yaml
verify:
  level: 4
  cmd: "python -m pytest -q"
  holdout: "../fixtures/holdout/test_calc_holdout.py"
  red_before: true
  timeout_s: 120
```

- `cmd` 在本次运行的一次性目录副本中执行可见测试；
- `holdout` 仅在验证阶段注入 `tests_holdout/`，Agent 执行期间不可见；
- `red_before: true` 要求未修改的副本上命令先失败，否则判为 `verify_weak`；
- 可见测试通过但 holdout 失败时判为 `F-VERIFY-FALSEPASS`；
- 补丁触碰 `tests/`、`conftest.py`、`pytest.ini` 等受保护路径时直接 `blocked`，
  不会执行该补丁。

### 失败恢复

- 节点级 `retry`；契约失败（缺字段、类型错误）会重试并记录原因；
- `state.json` 采用临时文件 + fsync + `.prev` 的原子写入；
- `RunLock` 使用租约和心跳，陈旧租约可以被接管；
- `awf resume <run_id>` 从保存的 cursor 继续，并从 manifest 恢复运行模式、
  cassette 目录与项目路径；
- `awf explain <run_id>` 输出停止原因、计数器、验证结果和排查建议。

### 可观测性

- 统一事件信封 `{v, seq, ts, run, type, node, round, data}`，追加写入；
- API key、Authorization、token 字段在落盘前脱敏；
- `flow.mmd`、`sequence.mmd` 可直接在 Mermaid 中渲染；
- `replay.html` 是自包含播放器，支持节点高亮、单步、进度条和键盘控制。

### 成本控制

`pricing.toml` 按模型配置每百万 token 的价格（默认 CNY），支持缓存输入价。
每次 LLM 调用即时记账，`--max-cost` 与工作流自身的 `converge.cost` 都会在执行前
和轮次边界检查。报告按模型汇总 token 与成本。

---

## CLI

| 命令 | 作用 |
|---|---|
| `awf demo [--only NAME] [--json]` | 运行六条离线演示并生成报告 |
| `awf validate <workflow> [--graph]` | 校验 YAML；可输出 Mermaid |
| `awf run <workflow>` | 运行工作流（`--fake` / `--live` / `--record`） |
| `awf resume <run_id>` | 从检查点恢复 |
| `awf show <run_id> [--json]` | 查看状态、历史与 artifact |
| `awf explain <run_id>` | 解释停止原因并给出建议 |
| `awf trace <run_id> [--follow]` | 打印事件流 |
| `awf report <run_id>` | 重新生成 Markdown/JSON 报告 |
| `awf replay <run_id>` | 重新生成并打开 `replay.html` |
| `awf ls [--json]` | 列出运行记录 |
| `awf nodes [--json]` | 列出可发现节点 |
| `awf new <name> --dir <path>` | 创建可运行的项目脚手架 |

`run` 常用参数：`-i key=value`、`--workdir`、`--disable NODE`、`--max-rounds`、
`--max-steps`、`--max-cost`、`--no-verify`、`--dry-run`。

退出码：`0` 成功；`1` 未通过或按规则停止；`2` 用法/规格错误；`3` 内部错误；
`130` 中断。

---

## 工作流示例

```yaml
name: hello
version: 1
defaults: {model: deepseek-chat, temperature: 0.0, max_tokens: 2048, max_steps: 6}

nodes:
  plan:
    type: builtin
    impl: echo
    config: {value: "Say hello to $name"}
  greet:
    type: llm_agent
    impl: awengine.nodes.code.greeter
  wrap:
    type: builtin
    impl: set
    config: {values: {ok: true}}

edges:
  - {src: plan, dst: greet}
  - {src: greet, dst: wrap}
  - {src: wrap, dst: END}

converge:
  goal: "greet.words >= 1"
  rounds: {min: 1, max: 1}
  steps: {min: 1, max: 6}
  progress: {key: "greet.words", window: 2}
```

完整字段、表达式语法和节点协议见
[`docs/workflow-reference.md`](docs/workflow-reference.md)。

---

## 扩展方式

新增节点不需要修改引擎：

1. **内置动作组合**：`echo`、`set`、`log`、`assert`、`write_file`、
   `apply_patch`、`call_tool`；
2. **项目内 Python 节点**：在 `nodes/<name>.py` 中实现 `run(ctx) -> NodeResult`；
3. **进程节点**：`type: process`，通过 stdin/stdout JSON 协议接入任意语言；
4. **LLM Agent**：模块中的 `AGENT` dict，或完全内联在 YAML 的
   `config.system` / `config.task` / `config.normalize` 中。

```yaml
nodes:
  greeter:
    type: llm_agent
    config:
      system: "You are a concise release assistant."
      task: "Summarize $input.change in one sentence."
      artifacts: [{"id": "summary", "type": "text"}]
```

```powershell
awf nodes --json
awf new my-workflow --dir .\my-workflow
```

`awf new` 会生成工作流、节点、测试、README 与 pyproject，并保证生成的测试在
无网络、无 API key 的情况下可运行。

---

## 运行产物

```text
runs/<run_id>/
  manifest.json     工作流、spec hash、输入、工作目录、运行选项
  state.json        完整检查点，可恢复
  events.jsonl      追加式事件日志
  report.md         人类可读报告
  summary.json      机器可读摘要
  flow.mmd          流程图
  sequence.mmd      事件时序图
  replay.html       自包含执行回放
  workdir/          本次运行的一次性沙箱副本
```

---

## 开发

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
```

测试覆盖表达式安全边界、YAML 校验与旁路、收敛守卫、沙箱与补丁应用、
holdout 隔离、事件脱敏、价格预算、检查点回退与租约、进程节点协议、三 Agent
闭环、失败分类、CLI、脚手架、分层依赖和六条演示。测试完全离线。

重新录制 LLM cassette（脚本内置固定响应，不联网）：

```powershell
python scripts/record_demo.py
```

---

## 目录结构

```text
awengine/
  awengine/                # 引擎包
    cli.py                 # 命令行入口
    engine.py              # 调度、路由、重试、收敛
    spec.py loader.py      # YAML 模型与静态校验
    expr.py                # 安全表达式求值
    contracts.py           # 状态、事件、结果模型
    state.py checkpoint.py # 状态写入、原子检查点、租约
    guard.py budget.py     # 收敛判定与成本控制
    executor.py            # 沙箱、命令执行、补丁、验证
    context.py             # 上下文组装与裁剪
    events.py              # 事件总线与脱敏
    demo.py                # 离线演示编排
    nodes/                 # python/process/llm_agent/builtin/code
    llm/                   # OpenAI 兼容客户端、fake/record、cassette
    trace/                 # Mermaid、报告、回放、explain
    scaffold.py            # 项目脚手架
  workflows/               # 示例工作流
  fixtures/                # 演示仓库与 holdout
  nodes/                   # 项目级自定义节点示例
  tests/                   # 离线测试与 cassette
  scripts/                 # 演示与录制脚本
  docs/                    # 架构、YAML 参考、错误码、设计说明
```

---

## 兼容性与边界

- 支持 Python 3.11–3.13，CI 覆盖 3.11 与 3.13；
- 沙箱是目录副本而非容器，`process` 节点能访问宿主文件系统，只应运行可信代码；
- 成本价格来自静态 `pricing.toml`，供应商调价后需要手动更新；
- cassette 与请求内容逐字节绑定，修改 Agent 提示词后需要重新录制；
- 未实现交互式 REPL、多进程并行调度和 pass@k 评估框架；
- `--live` 需要 `DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY`，否则自动降级为离线模式。

架构与取舍见 [`docs/architecture.md`](docs/architecture.md) 和
[`docs/design.md`](docs/design.md)。

---

## 贡献

欢迎提交 issue 和 pull request。提交前请运行 `python -m pytest -q`，并保持
工作流控制流与业务节点实现分离。详见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

## 许可证

[MIT](LICENSE)
