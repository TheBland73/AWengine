# 工作流 YAML 参考

一个工作流文件就是一个 `WorkflowSpec`。加载期会做结构校验；错误信息带
文件名与行号（`SpecError`，退出码 2）。工作流文件默认放在
`workflows/<name>.yaml`，`awf run <name>` 会依次在 `cwd`、`cwd/workflows`、
包内 `workflows/` 查找。

## 1. 顶层字段

```yaml
name: bugfix                 # 必填，唯一
version: 1                   # 默认 1
defaults:                    # 可选
  model: deepseek-chat
  temperature: 0.0
  max_tokens: 2048
  timeout_s: 120
  max_steps: 6
nodes: {...}                 # 必填；list 或 name -> node 映射
edges: [...]                 # 必填
converge: {...}              # 可选，见第 5 节
verify: {...}                # 可选，见第 6 节
```

`spec_hash` 由除 `source_path` 外的全部字段计算；`--disable` 会生成一个
**新的** spec 与 hash，因此报告描述的永远是实际执行图。

## 2. 节点

推荐用映射形式（键即节点名）：

```yaml
nodes:
  plan:
    type: builtin
    impl: echo
    config: {value: "Fix the bug"}
  coder:
    type: llm_agent
    impl: awengine.nodes.code.coder
    config: {task: "make add() return the sum", max_steps: 4}
    contract:
      required: [summary, patch]
      types: {summary: str, patch: dict}
    retry: 1
    timeout_s: 120
```

也支持列表形式：

```yaml
nodes:
  - {name: plan, type: builtin, impl: echo, config: {value: hi}}
```

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `name` | str | 必填 | 节点唯一名；表达式与输出以它为键 |
| `type` | `python`/`process`/`llm_agent`/`builtin` | 必填 | 运行时 |
| `impl` | str | 空 | `pkg.mod:attr` 或 `pkg.mod.attr`；`python`/`builtin` 必填 |
| `cmd` | str | 空 | `process` 必填，例如 `"python node.py"` |
| `config` | dict | `{}` | 运行时配置，见下 |
| `contract` | dict | 无 | `required` / `types` / `min_items` |
| `retry` | int | 0 | 失败后额外重试次数（总尝试 = retry+1） |
| `timeout_s` | int | 120 | 节点超时 |
| `enabled` | bool | true | false 时加载期旁路；也可 `--disable` |

### 2.1 `type: python`

`impl` 指向模块里的 `run(ctx) -> NodeResult`。模块可以放在
`nodes/<name>.py`，由 `awengine.nodes.registry` 自动发现。

```python
from awengine.contracts import NodeResult

def run(ctx):
    return NodeResult(status="ok", output={"answer": 42})
```

### 2.2 `type: process`

子进程从 stdin 读一个 JSON 信封，向 stdout 写一个 JSON 对象：

```json
// stdin
{"node": "scan", "round": 1, "attempt": 0, "inputs": {}, "workdir": "...",
 "artifacts": {}, "counters": {"rounds": 1, "steps": 0, "attempts": 0}}
// stdout
{"status": "ok", "output": {"files": ["a.py"]},
 "artifacts": [{"id": "listing", "type": "report", "content": {"files": ["a.py"]}}]}
```

非零退出码、超时、非 JSON stdout 都有明确失败/降级路径。命令在沙箱 cwd
下执行，不支持 shell 语法。

### 2.3 `type: builtin`

| impl | 作用 | config |
|---|---|---|
| `noop` | 什么都不做 | — |
| `echo` | 原样输出 `message` | `value` / `message` |
| `log` | 写一条 `node_log` 事件 | `value` |
| `set` | 写输出与 artifact | `values: {k: v}`、`artifacts: [...]` |
| `assert` | 条件不成立则失败 | `condition`、`message` |
| `write_file` | 在沙箱写文件 | `path`、`content` |
| `apply_patch` | 用 `git apply` 应用 artifact | `artifact`（默认 `patch`）、`reads` |
| `call_tool` | 调一次内置工具 | `tool`、`arguments` |

