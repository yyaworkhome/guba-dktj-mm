# 多空研究台｜技术架构与开发计划（3.0）

## 1. 架构原则
- 延续已有 Node.js 项目，避免 MVP 阶段同时维护 Node 与 Python 两套业务后端。
- 前后端分离，采集、分类、统计、AI 解读均通过服务接口协作。
- 数据源、行情源、AI 模型采用适配器接口，可替换和扩展。
- 全市场扫描采用异步任务队列和限速，不在 HTTP 请求内同步扫描完整股票池。
- 原始数据、清洗数据、模型输出、聚合指标分层保存，确保可追溯和可回算。
- 无法合法稳定获取的数据源不接入生产；不绕过验证码、登录或访问控制。

## 2. 推荐技术栈
### 前端
- Vue 3 + TypeScript + Vite
- Pinia、Vue Router
- ECharts
- CSS variables / design tokens：暗色默认、浅色主题
- PWA 首发；后续 Capacitor（Android）与 Tauri（Windows）复用前端

### 后端
- Node.js LTS + TypeScript
- Fastify；若仓库已有稳定 Express 代码，则沿用 Express，不为换框架而重写
- Zod 请求校验、OpenAPI 文档
- WebSocket 或 SSE 推送扫描进度

### 数据与任务
- PostgreSQL：股票主数据、帖子、分类、聚合指标、任务记录
- Redis + BullMQ：采集、去重、分类、聚合、AI 报告任务
- 定时调度：股票池同步、全市场扫描、聚合计算、数据质量检查

### 部署
- Docker Compose 开发环境
- Web 静态资源 + Node API + Worker 独立进程
- PostgreSQL / Redis 持久化卷与备份
- 生产环境配置环境变量、日志、健康检查、任务重试与告警
- 首先部署网页；Web 稳定后封装 Android APK / Windows 安装包

## 3. 服务模块
1. web-app：页面、路由、主题、图表、交互。
2. api-server：股票搜索、分析查询、任务管理、配置接口。
3. stock-universe：A 股股票池同步、证券代码标准化、上市状态维护。
4. source-adapters：东方财富股吧首发适配器；后续新增来源实现统一接口。
5. ingestion-worker：按股票和时间窗采集帖子，限速、断点续扫、失败退避。
6. normalization：文本规范化、去重、广告/灌水过滤、用户标识哈希化。
7. sentiment-worker：看多/看空/中性/无法判断分类，输出置信度、理由、模型版本。
8. aggregation：独立用户数、帖子数、多空占比、情绪指数、热度、分歧、变化率。
9. market-data-adapter：行情与交易日历数据接入，记录来源和时间戳。
10. ai-report-worker：基于统计结果和证据生成结构化个股报告。
11. backtest：事件研究、滚动回测、成本与交易制度约束。
12. scheduler：全市场任务编排、扫描窗口、优先级和重试。

## 4. 数据流
股票池同步 -> 扫描任务入队 -> 来源适配器采集 -> 原始帖子落库 -> 规范化/去重/质量过滤 -> 情绪分类 -> 按股票/时间窗聚合 -> 行情对照 -> AI 报告 -> API 查询 -> 前端展示。

任务按股票分片、限并发、限速、指数退避；失败任务保留错误码与最后成功游标，支持断点续扫。扫描批次记录开始/结束时间、覆盖股票数、成功数、失败数、跳过数。

