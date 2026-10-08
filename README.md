# StockPilot

StockPilot 是面向 A 股的本地看盘与 AI 辅助分析应用，将行情、自选股、个股走势、资讯和 AI 对话放在同一工作台。

当前版本：**v0.2.0**。这是源码发布，需要分别启动 Python 后端和 Next.js 前端。

## 功能

- **行情与图表**：三大指数、个股报价、分时价格与成交量、日 K / 周 K，展示实际来源和数据时间。
- **自选股**：搜索添加、移除、涨跌幅排序；后端预采集已接入的数据，打开详情时优先使用缓存。
- **新闻与公告**：按股票、日期和关键词筛选，查看来源片段、可提取的公告正文和原文链接。
- **资金流与市场概览**：展示来源支持的分类资金流、市场涨跌计数；缺失与旧数据明确标注。
- **手动情绪统计**：点击个股页“统计当前股民情绪”，使用 DeepSeek Flash 将已有样本分成积极、消极、中立，以饼图展示数量和占比。
- **AI 分析**：流式回答、Markdown、停止生成、历史对话、本地资料检索和可展开的真实引用。普通页面不显示执行追踪面板，调试 API 保留。
- **本地保存**：SQLite 保存自选股、对话、资料、分类结果与行情快照；源失败时保留有效旧数据和原保存时间。

## 安装与启动

