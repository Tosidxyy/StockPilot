# 东方财富数据源实现参考

> 职责：记录旧项目中已经验证过的东方财富数据访问方式。公开 Web 接口可能变化，开发时必须重新测试。

## 1. 旧项目结论

旧项目已验证：

```text
Service
→ EastMoneyClient
→ 东方财富接口
→ 领域模型
```

成熟做法：
- 批量行情
- `secid` 转换
- 主 / 备节点
- 统一数据源异常
- Service 缓存与 stale 降级
- 技术指标基于 K 线本地计算
- Agent 不直接访问东方财富

StockPilot 改为：

```text
Service
→ MarketDataProvider
→ EastMoneyProvider
→ httpx.AsyncClient
```

## 2. 旧项目已验证 Endpoint

| 能力 | Endpoint |
|---|---|
| 批量行情 | `push2.eastmoney.com/api/qt/ulist.np/get` |
| 批量行情备用 | `push2delay.eastmoney.com/api/qt/ulist.np/get` |
| 分时 | `push2.eastmoney.com/api/qt/stock/trends2/get` |
| 分时备用 | `push2delay.eastmoney.com/api/qt/stock/trends2/get` |
| K 线 | `push2his.eastmoney.com/api/qt/stock/kline/get` |
| K 线备用（2026-09-28 验证） | `1.push2his.eastmoney.com/api/qt/stock/kline/get` |
| 股票搜索 | `searchapi.eastmoney.com/api/suggest/get` |

资金流、新闻、公告在新项目开发时单独验证，不预设未经验证的 Endpoint。

## 3. secid

```text
600519 → 1.600519
000001 → 0.000001
300750 → 0.300750
```

统一实现：

```python
to_secid(symbol: str) -> str
```

无效代码抛 `InvalidSymbolError`。

## 4. 批量行情

使用 `ulist.np/get` + `secids` 一次请求多只股票。

优先用于：
- Dashboard 自选股
- Agent 自选股总结
- 多股票行情比较

旧项目常见字段：

```text
f2 最新价
f3 涨跌幅
f5 成交量
f9 市盈率
f15 最高价
```

`fX` 必须在 Provider 内转换为内部模型。

## 5. 分时与 K 线

分时：`stock/trends2/get`
- 解析 `data.trends`
- 只保留最新交易日
- 保留主 / 备节点

K 线：`stock/kline/get`

旧项目已验证：

```text
klt=101  日 K
fqt=0    不复权
lmt      数量
```

新项目上层只传：

```text
period="daily" / "weekly"
```

具体参数映射在 Provider 内。

## 6. 股票搜索

接口：`searchapi.eastmoney.com/api/suggest/get`

旧项目使用：

```text
type=14
count=20
Classify == "AStock"
```

Provider / Service 负责过滤、去重和排序。

## 7. 请求与异常

旧项目：`requests.Session`，Connect Timeout 约 1.5s、Read Timeout 约 3s。

StockPilot：改用 `httpx.AsyncClient`，保留：
- 合理 Timeout
- 主 / 备 Endpoint
- 最近成功节点优先
- 网络、非 JSON、异常 rc 统一为 Provider 异常

建议异常：

```text
DataSourceError
ProviderTimeoutError
InvalidSymbolError
```

原始 httpx 异常不直接暴露给 API / Agent。

## 8. 缓存与 stale

旧项目已验证缓存降级思路。

StockPilot 建议：

```text
行情 / 指数  1 秒（配合前端 2 秒轮询）
K 线         30～60 秒
新闻         3～5 分钟
```

数据源失败但有旧缓存：

```text
stale=true
```

Agent 应能提示当前使用缓存数据。

## 9. 本地指标

旧项目基于 K 线本地计算：

```text
MA / EMA / RSI / MACD / 波动率
区间收益 / 区间最高 / 最低
```

StockPilot 继续保留此原则；V0.1 按需实现，不一次性堆指标。

## 10. 迁移结论

保留：

```text
secid / 批量行情 / 主备节点 / 统一异常 / 缓存降级 / 本地计算
```

