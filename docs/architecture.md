# StockPilot V0.1 技术架构文档

> 职责：定义“怎么实现”。东方财富细节见 `eastmoney-data-source.md`。

## 1. 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Next.js + React + TypeScript + Tailwind CSS |
| 图表 | Apache ECharts |
| 后端 | FastAPI + Pydantic |
| HTTP | httpx.AsyncClient |
| 数据 | SQLAlchemy + SQLite |
| 缓存 | cachetools.TTLCache |
| Agent | PydanticAI |
| LLM | DeepSeek / OpenAI-compatible API |
| Eval | pydantic-evals |
| 测试 | pytest |
| Python 管理 | uv |

V0.1 不主动引入 LangChain、LangGraph、Redis、PostgreSQL、Vector DB、Celery、Kafka、Kubernetes。

## 2. 总体架构

```text
Next.js
   │ REST
FastAPI Route
   ├──────────── Agent
   │               │ Tool
   └───────┬───────┘
           ▼
       Service Layer
        │         │
    TTL Cache   MarketDataProvider
                  │
            EastMoneyProvider
                  │
              东方财富

Supporting: SQLite / Trace / Evaluation
```

统一依赖方向：

```text
Frontend → Route → Service → Provider
Agent Tool → Service → Provider / Database
```

## 3. 仓库结构

以下是 V0.1 的目标结构；目前已建立 `frontend/`、`backend/app/api/`、`backend/app/agent/`、`backend/app/core/`、`backend/app/models/`、`backend/app/providers/`、`backend/app/services/`、`backend/app/database/`、`backend/tests/`、`evals/` 和 `docs/`。其余业务目录随对应 TODO 阶段创建。

```text
stockpilot/
├── frontend/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── agent/
│   │   ├── core/
│   │   ├── database/
│   │   ├── models/
│   │   ├── providers/
│   │   ├── services/
│   │   └── main.py
│   └── tests/
├── evals/
├── docs/
│   ├── prd.md
│   ├── design.md
│   ├── architecture.md
│   ├── eastmoney-data-source.md
│   └── ui-demo.html
├── TODO.md
├── AGENT.md
├── README.md
└── .env.example
```

## 4. 后端分层

### API
只负责 HTTP 参数、依赖注入、调用 Service / Agent、状态码。

### Service
负责业务组合、缓存、批量查询、自选股聚合和降级。

核心：

```text
StockService
MarketService
WatchlistService
```

`StockService` 提供搜索、单只/批量行情、个股分时和日/周 K 线；`MarketService` 提供三大指数及指数分时；`WatchlistService` 通过 SQLAlchemy Session 增删查自选股。Service 只接收 Provider 内部模型，不接触东方财富原始字段。

### Provider
`MarketDataProvider` 定义统一接口，`EastMoneyProvider` 实现：
- `secid` / Endpoint 等东方财富细节
- HTTP 请求
- 主备节点
- 原始字段解析
- Provider 异常
- 转换为内部 Pydantic Model

东方财富 `fX` 字段不得进入 Service、Agent 或前端。

当前 Provider 接口包含 `search_stocks()`、`get_quotes()`、`get_indices()`、`get_index_intraday()`、`get_stock_intraday()` 和 `get_kline()`；均返回内部模型。Provider 使用 `httpx.AsyncClient`，可注入客户端用于测试。行情、指数、个股/指数分时及 K 线均有主备节点，搜索使用单一节点。分时和 K 线共享网页参数、动态时间戳与单次断连重试，分时解析复用最新交易日过滤。K 线只在完整解析成功后记住可用节点，返回按日期升序排列的最近 `limit` 根数据。具体节点、网页参数和在线验证见数据源文档。

## 5. 内部模型

核心模型：

```text
StockQuote
KlineItem
MarketIndex
IntradayPoint
MoneyFlow
StockNews
SymbolSearchResult
```

当前已实现 P0 的 `StockQuote`、`KlineItem`、`MarketIndex`、`IntradayPoint`、`SymbolSearchResult`；其余模型随对应功能阶段添加。

数据流：

```text
EastMoney Raw → Provider → Pydantic Model → Service → API / Tool
```

## 6. 异步与缓存

统一使用 `httpx.AsyncClient`；需要并发时使用 `asyncio.gather()`。

优先使用东方财富批量行情接口，避免自选股逐只请求。

建议 TTL：

```text
行情 / 指数  3～5 秒
K 线         30～60 秒
资金流       30～60 秒
新闻         3～5 分钟
```

当前已实现：行情、指数 TTL 为 5 秒，指数/个股分时独立 TTL 为 1 秒，K 线 60 秒、搜索 300 秒。分时缓存独立于报价和指数缓存，配合前端 2 秒刷新。每类缓存最多保留 256 个键；最近成功结果额外保留 3600 秒。新请求遇到 `DataSourceError` 且存在未过期的旧结果时，返回 `CachedResult(data=..., stale=True)`；无旧结果则继续抛异常。返回缓存数据时复制模型，避免调用方修改缓存。

