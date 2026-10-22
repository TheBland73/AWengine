# AWEngine 演示与录屏指南

本文面向第一次接触 AWEngine 的观众，也给录制演示视频的人一份可以直接照做的脚本。
读完之后，你应该能够：

1. 在干净环境里安装并验证 AWEngine；
2. 用一句命令跑通六条离线演示；
3. 单独运行、检查和回放任意一条工作流；
4. 在 8 分钟内讲清楚“流程控制、Agent 协作、可扩展性”三个重点；
5. 用 60 秒剪出一版适合提交或发到社交平台的短片。

> 本文以 Windows + PowerShell 为例，Python 3.11–3.13 均可。所有演示都不需要 API key。

---

## 1. 先理解 AWEngine 在演示什么

AWEngine 不是一个“让模型自己说完成了”的 Agent 框架。它把协作流程写成 YAML，把成功条件写成可执行的验证命令，然后用事件、artifact 和检查点把整个过程记录下来。

演示时只需要反复强调三件事：

| 卖点 | 观众应该看到什么 | 对应演示 |
|---|---|---|
| 流程是声明式的 | 分支、循环、收敛条件都在 YAML 里 | `bugfix` 的评审循环、`recover` 的恢复边 |
| 协作是可追踪的 | Agent 之间靠 artifact/handoff 传递信息 | `show` 里的 messages 和 report 的 Collaboration timeline |
| 成功是可验证的 | 可见测试、隐藏 holdout、red-before 三者共同判定 | `bugfix` 的 L4 PASS、`falsepass` 的假通过拦截 |

一个很实用的讲法是：

> “模型可以写代码，但模型不能决定自己是否成功。AWEngine 让测试、隐藏用例和补丁检查来决定。”

---

## 2. 安装与自检

### 2.1 环境要求

- Windows 10/11、macOS 或 Linux；
- Python **3.11+**（推荐 3.12）；
- Git（沙箱基线、`git apply` 需要）；
- PowerShell 7 或 Windows Terminal（录屏推荐）。

检查：

```powershell
python --version
git --version
```

### 2.2 获取代码

```powershell
git clone https://github.com/TheBland73/AWengine.git
cd AWengine
```

如果已经下载了源码，直接进入项目根目录即可。后续所有命令都默认在项目根目录执行。

### 2.3 创建虚拟环境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

如果 PowerShell 提示禁止运行脚本：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

macOS / Linux：

```bash
python -m venv .venv
source .venv/bin/activate
```

### 2.4 安装

```powershell
python -m pip install --upgrade pip
python -m pip install -e ".[dev,shell]"
```

### 2.5 自检

```powershell
python -m pytest -q
```

预期输出：

```text
72 passed
```

这一步同时验证了：依赖安装、工作流校验、沙箱执行、补丁应用、六个演示、
CLI、检查点和报告生成。**如果这一步不绿，不要开始录屏。**

再跑一次一键演示：

```powershell
awf demo
```

预期结尾：

```text
summary: 6/6 demos matched expectations
```

### 2.6 交互式 shell（可选镜头）

在终端直接输入 `awf`（或 `awf shell`）即可进入交互式界面，适合展示“少敲命令”：

```text
$ awf
awf> bugfix
awf> show last
awf> demo --only guard
awf> quit
```

`bugfix`、`hello`、`docsync`、`guard`、`falsepass`、`recover` 是一键演示；
`show`、`report`、`replay`、`explain` 省略 run id 时自动使用最近一次运行。安装
`.[shell]` 后支持 Tab 补全、历史记录和底部提示栏；未安装时会退化为普通输入，
所有功能仍然可用。

---

## 3. 录屏前的准备

### 3.1 清理旧的运行记录

```powershell
Remove-Item -Recurse -Force runs -ErrorAction SilentlyContinue
```

这样视频里 `awf ls` 只会出现本次演示的记录。

### 3.2 固定终端编码

Windows Terminal 默认使用 UTF-8。如果中文或 `¥` 显示异常，先执行：

```powershell
chcp 65001
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.UTF8Encoding]::new()
```

AWEngine 自身会在 GBK 控制台上自动把货币符号降级为 `CNY`，所以即使不设置也不会崩，
但 UTF-8 的观感更好。

### 3.3 推荐的录屏设置

