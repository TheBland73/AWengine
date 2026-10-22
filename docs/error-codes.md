# 错误码与失败分类

AWEngine 把"**异常**"（进程内错误，带退出码）和"**停止分类**"（一次 run
为什么结束，写入 `state.failure_class`）分开。这样报告可以先解释"为什么
停"，再解释"哪里出错"。

## 1. 退出码

| 退出码 | 含义 |
|---|---|
| 0 | 成功（`terminal=success`） |
| 1 | 未通过/未完成（blocked、exhausted、stalled、no_op） |
| 2 | 用法或规格错误（参数、YAML、pricing、缺 key） |
| 3 | 内部错误（未预期异常、契约/状态损坏） |
| 130 | Ctrl+C 中断 |

`awf run` 的退出码由 `engine.outcome_exit_code()` 决定；`resume` 沿用同一
规则。

## 2. 规格加载错误（`SpecError`，退出码 2）

带文件名与行号，格式形如
`demo.yaml:12: [SPEC_UNKNOWN_EDGE] edge to unknown node 'x'`。

| 代码 | 触发条件 |
|---|---|
| `SPEC_NOT_FOUND` | 找不到工作流文件 |
| `SPEC_TOO_LARGE` | 文件超过 512 KiB |
| `SPEC_PARSE` | YAML 语法错误 |
| `SPEC_ROOT` | 根不是映射 |
| `SPEC_NAME` / `SPEC_VERSION` | name 为空 / version 非正整数 |
| `SPEC_NO_NODES` | nodes 为空 |
| `SPEC_DUP_NODE` | 节点重名 |
| `SPEC_NODE_INVALID` | 类型、字段、impl 缺失等 |
| `SPEC_UNKNOWN_BUILTIN` | builtin impl 不存在 |
| `SPEC_EDGE_INVALID` | 边格式错误 |
| `SPEC_UNKNOWN_EDGE` | 边指向不存在的节点 |
| `SPEC_NO_ENTRY` / `SPEC_MULTI_ENTRY` | 入口不唯一 |
| `SPEC_DEFAULT_INVALID` | defaults 字段非法 |
| `SPEC_CONVERGE_INVALID` | converge 字段非法 |
| `SPEC_CONVERGE_UNREACHABLE` | 配置了需要循环的规则，但图里没有环 |
| `SPEC_VERIFY_INVALID` | verify 字段非法 |
| `SPEC_VERIFY_HOLDOUT_MISSING` | holdout 文件不存在（相对 workflow 文件目录解析） |
| `SPEC_UNKNOWN_DISABLE` | `--disable` 指定了不存在的节点 |
| `SPEC_DISABLED_AMBIGUOUS` | 被禁用节点有多个入边或出边，无法安全旁路 |
| `SPEC_NO_ACTIVE_NODE` | 所有节点都被禁用 |

## 3. 运行时错误

| 代码 | 退出码 | 含义/恢复 |
|---|---|---|
| `E-INPUT` / `E-INPUT-FILE` | 2 | `-i` 不是 `k=v` / 输入文件不可读 |
| `E-DEMO` | 2 | `awf demo --only` 指定了未知演示 |
| `E-EXPR-EMPTY` / `E-EXPR-SYNTAX` / `E-EXPR-FORBIDDEN` / `E-EXPR-TOO-LONG` | 3 | 表达式不合法；检查 `when`/`goal` |
| `E-NODE` | 3 | Python 节点抛异常；失败可重试 |
| `E-PROCESS` | 3 | 子进程非零、超时或输出非 JSON |
| `E-PATCH` | 3 | `git apply` 失败；通常让 coder 重新生成补丁 |
| `E-GUARD` | 1 | 补丁触碰受保护文件/路径；run 直接 `blocked`（cheat） |
| `E-EXEC-UNSAFE` / `E-EXEC-DENIED` | 3 | 命令不在白名单/试图用 shell 特性 |
| `E-EXEC-NOTFOUND` | 3 | 可执行文件不存在 |
| `E-EXEC-PATH` / `E-EXEC-READ` | 3 | 沙箱路径逃逸或读不到文件 |
| `E-EXEC-PROTECTED` | 3 | 节点试图写测试/配置 |
| `E-EXEC-SYMLINK` | 3 | 起始目录含符号链接（拒绝复制） |
| `E-EXEC-COPY` | 3 | 复制沙箱失败 |
| `E-EXEC-EMPTY` / `E-EXEC-WORKDIR` | 3 | 命令为空 / workdir 无效 |
| `E-LLM` | 3 | provider 调用失败、返回非 JSON |
| `E-LLM-NOKEY` | 2 | 需要真实模型但无 key |
| `F-COORD-HANDOFF` | 3 | 节点输出不满足 `contract` |
| `F-COORD-STATE` | 3 | 状态文件损坏、版本不兼容、run 被锁 |
| `F-CONTEXT-OVERFLOW` | 3 | 上下文裁剪后仍超预算 |
| `E-STATE-SPEC-MISMATCH` | 3 | resume 时 spec_hash 与存档不符 |
| `E-SCAFFOLD-EXISTS` / `E-SCAFFOLD-NAME` | 2 | `awf new` 目标非空 / 名字非法 |
| `E-INTERNAL` | 3 | 兜底内部错误 |

## 4. 停止分类（`failure_class`）

| 分类 | 触发场景 | 建议 |
|---|---|---|
| `F-STOP-EARLY` | 成本/尝试/作弊等提前终止 | 提高预算或修正 patch |
| `F-STOP-LATE` | 轮数/步数耗尽、progress 停滞 | 改小任务、改 rubric、检查 verify |
| `F-COORD-HANDOFF` | 契约不满足、Agent 输出丢失 | 看 `report.md` 的 handoff 与 issues |
| `F-COORD-STATE` | 检查点/租约异常 | `awf show`、必要时 `resume` |
| `F-VERIFY-WEAK` | holdout 缺失、red_before 失效 | 补 holdout 或修 verify 命令 |
| `F-VERIFY-FALSEPASS` | 可见测试过但 holdout 失败，或声称成功却未通过 | 检查是否改测试、是否只修表象 |
| `F-CONTEXT-OVERFLOW` | 上下文超预算 | 调小 `context.max_tokens` 或改 compaction |
| `F-SPEC-AMBIG` | 无进展、规则互相矛盾 | 检查 goal/rounds/escalate |
| `F-SPEC-MISSING` | pricing/API key 等缺失 | 补 `pricing.toml` 或环境变量 |

`guard.classify_stop()` 只用规则映射，不调用模型；同一份 state 重放会得到
同一分类。

## 5. 恢复路径

1. 读 `report.md` 的 **Why it stopped** 与 **Issues**；
2. `awf explain <run_id> --json` 看结构化原因与建议；
3. 修 workflow/输入/补丁后重新 `awf run`；
4. 已终态 run 不能 resume；未终态（中断、崩溃）用
   `awf resume <run_id>` 从 `cursor` 继续；
5. 遗留 `.lease` 超过 120s 会被自动接管，并记录 `lease_stolen`。