需要 Python **3.13+**、[uv](https://docs.astral.sh/uv/getting-started/installation/)、Node.js **20.9+** 和 npm。当前版本不需要 Redis、Docker 或独立数据库服务。

### 1. 获取源码

```bash
git clone https://github.com/Tosidxyy/StockPilot.git
cd StockPilot
git checkout v0.2.0
```

也可从 [v0.2.0 Release](https://github.com/Tosidxyy/StockPilot/releases/tag/v0.2.0) 下载源码并解压。

### 2. 配置后端

从项目根目录复制模板：

```powershell
# Windows PowerShell
Copy-Item .env.example backend/.env
```

```bash
# macOS / Linux
cp .env.example backend/.env
```

只看行情可保持模型配置为空。使用 DeepSeek 对话与手动情绪统计时，在 **backend/.env** 设置：

```dotenv
MODEL_NAME=deepseek-flash
MODEL_API_KEY=你的模型密钥
MODEL_BASE_URL=https://api.deepseek.com
SENTIMENT_MODEL_NAME=deepseek-flash
```

对话可使用其它 OpenAI 兼容模型；情绪分类默认使用 DeepSeek Flash，复用同一 Key 和 API 地址，配置应支持该模型。真实密钥仅保存在本地 `.env`，不要写入配置模板。

### 3. 启动后端

在第一个终端执行：

```bash
cd backend
uv sync --locked
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

默认使用 **一个后端进程**。首次启动自动创建本地 SQLite 表。保持终端运行；[健康检查](http://127.0.0.1:8000/health) 返回 `{"status":"ok"}`，[API 文档](http://127.0.0.1:8000/docs) 的版本为 0.2.0。

### 4. 启动前端

另开终端，从项目根目录执行：

```bash
cd frontend
npm ci
npm run build
npm run start
```

打开 **[http://localhost:3000](http://localhost:3000)**。日常使用推荐上述构建后启动方式；开发时可用 `npm run dev`，后端可追加 `--reload`。停止时在各终端按 Ctrl+C。

## 使用方法

1. 首页点击指数切换分时；搜索股票打开详情。
2. 添加自选股，点击“涨跌幅”表头切换降序、升序和添加顺序。
3. 个股页切换分时、日 K 和周 K；资讯卡可筛选、分页和查看原文。
4. 股吧卡已有样本后，点击 **“统计当前股民情绪”**。统计期间按钮防止重复点击，显示进度，其他页面操作可继续使用。
5. 完成后查看积极／消极／中立饼图、数量、占比、样本范围、采集时间、模型统计时间和最多八条实际统计时的原文预览。
6. 向 AI 提问，例如“总结我的自选股近三天资讯”或“结合走势、资金流和新闻分析宁德时代”，展开短引用核对资料与时间。

**情绪模型只在用户点击时运行**。页面加载、刷新、后台采集和普通工具读取不会自动启动批量分类；相同文本复用已有分类，新样本或编辑文本需要再次点击。空样本不画假饼图，失败保留上次完整统计，未完成分类不算中立。模型统计时间不代替发言或数据采集时间。

统计最多近24小时的200条去重公开标题／页面附带回复，每批40条、全局两并发。分类记录保留30天、最多10,000条，保存最多256股的最后完整统计。任务按默认单进程运行，重启后已完成结果可读取，未完成任务需重新点击。分类使用[DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)并严格校验标签及样本ID，不记录私有推理。

## 数据源、缓存与已知限制

- 报价默认腾讯财经 → 新浪财经 → 东方财富；个股和指数分时默认新浪财经 → 腾讯财经 → 东方财富；历史日／周 K 优先东方财富、腾讯备用，均不复权。实际来源显示在页面。
- 报价／分时采集以交易时段 **2秒**为目标，日／周 K 60秒，其他时段300秒。分时请求链共用2秒网络预算，慢备用超时取消，已有有效缓存继续显示；请求、调度和源数据更新不保证每轮都有新成交。
- 分时按源分钟数据展示，末根可能未完成；腾讯累计量额按分钟差分，缺字段不补造。历史腾讯接口未提供成交额时显示为空。
- 免费公开源可能断连、限流或返回验证页。**市场统计、部分股票资金流和股吧评论仍可能不可用**；成功资金流也可能只截至此前交易日。本版保留这些限制，按用户修订暂缓继续修复数据源可用性。
- 行情旧快照最多用于降级14天，原股吧样本缓存最长2天；旧值、部分数据和缺失均明确标注。没有有效样本，模型无法生成当前情绪分布。
- 股吧主要覆盖公开首页用户发帖标题及页面附带回复，未接入完整楼下回帖，样本不代表全体投资者；三类占比是样本分类结果，不是涨跌预测或模型置信度。
- 新闻检索主要使用来源片段，未保证新闻全文；公告只提取可访问首附件的有上限正文，扫描页不做 OCR，部分正文不算全文。
- 板块排行、更多技术指标、长期全源稳定性与多进程任务管理列后续。Redis已评估但未接入，本版使用进程内缓存与 SQLite。

## 性能与验证

在同条件40股固定缓存样本测试中，状态接口 P95 **549.964→16.097 ms**，每请求 SQL **4→2次**；图表动态文件原始体积 **1,142,050→580,974字节**。优化采用轻量元信息缓存和按需图表组件；[性能报告](evals/results/2026-10-08-performance.json)保留条件、原始测量和哈希。这不是所有设备／真实源延迟承诺，也不是完整首屏或 gzip 传输量指标。

手动分类两轮真实 Flash 对36条人工标注固定测试文本均全部正确，macro-F1=1.0；[分类评测报告](evals/results/2026-10-08-sentiment-final.json)保留实际预测，不代表真实股民样本的总体准确率。此前模型失败／源失败报告保留在 `evals/results/`。

分类按钮、进度、重复及刷新复用、失败旧结果、空样本、桌面／390px手机，以及分时／日周 K／指数／饼图切换已通过隔离浏览器验证。部分 UI 使用明确的固定样本，模型调用与源采集验证分别记录。

本地检查：

```bash
# backend/
uv run pytest
# frontend/
npm run lint
npx tsc --noEmit
npm run build
```

从项目根目录复验模型分类或缓存性能：

```powershell
backend\.venv\Scripts\python.exe -X utf8 evals/run_sentiment_eval.py --output evals/results/local-sentiment.json
backend\.venv\Scripts\python.exe -X utf8 evals/benchmark_cache.py --output evals/results/local-cache.json
```

分类评测使用真实模型且会消耗配置的模型额度；未配置 Key 时跳过、不生成分数。性能脚本使用独立临时库和固定样本，不调用模型／行情源。其他评测说明见各脚本帮助和数据集；固定语料不代替全面语义或真实源稳定性验收。

## 升级与常见问题

- **从 v0.1 升级**：先停止前后端并备份 `backend/stockpilot.db`，切换 v0.2.0；后端重新 `uv sync --locked`，前端重新 `npm ci`、构建和启动。首次启动自动增加新表，无须清空已有数据库。
- **没有情绪样本**：先查看股吧卡的源状态。源受限且没有有效缓存时无法统计，不会用虚构评论填充。
- **模型未配置**：检查 `backend/.env` 的 Key、地址和模型名，并重启后端。
- **有旧数据提示**：核对来源时间和缓存时间，等待下一次成功获取；旧缓存不能当作当前成交。
- **首次加载慢**：日常看盘使用构建后的前端；上游请求慢时各卡可独立保留内容／显示状态。
- **数据保存位置**：默认 `backend/stockpilot.db`，包含自选股、对话、资料及分类。备份或迁移前停止后端。
- **修改接口地址**：复制 `frontend/.env.example` 为 `frontend/.env.local` 设置 `NEXT_PUBLIC_API_BASE_URL`，然后重新构建；同步后端 `CORS_ORIGINS`。

## 旧版界面示例

以下为 v0.1 示例行情／测试模型截图。v0.2 普通页面已移除执行追踪面板，并增加资讯、手动情绪饼图和性能优化。

![v0.1 市场概览示例](docs/screenshots/dashboard.png)
![v0.1 AI 对话与执行追踪示例](docs/screenshots/agent-trace.png)

## 技术栈

Next.js、TypeScript、Tailwind CSS、ECharts；Python、FastAPI、httpx、Pydantic AI、SQLite。行情无需账户 Key，AI 需要自行配置模型服务。本项目不提供交易下单。