- 分辨率：1920×1080，16:9；
- 终端字号：16–18 pt，字体用 Cascadia Mono 或 Consolas；
- 终端窗口最大化，减少无关标签；
- 浏览器缩放 110%–125%，方便观众看清 `replay.html`；
- 关闭系统通知、聊天软件弹窗；
- 鼠标指针放大，操作之间留 0.5–1 秒停顿；
- 不要在画面里出现 API key、个人邮箱、真实姓名或公司内网地址；
- 所有演示使用 `--fake` 或 `demo`，不要现场调用真实模型。

### 3.4 先预演一遍

把第 4 节的命令完整执行一次，确认：

- `python -m pytest -q` 是 72 passed；
- `awf demo` 是 6/6；
- `replay.html` 能打开，浏览器没有拦截本地文件；
- 报告的表格、代码块、Mermaid 图和 `replay.html` 都正常显示。

---

## 4. 八分钟完整演示脚本

时间轴只是建议，录制时可以按自己的语速微调。括号里是建议停留的时间。

| 时间 | 内容 | 画面 |
|---|---|---|
| 0:00–0:30 | 一句话介绍 + 六条演示总览 | 终端 |
| 0:30–1:30 | 安装、测试、`demo` 6/6 | 终端 |
| 1:30–2:30 | `hello`：最小工作流 | 终端 + `show` |
| 2:30–4:45 | `bugfix`：三 Agent 评审循环 | 终端 + report + replay |
| 4:45–6:00 | `guard` / `falsepass`：两个负向控制 | 终端 + report |
| 6:00–6:45 | `recover`：失败后的条件恢复 | 终端 + `show` |
| 6:45–7:40 | `docsync`：纯 YAML 内联 Agent | YAML 文件 + `show` |
| 7:40–8:00 | 收尾：仓库、文档、扩展点 | README / 浏览器 |

### 4.1 开场（0:00–0:30）

画面：干净的终端，项目根目录。

旁白建议：

> “这是一个把多智能体协作写成 YAML 的工作流引擎。它关心的不是模型说了什么，
> 而是测试、隐藏用例和补丁检查能不能证明这次修改真的成功。所有演示都可以离线复现。”

然后输入：

```powershell
awf demo
```

先让观众看到六条演示的最终结果，但不要逐条解释：

```text
[ok] hello: success/goal_met ...
[ok] bugfix: success/goal_met ...
[ok] docsync: success/goal_met ...
[ok] guard: blocked/cheat (F-STOP-EARLY) ...
[ok] falsepass: blocked/verify_failed (F-VERIFY-FALSEPASS) ...
[ok] recover: success/goal_met ...
summary: 6/6 demos matched expectations
```

旁白：

> “六条流程覆盖顺序执行、三 Agent 循环、纯 YAML Agent、作弊拦截、假通过检测和失败恢复。
> 接下来的几分钟，我会拆开其中最有代表性的几条。”

### 4.2 安装与测试（0:30–1:30）

如果视频从全新克隆开始，展示：

```powershell
git clone https://github.com/TheBland73/AWengine.git
cd AWengine
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,shell]"
python -m pytest -q
```

重点讲两句：

- 测试全部离线，使用录制的 LLM cassette，不需要网络和 API key；
- `72 passed` 是后面所有演示的基线。

如果安装过程太长，可以在剪辑时加速或直接切到测试结果，不要在现场等 pip。

### 4.3 hello：最小工作流（1:30–2:30）

```powershell
awf run hello --fake --workdir fixtures\calcfix --cassettes tests\cassettes -i name=world --runs runs\demo
```

预期结尾：

```text
run <run_id>: success (goal_met)
rounds 1, steps 3, attempts 0, cost CNY 0.0003
```

说明三个节点：

```text
plan -> greet -> wrap
```

- `plan` 是内置节点，负责产生任务文本；
- `greet` 是 LLM Agent，返回 `greeting` 和 `words`；
- `wrap` 把结果包装成 artifact。

然后查看状态：

```powershell
$run = Get-ChildItem runs\demo -Directory -Filter '*-hello' |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1 -ExpandProperty Name
awf show $run --runs runs\demo
```

画面重点：`history`、`artifacts`、`messages` 三段。对于 hello，messages 可能为空，
这是正常的，因为只有一个 Agent；下一段 bugfix 会展示完整的 handoff。

