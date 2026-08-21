# A 股量化监控桌面软件

这是一个 Windows 桌面股票监控软件，同时保留 FastAPI 和 CSV 回放能力。软件负责行情同步、复权口径隔离、技术指标、规则告警、资讯展示和历史数据回放，不包含券商下单接口，也不构成投资建议。

## 桌面版功能

- 原生 Windows 窗口，不需要打开浏览器。
- 观察列表增删、最新行情、MA/MACD/RSI/量比规则和去重告警。
- 可视化研判页：K 线、MA5/MA20、成交量、鼠标悬停明细，以及趋势、动量、波动率、回撤与可解释建议。
- 行情来源：CSV、AkShare、TuShare Pro、同花顺 iFinD QuantAPI。
- 财联社资讯：支持合同授权 REST API；没有授权时提供官网快捷入口，不抓取网页内容。
- 网络同步在后台线程执行，不会卡住界面。
- 应用内可启动 5/15/30/60 分钟自动监控；盘中行情可展示，但不会进入已收盘指标和建议。
- 软件默认启动自动监控：行情、资金流和授权资讯会独立更新，单一路径异常不会阻塞其他数据。
- “市场雷达”提供个股主力净流入/净流出、行业资金流、资讯利好/利空标签、原文证据、置信度和数据源健康状态。
- “智能筛选”借鉴成熟量化项目的数据/特征/信号分层，将已闭合行情、资金流方向与变化、授权资讯情绪、数据新鲜度和风险惩罚合成为可解释观察评分；每条结果都显示证据、不确定性和复核条件，不输出买卖指令。
- 资金流优先使用已授权 iFinD 问财接口；未配置或失败时自动降级到 AkShare 暴露的东方财富资金流接口，并明确标注“公开源(降级)”。
- TuShare、iFinD 和财联社 Token 存入 Windows 凭据库，不写入普通配置文件。
- SQLite 使用 UTC 毫秒时间戳、WAL 和事务化告警冷却去重。

## 启动桌面软件

开发环境直接运行：

```powershell
python -m pip install -e ".[dev,providers]"
python -m stock_monitor desktop
```

如果已经构建 EXE，直接双击：

```text
dist\A股量化监控.exe
```

桌面数据库和配置默认保存在：

```text
%APPDATA%\AStockMonitor\
```

## 数据源配置

### AkShare

不需要 Token。当前桌面同步使用官方文档中的 `stock_zh_a_hist` 日线接口，同时请求原始价格和前复权价格。AkShare 的数据来自第三方公开站点，可能受到数据端点变更、代理、限流或网络策略影响。

### TuShare Pro

在“数据源设置”中输入 TuShare Token，点击“保存设置”，然后测试连接。桌面同步使用官方 Python SDK 的 `pro_bar`，分别读取未复权和 QFQ 日线，并使用官方 `pre_close`。

实时分钟行情需要账号具备 `rt_min` / `rt_min_daily` 权限；当前桌面版先以日线稳定同步为主。

### 同花顺 iFinD

本项目使用官方 QuantAPI HTTP 接口，不自动操作或逆向同花顺客户端。在 iFinD 接口工具中取得合同授权的 `refresh_token`，保存到设置页即可。

iFinD 当前适配器读取日线原始 OHLCV 和昨收。因为不同授权套餐的复权指标参数可能不同，代码不会猜测其 QFQ 语义；没有明确复权字段时，技术指标会安全跳过，原始价格和昨收告警仍可运行。

配置 iFinD 后，自动监控还会调用官方 `real_time_quotation` 获取最新快照，并通过官方 `smart_stock_picking` 查询主力资金流排名。查询结果字段可能随 iFinD 账号套餐及超级命令模板变化；若无法安全映射，软件会显示具体返回列并自动降级，不会猜测字段含义。

### 财联社

财联社官网版权声明禁止未经书面授权复制或使用内容。本项目不内置网页抓取，只支持企业/机构合同提供的 REST API。设置页需要填写：

- 授权 API 地址；
- 资讯端点；
- Bearer Token。

