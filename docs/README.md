# AWEngine 文档

本目录保存 AWEngine 的设计、参考和运维文档。README 负责「快速上手」，
这里负责「查清楚细节」。

## 建议阅读顺序

| 顺序 | 文档 | 适合谁 | 你会得到什么 |
|---|---|---|---|
| 1 | [README](../README.md) | 第一次接触 | 安装、六条 demo、CLI 全貌 |
| 2 | [MANUAL](../MANUAL.md) | 要完整使用/录屏 | 逐命令教程、演示脚本、排错练习 |
| 3 | [架构](architecture.md) | 要改引擎 | 分层、状态模型、控制流、协作协议 |
| 4 | [工作流参考](workflow-reference.md) | 要写 YAML | 全部字段、节点协议、表达式、收敛、验证 |
| 5 | [错误码](error-codes.md) | 排查停止原因 | 退出码、`SPEC_*`/`E-*`/`F-*` 对照表 |
| 6 | [设计说明](design.md) | 要理解取舍 | 为什么这样设计、边界和非目标 |

补充材料：

- [Demonstration](../Demonstration.md)：录屏分镜、讲解词和演示清单；
- [CONTRIBUTING](../CONTRIBUTING.md)：开发环境、提交规范和 PR 检查；
- [CODE_OF_CONDUCT](../CODE_OF_CONDUCT.md)：社区行为准则；
- [SUPPORT](../SUPPORT.md)：提问渠道、排查清单和响应范围；
- [SECURITY](../SECURITY.md)：安全模型和漏洞报告；
- [CITATION](../CITATION.cff)：引用元数据；
- [CHANGELOG](../CHANGELOG.md)：版本变更。

## 按任务查找

- **我想跑起来**：README 的「第一次运行」→ `awf demo`；
- **我想写自己的流程**：README 的「工作流 YAML」→ [workflow-reference](workflow-reference.md)；
- **我想加一个节点**：README 的「开发者指南」→ [architecture](architecture.md) 的分层和扩展章节；
- **运行失败了**：`awf explain <run_id>` → [error-codes](error-codes.md) → README 排查表；
- **我想看内部实现**：[architecture](architecture.md) → [design](design.md)；
- **我要贡献代码**：[CONTRIBUTING](../CONTRIBUTING.md) → [tests](../tests)。

## 文档约定

- 命令默认在仓库根目录执行；Windows 使用 PowerShell 示例，POSIX 差异会单独标注。
- `runs/<run_id>/` 是运行产物，不属于源码；排错时优先附 `report.md` 和脱敏后的 `events.jsonl`。
- API Key、私有代码和客户数据不要贴进 issue。
- 文档中的行为以当前 `main` 分支和 `pyproject.toml` 的版本为准。
