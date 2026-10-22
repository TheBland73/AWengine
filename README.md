# AWEngine

> **用 YAML 描述协作流程，用确定性验证判断结果。**
>
> AWEngine 是一个可验证、可恢复的多智能体工作流引擎。它把「谁在什么时候做什么」
> 写进一份可审查的 YAML，把节点执行交给 Python、子进程、内置动作或 LLM Agent；
> 一次运行是否成功，由真实的验证命令和隐藏测试决定，而不是由模型自称完成。

[![CI](https://img.shields.io/github/actions/workflow/status/TheBland73/AWengine/ci.yml?branch=main&label=CI&logo=github&cache=20261022)](https://github.com/TheBland73/AWengine/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/TheBland73/AWengine?label=release&cache=20261022)](https://github.com/TheBland73/AWengine/releases)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-read%20the%20manual-2f6f9f.svg)](docs/README.md)

作者：[TheBland73](https://github.com/TheBland73) ｜ 版本：1.1.0 ｜ 接口：CLI / Python 包

> 演示视频：[观看 AWEngine 完整演示（约 90 秒）](docs/media/awengine-demo.mp4)
>
> 视频使用真实模型（`--live`）录制；仓库默认的 `awf demo` 使用 cassette，便于离线复现。

---

## 目录

- [一、这个项目解决什么问题](#一这个项目解决什么问题)
- [二、环境准备](#二环境准备)
- [三、安装](#三安装)
- [四、第一次运行（零 API Key）](#四第一次运行零-api-key)
- [五、配置真实模型](#五配置真实模型)
- [六、交互式 shell](#六交互式-shell)
- [七、六个演示](#七六个演示)
- [八、CLI 参考](#八cli-参考)
- [九、工作流 YAML](#九工作流-yaml)
- [十、验证、安全与边界](#十验证安全与边界)
- [十一、运行记录与可观测性](#十一运行记录与可观测性)
- [十二、恢复、重试与失败分类](#十二恢复重试与失败分类)
- [十三、测试与 CI](#十三测试与-ci)
- [十四、常见问题排查](#十四常见问题排查)
- [十五、已知限制与路线图](#十五已知限制与路线图)
- [十六、开发者指南](#十六开发者指南)
- [十七、贡献、支持与许可证](#十七贡献支持与许可证)

---

## 一、这个项目解决什么问题

### 1.1 从一段常见的 Agent 脚本说起

很多「多 Agent」实现最后会变成这样一段代码：

```python
for round in range(max_rounds):
    plan = planner(task)
    patch = coder(plan)
    review = reviewer(patch)
    if review.approved:
        return "done"
```

它看起来能跑，但一旦要回答下面这些问题，就会变得很难：

- 第 3 轮到底执行了哪些节点？为什么停在第 3 轮，而不是第 2 轮？
- reviewer 说「通过」，是真的通过，还是它没有认真看？
- 模型说「已修复」，测试真的跑过吗？隐藏测试通过了吗？
- 进程被 Ctrl+C 或机器重启，下一次能从断点继续吗？
- 这次运行花了多少钱、调用了哪些工具、读了哪些 artifact？
- 换一个工作流，是否要把引擎代码再复制一遍？

AWEngine 的答案是：**把流程、协作、验证和状态全部外化。**

```text
workflow.yaml
     │
     ▼
 loader / spec ──► engine ──► node runtime ──► sandbox
                     │            │
                     │            ├── Python function
                     │            ├── subprocess (JSON protocol)
                     │            ├── builtin action
                     │            └── LLM agent
                     │
                     ├── state.json / events.jsonl
                     ├── verify command + holdout
                     └── report.md / replay.html
```

控制流在 YAML 里，状态在 `runs/<run_id>/` 里，验证结果在 `verify` 里。
模型只负责「产出候选」，不负责「宣布成功」。

### 1.2 四个硬约束

| 约束 | 具体做法 |
|---|---|
| **控制流与实现分离** | 分支、循环、收敛条件、验证命令都在 YAML；引擎不包含 coder/reviewer/fixer 等业务节点名 |
| **验证优先** | 可见测试 + holdout + red-before；可见测试通过但隐藏测试失败会被判为 `F-VERIFY-FALSEPASS` |
| **状态外置** | 每个节点后原子写 `state.json`；事件追加到 `events.jsonl`；支持 `resume` 和 `replay` |
| **失败可解释** | 终止状态、停止原因、失败分类、计数器、验证 stderr 都进入 `report.md` 和 `awf explain` |

### 1.3 能力清单

| 能力 | 说明 |
|---|---|
| 声明式工作流 | 每个工作流是一份 YAML：`nodes`、`edges`、`converge`、`verify` |
| 多种节点运行时 | `python`、`process`、`builtin`、`llm_agent` |
| 三 Agent 协作 | coder → reviewer → fixer 的补丁/评审/修复闭环，消息和 artifact 可追踪 |
| 分支与循环 | 条件边按 YAML 顺序匹配；回边识别轮次，不会被正向节点误触发 |
| 契约校验 | 节点输出必须满足 `required` / `types` / `min_items`，否则按失败重试 |
| 确定性验证 | 在一次性沙箱副本中跑 `verify.cmd`；holdout 只在验证时注入 |
| 失败恢复 | 节点重试、`red_before` 门禁、原子检查点、租约锁、`resume` |
| 成本控制 | `pricing.toml` 记账；`--max-cost` 与 `converge.cost` 双层限制 |
| 可观测性 | 事件流、Mermaid 流程图/时序图、Markdown/JSON 报告、自包含 HTML 回放 |
| 离线演示 | 六个互不依赖的 demo 和录制 cassette，无需 API Key |
| 可扩展 | 一条命令生成新项目；支持模块 Agent、内联 Agent、Python 节点和进程节点 |

### 1.4 适合什么，不适合什么

| 适合 | 不适合 |
|---|---|
| 需要「模型产出 + 测试判定」的代码修复、文档生成、评审流程 | 需要毫秒级延迟的在线推理服务 |
| 想把 Agent 流程交给团队审查、复现和审计 | 需要大规模分布式调度或容器编排 |
| 想在本地/CI 中离线回放模型行为 | 把不受信任的代码当作安全沙箱执行 |
| 想把多个 Agent 的中间产物显式保存下来 | 需要完整 Web IDE、可视化编排器或托管平台 |

> AWEngine 当前是一个**单机 CLI 引擎**，不是生产级容器平台。它解决的是流程正确性、
> 可验证性和可恢复性；不试图替代 Kubernetes、Temporal 或托管 Agent 平台。

---

## 二、环境准备

| 依赖 | 要求 | 说明 |
|---|---|---|
| Python | 3.11 及以上 | CI 覆盖 3.11 和 3.13；3.12 未单独列入矩阵，但语法兼容 |
| Git | 任意较新版本，且在 `PATH` 中 | 沙箱基线提交和 `git apply` 需要 |
| 操作系统 | Windows 为主要开发和 CI 平台 | Linux/macOS 可运行，但当前 CI 未覆盖 |
| Node.js | 可选，18+ | 只有 `process` 节点调用 Node 脚本时才需要 |
| `prompt_toolkit` | 可选 | 交互式 shell 的历史记录、补全和底部提示栏 |
| `openai` | 可选 | 只有 `--live` / `--record` 调用真实模型时需要 |

检查 Python 和 Git：

```powershell
python --version
git --version
```

如果使用 Windows PowerShell，建议先确认执行策略允许激活虚拟环境：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

---

## 三、安装

AWEngine 目前通过源码安装，尚未发布到 PyPI。推荐在虚拟环境中安装：

```powershell
git clone https://github.com/TheBland73/AWengine.git
cd AWengine

python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 日常使用：引擎 + 测试工具 + 交互式 shell
python -m pip install -e ".[dev,shell]"

# 需要真实模型时，再安装 OpenAI 兼容客户端
python -m pip install -e ".[dev,shell,llm]"
```

macOS / Linux 激活命令为 `source .venv/bin/activate`。

安装完成后应能看到版本号和命令：

```powershell
awf --version
# awengine 1.1.0
```

如果不想安装，也可以从仓库根目录直接运行：

```powershell
python -m awengine --version
```

`awf` 和 `python -m awengine` 是同一套 CLI；本文后续统一用 `awf` 举例。

### 3.1 可选依赖说明

| 安装方式 | 得到什么 |
|---|---|
| `pip install -e .` | 仅引擎，Pydantic + PyYAML |
| `pip install -e ".[shell]"` | 增加 `prompt_toolkit`，交互式 shell 更好用 |
| `pip install -e ".[llm]"` | 增加 `openai`，可连接 DeepSeek/OpenAI 兼容端点 |
| `pip install -e ".[dev,shell,llm]"` | 开发、演示和真实模型全部可用 |
| `pip install -e ".[dev]"` | CI 和单元测试所需的最小集合 |

---

## 四、第一次运行（零 API Key）

第一次使用不需要任何 Key。内置的六条演示全部使用录制的 LLM cassette，不发网络请求：

```powershell
awf demo
```

它会依次运行 `hello`、`bugfix`、`docsync`、`guard`、`falsepass`、`recover`，并打印类似：

```text
hello      success  (goal_met)
bugfix     success  (goal_met)
docsync    success  (goal_met)
guard      blocked  (F-STOP-EARLY)
falsepass  blocked  (F-VERIFY-FALSEPASS)
recover    success  (goal_met)
summary: 6/6 demos matched expectations
```

`guard` 和 `falsepass` 是**故意失败**的负向用例；`awf demo` 会核对预期结果并以 0 退出。
单独运行它们时，运行本身是 `blocked`，CLI 会返回退出码 1。

查看刚刚发生了什么：

```powershell
awf ls                         # 列出所有 run
awf show <run_id> --events 10  # 状态、历史和最近事件
awf report <run_id>            # 重新生成报告
awf replay <run_id> --open     # 生成并打开 replay.html
```

每次运行都会在 `runs/<run_id>/` 下留下：

```text
manifest.json     不可变元数据：工作流、spec hash、输入、workdir、运行选项
state.json        当前检查点，可恢复
events.jsonl      追加式事件日志
report.md         人类可读报告
summary.json      机器可读摘要
flow.mmd          流程图
sequence.mmd      Agent 协作时序图
replay.html       自包含执行回放
workdir/          本次运行的一次性沙箱副本
```

> 默认写入当前目录的 `runs/`，可用 `--runs <dir>` 或环境变量 `AW_RUNS` 改到别处。
> `runs/` 已在 `.gitignore` 中忽略，不会进入版本库。

---

## 五、配置真实模型

### 5.1 选择 provider

AWEngine 通过 OpenAI 兼容协议访问模型，按 `defaults.model` 的前缀选择 Key：

| 模型前缀 | 使用的环境变量 | 默认 endpoint |
|---|---|---|
| `deepseek-*` | `DEEPSEEK_API_KEY`（或 `AW_API_KEY`） | `https://api.deepseek.com/v1` |
| `gpt-*` / `openai-*` / `o1-*` / `o3-*` / `o4-*` | `OPENAI_API_KEY`（或 `AW_API_KEY`） | `https://api.openai.com/v1` |
| 其他名称 | `AW_API_KEY` | `AW_BASE_URL`，否则 OpenAI endpoint |

PowerShell 示例：

```powershell
# 只对当前终端生效，不要写进仓库文件
$env:DEEPSEEK_API_KEY = "sk-your-key"

# 如果使用兼容端点，可以同时覆盖
$env:DEEPSEEK_BASE_URL = "https://your-proxy.example/v1"
```

bash / zsh：

```bash
export DEEPSEEK_API_KEY="sk-your-key"
```

API Key 只从环境变量读取，不写入运行产物。事件总线会在落盘前对
`api_key`、`authorization`、`token`、`secret` 等字段做脱敏，但不能替代你自己的密钥管理。

### 5.2 运行模式

| 模式 | 行为 | 适用场景 |
|---|---|---|
| `--fake` | 从 cassette 读取录制好的模型响应 | 离线演示、CI、复现实验 |
| `--live` | 调用真实 provider | 实际使用、评估模型 |
| `--record` | 调用真实 provider，同时写入 cassette | 制作新的离线样例 |
| 不指定 | 有 Key 时走 live，无 Key 时自动降级 fake 并提示 | 日常第一次运行 |

```powershell
# 真实调用一次三 Agent 修复流程
awf run bugfix --live --workdir fixtures/calcfix

# 录制新的 cassette；不要把包含私有代码的 cassette 提交到公共仓库
awf run hello --record --workdir fixtures/calcfix -i name=world
```

### 5.3 成本控制

价格在仓库根目录的 [`pricing.toml`](pricing.toml) 中维护，单位为人民币元/百万 token，
支持缓存输入价：

```toml
[models.deepseek-chat]
input_per_mtok = 2.0
output_per_mtok = 8.0
cached_input_per_mtok = 0.5
```

成本有两层硬限制和一层可观测：

1. 工作流里的 `converge.cost.max_cny`；
2. CLI 的 `--max-cost`；
3. 报告中按模型汇总的 token 与成本，用于复盘和调价。

超过预算会以 `exhausted` 停止，并在 `report.md` 的「Why it stopped」中写明原因。

---

## 六、交互式 shell

在终端直接运行 `awf` 会进入交互式界面（TTY 下自动启用；脚本中仍可用非交互形式）：

```text
$ awf
awengine 1.1.0  interactive shell
project  D:\work\AWengine
runs     runs  (0 runs)
model    offline: recorded cassettes

type `help` for commands, `quit` to exit

awf> bugfix
awf> show last
awf> demo --only guard
awf> quit
```

### 6.1 一键演示

| 输入 | 等价命令 |
|---|---|
| `hello` | `run hello --fake --workdir fixtures/calcfix -i name=world` |
| `bugfix` | `run bugfix --fake --workdir fixtures/calcfix` |
| `docsync` | `run docsync --fake --workdir fixtures/docsync` |
| `guard` | `run guard` |
| `falsepass` | `run falsepass --workdir fixtures/falsepass` |
| `recover` | `run recover --workdir fixtures/calcfix` |

### 6.2 命令与快捷方式

| 命令 | 作用 |
|---|---|
| `demo [--only NAME]` | 运行全部或单个演示 |
| `run <workflow> [...]` | 运行任意工作流，参数与 `awf run` 一致 |
| `show [last\|<id>]` | 查看运行状态；省略 id 时使用最近一次 |
| `report [last\|<id>]` | 重新生成报告 |
| `replay [last\|<id>]` | 重新生成回放 |
| `explain [last\|<id>]` | 解释停止原因 |
| `ls` | 列出运行记录 |
| `validate <workflow>` | 校验 YAML |
| `nodes` | 列出可发现节点 |
| `new <name>` | 创建项目脚手架 |
| `help` / `?` | 帮助 |
| `quit` / `exit` / Ctrl-D | 退出 |

所有命令都支持 `/run`、`/show` 这样的斜杠写法；`list`、`runs`、`status` 会映射到 `ls`。
安装 `[shell]` 后支持 Tab 补全、持久历史、底部提示栏；未安装时自动退化为普通输入。
要在脚本或 CI 中强制进入非交互模式，可使用：

```powershell
$env:AW_NO_SHELL = "1"
awf demo
```

或者显式运行 `awf shell --plain`。

---

## 七、六个演示

`awf demo` 覆盖了从最小顺序流程到负向安全用例的六条路径。它们互相独立，
可以单独运行，也可以作为编写自己工作流的模板。

### 7.1 hello：最小可运行流程

```powershell
awf run hello --fake --workdir fixtures/calcfix -i name=world
```

`plan → greet → wrap` 三个节点，`greet` 是 LLM Agent，`wrap` 写入输出。
`converge.goal` 为 `greet.words >= 1`，演示了「节点输出 + goal + 轮数上限」的最小组合。

### 7.2 bugfix：三 Agent 修复循环

```powershell
awf run bugfix --fake --workdir fixtures/calcfix
```

这是项目中最完整的协作示例：

```text
plan ──► coder ──► apply ──► review ──(blockers == 0)──► END
                              │
                              └──(blockers > 0)──► fix ──► apply_fix ──► review_final
                                                                              │
                                                              review_final ──(0)──► END
                                                              review_final ──(>0)──► fix
```

- **coder** 读取仓库，返回 unified diff，产出 `patch` artifact；
- **apply** 在沙箱中应用补丁；补丁为空或触碰受保护文件时进入失败/恢复路径；
- **reviewer** 只报告带文件、行号和原文证据的问题，产出 `review` artifact；
- **fixer** 读取 review，只修复 blocker；
- **review_final** 复核；仍有 blocker 时沿回边回到 `fix`，直到清零或触发轮数上限；
- 最终由 `verify` 在沙箱中跑可见测试和 holdout，决定 `success` 还是 `blocked`。

Agent 之间不共享可变内存。信息通过 artifact 和 `message` handoff 事件传递，
`report.md` 的 Collaboration 表和 `sequence.mmd` 会把这些交接画出来。

### 7.3 docsync：完全内联在 YAML 的三个 Agent

```powershell
awf run docsync --fake --workdir fixtures/docsync
```

`analyst → writer → write → editor` 四个节点中，三个 Agent 的 `system` / `task` /
`artifacts` 全部写在 YAML 里，不需要 Python 模块。它演示了：

- 纯 YAML Agent 的写法；
- Agent 之间通过 artifact 交接；
- 用 `write_file` 把最终文档写入沙箱；
- 用 holdout 验证生成文档包含必需内容。

### 7.4 guard：补丁修改测试文件

```powershell
awf run guard
```

补丁试图触碰 `tests/` 或 `test_*.py` 等受保护路径。引擎在 `git apply` 之前拦截，
返回 `E-GUARD` 与 `cheat=True`，直接以 `blocked` 结束。这是**预期失败**的负向用例。

### 7.5 falsepass：可见测试通过、隐藏测试失败

```powershell
awf run falsepass --workdir fixtures/falsepass
```

工作流只修改了可见测试，而没有真正修复实现。可见测试变绿，但 holdout 仍然失败，
最终被判为 `F-VERIFY-FALSEPASS`。这是「模型不能自己宣布成功」的核心演示。

### 7.6 recover：补丁失败后的条件恢复

```powershell
awf run recover --workdir fixtures/calcfix
```

第一次 patch 为空，`apply_patch` 返回失败；工作流沿 `failed` 条件边进入恢复节点，
重新生成补丁并完成修复。它演示了「失败路径也是流程的一部分」，而不是让异常穿透引擎。

### 7.7 负向用例的退出码

| 命令 | 运行结果 | 单独执行退出码 |
|---|---|---|
| `awf run guard` | `blocked` / `F-STOP-EARLY` | 1 |
| `awf run falsepass` | `blocked` / `F-VERIFY-FALSEPASS` | 1 |
| `awf demo` | 核对六条预期结果 | 0（预期负向也算匹配） |
| `awf demo --only guard` | 只运行单条负向用例 | 0（与预期 `blocked` 匹配） |

---

## 八、CLI 参考

### 8.1 命令总览

| 命令 | 作用 |
|---|---|
| `awf` / `awf shell` | 打开交互式 shell；`--plain` 使用普通输入 |
| `awf demo [--only NAME] [--json]` | 运行六条离线演示 |
| `awf run <workflow>` | 运行工作流 |
| `awf resume <run_id>` | 从中断的 cursor 恢复 |
| `awf show <run_id> [--json] [--events N]` | 查看状态、历史和 artifact |
| `awf explain <run_id> [--json]` | 解释停止原因并给出建议 |
| `awf trace <run_id> [-n N] [--follow]` | 打印/跟踪事件流 |
| `awf report <run_id>` | 重新生成 Markdown/JSON 报告 |
| `awf replay <run_id> [--open]` | 重新生成回放，可选打开浏览器 |
| `awf ls [--json]` | 列出运行记录 |
| `awf nodes [--json] [--project DIR]` | 列出可发现节点实现 |
| `awf new <name> [--dir DIR] [--force]` | 创建可运行的项目脚手架 |
| `awf validate <workflow> [--graph]` | 静态校验 YAML；可输出 Mermaid |

### 8.2 `run` 参数

```powershell
awf run <workflow> `
  -i key=value `
  --input-file inputs.json `
  --workdir . `
  --disable NODE `
  --fake | --live | --record `
  --cassettes tests/cassettes `
  --max-rounds 3 `
  --max-steps 20 `
  --max-cost 0.50 `
  --no-verify `
  --dry-run
```

| 参数 | 说明 |
|---|---|
| `-i/--input KEY=VALUE` | 传入工作流输入，可重复；对应表达式里的 `inputs.key` |
| `--input-file FILE` | 从 JSON 文件读取输入 |
| `--workdir DIR` | 被复制到运行沙箱的目录，默认当前目录 |
| `--spec FILE` | 显式指定工作流文件 |
| `--disable NODE` | 在加载期旁路节点；只适用于 1 进 1 出或端点节点 |
| `--fake` | 回放 cassette，不发网络请求 |
| `--live` | 调用真实 provider |
| `--record` | 调用真实 provider 并写 cassette |
| `--cassettes DIR` | cassette 目录 |
| `--max-rounds N` | 覆盖 `converge.rounds.max` |
| `--max-steps N` | 覆盖 `converge.steps.max` |
| `--max-cost N` | 硬成本上限（CNY） |
| `--no-verify` | 跳过验证命令，仅调试用 |
| `--dry-run` | 打印计划，不产生运行副作用 |
| `--runs DIR` | 运行记录目录，默认 `runs` |

### 8.3 退出码

| 退出码 | 含义 |
|---|---|
| `0` | 成功，或 `awf demo` 的所有预期结果都匹配 |
| `1` | 运行未通过、被规则停止，或预期负向结果 |
| `2` | 用法错误、YAML/规格错误、缺少 Key 或价格配置 |
| `3` | 内部错误、契约/状态损坏、未分类异常 |
| `130` | 用户中断（Ctrl+C），可用 `awf resume` 继续 |

### 8.4 自动化示例

```powershell
# 机器可读：stdout 只输出 JSONL 事件，stderr 保留人类提示
awf run bugfix --fake --json-events --quiet > events.jsonl

# 校验并输出 Mermaid 图
awf validate workflows/bugfix.yaml --graph

# 在 CI 中列出运行记录
awf ls --json
```

---

## 九、工作流 YAML

完整字段说明见 [`docs/workflow-reference.md`](docs/workflow-reference.md)，这里给出够用的最小集。

### 9.1 最小工作流

```yaml
name: hello
version: 1
defaults:
  model: deepseek-chat
  temperature: 0.0
  max_tokens: 2048
  max_steps: 6

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

运行：

```powershell
awf run workflows/hello.yaml --fake --workdir fixtures/calcfix -i name=world
```

### 9.2 节点字段

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `name` | string | 必填 | 节点唯一名；表达式和输出以它为键 |
| `type` | `python` / `process` / `builtin` / `llm_agent` | 必填 | 运行时类型 |
| `impl` | string | 空 | `pkg.mod:attr` 或 `pkg.mod.attr`；`llm_agent` 可省略并改用内联 prompt |
| `cmd` | string | 空 | `process` 节点的命令，例如 `python node.py` |
| `config` | mapping | `{}` | 节点运行时配置 |
| `contract` | mapping | 无 | `required` / `types` / `min_items` |
| `retry` | integer | `0` | 失败后的额外重试次数，总尝试数为 `retry + 1` |
| `timeout_s` | integer | `120` | 节点超时 |
| `enabled` | boolean | `true` | 为 false 时在加载期旁路；也可用 `--disable` |

### 9.3 四种节点

| 类型 | 适合做什么 | 关键约束 |
|---|---|---|
| `python` | 项目内业务逻辑、解析、调用库 | 实现 `run(ctx) -> NodeResult`；异常会被包装为失败 |
| `process` | 任意语言的子进程、独立工具 | stdin 收 JSON 信封，stdout 返回 JSON；无 shell |
| `builtin` | 轻量动作：`echo`、`set`、`log`、`assert`、`write_file`、`apply_patch`、`call_tool` | `apply_patch` 受保护路径检查 |
| `llm_agent` | 需要工具循环、上下文组装、artifact 的 Agent | 工具白名单；最终输出必须是 JSON |

`builtin` 的完整列表和行为见 [`docs/workflow-reference.md`](docs/workflow-reference.md#23-type-builtin)。

### 9.4 内联 Agent

不需要 Python 模块，prompt 全部写在 YAML：

```yaml
nodes:
  analyst:
    type: llm_agent
    config:
      system: |
        You are a precise repository analyst.
        Return JSON with `brief` and `facts`.
      task: |
        Read the working copy and summarize the architecture.
      tools: [read_file, search_code, list_files]
      artifacts:
        - {id: brief, type: brief, from: brief}
    contract:
      required: [brief, facts]
      types: {brief: str, facts: list}
```

`impl` 与 `config.system` 至少提供一个；两者都没有会在加载期报 `SPEC_NODE_INVALID`。
内联 Agent 与模块 Agent 共享上下文组装、工具循环、artifact 生成和成本记账。

### 9.5 边与表达式

```yaml
edges:
  - {src: plan, dst: coder}
  - {src: review, dst: END, when: "review.blockers == 0"}
  - {src: review, dst: fix, when: "review.blockers > 0"}
```

- 按 YAML 顺序检查出边，第一条 `when` 为真的边获胜；没有 `when` 的边恒真；
- `dst: END` 结束图；
- 回边由 DFS 识别；只有在真正走回边时，才结束当前 round；
- 表达式由安全 AST 求值器执行，不使用 `eval`；不允许函数调用、推导式、lambda 和私有属性；
- 缺失变量求值为 `None`，布尔上下文为 `False`，同时记录到 missing 列表，便于排查。

### 9.6 收敛与验证

```yaml
converge:
  goal: "verify.passed"
  streak: 1
  rounds: {min: 1, max: 2}
  steps: {min: 3, max: 12}
  attempts: {min: 0, max: 3}
  cost: {max_cny: 0.50}
  progress:
    key: "verify.passed"
    window: 2
  escalate:
    after_round: 1
    model: deepseek-reasoner
    temperature: 0.2
    max_times: 1
  on_exhausted: report

verify:
  level: 4
  cmd: "python -m pytest -q"
  holdout: "../fixtures/holdout/test_calc_holdout.py"
  red_before: true
  timeout_s: 120
```

停止优先级是固定的：

1. 受保护文件被修改（cheat）→ `blocked`；
2. 轮数/步数/尝试次数/成本超限 → `exhausted`（或 `exited`）；
3. progress 判定停滞 → `stalled`；
4. goal 成立、streak 达标且轮数达标 → `success`；
5. 图已结束但 goal 不成立 → `blocked`。

### 9.7 旁路节点

`enabled: false` 或 `--disable NODE` 会在加载期把节点从图中移除：

- 1 进 1 出：把入边和出边拼接，保留两侧 `when` 的组合；
- 端点节点：删除相连边；
- 多进多出：拒绝并报 `SPEC_DISABLED_AMBIGUOUS`；
- 所有节点都被移除：报 `SPEC_NO_ACTIVE_NODE`。

旁路后生成新的 spec 和 `spec_hash`，报告描述的是实际执行图。

---

## 十、验证、安全与边界

### 10.1 验证在做什么

`verify` 在每轮结束、goal 判定之前执行：

```yaml
verify:
  level: 4
  cmd: "python -m pytest -q"
  holdout: "../fixtures/holdout/test_calc_holdout.py"
  red_before: true
```

- `cmd` 在**本次运行的一次性目录副本**中执行；
- `holdout` 在 Agent 工作期间不可见，只在验证阶段复制到 `tests_holdout/`；
- `red_before: true` 要求「未修改的副本」上 `cmd` 先失败，否则判为 `verify_weak`；
- 可见测试通过 + holdout 失败 → `F-VERIFY-FALSEPASS`；
- 可见测试失败 → 验证不通过，进入下一轮或按收敛规则停止。

### 10.2 四道保护

| 保护 | 行为 |
|---|---|
| 受保护路径 | 补丁触碰 `tests/`、`tests_holdout/`、`conftest.py`、`pytest.ini`、`test_*.py` 等直接 `blocked` |
| 路径检查 | 拒绝绝对路径、`..` 穿越和空补丁，且不执行 `git apply` |
| 命令白名单 | 只允许 `python` / `python3` / `pytest` / `node` / `py`，不经过 shell |
| 环境隔离 | 子进程只继承白名单环境变量；API Key 默认不传给节点 |

### 10.3 这不是安全沙箱

`workdir/` 是**目录副本**，不是容器，也不是虚拟机：

- Python 节点和 `process` 节点仍然可以访问宿主文件系统和网络；
- 只应运行你信任的工作流、节点实现和 Agent 工具；
- holdout 防止的是「Agent 看见并篡改隐藏测试」，不是隔离恶意代码；
- 需要硬隔离时，请把引擎运行在容器或专用虚拟机中，并限制工作目录为可丢弃卷。

安全模型和漏洞报告方式见 [`SECURITY.md`](SECURITY.md)。

---

## 十一、运行记录与可观测性

### 11.1 一次运行的文件

```text
runs/<run_id>/
  manifest.json
  state.json
  state.json.prev
  events.jsonl
  report.md
  summary.json
  flow.mmd
  sequence.mmd
  replay.html
  workdir/
  .lease
```

### 11.2 报告里有什么

`report.md` 包含：

- 结论、退出码、停止原因、失败分类；
- 验证级别、验证命令、holdout 与 red-before 结果；
- 节点表：状态、轮次、耗时、token、成本、错误；
- Collaboration 表：artifact 的 producer/consumer 和 handoff 消息；
- Issues 表：问题、证据、是否被后续 review 解决；
- progress 表、escalation 记录、按模型汇总的成本；
- 复现命令和运行参数。

### 11.3 事件与回放

```powershell
awf trace <run_id> --follow
awf show <run_id> --events 20
awf replay <run_id> --open
```

事件信封统一为：

```json
{"v":1,"seq":12,"ts":"2026-10-22T04:00:00Z","run":"20261022-120000-demo","type":"node_end","node":"coder","round":1,"data":{}}
```

`flow.mmd` 根据 spec 生成，`sequence.mmd` 根据真实事件生成，`replay.html` 是自包含播放器，
支持节点高亮、单步、进度条和键盘控制。

---

## 十二、恢复、重试与失败分类

### 12.1 中断后继续

```powershell
awf resume <run_id>
```

恢复流程会校验 `spec_hash` 和 terminal 状态，从 `cursor` 继续，并从 `manifest.json`
恢复运行模式、cassette 目录、项目路径和成本上限。已经到达 terminal 的 run 会被拒绝恢复。

### 12.2 崩溃安全

- `state.json` 通过临时文件 + fsync + `.prev` + `os.replace` 原子写入；
- 损坏时自动回退到 `state.json.prev`；
- `RunLock` 使用 `.lease` + 30 秒心跳，超过 120 秒无心跳的租约可被接管；
- 旧 schema 的 state 会迁移到 schema 2，而不是静默丢弃。

### 12.3 重试与收敛

| 机制 | 行为 |
|---|---|
| 节点 `retry` | 失败后最多再执行 `retry` 次；契约失败也会重试 |
| `attempts` | 失败尝试总数上限，超过后 `exhausted` |
| `escalate` | 在轮末切换模型/温度/rubric，次数受 `max_times` 限制 |
| `progress` | 连续窗口内 key 不变或 metric 不下降，判为 `stalled` |
| `on_exhausted` | `report` 以 `exhausted` 停止并返回 1；`fail` 仍保留 `exhausted`，但返回退出码 2 |

### 12.4 停止原因速查

| 终止状态 | 常见原因 | 处理方式 |
|---|---|---|
| `success` | goal 成立且 streak/轮数达标 | 查看 report 的验证结果 |
| `blocked` | 图结束但 goal 不成立；或 cheat/验证失败 | 看 `awf explain` 和 verify stderr |
| `stalled` | progress 连续不变 | 检查 goal 是否可达、prompt 是否在重复 |
| `exhausted` | 轮数、步数、尝试次数或成本超限 | 调整 converge、模型或任务拆分 |
| `exited` | `on_exhausted: fail` 或主动退出 | 按退出码处理 |

完整的错误码和失败分类见 [`docs/error-codes.md`](docs/error-codes.md)。

---

## 十三、测试与 CI

### 13.1 本地测试

```powershell
python -m pytest -q
```

当前测试套件包含 **73 个用例**，全部离线执行，不需要 API Key，也不发网络请求。
覆盖范围包括：

- 安全表达式求值与缺失变量；
- YAML 加载、校验、禁用节点和 spec hash；
- 收敛守卫、streak、progress、成本上限；
- 沙箱复制、补丁应用、holdout 注入、`F-VERIFY-FALSEPASS`；
- 事件脱敏、价格计算、检查点回退、租约；
- 三 Agent 闭环、handoff 消息、失败分类；
- CLI 子命令、脚手架、分层依赖和六个 demo。

### 13.2 演示回归

```powershell
awf demo
```

期望 `6/6 demos matched expectations`；这是比单元测试更接近真实使用的 smoke test。

### 13.3 持续集成

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) 在 Windows runner 上运行：

- Python 3.11 和 3.13；
- `python -m compileall -q awengine`；
- `python -m pytest -q`。

工作流不需要任何 Key；所有模型调用都走 cassette。推送到 `main` 或提交 PR 会自动触发。

### 13.4 重新录制 cassette

```powershell
python scripts/record_demo.py
```

脚本使用固定的 ScriptedLLM 和 RecordingLLM，不联网；修改 Agent 提示词后必须重录，
因为 cassette key 与请求内容逐字节绑定。

---

## 十四、常见问题排查

| 现象 | 原因与处理 |
|---|---|
| `awf: command not found` | 没有安装到当前环境；先激活虚拟环境，再 `pip install -e ".[dev,shell]"` |
| `No module named pydantic` / `yaml` | 依赖未安装；重新执行安装命令 |
| `No module named openai` | `--live`/`--record` 需要 `pip install -e ".[llm]"` |
| 没有 Key，仍然能跑 | 正常：默认自动降级为 `--fake`；要真实调用请设 Key 并加 `--live` |
| `CassetteMiss` | cassette 与请求不匹配；确认 `--cassettes` 指向正确目录，或重新录制 |
| `verify_weak` | `red_before` 在未修改副本上已经通过，测试没有区分度；换验证命令或 fixture |
| `F-VERIFY-FALSEPASS` | 可见测试过、holdout 失败；实现没有真正修好，或测试被绕过 |
| 补丁报 `E-PATCH` | hunk 行号/上下文不匹配，或路径错误；看 `report.md` 的 stderr 尾部 |
| `E-GUARD` / `cheat=True` | 补丁触碰了受保护测试文件；修改实现，不要改测试 |
| `RunLocked` | 同一 run 有活跃进程；等待结束；陈旧租约 120 秒后可接管 |
| `SPEC_*` 错误 | YAML 结构/引用/收敛配置有误；先运行 `awf validate <workflow>` |
| `UnicodeEncodeError` / 乱码 | Windows 终端编码问题；当前 CLI 会自动回退 `CNY` 并替换不可编码字符，也可 `chcp 65001` |
| `git apply` 失败 | 确认 `git` 在 PATH；查看沙箱中的 `report.md`，不要直接改 `runs/*/workdir` |
| 报告中成本为 `CNY 0.00` | 使用了 fake 模式，或 pricing 中没有对应模型；检查 `pricing.toml` |

无法定位时：

```powershell
awf show <run_id> --events 30
awf explain <run_id>
awf trace <run_id>
```

仍然无法复现时，请附上工作流 YAML、命令、run id、`report.md` 和脱敏后的
`events.jsonl` 片段。不要附带 API Key、私有代码或客户数据。

---

## 十五、已知限制与路线图

### 15.1 已知限制

- **单机、单进程**：一次 run 只由一个进程推进；没有多进程并行调度或分布式 worker；
- **沙箱不是容器**：`workdir/` 是目录副本，不能执行不可信代码；
- **单 run 单工作流**：不做跨 run 的共享状态或长事务；
- **cassette 严格匹配**：修改提示词、工具 schema 或输入后需要重录；
- **价格是静态的**：供应商调价后需要手动更新 `pricing.toml`；
- **没有 Web UI**：交互通过 CLI/shell；回放是静态 HTML；
- **没有内置 pass@k 评估器**：可以在外部脚本中多次运行并汇总 `summary.json`；
- **Windows 优先**：CI 目前只覆盖 Windows；POSIX 兼容性未纳入持续验证；
- **尚未发布 PyPI**：当前通过源码或 editable install 使用。

### 15.2 路线图

- [ ] 并行 fan-out / fan-in 与资源配额；
- [ ] 容器化执行后端；
- [ ] 结构化 pass@k / pass^k 评估命令；
- [ ] Linux/macOS CI 矩阵；
- [ ] 可选的只读 Web 报告浏览器；
- [ ] 更细粒度的成本预算与模型路由策略。

路线图是方向，不是承诺；欢迎通过 issue 讨论优先级。

---

## 十六、开发者指南

这一节面向要改引擎、加节点或维护仓库的人。只想使用 AWEngine 的读者可以到此为止。

### 16.1 分层与依赖方向

```text
shell (交互适配) ──► cli
cli ──► loader / spec / expr
cli ──► engine
engine ──► state / checkpoint / contracts / guard / budget / events
engine ──► nodes runtime ──► llm / executor
engine ──► trace
```

三条硬约束由 `tests/test_layering.py` 用 AST 扫描守护：

1. 下层模块不得 import `engine` 或 `cli`；
2. `engine.py` 不得出现 coder/reviewer/fixer 等业务节点名；
3. 业务节点只返回 `NodeResult`，不直接修改 `RunState`。

### 16.2 核心模块

| 模块 | 职责 |
|---|---|
| `awengine/spec.py` | Pydantic 模型、字段校验、`spec_hash`、回边识别 |
| `awengine/loader.py` | YAML 加载、引用校验、启用/禁用节点、holdout 解析 |
| `awengine/expr.py` | 安全 AST 表达式求值，不使用 `eval` |
| `awengine/engine.py` | 调度、路由、重试、轮次、验证、终止判定 |
| `awengine/state.py` | 唯一的状态写入路径：`record_result` / `finalize` |
| `awengine/checkpoint.py` | 原子写、`.prev` 回退、`RunStore`、租约锁 |
| `awengine/guard.py` | 收敛判定、progress、停止原因 |
| `awengine/budget.py` | 价格加载、逐次记账、成本上限 |
| `awengine/executor.py` | 沙箱、命令白名单、补丁、holdout、verify |
| `awengine/events.py` | 事件总线、JSONL、脱敏、截断 |
| `awengine/context.py` | 上下文组装与裁剪顺序 |
| `awengine/nodes/` | python / process / builtin / llm_agent / code 节点 |
| `awengine/llm/` | OpenAI 兼容客户端、Fake、Recording、cassette |
| `awengine/trace/` | Mermaid、report、replay、explain |
| `awengine/scaffold.py` | `awf new` 项目脚手架 |
| `awengine/demo.py` | 六条离线演示编排 |
| `awengine/shell.py` | 交互式 shell 薄适配层 |

### 16.3 一次 run 的控制流

1. `loader` 解析 YAML，校验节点、边、入口、收敛和 verify；
2. `engine.start()` 创建 run 目录、manifest、租约，复制沙箱并初始化 git baseline；
3. `red_before` 门禁在未修改副本上跑 verify；
4. 从入口节点开始，按出边顺序取第一条 `when` 为真的边；
5. 每个节点执行后写 history、outputs、artifact、message，并保存 checkpoint；
6. 走过回边或到达 END 时结束 round，执行 verify、progress、goal 和 limits；
7. 未终止则按 `escalate` 规则进入下一轮；终止则生成 report、stop event、run_end。

### 16.4 新增节点

#### Python 节点

```python
from awengine.contracts import NodeResult

def run(ctx):
    name = ctx.inputs.get("name", "world")
    return NodeResult(status="ok", output={"message": f"hello {name}"})
```

放在项目 `nodes/<name>.py`，YAML 中：

```yaml
nodes:
  greet:
    type: python
    impl: nodes.greet:run
```

`awf nodes` 会扫描当前项目的 `nodes/` 并列出实现。

#### 进程节点

```python
import json
import sys

envelope = json.load(sys.stdin)
print(json.dumps({
    "status": "ok",
    "output": {"files": ["a.py"]},
    "artifacts": [{"id": "listing", "type": "report", "content": {"files": ["a.py"]}}],
}))
```

```yaml
nodes:
  scan:
    type: process
    cmd: "python nodes/scan.py"
```

stdin 是 JSON 信封，stdout 必须是 JSON 对象；非零退出码、超时和非 JSON 输出都有明确失败路径。

#### 内联 Agent

```yaml
nodes:
  summarizer:
    type: llm_agent
    config:
      system: "You are a release-notes editor."
      task: "Summarize $input.change in one sentence."
      artifacts: [{"id": "summary", "type": "text"}]
```

#### 新项目脚手架

```powershell
awf new my-workflow --dir .\my-workflow
cd my-workflow
python -m pytest -q
awf run hello --live
```

脚手架会生成工作流、节点、离线测试、README 和 pyproject，并保证生成的测试不依赖网络。

### 16.5 测试与质量门禁

```powershell
python -m pytest -q
python -m compileall -q awengine
awf demo
```

- 所有单元测试必须离线；LLM 交互通过 `FakeLLM` / `ScriptedLLM` 或 cassette；
- 新增公共行为时，同时更新 `docs/workflow-reference.md` 或 `docs/error-codes.md`；
- 修改事件字段时，更新 `docs/architecture.md` 的事件表；
- 不要绕过 `tests/test_layering.py` 的依赖约束；
- 修改提示词或工具 schema 后，重新录制 cassette。

### 16.6 发布流程

1. 更新 `pyproject.toml` 的 `version`；
2. 在 `CHANGELOG.md` 增加对应版本条目；
3. 运行完整测试和 `awf demo`；
4. 提交并打 tag（例如 `v1.2.0`）；
5. 推送 tag；`.github/workflows/release.yml` 会重新测试、构建 `dist/`、创建 GitHub Release 并生成 release notes；
6. 检查 release notes、wheel/sdist 附件和升级说明；
7. 确认 README、`docs/` 与 `MANUAL.md` 中的版本号/命令没有过期。

### 16.7 文档地图

| 文档 | 内容 |
|---|---|
| [`docs/README.md`](docs/README.md) | 文档索引和阅读顺序 |
| [`docs/architecture.md`](docs/architecture.md) | 架构、控制流、状态模型、协作协议 |
| [`docs/workflow-reference.md`](docs/workflow-reference.md) | YAML 字段、节点、表达式、收敛、验证、上下文 |
| [`docs/error-codes.md`](docs/error-codes.md) | 退出码、错误码、失败分类和恢复建议 |
| [`docs/design.md`](docs/design.md) | 设计取舍、边界和非目标 |
| [`MANUAL.md`](MANUAL.md) | 面向使用者的完整手册和演示视频脚本 |
| [`Demonstration.md`](Demonstration.md) | 录屏分镜、讲解词和演示清单 |

### 16.8 代码风格

- 使用 `from __future__ import annotations` 和显式返回类型；
- 保持函数短小、单一职责；
- 不随意增加运行时依赖；引擎核心只依赖 Pydantic + PyYAML；
- 不打印 API Key、Authorization 或原始 provider payload；
- 新增 CLI 参数时同步更新 README 的 CLI 表和测试。

---

## 十七、贡献、支持与许可证

### 17.1 贡献

欢迎 issue、discussion 和 pull request。提交前请：

1. 运行 `python -m pytest -q`；
2. 运行 `awf demo`；
3. 更新相关文档和测试；
4. 保持 YAML 控制流与业务节点实现分离；
5. 阅读 [`CONTRIBUTING.md`](CONTRIBUTING.md) 和 [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)。

安全问题请不要开公开 issue，按 [`SECURITY.md`](SECURITY.md) 私下报告。

### 17.2 支持

- 使用问题：先查 [`MANUAL.md`](MANUAL.md) 和本 README 的排查表，或使用 [GitHub Discussions](https://github.com/TheBland73/AWengine/discussions)；
- 可复现的 bug：使用 bug report 模板，附工作流、命令、run id 和脱敏日志；
- 功能建议：使用 feature request 模板，说明使用场景和期望行为；
- 支持矩阵和响应范围：[`SUPPORT.md`](SUPPORT.md)；
- 安全漏洞：按 [`SECURITY.md`](SECURITY.md) 私下报告。

### 17.3 许可证

[MIT](LICENSE)。你可以自由使用、修改和分发，保留版权与许可声明即可。

如果在学术工作或公开项目中引用本仓库，可使用 [`CITATION.cff`](CITATION.cff) 中的元数据。

---

<p align="right"><a href="#awengine">回到顶部 ↑</a></p>