授权接口响应默认兼容 `data` 或 `items` 列表，以及常见的 `id/title/published_at/url/symbols` 字段。若合同字段不同，应在 `CLSAuthorizedNewsProvider` 中按正式接口文档映射。

自动资讯同步保存增量游标并按 `来源 + 资讯 ID` 幂等更新。利好/利空只在标题或摘要中出现明确证据词时标注；界面同时显示完全来自原文的证据和置信度，无明确方向时保持“中性”。

## 自动更新与时效

- 启动软件后默认每 5 分钟更新，可在顶部切换为 15/30/60 分钟或手动关闭；关闭状态会被记住。
- iFinD、财联社和公开资金流各自维护成功时间、连续失败次数与熔断状态。
- 连续 5 次失败会暂时熔断 60 秒，之后允许恢复探测；界面将数据标为正常、延迟或停滞。
- “主力资金流”是数据供应商按其口径计算的观察指标，不等同于真实机构账户资金，也不能单独作为买卖依据。

## CSV 输入

CSV K 线字段：

```text
symbol,interval,timestamp,trading_date,open,high,low,close,qfq_open,qfq_high,qfq_low,qfq_close,volume,is_closed,source
```

CSV 昨收字段：

```text
symbol,trading_date,previous_close,source
```

逐根回放：

```powershell
python -m stock_monitor replay --bars .\bars.csv --reference-closes .\reference_closes.csv --symbol 000001.SZ --interval 1d
```

## Web API（保留兼容）

```powershell
python -m stock_monitor serve
```

- API 文档：<http://127.0.0.1:8000/docs>
- Web 仪表盘：<http://127.0.0.1:8000/dashboard>
- 多维观察 API：<http://127.0.0.1:8000/research-signals>
- 健康检查：<http://127.0.0.1:8000/health>

## 构建 Windows EXE

```powershell
.\build_desktop.ps1
```

输出文件位于 `dist\A股量化监控.exe`。

## 安全和数据口径

- `Bar.is_closed` 默认 `false`，未闭合 K 线不参与指标或告警。
- 当日日线在上海时间 15:00 前保持未闭合状态，不用于技术研判。
- 原始价格用于绝对价格和相对昨收，前复权价格用于技术指标。
- 缺少 QFQ 数据时不会混用原始价格冒充复权价格。
- SMA、MACD、RSI 和量比都有显式 warm-up。
- 输入会校验时区、交易日期、OHLC、成交量、NaN 和 Inf。
- 自动下单、券商账户和交易权限不在本项目范围内。
- 软件输出是基于历史技术指标的情景提示，不是收益承诺或个性化投资建议；重大决策仍需结合公告、基本面、估值和自身风险承受能力。

## 测试

```powershell
python -m pytest
```

测试覆盖数据质量、未来数据隔离、指标边界、昨收口径、SQLite 并发去重、CSV、供应商字段映射、Token 脱敏和 REST API。

## DeepSeek 记忆桥

先安装可编辑包：

```powershell
python -m pip install -e ".[dev]"
```

`DEEPSEEK_API_KEY` 只支持进程环境变量，不会从 JSON、记忆文件或 Git 读取：

```powershell
$env:DEEPSEEK_API_KEY = "replace-with-your-key"
```

`notes.md` 必须是已批准的 ECC 记忆格式；普通笔记或原始对话记录会被拒绝。最小示例：

```markdown
---
format: ecc.memory.v1
title: Reviewed project context
---
Only reviewed, non-sensitive project context belongs here.
```

默认导出是 dry run，只返回候选数量、写入数量、脱敏数量和哈希，不会写入项目记忆：

```powershell
python -m stock_monitor memory-export --source ".\notes.md"
```

确认 dry run 输出后，再把审核过的记忆本地写入 `.ecc/memory/project/`：

```powershell
python -m stock_monitor memory-export --source ".\notes.md" --apply
```

仓库内提供安装脚本把技能复制到 `%USERPROFILE%\.codex\skills\deepseek-memory-bridge`。脚本只复制技能目录本身，不会复制 `.ecc`、`.env`、数据库或凭据：

```powershell
.\scripts\install_deepseek_memory_skill.ps1
```