`apply_patch` 会拒绝修改测试/`conftest.py`/`pytest.ini` 等受保护路径，
以及绝对路径与 `..` 穿越；违规时返回 `E-GUARD` 并带 `cheat=True`。

### 2.4 `type: llm_agent`

`impl` 指向一个模块，模块提供 `AGENT` dict（或 `agent` 函数）：

```python
AGENT = {
    "name": "coder",
    "system": "...",                  # system prompt
    "task": "...",                    # 可用 $input、$artifact、$node.key
    "tools": ["read_file", "search_code", "list_files", "run_cmd"],
    "reads": ["patch"],               # 注入 artifact 并产生 handoff 消息
    "artifacts": [{"id": "patch", "type": "patch", "from": "patch"}],
    "max_steps": 6,
    "normalize": callable,            # 可选：校验/补全模型 JSON
    "repo_map": True,                 # 可选：false 关闭仓库清单
}
```

运行时：

1. 组装 system + repo map + 历史摘要 + artifact + task；
2. 调 provider，最多 `max_steps` 轮；
3. 模型请求的工具必须在 `tools` 白名单内，否则记录 `tool_call denied`；
4. 最终文本按 JSON 解析（容忍 ```json 围栏），失败即节点失败；
5. `normalize` 可修正/裁剪输出；
6. 按 `artifacts` 生成 artifact（`from: "__self__"` 表示复制输出中的
   非 `_` 前缀键）；
7. 每次调用记账并发出 `llm_call`。

可用的工具：`read_file`、`search_code`、`list_files`、`run_cmd`。
写文件/打补丁的工具对 Agent 默认禁用；修改代码必须通过 `patch` artifact
和引擎的 `apply_patch`。

`config` 可覆盖 `system`、`task`、`tools`、`reads`、`artifacts`、
`max_steps`、`repo_map`、`context`（见第 7 节）。

#### 2.4.1 纯 YAML 内联 Agent（无 Python 模块）

`impl` 可以省略，此时必须提供 `config.system` 和 `config.task`。校验发生在
`loader.py`：缺少两者会报 `SPEC_NODE_INVALID`。完整示例见
`workflows/docsync.yaml`：

```yaml
nodes:
  writer:
    type: llm_agent
    config:
      system: "You are a meticulous documentation editor."
      task: |
        Read the analyst brief and write a concise Markdown note.
        Brief: $analyst.brief
      tools: [read_file, search_code, list_files]
      artifacts: [{"id": "doc", "type": "document", "from": "doc"}]
```

`normalize` 是可选的点分路径（加载时 import）；它接收模型 JSON，返回修正后的
dict。内联 Agent 与模块 Agent 共享同一套上下文组装、工具白名单、artifact
生成和成本记账逻辑；`workflows/docsync.yaml` 的 `analyst`、`writer`、
`editor` 都是内联形式。

## 3. 边与表达式

```yaml
edges:
  - {src: plan, dst: coder}
  - {src: review, dst: END, when: "review.blockers == 0"}
  - {src: review, dst: fix, when: "review.blockers > 0"}
```

- 每个节点按 YAML 顺序检查出边，**第一条** `when` 为真的边获胜；
  没有 `when` 的边恒真；
- `dst: END` 结束图；
- 入口必须唯一（没有入边的节点），且至少有一条到 END 的路径；
- 回边由 DFS 识别，`loop_heads` 是回边目标；引擎只在真正走回边时结束轮。

表达式语言（`awengine/expr.py`，不使用 `eval`）：

- 变量：节点输出（`plan.message`）、`inputs.*`、`artifacts.<id>`、
  `verify.*`、`counters.*`、`runtime.*`、`messages`、`blockers`；
- 运算：`and or not`、`== != < <= > >=`、`in / not in`、`+ - %`、
  列表/元组/常量、属性与下标；
- 不允许函数调用、推导式、lambda、dunder、私有属性；
- 缺失变量求值为 `None`，布尔上下文为 `False`，同时记录到 missing 列表。

## 4. 契约（handoff）

```yaml
contract:
  required: [summary, patch]
  types: {summary: str, patch: dict}
  min_items: {issues: 1}