升级：

```text
EastMoneyClient  → MarketDataProvider + EastMoneyProvider
requests.Session → httpx.AsyncClient
Desktop 调用     → FastAPI + Agent Web
```

开发 Provider 时优先参考旧项目已通过测试的请求参数，再用 StockPilot 测试确认当前接口仍可用。

## 11. StockPilot 当前实现与在线验证（2026-09-25）

- `to_secid()` 接收六位 A 股代码：`6` 开头映射沪市，`0` / `3` 开头映射深市，`4` / `8` 开头映射北交所；其他代码抛 `InvalidSymbolError`。
- `search_stocks()` 请求 `input`、`type=14`、`count`，仅保留 `Classify=AStock` 并去重。接口无匹配时返回 `Data=null`、`TotalCount=0`，Provider 转为空列表。
- `get_quotes()` 一次发送多个 `secid`。`f2` / `f3` / `f4` 等价格和涨跌字段除以 100；`f5` 保留原始成交量数值，`f6` 保留原始成交额数值；缺失或 `-` 转为 `None`。
- `get_indices()` 用 `1.000001`、`0.399001`、`0.399006` 一次获取上证、深证、创业板指数，并要求三项齐全。
- `get_index_intraday()` 用 `stock/trends2/get` 取得三大指数之一的分时数据，解析 `data.trends` 中的时间、最新价、成交量、成交额，只保留最新交易日；主节点失败时尝试 `push2delay`，成功节点优先复用。指数行情的 `f15` / `f16` 转为最高 / 最低点位。
- 行情和指数优先最近成功节点，主节点请求失败、返回异常或数据结构不合法时尝试 `push2delay`。超时、HTTP 错误、非 JSON 和异常 `rc` 转为统一 Provider 异常。
- `get_kline()` 用 `klt=101` / `102` 表示日 K / 周 K、`fqt=0` 表示不复权，并解析逗号分隔的 K 线。空 K 线作为数据源失败处理，避免把接口受限误报为无历史数据。

本机在线请求已确认搜索、无结果搜索、批量行情及备用节点指数响应结构。2026-09-25 备用分时节点曾返回真实分钟数据，随后的在线请求又出现连接中断；主备切换和解析已用固定响应测试。`push2his.eastmoney.com` 在本次验证中持续断开连接；日 K / 周 K 的解析和错误路径已用固定响应测试，K 线在线成功验证仍待节点恢复后重试。

## 12. K 线修复与在线复验（2026-09-28）