命令行回退可以直接调用仓库里的 DeepSeek 客户端；只有显式传入 `--include-memory` 时才会发送已审核的项目记忆：

```powershell
python -m stock_monitor deepseek --question "Summarize the latest changes"
python -m stock_monitor deepseek --question "Summarize the latest changes" --include-memory --memory-root ".ecc/memory/project"
```

CI 只安装 `.[dev]`、运行本地测试和 `python scripts/check_public_release.py`。它不会发起真实 DeepSeek 网络调用，也不会依赖任何在线密钥。

## 高级架构：TDengine + Flink

桌面版现在支持双模式运行：默认模式继续只使用 SQLite；开启“高级数据流”后，SQLite 保存观察列表、配置、告警、同步游标和事务外盒，Kafka 保存可重放的标准事件，Flink 进行事件时间去重与一分钟窗口聚合，TDengine 保存高频行情、资金流和实时聚合结果。

数据链路：

```text
同花顺 / TuShare / AkShare / CSV / 财联社授权 API
                    │
          桌面采集、校验与 SQLite 事务外盒
                    │
                  Kafka
                    │
        Flink 去重、Watermark、窗口聚合
                    │
                TDengine
```

高级栈使用 Apache Kafka 3.9.1、Apache Flink 1.20.3、TDengine 3.4.1.6，以及 TDengine 官方 Flink Connector 2.1.4。Flink 版本固定在 connector 明确编译支持的 1.20 系列；未直接使用当前尚缺 Kafka Connector 的 Flink 2.3 组合。

首次启动：

```powershell
./start_advanced_stack.ps1
python -m pip install -e ".[dev,providers,streaming]"
```

启动脚本首次运行时会自动创建 `.env.advanced`、生成 TDengine 强密码，并把同一密码保存到 Windows 凭据库；不需要手工复制密码。随后在桌面软件“数据源设置 → 高级数据架构”中启用高级数据流并测试连接。默认入口：

- Kafka：`localhost:19092`
- Flink 控制台：<http://localhost:8081>
- TDengine taosAdapter：<http://localhost:6041>

停止服务但保留数据：

```powershell
./stop_advanced_stack.ps1
```

容器数据保存在 Docker named volumes。不要使用 `docker compose down -v`，除非明确需要删除 Kafka、Flink checkpoint 和 TDengine 历史数据。

一致性边界：桌面采集数据与待发送事件在同一 SQLite 事务提交；Kafka 生产者启用幂等投递；Flink 使用 checkpoint 和 at-least-once；TDengine 以时间戳为主键并在 Flink 前置去重。高级集群离线时，本地监控不停止，事件保留在 `event_outbox`，下次同步自动重试。

## GitHub 架构借鉴

- `vnpy/vnpy`：借鉴事件驱动和组件解耦思想；当前桌面版保持后台同步、存储、规则和 UI 分离。
- `microsoft/qlib`：借鉴数据、特征、信号的松耦合研究管线；智能筛选只组合规范化数据，不让模型直接控制交易。
- `akfamily/akshare`：作为公开数据降级路径，并在界面明确标记公开源的数据风险和端点不稳定性。
- `klinecharts/KLineChart`：作为后续 WebView/桌面 2.0 高性能 K 线渲染候选；当前 Tk 版本不新增 Node 运行时，先稳定领域接口与评分输出。

以上仅借鉴公开架构与接口思想，本仓库本轮未复制这些项目的源代码。

## 同步一致性与单位

- 数据库中的 `volume` 统一按“股”保存；AkShare/TuShare 股票日线会将“手”乘以 100。
- CSV 与 iFinD 的成交量口径由数据源设置页显式选择“股”或“手”，应以文件说明或 iFinD 合同接口文档为准。
- 每只股票的 K 线与昨收在同一 SQLite 事务中写入；写入失败会整只股票回滚。
- 外部行情失败会指数退避重试，最终失败只记录该股票，不中断观察列表中的其他股票。
- 当前设计保存一个“标准行情序列”。切换供应商时，会清除该股票旧供应商的同周期历史，避免不同供应商口径混入同一指标序列。