同一 Service 缓存内、同一键的并发未命中请求共用一个异步查询任务，调用方取消不会取消其他调用方等待的查询。连续数据源失败按 2、4、8、16、30 秒退避，后续最高 30 秒；冷却期间不访问上游，有有效旧数据则返回 stale，否则保留数据源错误类型。成功清除退避；失败元数据有容量与过期限制。不同键或进程不共享任务。缓存只在内存中，后端重启或开发热重载会清空；首次取数失败无法降级。前端仍每 2 秒请求。

Provider 短暂失败且存在旧缓存时，可返回：

```text
stale=true
```

## 7. 数据库

SQLite + SQLAlchemy。

V0.1 表：

```text
watchlist
chat_session
chat_message
agent_trace
```

Trace 至少保存：

```text
session_id / step_index / tool_name / tool_input
tool_output_summary / status / latency_ms / created_at
```

当前已建立四张表及 `watchlist` 唯一代码约束、聊天消息和 Trace 到会话的外键。`create_database_engine()` 读取 `DATABASE_URL`，SQLite 连接启用外键；FastAPI 启动时调用 `init_db(engine)` 创建表，关闭时释放数据库连接与 Provider 客户端。自选股添加同一代码是幂等操作，列表按添加顺序返回，删除不存在的代码返回 `False`。Agent 对话将用户与助手可见消息写入 `chat_session` / `chat_message`；每次实际执行的 Tool 步骤写入 `agent_trace`。

## 8. API

已实现 `GET /health` 和以下 P0 业务 API：

```text
GET    /api/market/indices
GET    /api/market/indices/{code}/intraday
GET    /api/market/overview               三大指数 + 自选股数量

GET    /api/stocks/search?q=
GET    /api/stocks/quotes?codes=600519,000001
GET    /api/stocks/{code}/quote
GET    /api/stocks/{code}/intraday
GET    /api/stocks/{code}/kline?period=daily|weekly&limit=120

GET    /api/watchlist
POST   /api/watchlist
DELETE /api/watchlist/{code}
```

行情查询统一返回 `{ "data": ..., "stale": false }`；`overview` 返回 `indices`、`watchlist_count` 和 `stale`。股票未找到返回 404，参数错误返回 422，数据源不可用返回 503，超时返回 504。自选股添加接受 `{ "symbol": "600519" }`，返回 201；删除成功返回 204。批量行情最多接受 50 个代码，并经 `StockService` 一次调用 Provider。

指数分时接口支持上证 `000001`、深证 `399001`、创业板 `399006`，仅返回最新交易日分钟点。指数模型还包含最高、最低点位。市场涨跌家数属于 P1，当前 `overview` 不提供该数据。Agent 已提供：

```text
GET    /api/agent/status
POST   /api/agent/chat
POST   /api/agent/chat/stream
GET    /api/agent/sessions/{session_id}
GET    /api/agent/traces/recent?limit=5
GET    /api/agent/traces/{session_id}
```

聊天请求包含 `message` 和可选 UUID `session_id`，响应包含 `session_id` 与 `answer`。未配置模型返回 503；模型请求失败返回 502；行情源不可用返回 503，超时返回 504；不存在的会话返回 404。执行失败时响应头 `X-Agent-Session-ID` 提供已创建的会话 ID，可查询失败 Tool 的 Trace；该头已对跨域前端开放。Trace 查询统一返回 `{ "data": [...], "stale": false }`，最近记录最多查询 20 条。以下 API 仍未启用：

```text
GET    /api/stocks/{code}/money-flow
GET    /api/stocks/{code}/news
```

本地 Next.js 来源通过 `CORS_ORIGINS` 配置允许跨域访问后端。

`GET /api/stocks/{code}/intraday` 经 StockService → Provider 获取最新交易日分钟价格、成交量与成交额，仍返回 `{ data, stale }`。详情默认展示分时，每 2 秒刷新，首页指数分时也每 2 秒刷新；日/周 K 切换使用原接口。前端轮询在上次请求完成前跳过重复调用，避免慢源请求堆积；切换资源或卸载时取消旧请求。

`POST /api/agent/chat/stream` 接受同一 ChatRequest，返回 `text/event-stream`：`session` 提供会话 ID，`delta` 提供文本增量，`done` 提供完整答案；模型或行情失败发送包含规范化状态码和会话 ID 的 `error` 事件。未配置、未知会话、无效输入在流开始前分别返回 HTTP 503/404/422；开始后的失败以流内事件通知。原 JSON chat 接口保留供现有调用和 Eval 使用。

Web 行情页面使用浏览器端请求访问上述 API，默认后端地址为 `http://localhost:8000`，可由 `NEXT_PUBLIC_API_BASE_URL` 配置。指数分时及日/周 K 线使用 ECharts，页面按 API 的 `stale` 标记提示旧缓存；请求失败时展示错误与重试，不合成行情数值。市场温度、资金流和新闻仍等待 P1 数据能力。

## 9. Agent

```text
User
→ PydanticAI
→ Tool
→ Service
→ Provider / DB
→ Tool Result
→ Final Answer
```

