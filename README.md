# StockPilot

StockPilot 是面向 A 股的本地看盘与 AI 辅助分析应用，将指数、自选股、个股走势和 AI 对话放在同一个工作台。

## v0.1 功能

- **市场概览**：三大指数及分时走势。
- **自选股**：搜索添加、移除、批量报价、涨跌幅升降序排序。
- **个股详情**：报价、分时价格与成交量、日 K / 周 K。
- **自动更新**：报价、指数与分时每 2 秒查询；失败时保留有效旧行情并标注时间。
- **AI 分析**：通过工具读取行情，支持流式回答、Markdown、停止生成与历史对话。
- **执行追踪**：查看 AI 调用的工具、参数、结果摘要、状态与耗时。
- **本地保存**：SQLite 保存自选股、对话、执行记录和行情快照。

实时行情来自东方财富公开接口；历史日 K / 周 K 在东方财富失败时自动尝试腾讯财经备用接口，页面标注实际来源，均使用不复权数据。AI 使用 OpenAI 兼容模型服务，需要自行提供模型 API Key；不配置模型也可以看行情。v0.1 暂不提供交易下单、资金流、新闻与全市场涨跌家数统计。

## 界面预览

截图使用示例行情与测试模型，数值和回答仅展示界面效果。

![市场概览](docs/screenshots/dashboard.png)
![AI 对话与执行追踪](docs/screenshots/agent-trace.png)

## 安装与运行