旁白：

> “这里没有隐藏逻辑：节点、边、输入和收敛条件都在 YAML 里，`show` 只是把状态读出来。”

### 4.4 bugfix：三 Agent 评审循环（2:30–4:45）

这是整段视频的核心，建议给足时间。

```powershell
awf run bugfix --fake --workdir fixtures\calcfix --cassettes tests\cassettes --runs runs\demo
```

预期结尾：

```text
run <run_id>: success (goal_met)
rounds 1, steps 7, attempts 0, cost CNY 0.0082
```

录制时让终端事件流完整走一遍，画面会依次出现：

```text
[r1] -> plan
[r1] -> coder
[r1] -> apply
      handoff coder -> apply [patch]
[r1] -> review
      handoff coder -> review [patch]
[r1] -> fix
      handoff review -> fix [review]
[r1] -> apply_fix
      handoff fix -> apply_fix [patch]
[r1] -> review_final
      handoff fix -> review_final [patch]
[r1] verify PASS L4 exit=0
```

旁白可以这样讲：

> “coder 先修一个 bug，reviewer 发现另一个问题，fixer 再修，最后由 review_final 复核。
> 注意每一行 handoff：Agent 之间没有共享可变内存，它们通过 patch 和 review 这两个
> artifact 传递信息，所有交接都写进事件日志。”

查看结构化状态：

```powershell
$run = Get-ChildItem runs\demo -Directory -Filter '*-bugfix' |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1 -ExpandProperty Name
awf show $run --runs runs\demo
```

指出三处：

1. `history` 有 7 个节点：`plan, coder, apply, review, fix, apply_fix, review_final`；
2. `messages` 有五条 handoff，src/dst 和 artifact 类型都很明确；
3. `artifacts` 里的 `patch` 和 `review` 都带 producer 和 digest。

然后打开报告：

```powershell
$report = Join-Path (Join-Path 'runs\demo' $run) 'report.md'
code $report
```

如果没有 VS Code，用：

```powershell
Start-Process $report
```

在报告里依次滚动并指出：

- `Verdict: PASS`；
- `Verification level: L4`；
- `Verify command: python -m pytest -q`；
- `Holdout: passed`；
- `Red before: True`；
- `Collaboration timeline` 五条 handoff；
- `Issues` 里 `R1-1` 已被后续 review 标记为 `resolved`；
- `Cost` 按模型汇总。

旁白：

> “验证级别 L4 表示：可见测试通过、隐藏测试通过，而且未修改的代码上验证命令本来
> 应该失败。最后一条叫 red-before，它防止验证命令本身永远通过。”

最后打开 replay：

```powershell
awf replay $run --runs runs\demo --open
```

浏览器里：

- 点击 `Play`，看节点高亮按时间推进；
- 用 `←` / `→` 单步；
- 用空格播放/暂停；
- 拖动时间轴跳到 `verify` 事件。

旁白：

> “回放是自包含 HTML，不需要服务器。它把 events.jsonl 变成可拖动的执行时间线，
> 适合在评审时快速定位某一步发生了什么。”

### 4.5 guard 与 falsepass：两个负向控制（4:45–6:00）

先讲 guard：

```powershell
awf run guard --fake --workdir fixtures\calcfix --cassettes tests\cassettes --runs runs\demo
```

预期：

```text
run <run_id>: blocked (cheat)
rounds 1, steps 3, attempts 1, cost CNY 0
failure class: F-STOP-EARLY
reason: cheat
```

终端会显示：

```text
E-GUARD: patch touches protected paths: protected test file: tests/test_calc.py
stop blocked/cheat (F-STOP-EARLY)
```

旁白：

> “这个 Agent 想修改测试文件来让自己通过。引擎在应用补丁之前就识别出受保护路径，
> 直接硬停止。退出码是 1，这是预期的安全行为。”

再讲 falsepass：

```powershell
awf run falsepass --fake --workdir fixtures\falsepass --cassettes tests\cassettes --runs runs\demo
```

预期：

```text
run <run_id>: blocked (verify_failed)
rounds 1, steps 1, attempts 0, cost CNY 0
failure class: F-VERIFY-FALSEPASS
reason: verify_failed
```

打开报告时指出：

