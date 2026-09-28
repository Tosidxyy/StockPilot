# StockPilot V0.1 产品需求文档

> 职责：定义“做什么”。实现见 `architecture.md`，UI 见 `design.md`。

## 1. 定位

**StockPilot — A 股智能看盘 Agent**

面向个人使用的轻量 Web 看盘工具：以东方财富行情数据为基础，通过 LLM Tool Calling 提供股票查询、自选股总结和行情分析。

项目目标是 **GitHub Portfolio + Agent 开发实习展示**，不是生产级交易平台。

核心展示：

```text
真实行情 → Agent Tools → Trace → Evaluation → Web UI
```

## 2. V0.1 目标

1. 查看三大指数、股票行情、个股实时分时及日 K / 周 K。
2. 搜索股票并管理自选股。
3. 查看市场概览、资金流和新闻。
4. 用自然语言调用真实行情 Tool。
5. 展示 Agent Tool 调用过程。
6. 对 Tool 选择、参数和任务完成情况做基础评测。

## 3. 页面

| 页面 | 路由 | 核心内容 |
|---|---|---|
| Dashboard | `/` | 搜索、指数、市场走势、自选股、市场温度、Agent、最近 Trace |
| 个股详情 | `/stock/[code]` | 行情、实时分时、日/周 K、成交量、资金流、新闻、AI 快捷分析 |
| Agent 工作台 | `/agent` | Markdown 流式对话、Tool Trace、输入/输出摘要 |

## 4. 功能优先级

### P0
- 东方财富 Provider
- 股票搜索、三大指数、实时行情、个股分时、日/周 K
- 自选股增删查
- Dashboard、个股详情
- Agent Chat（Markdown 渲染、流式回答）、核心 Tool Calling、Agent Trace

### P1
- 市场涨跌概览
- 资金流、个股新闻
- 股票比较
- Agent Evaluation

### P2
- 公司公告、板块排行、更多指标
- 完整移动端优化
- Docker / CI
- 新闻或公告 RAG

## 5. Agent

核心 Tool：

```text
get_stock_quote
get_stock_kline
get_market_indices
get_watchlist
get_stock_money_flow
get_stock_news
```

典型任务：

| 用户问题 | 预期行为 |
|---|---|
| 宁德时代今天涨了多少？ | `get_stock_quote` |
| 东方财富最近五天走势？ | `get_stock_kline` |
| 今天我的自选股怎么样？ | `get_watchlist` → 批量行情 → 汇总 |
| 比较宁德时代和比亚迪近 5 天 | 获取两只 K 线 → 本地比较 |
| 东方财富今天为什么波动大？ | 行情 + K 线 + 资金流 + 新闻 |

规则：
- 行情事实必须来自 Tool。
- Tool 失败时不得凭空补全实时数据。
- 回答区分“数据事实”和“模型分析”。
- 流式回答也遵守行情 Tool 约束；中断的部分输出不保存为完整对话。

## 6. Trace 与 Evaluation

Trace 至少包含：

```text
tool_name / tool_input / output_summary / status / latency
```

不记录模型私有推理。

Evaluation 使用 20～50 条 Case，至少输出：

```text
Tool Selection Accuracy
Argument Accuracy
Task Success Rate
```

结果必须来自真实运行。

## 7. 非目标

V0.1 不做：

- 股票交易、券商接入、实盘资产管理
- 自动买卖建议、涨跌预测
- 高频 / Level-2 / 毫秒级行情
- 多用户、登录、RBAC
- 专业量化回测
- Multi-Agent
- 大规模 RAG / Vector DB
- 无明确需求的复杂基础设施

## 8. MVP 验收

- Dashboard、搜索、详情、自选股、K 线可用。
- Agent 能完成上述核心任务。
- Trace 能看到 Tool、参数、状态、耗时。
- Eval 可真实运行。
- 后端核心测试通过；前端 Build / Type Check 通过。
- README、截图、架构说明、`.env.example` 完整。
- 仓库无真实 API Key。

## 9. 后续

```text
V0.2：新闻 / 公告 RAG
V0.3：用户偏好 Memory
V0.4：每日市场 Agent Brief / 复杂工作流
```

原则：先完成 **可运行、可解释、可评测** 的单 Agent 产品。