```

节点返回后立即检查；不满足则视为失败（`F-COORD-HANDOFF`），按 `retry`
重试，重试耗尽后停止。`types` 支持 `str/int/float/bool/dict/list`。

## 5. 收敛（converge）

```yaml
converge:
  goal: "verify.passed"          # 布尔表达式；缺省时 verify 存在则用 verify.passed
  streak: 1                      # goal 连续成立多少轮才算成功
  rounds: {min: 1, max: 2}       # 轮数下限/上限
  steps: {min: 3, max: 12}       # 全 run 节点步数
  attempts: {min: 0, max: 3}     # 失败尝试总数
  cost: {max_cny: 0.50}          # 成本上限（CNY）
  progress:
    key: "verify.passed"         # 每轮记录的值
    window: 2                    # 连续相同则停滞
    metric: "usage.cost"         # 可选：要求严格下降的指标
  escalate:
    after_round: 1
    model: deepseek-reasoner
    temperature: 0.2
    rubric: "Focus on the failing assertion."
    max_times: 1
  on_exhausted: report           # report -> blocked；fail -> exited
```

判定优先级（`guard.evaluate_round`）：

1. cheat（受保护文件被改）→ `blocked`；
2. rounds/steps/attempts/cost 超限 → `exhausted`（或 `exited`）；
3. progress 停滞 → `stalled`；
4. goal 成立且 streak 达标且轮数达标 → `success`；
5. 图结束但 goal 不成立 → `blocked`。

失败分类见 [`error-codes.md`](error-codes.md)。

## 6. 验证（verify）

```yaml
verify:
  level: 4
  cmd: "python -m pytest -q"
  holdout: "../fixtures/holdout/test_calc_holdout.py"
  red_before: true
  timeout_s: 120
```

| 字段 | 说明 |
|---|---|
| `level` | 1..5；>=2 且配置 holdout 时执行隐藏测试，>=3 建议配 `red_before`（加载期警告） |
| `cmd` | 在沙箱中执行的可见验证命令；退出码 0 即通过 |
| `holdout` | 相对路径按 workflow 文件目录解析，再按沙箱；缺失直接报 `SPEC_VERIFY_HOLDOUT_MISSING` |
| `red_before` | 未修改副本上 `cmd` 必须先失败，否则 `blocked` + `verify_weak` |
| `timeout_s` | 验证命令超时 |

判定：

- 可见通过 + holdout 通过 → `passed`；
- 可见通过 + holdout 失败 → `F-VERIFY-FALSEPASS`；
- 可见失败 → verify 不通过（通常导致下一轮或 exhausted）；
- holdout 缺失 → `F-VERIFY-WEAK`。

## 7. 上下文策略（llm_agent）

```yaml
config:
  context:
    sources: [input, history, repo_map, artifacts]
    window: 2                 # 历史上限
    compaction: summarize     # none|summarize|drop|mask
    max_tokens: 12000
    isolation: none           # none|fresh
    mask_observations: true
```

裁剪顺序：屏蔽大观察 > 摘要旧历史 > 丢弃 > 溢出失败
（`F-CONTEXT-OVERFLOW`）。`prefix_sha` 写入 `llm_call` 事件，便于观察
prompt 前缀是否稳定。

## 8. 完整示例

见 `workflows/bugfix.yaml`（三 Agent + 循环 + 分支 + holdout）与
`workflows/hello.yaml`（最小 LLM Agent）、`workflows/uppercase.yaml`
（Python 节点）。