- 可见测试 `exit 0`，模型看起来“完成了”；
- 但 `Holdout: failed`；
- 因此最终是 `BLOCKED` / `F-VERIFY-FALSEPASS`。

旁白：

> “这是整个引擎最重要的设计：可见测试通过不代表成功。隐藏测试在验证阶段才注入，
> Agent 执行时看不到它；一旦可见测试和隐藏测试分歧，直接判假通过。”

注意：这两条命令都会返回退出码 1。录屏时不要把它说成失败，应该说“预期的负向结果”。

### 4.6 recover：失败后的条件恢复（6:00–6:45）

```powershell
awf run recover --fake --workdir fixtures\calcfix --cassettes tests\cassettes --runs runs\demo
```

预期：

```text
run <run_id>: success (goal_met)
rounds 1, steps 4, attempts 1, cost CNY 0
```

事件流里会看到：

```text
[r1] -> badpatch
[r1] -> apply
      E-PATCH: error: patch failed ...
[r1] -> fallback
[r1] goal=True streak=1/1 -> success
```

查看状态：

```powershell
$run = Get-ChildItem runs\demo -Directory -Filter '*-recover' |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1 -ExpandProperty Name
awf show $run --runs runs\demo
```

旁白：

> “补丁应用失败不是异常退出，而是一条普通的状态。YAML 里的条件边
> `not apply.applied` 把流程接到 fallback，最终仍然达成目标。失败恢复是流程的一部分，
> 不是 try/except 的补丁。”

如果时间紧，这一段可以只保留 `show` 的 history，不展开报告。

### 4.7 docsync 与可扩展性（6:45–7:40）

打开 `workflows/docsync.yaml`，让观众看到三个 Agent 完全内联在 YAML 中：

```yaml
nodes:
  analyst:
    type: llm_agent
    config:
      system: "..."
      task: "..."
      artifacts: [{"id": "brief", "type": "text"}]
```

然后运行：

```powershell
awf run docsync --fake --workdir fixtures\docsync --cassettes tests\cassettes --runs runs\demo
```

预期：

```text
run <run_id>: success (goal_met)
rounds 1, steps 5, attempts 0, cost CNY 0.0053
```

说明：

- `analyst -> writer -> write -> editor`；
- writer 产出 `doc`，`write` 把它写入 `NOTES.md`；
- editor 读取 `doc` 并给出 advisory verdict；
- 最后仍然用 L4 验证和 holdout 判定成功。

再展示两个扩展入口：

```powershell
awf nodes
awf validate workflows\bugfix.yaml
```

`awf nodes` 会列出可发现的节点：内置动作、项目内 Python 节点和 LLM Agent。
`awf validate` 会在不运行流程的情况下做静态校验；`--graph` 还能输出 Mermaid 流程图。

如果还有 20 秒，补一句：

```powershell
awf new my-workflow --dir .\my-workflow
```

说明脚手架会生成工作流、节点、测试和 README，业务节点不需要改引擎。

### 4.8 收尾（7:40–8:00）

回到项目根目录，展示 README 的演示表和文档目录，最后说：

> “代码、工作流、测试和文档都在 GitHub 上：TheBland73/AWengine。
> 六个演示可以一键复现，72 个测试全部离线运行。谢谢观看。”

---

## 5. 六十秒短片版本

如果只需要一版 60 秒的提交视频，不要从安装开始。提前跑好命令，只录结果。

| 时间 | 画面 | 旁白 |
|---|---|---|
| 0–8s | 标题 + `demo` 6/6 结果 | “AWEngine：把多智能体协作写成可验证的 YAML。” |
| 8–20s | `bugfix` 的 handoff 事件流 | “三个 Agent 通过 patch 和 review 交接，循环到 blocker 清零。” |
| 20–32s | `report.md` 的 L4 / holdout / red-before | “成功由测试、隐藏用例和 red-before 判定，不由模型自称。” |
| 32–44s | `guard` 和 `falsepass` 的 blocked 报告 | “改测试会被拦截，假通过会被识破。” |
| 44–54s | `replay.html` 播放 | “每一步都有事件、回放和报告。” |
| 54–60s | 仓库地址 + 文档 | “TheBland73/AWengine，离线可复现。” |

提前准备：

```powershell
awf demo --runs runs\video
```