Tool 只做：
- 参数 Schema
- Service 调用
- 结构化返回

流式调用使用 PydanticAI `run_stream` / `stream_text`，模型运行位于独立生产任务，通过有界队列传递可见文本；取消时关闭生产任务并保留 Tool Trace。行情问题在成功 Tool 执行之前不会释放模型文本，无 Tool 的回答替换为固定提示。完整答案才保存为对话；不传输或持久化私有推理。前端使用 `react-markdown` + `remark-gfm` 渲染最终和逐步追加的文本，禁用原始 HTML。

当前四个 P0 Tool 为 `get_stock_quote`、`get_stock_kline`、`get_market_indices`、`get_watchlist`；均经 Service 读取数据。自选股 Tool 用单次批量行情查询。P1 资金流与新闻 Tool 尚未注册。单次对话最多 8 次模型请求、12 次 Tool 调用。对话开始前创建会话；成功后保存用户与助手可见消息，失败的 Tool 步骤仍可按会话查询。续聊仅重建最近 20 条可见消息，不保存模型私有推理。行情事实问题若没有发生 Tool 调用，后端返回固定的无数据提示，不转发模型编出的数值。

配置：

```text
MODEL_NAME
MODEL_API_KEY
MODEL_BASE_URL
```

Agent 不硬编码模型 Key。缺少名称或 Key 时不构造模型，前端显示未配置状态。真实 DeepSeek 模型已验证会选择指数 Tool；使用固定测试行情时完成回答。2026-09-28 修复 K 线请求后，真实 DeepSeek 与东方财富联调完成宁德时代最近 5 根日 K 走势任务，K 线 Tool 和 Trace 均成功；其余核心 Case 的完整在线复验仍待完成。

## 10. Trace

Tool 执行时按调用顺序记录：

```text
记录受限 Input → 计时执行 → Output Summary / Status / Latency → 持久化
```

每轮使用独立步骤列表，会话内 `step_index` 跨轮递增。Input 仅包含 Tool 参数，字符串最长保留 64 字符；结果仅保存条数等摘要，失败仅保存规范化错误摘要，不保存原始行情响应、异常内容或模型私有推理。Dashboard 展示最近记录，`/agent` 展示当前会话全部记录并可展开输入与摘要。真实模型与真实行情源联调时，行情源失败步骤已成功持久化。

## 11. Evaluation

```text
evals/
├── datasets/
│   ├── tool_selection.yaml
│   ├── arguments.yaml
│   └── workflow.yaml
├── fixtures.py
└── run_eval.py
```

当前 24 条 Case 覆盖单 Tool、多 Tool、自选股和两只股票 K 线比较。运行器用 `pydantic-evals` 执行真实配置的模型，每条 Case 使用独立临时 SQLite 和固定 Provider 行情；评测不会请求实时东方财富数据，也不会修改用户自选股。评分依据实际持久化的 Tool Trace 和模型回答。

指标按 Case 严格计算：

```text
Tool Selection Accuracy = Tool 名称及调用次数完全匹配的 Case 数 / 总数
Argument Accuracy = Tool 名称、次数及参数完全匹配的 Case 数 / 总数
Task Success Rate = 参数正确、Tool 全部成功且回答包含预期测试事实的 Case 数 / 总数
```

CLI 输出三项分数和失败 Case 的预期/实际 Tool 调用；额外调用也计为选择失败。无模型配置时明确输出 `SKIP`，不生成分数。固定行情让输入可复验，但模型输出可能随运行变化；每次结果都需真实运行取得。2026-09-26 DeepSeek `deepseek-flash` 完整运行 24 条：三项均为 22/24，两条失败 Case 是正确调用日 K 后又额外调用报价 Tool。

## 12. 测试

重点：
- Provider：代码转换、字段解析、备用节点、异常
- Service：缓存、批量聚合、stale 降级
- API：正常 / 参数错误 / Provider 不可用
- Tool：参数、Service 调用、返回 Schema
- 前端：Build / Type Check + 核心页面人工检查

执行流程见根目录 `AGENT.md`。

## 13. 环境变量

根目录 `.env.example` 提供示例；后端从 `backend/.env` 读取本地配置。数据库工厂使用 `DATABASE_URL`，Agent 使用模型配置：

```text
APP_NAME=StockPilot
ENVIRONMENT=development
MODEL_NAME=
MODEL_API_KEY=
MODEL_BASE_URL=
DATABASE_URL=sqlite:///./stockpilot.db
MARKET_DATA_PROVIDER=eastmoney
CORS_ORIGINS=["http://localhost:3000","http://127.0.0.1:3000"]
```

## 14. 架构决策

1. Provider 抽象，不让业务层直接爬接口。
2. Async HTTP，不使用阻塞式 requests。
3. 批量行情优先。
4. 指标优先基于 K 线本地计算。
5. TTLCache 优先于 Redis。
6. 单 Agent + Tool Calling 优先于复杂 Workflow。
7. Trace + Eval 优先于继续堆功能。