需要 Python **3.13+**、[uv](https://docs.astral.sh/uv/getting-started/installation/)、Node.js **20.9+** 和 npm。

### 1. 下载项目

```bash
git clone https://github.com/Tosidxyy/vibe_stock.git
cd vibe_stock
```

也可以从 [Releases](https://github.com/Tosidxyy/vibe_stock/releases) 下载源码并解压。当前发布为源码版本，需要分别启动前后端。

### 2. 配置后端

复制根目录 `.env.example` 为 `backend/.env`：

```powershell
# Windows PowerShell
Copy-Item .env.example backend/.env
```

```bash
# macOS / Linux
cp .env.example backend/.env
```

需要 AI 对话时，在 `backend/.env` 填写服务商提供的配置：

```dotenv
MODEL_NAME=你的模型名称
MODEL_API_KEY=你的模型密钥
MODEL_BASE_URL=你的OpenAI兼容API地址
```

`MODEL_NAME`、`MODEL_API_KEY` 留空则禁用 AI 对话。真实密钥只放在本地 `.env`，不要写入配置模板。

### 3. 启动后端

在第一个终端执行：

```bash
cd backend
uv sync --locked
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

首次启动自动创建 SQLite 数据库。保持终端运行；[健康检查](http://127.0.0.1:8000/health) 应返回 `{"status":"ok"}`，[API 文档](http://127.0.0.1:8000/docs) 可查看接口。

### 4. 启动前端

另开一个终端，从项目根目录执行：

```bash
cd frontend
npm ci
npm run dev
```

打开 **[http://localhost:3000](http://localhost:3000)**。停止时，在两个终端分别按 `Ctrl+C`。

如需使用构建后的前端，先执行 `npm run build`，再执行 `npm run start`。开发时可在后端启动命令末尾加 `--reload`。

## 如何使用

1. 首页点击指数切换分时走势。
2. 顶部搜索股票，点击结果打开个股详情。
3. 在自选股中添加股票，或在详情页点击“加入自选”。
4. 点击“涨跌幅”表头，依次切换降序、升序、添加顺序；行情更新保持排序，整页刷新恢复默认。
5. 详情页切换分时、日 K、周 K。
6. 配置模型后，向 AI Agent 提问，例如“查看我的自选股”或“用宁德时代最近五根日 K 概括走势”；右侧查看工具执行记录。

每 2 秒查询不代表每轮都有新成交或数值变化，分时点按分钟记录。自选股页面每 2 秒读取后台缓存；非自选股日/周 K 每 60 秒查询。第三方接口可能延迟或断连；已有有效行情会继续显示为旧缓存，并标注保存时间，快照最多用于降级 7 天。没有有效缓存时显示预热或不可用状态与重试入口。分时不可用时可点击“查看历史日 K”；历史 K 线可独立获取，不依赖实时报价成功。腾讯备用历史接口不提供成交额，该字段返回空值，不估算补齐。AI 工具优先读取最新有效缓存，并向模型提供数据时间与旧数据状态。

## 可选配置

### V0.2 开发中：自选股后台采集

最新主分支已支持：后端启动后自动预热自选股的报价、分时、最近 120 根日 K / 周 K，关闭页面后继续采集。新增自选股进入预热，移除后停止后续采集；非自选股保持进入详情或显式工具调用时请求。

北京时间工作日 09:15–11:30、13:00–15:05，报价与分时以 2 秒为刷新目标，日/周 K 每 60 秒；其他时段每 300 秒，启动与新增成员仍预热一次。此时段判断不含节假日交易日历。慢源或多只股票会受到并发限制，成功数据才写入缓存；上游持续失败且无历史快照时仍可能无数据。

默认启用；在 `backend/.env` 可设置 `WATCHLIST_PREFETCH_ENABLED=false` 关闭，`WATCHLIST_PREFETCH_WORKERS=4` 设置分时最大并发（1–16）。使用一个后端进程运行，多个 worker 会重复采集。此功能尚未包含在已发布的 v0.1.0 标签中。

后台采集开启时，自选股页面、详情和 AI 工具直接读取同一份快照，冷启动等待后台预热，避免打开页面时重复取数。列表展示每只股票的采集状态，详情分别展示报价、分时、日 K、周 K 的状态、最近成功保存时间及历史数据来源。部分失败时保留成功内容；“重试更新”请求后台重新采集，仍遵循失败退避。关闭后台采集后恢复按需请求。

目前已验证真实日/周 K 自动落盘与缓存读取不增加源请求；真实报价、分时预热及完整在线稳定性验收仍待上游恢复后确认。

V0.2 的后续目标还包括：

- 个股/市场新闻、公司公告与可访问正文。
- 个股资金流、市场涨跌概览；随后完善板块排行与本地指标。
- 新闻/公告检索增强回答（RAG），提供可追溯的来源与引用。
- 自选股资讯主动缓存、多数据 Agent 分析及相应评测。

上述能力尚未实现，将分模块开发、测试和验收。

后端设置写入 `backend/.env`：

| 变量 | 用途 | 默认值 |
| --- | --- | --- |
| `MODEL_NAME` | 模型名称 | 空 |
| `MODEL_API_KEY` | 模型密钥 | 空 |
| `MODEL_BASE_URL` | OpenAI 兼容 API 地址 | 空，使用默认地址 |
| `DATABASE_URL` | 数据库路径 | `sqlite:///./stockpilot.db` |
| `CORS_ORIGINS` | 允许的网页来源 | `["http://localhost:3000","http://127.0.0.1:3000"]` |

前端默认连接 `http://localhost:8000`。更换地址时，复制 `frontend/.env.example` 为 `frontend/.env.local`，设置 `NEXT_PUBLIC_API_BASE_URL`，然后重启前端；构建后运行需重新构建。

## 常见问题

- **无法连接 API**：确认后端仍在运行，健康检查正常，前后端端口与地址配置一致。
- **行情不更新**：查看交易时段、数据时间与旧缓存提示。接口失败后会退避重试，没有缓存时需等数据源恢复。
- **AI 不可用**：检查模型名称、密钥、API 地址；修改 `backend/.env` 后重启后端。
- **数据在哪里**：默认保存在 `backend/stockpilot.db`，包含自选股、对话与执行记录。备份或迁移前先停止后端。

## 技术与检查

前端：Next.js、TypeScript、Tailwind CSS、ECharts。后端：FastAPI、PydanticAI、SQLAlchemy、SQLite。

后端测试在 `backend/` 执行 `uv run pytest`；前端检查在 `frontend/` 执行 `npm run lint`、`npx tsc --noEmit`、`npm run build`。

配置模型后，可在 `backend/` 执行 `uv run python ../evals/run_eval.py` 运行评测。评测实际请求模型、使用固定测试行情，不代表在线行情源的可用率。

## 免责声明

行情与 AI 回答仅供参考，不构成投资建议、交易指令或收益保证。第三方数据与模型可能延迟、不可用或出错，请核对数据时间。