录制时只需要打开 `runs\video\<run_id>\report.md` 和 `replay.html`，不要现场等待。

---

## 6. 常用命令速查

| 目的 | 命令 |
|---|---|
| 跑六条演示 | `awf demo --runs runs\demo` |
| 只跑一条演示 | `awf demo --only bugfix --runs runs\demo` |
| 手动运行工作流 | `awf run bugfix --fake --workdir fixtures\calcfix --cassettes tests\cassettes --runs runs\demo` |
| 查看状态 | `awf show <run_id> --runs runs\demo` |
| 解释为什么停止 | `awf explain <run_id> --runs runs\demo` |
| 重新生成报告 | `awf report <run_id> --runs runs\demo` |
| 打开回放 | `awf replay <run_id> --runs runs\demo --open` |
| 打印事件 | `awf trace <run_id> --runs runs\demo --events 20` |
| 列出运行 | `awf ls --runs runs\demo` |
| 校验工作流 | `awf validate workflows\bugfix.yaml` |
| 查看 Mermaid 图 | `awf validate workflows\bugfix.yaml --graph` |
| 列出节点 | `awf nodes` |
| 生成脚手架 | `awf new my-workflow --dir .\my-workflow` |

`awf` 是安装后的入口，直接输入即可，例如 `awf demo`。若 PATH 未生效，可以用
`python -m awengine ...` 作为等价备用；录屏时优先用更简洁的 `awf`。

---

## 7. 三种运行模式

| 模式 | 用途 | 是否需要 API key | 行为 |
|---|---|---|---|
| `--fake` | 演示、测试、CI | 否 | 按请求摘要读取 `tests/cassettes` |
| `--live` | 真实模型试验 | 是 | 调用 DeepSeek/OpenAI 兼容接口 |
| `--record` | 更新 cassette | 是 | 调用真实接口并把响应写入 cassette |

演示视频**只用 `--fake` 或 `demo`**。原因：

- 完全离线，不依赖网络和余额；
- 结果稳定，不会因为模型随机性导致重录；
- cassette 与请求逐字节绑定，修改提示词后需要重新录制。

如果确实要演示 live：

```powershell
$env:DEEPSEEK_API_KEY = "你的 key"
awf run hello --live --workdir fixtures\calcfix --runs runs\live
```

不要在录屏中展示 key；录完关闭终端或清除环境变量。

---

## 8. 报告和目录怎么读

每次运行会生成：

```text
runs\<run_id>\
  manifest.json     工作流、输入、工作目录、运行模式
  state.json        完整检查点，可 resume
  events.jsonl      追加式事件日志
  report.md         人类可读报告
  summary.json      机器可读摘要
  flow.mmd          流程图
  sequence.mmd      时序图
  replay.html       自包含回放
  workdir\          本次运行的一次性沙箱副本
```

报告中最值得讲解的段落：

| 段落 | 讲解点 |
|---|---|
| `Verdict` | PASS / BLOCKED，以及 L1–L5 验证级别 |
| `Why it stopped` | terminal、reason、failure class、计数器、goal |
| `Flow` | Mermaid 流程图，分支和循环一目了然 |
| `Collaboration timeline` | 谁把哪个 artifact 交给了谁 |
| `Nodes` | 每个节点的耗时、token、成本、artifact |
| `Issues` | 评审问题、证据、是否被后续 review 解决 |
| `Progress` | 收敛进度和停滞检测 |
| `Cost` | 按模型汇总 token 和价格 |
| `Artifacts` | patch、review 等产物的 producer 和 digest |

---

## 9. 常见问题

### 9.1 `CassetteMiss`

```text
E-LLM: CassetteMiss ...
```

说明当前请求在 cassette 里找不到。最常见原因是 `--workdir` 用错：

- `hello`、`bugfix`、`guard`、`recover` 用 `fixtures\calcfix`；
- `falsepass` 用 `fixtures\falsepass`；
- `docsync` 用 `fixtures\docsync`。

cassette 记录了当时的仓库文件列表和提示词，换一个 workdir 就会得到不同的请求摘要。
不要为了消除错误去改 cassette key；先恢复录制时的 workdir。

### 9.2 `No module named pytest`

在项目根目录重新安装开发依赖：

