# StockPilot V0.1 TODO

> `[ ]` 未开始 · `[-]` 进行中 · `[x]` 已完成  
> 完成规则见 `AGENT.md`。

当前阶段：

```text
V0.1 验收进行中：7 项通过；真实日/周 K 线与单股 K 线 Agent 已复验，剩余完整页面及核心 Case 待复验
```

## 1. 初始化

- [x] 建立 `frontend/ backend/ evals/ docs/`
- [x] 放入当前 Markdown 文档
- [x] 创建 `.gitignore`、`.env.example`、`README.md`
- [x] 使用 `uv` 初始化后端
- [x] FastAPI + `GET /health`
- [x] 初始化 Next.js + TypeScript + Tailwind
- [x] 初始化 Commit

验收：前后端可启动，`/health` 返回 200。

## 2. EastMoney Provider

- [x] `MarketDataProvider`
- [x] Quote / Kline / Index / Search 内部模型
- [x] `to_secid()`
- [x] 股票搜索
- [x] 批量实时行情
- [x] 三大指数
- [x] 日 K / 周 K
- [x] 个股最新交易日分时
- [x] 主 / 备 Endpoint
- [x] Timeout / 统一异常
- [x] Provider 基础测试

验收：P0 数据能力均返回内部标准模型。

注：2026-09-28 已修复 K 线主备节点、网页请求参数、动态时间戳及断连单次重试；宁德时代和贵州茅台真实日/周 K API 均返回 200、120 根、最新日期为当天。42 个后端测试通过，详见数据源文档。

## 3. Service + DB

- [x] `StockService`
- [x] `MarketService`
- [x] TTL Cache
- [x] stale 降级
- [x] SQLite + SQLAlchemy
- [x] `watchlist`
- [x] `chat_session`
- [x] `chat_message`
- [x] `agent_trace`
- [x] `WatchlistService`
- [x] Service / DB 测试

验收：自选股可增删查；Service 不暴露东方财富原始字段。

## 4. REST API

- [x] Market indices / overview
- [x] Stock search / quote / kline
- [x] Stock intraday API + 1 秒缓存 / stale
- [x] Watchlist GET / POST / DELETE
- [ ] P1：money-flow
- [ ] P1：news
- [x] 核心 API 测试

验收：P0 API 足够支撑 Dashboard 和详情页。

注：已提供三大指数分时接口供 Dashboard 绘图；`overview` 当前提供三大指数与自选股数量。市场涨跌家数及 P1 资金流、新闻 API 等待对应数据能力，不返回虚构行情。

## 5. Web 行情

- [x] Layout / Sidebar / Topbar
- [x] 三大指数
- [x] 市场走势图
- [x] 自选股增删与跳转
- [ ] 市场温度
- [x] 股票搜索
- [x] 个股基础行情
- [x] 个股默认分时 + 成交量 + 2 秒刷新（首页指数分时同频率）
- [x] 日 K / 周 K + 成交量
- [x] 自选股导航锚点选中状态
- [ ] P1：资金流
- [ ] P1：新闻
- [x] Loading / Empty / Error

验收：整体布局与视觉方向参考 `docs/ui-demo.html`，并与 `design.md` 基本一致；Build / Type Check 通过。

注：P0 页面已接入真实 API，并提供错误重试与旧缓存提示。Agent 对话和 Trace 已可交互；市场温度、资金流和新闻仍待对应 P1 数据能力。2026-09-28 K 线真实 API 已修复并复验通过；完整浏览器页面流程仍待复验。

## 6. Agent

- [x] PydanticAI + 模型配置
- [x] System Prompt
- [x] `get_stock_quote`
- [x] `get_stock_kline`
- [x] `get_market_indices`
- [x] `get_watchlist`
- [ ] P1：`get_stock_money_flow`
- [ ] P1：`get_stock_news`
- [x] `POST /api/agent/chat`
- [x] Agent Chat 前端
- [x] Markdown 回复渲染（首页 / 工作台 / 历史）
- [x] SSE 流式回答与停止 / 中断处理
- [x] Tool 基础测试

验收：通过 `prd.md` 核心 Agent Case。