开盘后旧请求仍出现 `RemoteProtocolError`；交易时段不能解决节点连接与请求兼容问题。参考东方财富[官方图表脚本](https://quote.eastmoney.com/newstatic/libs/quotekchart/1.0.6.js)中的编号节点、日期范围和公开网页 `ut` 参数，验证 HTTPS 编号节点 `1.push2his.eastmoney.com` 可返回真实日 K 与周 K。

当前请求约定：

- 主节点失败、返回空数据或无法解析时回退编号节点，完整解析成功的节点优先复用。
- 使用完整浏览器 User-Agent、Referer、Accept；请求增加 `beg=0`、`end=20500101`、公开网页 `ut` 和动态 `_` 时间戳。该 `ut` 来自公开脚本，不是用户账户 API Key。
- 固定 URL 在复验中仍曾断开，增加动态时间戳后多轮请求成功；断开原因无法仅凭这些响应确定。`RemoteProtocolError` 每节点最多重试一次，不无限重试；超时、HTTP 错误或异常数据直接尝试另一节点。
- 上游可能忽略 `lmt` 并返回全历史；Provider 按日期升序取最近 `limit` 根。Service 继续使用既有 60 秒 TTL 和 stale 降级。

最终真实 FastAPI 联调：宁德时代 `300750`、贵州茅台 `600519` 的日 K / 周 K 四个请求均为 HTTP 200、120 根、`stale=false`，最新日期 `2026-09-28`；另一次验证日/周各 5 根也返回 200。真实 DeepSeek 调用宁德时代最近 5 根日 K Tool 成功，Agent 返回 200，Trace 为 success。42 个后端测试覆盖断连重试、超时/空/异常响应回退、成功节点优先、备用失效回主节点及数量裁剪。

## 13. 个股分时（2026-09-28）

`get_stock_intraday(symbol)` 将股票代码转换为 `secid`，与指数分时复用 `stock/trends2/get`：`ndays=1`、`iscr=0`、公开网页 `ut` 与动态时间戳。主节点 `push2` 失败后回退 `push2delay`，断连单节点最多重试一次；解析时间、价格、成交量、成交额，仅保留最新交易日，字段仍全部封装在 Provider。

Service 按股票代码缓存 1 秒，并保留既有 stale 降级；指数分时也使用独立的 1 秒缓存。前端个股和指数分时每 2 秒请求一次，上一请求仍在执行时跳过重复请求。不将接口失败解释为股票没有成交，也不合成分时数据。刷新频率与分钟点的时间粒度不同，实际返回内容以行情源为准。

真实验证：贵州茅台分时 HTTP 200、51 个分钟点、最后 `2026-09-28 10:20`；宁德时代在接入动态请求后 HTTP 200、53 个分钟点、最后 `10:22`。浏览器中宁德时代曲线与成交量实际显示并更新至 `10:24`；随后上游偶发失败时继续显示最近成功数据和明确的旧数据提示。休市时应按接口实际最新交易日展示。

## 14. 2026-09-28 再次断连与请求保护

后续复验指数、宁德时代报价/分时/日 K 均返回 503；直接调用 Provider 主备节点出现 `RemoteProtocolError`（未收到响应即断开）。东方财富网页返回 200，行情接口在启用或关闭环境代理读取时都断连，当前无代理环境变量。没有 HTTP 429 证据，不能确认限流原因，也不能将午休期间无新增成交等同于连接错误。

Service 已增加同键并发请求合并与失败退避：连续失败等待 2、4、8、16、30 秒，最高 30 秒；冷却期间使用有效旧缓存或返回规范化错误，成功后重置。前端 2 秒刷新及 Provider 主备重试规则保持不变。内存缓存随后端重启或热重载清空；首次请求失败没有旧行情可显示。本次降低重复失败请求，但在线复验仍为 503，尚不能声明行情源恢复。

## 15. 持久化旧行情降级

上述内存缓存缺口现已增加 SQLite 快照补充：指数、报价、股票/指数分时、日/周 K 的成功非空结果落盘，按完整查询键隔离，失败时返回有效快照并标记 `stale=true` 和 `cached_at`。各类最多 256 键、最多降级 7 天；成功写入清理失效快照。后端重启不再丢失已落盘行情，前端展示旧数据和保存时间。保存时间不是交易时间；分钟点与日 K 日期仍来自 Provider。空结果、损坏或过期快照不作为成功行情，真实源失败且无快照仍报错。Provider HTTP 请求规则不变。

58 个后端测试通过，覆盖成功取数后重启再断连的真实 API 链路（使用测试 Provider 与隔离数据库）。本轮实际东方财富请求仍为 503，无法补出此前已丢失的内存行情，也未向用户数据库注入测试数据。

## 16. 统一两秒实时报价刷新

对照用户旧项目：桌面版 `QUOTE_INTERVAL_SECONDS=2`，Web 版批量报价使用 `setInterval(getQuotes, 2000)`，股票批量接口与现有实现相同。StockPilot 原自选股/指数轮询为 30 秒、单股报价为 10 秒，报价与指数 TTL 为 5 秒；现统一报价/指数/分时前端 2 秒轮询、后端 1 秒 TTL。自选股保持一次批量请求，Provider 无协议改动。

在线观察 39 只自选股确实返回过新报价与成交额；随后源仍间歇断连，连续两秒 API 检查返回旧快照并正确标注 stale。对照旧项目请求头与 `fltt=2/invt=2` 参数，以及增加时间戳的探测，同样出现空响应、超时或断连，未证实兼容性调整可解决。不能将每两秒请求等同于每两秒取得新行情。原有失败退避与快照降级保留。