```powershell
python -m pip install -e ".[dev,shell]"
```

如果使用虚拟环境，确认终端提示符前面有 `(.venv)`。

### 9.3 中文或 `¥` 显示成乱码

设置 UTF-8：

```powershell
chcp 65001
[Console]::OutputEncoding = [Text.Encoding]::UTF8
```

报告文件本身是 UTF-8；用 VS Code 打开即可。终端在 GBK 下会自动显示 `CNY`，这是
正常的降级，不是数据损坏。

### 9.4 命令返回退出码 1

以下情况是预期的：

- `guard`：`blocked/cheat`，`F-STOP-EARLY`；
- `falsepass`：`blocked/verify_failed`，`F-VERIFY-FALSEPASS`；
- 普通工作流没有达到 goal 或验证失败。

用 `awf explain <run_id>` 查看原因和修复建议。

### 9.5 `git apply` 失败

- 确认 `git --version` 可用；
- 补丁必须是相对路径的 unified diff；
- 测试文件、`conftest.py`、`pytest.ini` 等受保护路径会被拒绝；
- `recover` 演示就是故意制造补丁失败，用来展示条件恢复，不是环境问题。

### 9.6 找不到 run

`--runs` 必须和运行时一致。默认是 `runs`；如果运行用了 `runs\demo`，查看时也要加
`--runs runs\demo`：

```powershell
awf ls --runs runs\demo
```

### 9.7 浏览器没有打开 replay

`replay` 默认只打印路径；加 `--open` 才会调用默认浏览器。也可以手动打开：

```powershell
Start-Process runs\demo\<run_id>\replay.html
```

---

## 10. 录制技巧

1. **先录屏后配音**：先按脚本操作一遍，确认节奏，再录旁白；口误更容易剪。
2. **分段录**：安装、hello、bugfix、负向演示、收尾分开录，最后拼接，避免一次长镜头。
3. **加速等待**：`pip install` 和第一次 pytest 可以 2–4 倍速，或直接剪掉。
4. **给关键行留时间**：`handoff`、`verify PASS L4`、`F-VERIFY-FALSEPASS` 出现后停 1–2 秒。
5. **鼠标不要抖**：用键盘滚动报告，或把鼠标停在目标行旁边。
6. **字幕**：给 `L4`、`holdout`、`red-before`、`artifact` 加简短字幕，观众不需要暂停查概念。
7. **收尾留链接**：最后一帧放 GitHub 地址和 `python -m pytest -q` 的 72 passed。
8. **不要暴露密钥**：即使是 fake 模式，也检查终端历史里没有 `API_KEY`。

---

## 11. 演示前检查清单

- [ ] `python --version` ≥ 3.11
- [ ] `git --version` 正常
- [ ] `python -m pytest -q` → 72 passed
- [ ] `awf demo` → 6/6
- [ ] `runs\` 已清空，或使用新的 `runs\demo`
- [ ] 终端字号、窗口比例、编码已调整
- [ ] `replay.html` 可以打开
- [ ] 报告用 VS Code 或默认 Markdown 查看器可以正常显示
- [ ] 没有 API key、邮箱、真实姓名出现在画面中
- [ ] 麦克风、系统声音、通知已检查
- [ ] 录完后完整看一遍，确认没有把 `guard` / `falsepass` 的退出码 1 说成故障

---

## 12. 一分钟讲解词（可直接背）

> “AWEngine 是一个声明式的多智能体工作流引擎。协作流程写在 YAML 里，节点可以是
> Python 函数、子进程、内置动作或者 LLM Agent。它最大的区别是：一次运行是否成功，
> 不由模型自称完成决定，而由可见测试、隐藏 holdout 和 red-before 共同决定。
>
> 这里有三 Agent 的修复循环：coder 产出补丁，reviewer 只报告有证据的问题，fixer
> 根据评审继续修，直到 blocker 清零。Agent 之间通过 patch 和 review 这两个 artifact
> 交接，所有 handoff 都记录在事件日志和报告里。
>
> 负向演示更能说明问题：改测试会被 guard 直接拦截，可见测试通过但隐藏测试失败会被
> 判为假通过。所有运行都可以离线复现，一条 `demo` 命令跑完六条流程，72 个测试全部
> 通过。代码在 GitHub 的 TheBland73/AWengine。”