## 5. 核心数据表（建议）
- stocks：证券代码、名称、交易所、板块、上市状态、更新时间。
- data_sources：来源标识、授权/条款说明、启用状态、速率配置。
- posts：来源帖子 ID、股票代码、作者匿名标识、发布时间、正文、原文链接、抓取时间、内容哈希。
- post_dedup：规范化哈希、重复组、主记录 ID。
- sentiment_results：帖子 ID、类别、置信度、理由、模型名/版本、处理时间。
- sentiment_snapshots：股票代码、窗口、帖子数、独立用户数、多空/中性数、指数、热度、分歧、质量分、口径版本、计算时间。
- market_bars：股票代码、周期、开高低收、成交量、来源、时间戳。
- scan_runs：批次、范围、状态、开始/结束时间、汇总计数。
- scan_tasks：批次、股票代码、窗口、状态、重试次数、游标、错误信息。
- ai_reports：股票代码、窗口、报告 JSON、模型版本、引用的 snapshot/post IDs、生成时间。
- watchlists / watchlist_items：用户自选组合。
- alert_rules / alert_events：预警规则与事件。

作者标识只保存业务必需的匿名化/哈希化值；原始文本保留期限与使用方式按来源授权确定。

## 6. 核心 API（初版）
- GET /api/v1/stocks?query=600900
- GET /api/v1/market/overview
- GET /api/v1/stocks/:symbol/sentiment?window=1d
- GET /api/v1/stocks/:symbol/posts?window=1d
- GET /api/v1/rankings?type=bullish&window=1d
- GET /api/v1/sectors/sentiment
- GET /api/v1/scans/latest
- POST /api/v1/scans
- POST /api/v1/scans/:id/pause
- POST /api/v1/scans/:id/resume
- POST /api/v1/scans/:id/retry-failed
- GET /api/v1/scans/:id
- GET /api/v1/stocks/:symbol/ai-report?window=1d
- GET /api/v1/history/:symbol

所有响应包含 data_as_of、source、window、metric_version；缺数据时返回明确状态而不是伪造 0。

## 7. 指标计算（首版可解释）
- 四类观点：看多、看空、中性、无法判断。
- 多空比例按有效且可识别观点计算；无法判断单独列示。
- 情绪指数先用平滑后的多空净倾向映射到 0-100；低样本时显示“样本不足”，不展示强结论。
- 用户去重与帖子去重分开计算。
- 置信度可用于加权，但首版同时保留未加权原始分布。
- 质量分由独立用户数、可识别率、重复率、数据新鲜度、来源覆盖组成。
- 分歧指标综合看多/看空并存程度与参与度。
- AI 报告只解释已计算的统计事实；每个关键判断关联证据和时间窗。

## 8. 安全、合规和可靠性
- 只使用公开且允许自动化访问、或已获得授权的数据接口。
- 遵守来源条款、速率限制和数据许可；不绕过验证码、登录墙、封禁或访问控制。
- API 密钥仅存服务端环境变量，不提交到 Git。
- 对外接口实施限流、输入校验、错误脱敏和基础审计。
- 数据源故障时展示数据延迟/不可用状态，避免把旧数据标成实时。
- 备份 PostgreSQL；Worker 幂等；任务可重试且避免重复入库。

## 9. 开发里程碑
- M0 工程基线：确认现有仓库代码、运行方式和部署配置；补齐 README、环境变量模板、Docker Compose、CI 检查。
- M1 前端正式壳：Vue 3 页面框架、顶部导航、深浅主题、总览页响应式布局、图表空状态与股票搜索。
- M2 后端与数据模型：股票搜索、数据库迁移、API 契约、扫描批次与任务管理。
- M3 首个数据源闭环：在合规可用前提下接入东方财富股吧，完成采集、去重、分类、聚合和来源追溯。
- M4 全市场扫描：股票池同步、队列限速、断点续扫、失败重试、扫描控制台。
- M5 AI 个股报告与异动：结构化报告、证据引用、样本质量提示、预警规则。
- M6 历史数据与跨端：情绪-行情对照、无未来数据泄漏的回测、PWA/Android/Windows 封装。

## 10. MVP 验收优先级
P0：股票搜索、单股帖子分析、真实多空统计、样本质量、扫描进度、AI 解读证据。
P1：全市场扫描稳定运行、排名、板块与大盘概览。
P2：异动预警、历史研究与回测、自选组合。
P3：Android / Windows 原生封装与发布。