注：四个 P0 Tool、模型配置、会话持久化和首页/`/agent` 对话已实现；使用本地函数模型验证 Tool 调用、接口和浏览器会话恢复。2026-09-28 真实 DeepSeek 与东方财富联调完成宁德时代最近 5 根日 K 走势任务，Agent 为 200，Tool Trace 为 success；`prd.md` 其余核心 Case 仍待完整在线复验。P1 资金流与新闻 Tool 未实现。

## 7. Trace

- [x] Tool / Input / Output Summary / Status / Latency
- [x] 写入 `agent_trace`
- [x] Trace 查询 API
- [x] Dashboard 最近 Trace
- [x] `/agent` 完整 Trace

验收：成功、失败 Tool 都可观察，不保存私有推理。

注：成功与失败 Trace 已通过 pytest 和浏览器检查；真实 DeepSeek 调用在行情源失败时记录了失败步骤，在固定测试行情下记录了成功步骤。2026-09-28 真实东方财富 K 线端到端调用也记录了成功 Trace。

## 8. Evaluation

- [x] 20～50 条 Case
- [x] 单 Tool / 多 Tool / 自选股 / 股票比较
- [x] Tool Selection Accuracy
- [x] Argument Accuracy
- [x] Task Success Rate
- [x] CLI 运行 + 失败 Case
- [x] README 写入真实结果

验收：可重复运行；无模型配置时明确跳过。

注：2026-09-26 使用真实 DeepSeek `deepseek-flash` 模型与固定测试行情运行 24 条 Case，三项指标均为 22/24；两条失败 Case 均因模型在正确的日 K Tool 之后额外调用报价 Tool。无模型配置的跳过行为已通过测试。评测结果不替代真实东方财富行情源的端到端验收。

## 9. GitHub 收尾

- [x] README / Quick Start / 环境变量
- [x] Dashboard 截图
- [x] Agent + Trace 截图
- [x] 架构图
- [x] 免责声明
- [x] 清理敏感信息 / 临时文件
- [ ] 可选：GIF / Docker / GitHub Actions

注：截图使用固定示例行情和测试模型，README 已明确标注。已清理本轮临时服务文件与缓存目录；跟踪文件中只有空白 `.env.example` 模板，未发现真实 API Key。可选项不影响本阶段必需项完成。

## 10. V0.1 验收

- [-] Dashboard / 搜索 / 详情 / K 线 / 自选股可用
- [-] Agent 核心任务可用
- [x] Trace 可用
- [x] Eval 可真实运行
- [x] 后端测试通过
- [x] 前端 Build / Type Check 通过
- [x] 文档与代码一致
- [x] Git 历史清晰
- [x] 无真实 API Key

注：2026-09-28 K 线专项修复与复验见 `docs/acceptance.md`。真实日/周 K 及单股 K 线 Agent 已通过，后端 42 测试和前端 lint / Type Check / Build 通过，修复已 Commit 并 Push。其余页面全链路、自选股批量报价和 Agent 核心 Case 尚未完整复验，前两项保持进行中；固定响应测试与 Eval 不替代这些在线验证。

2026-09-28 用户交互增补闭环：个股分时、导航蓝点、Markdown 与流式回答已实现、测试并分别 Commit / Push（`cb34556`、`a06ea9b`、`ab3fa12`）。最新后端 50 passed，前端 lint / Type Check / Build 通过；真实分时与 DeepSeek 流式接口、浏览器中的图表/导航/格式化回答均已验证。已同步 PRD、设计、架构、数据源、验收及 README；本轮未继续其他功能阶段。

分时刷新频率补充：个股和指数分时每 2 秒刷新，后端分时独立缓存 1 秒，并防止未完成请求重叠。后端 50 passed，前端 lint / Type Check / Build 通过；修复 `733e705` 已 Commit / Push，相关文档已同步。

2026-09-28 再次断连排查：真实行情主备节点 RemoteProtocolError，API 仍 503，健康检查及前端 200，原因未确证。已增加服务端同键请求合并与最高 30 秒失败退避，保留前端 2 秒刷新和 stale 降级。后端 55 passed，前端 lint / Type Check / Build 通过；修复 `0be4a10` 已 Commit / Push，架构、数据源、验收文档已同步。行情源未恢复，在线验收前两项保持进行中。
