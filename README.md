# 散户情绪量化研究工作台 2.0

Vue 3 + FastAPI 的可运行开发版本，目标为 Web/PWA、Android APK 与 Windows EXE 共用界面、API 和账号。独立研究工具，非东方财富官方产品。

GitHub仓库：[yyaworkhome/guba-dktj-mm](https://github.com/yyaworkhome/guba-dktj-mm)。`origin` 已关联该仓库；2.0代码在 `codex/v2-core` 开发分支上，基于 `main` 的初始提交。开发分支通过检查后再合入主分支，不把工程配置等同于线上发布。

**首页已接入5只股票股吧普通讨论的标题/正文及未复权日线手动采样，不是全市场实时监测。** 支持东方财富、平安银行、宁德时代、贵州茅台、中芯国际，标题与正文分别查询、分别披露采集状态。首页有独立的真实采集快照历史与5股样本观察榜；侧栏的大盘、板块、排行、预警、回测和Excel仍是演示数据。情绪识别使用保守规则，AI模型、资金接口尚未接入。注册、登录、自选与条件保存为实际后端功能。

**首页另有“妙想数据 · 行情与板块”真实只读查询入口。** 使用后端配置的 `MX_APIKEY` 查询股票/指数行情、财务及板块条件筛选，支持按账号保存历史、回看及导出Excel。它不提供帖子情绪分类，不混入舆论分或演示回测。详细配置见 [妙想接入说明](docs/MIAOXIANG.md)。

## 本地运行（Windows）

环境：Node 24、Python 3.11+。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1
```

打开 <http://127.0.0.1:5173>，API文档 <http://127.0.0.1:8000/docs>。首次运行下载依赖；关闭前端终端会停止该脚本启动的后端。

也可分别启动两个终端：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements.lock.txt
.venv/Scripts/python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

```powershell
npm ci
npm run dev
```

本地默认 SQLite（`backend/data/sentiment.db`，不提交Git）。生产配置 `DATABASE_URL=postgresql+psycopg://...`。账号未设置默认密码，请自行注册；会话在当前页面会话中保存，服务端7日过期，退出立即撤销。不同客户端须连接**同一个API**才能共享账号和自选。

## 真实采样（5只股票）

启动服务后，在项目目录运行：

```powershell
.venv/Scripts/python.exe -m backend.collect
# 指定明确的证券ID；正文单次最多请求5个候选详情
.venv/Scripts/python.exe -m backend.collect --instrument XSHE:300750 --content body --limit 3
```

默认采集东方财富标题，每次请求一页股吧列表和一批最多30根日线；正文模式另请求1～5个候选详情，详情请求之间至少1秒。同一股票的标题/正文模式共用至少10分钟采样间隔。遇到源站403/429、验证页或格式变化会记录失败，停止后续详情请求，不绕过限制、不以模拟数据补齐。首次运行新增采集记录表和行情观察表，已有帖子与快照保留。运行前可备份本地数据库。

首页默认展示东方财富近3个自然日的真实标题采样，可搜索/选择5只股票、切换正文模式或进入“全功能演示”。真实范围按北京时间自然日筛选（今日/近3、5、10、20日），不是交易日统计，也不是全量历史补采。规则按所选模式分类标题或标题加正文；只展示舆论分和35%维度覆盖率，不称为完整综合指数。样本卡片提供原文链接和发现/观察时间。详细口径、接口和故障处理见 [数据采集说明](docs/DATA_SOURCES.md)。

2026-11-06 23:49（北京时间）首次联网验证：列表83条，排除非普通讨论及跨股吧条目后入库49条标题、30根日线；其中当日45条标题，经内容去重后44条。2026-11-07 00:08～00:09补充验证：平安银行、贵州茅台、宁德时代、中芯国际合计保存10条正文观察；平安银行为部分成功，东方财富首次正文批次失败，均保留实际运行状态。随后修复详情才显示转帖时应筛除并继续的处理逻辑。样本会随后续采集改变，不代表当前实时市场。

2026-11-07 01:00（北京时间）完成5股标题采样验证，各股本批纳入49/65/26/31/36条（采样纳入量，不是全股吧新增发帖量）。当时近3自然日各股方向样本均不足10条，观察榜明确列出未达标原因，不生成名次。历史图只读取当时保存的快照：最近30天最多200点；无方向分保留空值，不同长度窗口不连线。首次接入前没有历史快照，不补造过去曲线。

## 已实现的路径

- 首页 / 大盘 / 个股 / 板块，统一对象模型及分析结果。输入 `000001` 返回上证指数与平安银行，必须明确选择。
- 首页独立真实采样入口：5股选择、标题/正文切换、采集状态、源错误、多空分布、有效作者、互动累计计数、舆论分、日线观察、行为标签、已采样日期明细与来源样本；缺失数据不补零，不借用其他股票或模式的帖子。
- 真实帖子首次发现时间、内容/互动修订、规则分类版本、每次采集响应摘要和观察时间持久化；同一内容去重，每位作者每天最多计3条；日线重复获取新增观察记录，不覆盖旧记录。
- 真实采集时点快照图与明细，和按发帖日期分组明确区分；5股真实观察榜按同一自然日窗口/内容口径排序，至少20条有效样本、10位作者、10条方向样本且观察未超过24小时才入榜。阈值尚待校准，未达标只展示覆盖原因。
- 妙想真实数据查询、按账号隔离历史、十分钟同问句缓存、独立观察时间、接口条件/证券/数据日期披露及有限行Excel导出；源错误保留失败记录。
- 今日、近3/5/10/20交易日聚合；趋势、关键词、多空比、一致性、分歧度、覆盖率与缺失维度披露。
- 最新单日看多榜、看空榜、综合榜、讨论热度榜，点击进入对应个股。
- 规则异动筛选及文本情绪规则分类（界面明确标为非AI）；手动分类结果按内容修订和规则版本入库，不混入行情演示样本。
- 注册/登录；按账号隔离的自选、预警条件增删、当前快照触发状态查询。
- 1/3/5/10交易日事件回测、样本数、未成熟剔除、上涨比例95%区间；Excel实际文件导出。
- PWA manifest及应用外壳离线缓存；私有API不缓存，离线分析会提示连接失败。
- Android原生工程、Tauri Windows工程、Docker/PostgreSQL部署配置、GitHub构建工作流。

## 明确未完成

| 项目 | 状态 |
| --- | --- |
| 全量股吧正文、全证券目录、实时行情和资金采集 | 5股首页普通标题、限量正文和日线手动采样；演示目录15个对象、20个模拟交易日 |
| AI语义分类、AI摘要和AI选股 | 未接入；当前为保守规则，不输出伪造模型置信度 |
| 完整行业成分、真实交易日历、资金与“主力”归因 | 未接入；板块成分仅演示，不推断真实主力身份 |
| 每日收盘自动采集/复盘/通知 | 未启用；手动复盘为模板摘要 |
| Android APK、Windows EXE | 已配置构建，交付状态以实际构建产物为准 |
| 公网网址与三端线上同步 | 未部署；GitHub仓库已关联，部署位置尚未确定 |
| 原生推送、应用自动更新、Windows多窗口 | 后续阶段；当前不显示虚假开关 |
| 上线认证完善 | 暂无找回密码/邮箱验证；限流为单进程实现，生产需共享限流和HTTPS |

## 验证

```powershell
npm run build
.venv/Scripts/python.exe -m pytest backend/tests -q
```

测试覆盖零方向样本、缺失权重、去重与作者上限、代码歧义、账号隔离、退出撤销、有效Excel、未来价格可用时间与重叠事件剔除。

当前本机验证：37项后端测试通过，Vue类型检查与PWA生产构建通过；内置浏览器已验证演示流程，以及真实采样首页、股票搜索/切换、标题与正文隔离、样本溯源、自然日切换和390×844手机布局。妙想入口已实际验证股票、上证指数、半导体筛选、缓存、历史回看、Excel下载和退出后私有数据清除。真实快照图/明细、观察榜排序条件与股票跳转、正文与标题历史隔离、手机表格局部横滚已验证。新增测试覆盖源结构校验、详情身份与发布时间校验、HTML纯文本处理、转帖排除、请求错误中止、增量幂等、历史可用时点、快照原值/空值保留、榜单样本门槛/陈旧排除、妙想错误脱敏/账号隔离/缓存过期/表格限量及Excel公式文本化。开发分支此前的GitHub网页与后端检查已通过；尚未验证真实安卓/Windows客户端、PostgreSQL容器与公网部署。

依赖采用 `package-lock.json` 与 `backend/requirements.lock.txt` 锁定；`requirements.txt` 为直接依赖范围。`npm audit --omit=dev` 当前0项；完整审计有6项构建依赖低/中风险提示，涉及Workbox传递依赖，未用破坏性跨版本升级掩盖。上线构建环境应继续跟踪更新。

## 三端打包

先部署HTTPS API，再为原生包设置 `VITE_API_URL` 为该API的**源地址，不含 `/api`**。原生发布构建会校验HTTPS及 `/api/health`，未部署可访问的API时拒绝继续，防止安装后错误连接手机本机。

```powershell
$env:VITE_API_URL = 'https://实际部署域名'
powershell -File scripts/build-android.ps1
npm run desktop:build
```

Android需Java 21与Android SDK；APK输出 `android/app/build/outputs/apk/debug/app-debug.apk`，为可安装的调试包，正式渠道发布需独立签名密钥。Windows需Rust、Microsoft C++ Build Tools、WebView2，NSIS EXE输出 `src-tauri/target/release/bundle/nsis/`。签名密钥不入库。

GitHub工作流 `Build installable clients` 可手动输入部署API地址，分别在Linux/Windows runner构建APK与EXE并上传Artifacts。配置存在不代表构建已通过，需实际运行验证。Web/PWA由 `npm run build` 输出至 `dist/`，手机添加桌面需HTTPS或浏览器认可的本地安全上下文。

原生要求参考：[Capacitor环境](https://capacitorjs.com/docs/getting-started/environment-setup)、[Tauri Windows前置条件](https://v2.tauri.app/start/prerequisites/)、[Windows安装包](https://v2.tauri.app/distribute/windows-installer/)。

## 公网部署

`Dockerfile` 将网页打包到API同一镜像；`compose.yml` 启动PostgreSQL与服务。设置 `POSTGRES_PASSWORD`、`CORS_ORIGINS`、`SEED_DEMO` 后执行 `docker compose up --build -d`，数据库不对外开放。API只绑定服务器127.0.0.1:8000，通过 `deploy/Caddyfile` 中的实际域名反向代理及申请HTTPS。原生来源加入CORS白名单。

当前 `SEED_DEMO=true` 仅用于演示验收。设为false停止生成演示样本，但不会清除已保存的演示数据；真实采样须另行运行采集命令。正式生产仍需确认数据源使用方式、迁移策略、数据库备份、共享限流和发行签名。不要将演示快照用于真实研究回测。

功能口径、数据库与后续开发拆分见 [2.0设计](docs/PRODUCT_V2.md)；视觉实现依据见 [界面设计](docs/DESIGN.md)。
