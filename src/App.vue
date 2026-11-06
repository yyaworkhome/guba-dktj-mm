<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from "vue";
import {
  House,
  ChartNoAxesColumnIncreasing,
  LayoutGrid,
  ChartLine,
  Trophy,
  ScanLine,
  Bell,
  History,
  Star,
  LogIn,
  X,
  Search,
  ChevronDown,
  Menu,
  FileText,
  ArrowRight,
  RefreshCw,
  Trash2,
  Plus,
  Settings2,
} from "lucide-vue-next";
import { request, post } from "./api";
import type {
  Kind,
  Instrument,
  Analysis,
  Ranking,
  Backtest,
  Alert,
  Signal,
} from "./types";
import AnalysisResult from "./components/AnalysisResult.vue";
import RankTable from "./components/RankTable.vue";
import BacktestPanel from "./components/BacktestPanel.vue";
import LivePanel from "./components/LivePanel.vue";
import MiaoxiangPanel from "./components/MiaoxiangPanel.vue";
type View =
  | "home"
  | "index"
  | "sector"
  | "stock"
  | "rankings"
  | "signals"
  | "alerts"
  | "backtest"
  | "portfolio";
const directionNames: Record<string, string> = {
  bullish: "看多",
  bearish: "看空",
  neutral: "中性",
};
const metricNames: Record<string, string> = {
  bullish_pct: "看多占比",
  bearish_pct: "看空占比",
  score: "情绪分",
};
const nav = [
  ["home", "首页", House],
  ["index", "大盘", ChartNoAxesColumnIncreasing],
  ["sector", "板块", LayoutGrid],
  ["stock", "个股", ChartLine],
  ["rankings", "排行榜", Trophy],
  ["signals", "AI选股", ScanLine],
  ["alerts", "预警中心", Bell],
  ["backtest", "历史回测", History],
  ["portfolio", "自选组合", Star],
] as const;
const modes: { id: Kind; label: string }[] = [
  { id: "market", label: "全市场" },
  { id: "index", label: "大盘指数" },
  { id: "stock", label: "个股" },
  { id: "sector", label: "板块" },
  { id: "portfolio", label: "自选组合" },
];
const view = ref<View>("home"),
  mode = ref<Kind>("market"),
  period = ref(10);
const homeData = ref<'live' | 'demo' | 'mx'>('live');
const showLive = computed(() => view.value === 'home' && homeData.value === 'live');
const showMx = computed(() => view.value === 'home' && homeData.value === 'mx');
async function switchHomeData(value: 'live' | 'demo' | 'mx') {
  homeData.value = value;
  error.value = '';
  if (value === 'demo') { await navigate('home'); await loadRanks(); }
}
const selected = ref<Instrument | null>(null),
  query = ref(""),
  candidates = ref<Instrument[]>([]),
  searching = ref(false),
  pickerOpen = ref(false);
const analysis = ref<Analysis | null>(null),
  ranks = ref<Ranking[]>([]),
  sort = ref("score"),
  busy = ref(false),
  error = ref(""),
  toast = ref("");
const menuOpen = ref(false),
  modal = ref<"auth" | "method" | "report" | null>(null);
const username = ref(sessionStorage.getItem("sentiment-user") || ""),
  authMode = ref("login"),
  authName = ref(""),
  authPassword = ref(""),
  authBusy = ref(false),
  authError = ref("");
const watches = ref<Instrument[]>([]),
  watchAnalyses = ref<Analysis[]>([]),
  alerts = ref<Alert[]>([]),
  alertMetric = ref("bullish_pct"),
  alertThreshold = ref(80);
const threshold = ref(60),
  backtest = ref<Backtest | null>(null),
  backtestBusy = ref(false);
const signalItems = ref<(Signal & { instrument: Instrument })[]>([]),
  classifyText = ref(""),
  classified = ref<{ label: string; reason: string; tags: string[] } | null>(
    null,
  ),
  classifyBusy = ref(false);
const report = ref<{ title: string; paragraphs: string[] } | null>(null),
  installPrompt = ref<any>(null);
let returnFocus: HTMLElement | null = null;
watch(modal, async (value) => {
  if (value) {
    returnFocus = document.activeElement as HTMLElement;
    await nextTick();
    (
      document.querySelector<HTMLElement>(".modal input") ||
      document.querySelector<HTMLElement>(".modal button")
    )?.focus();
  } else {
    returnFocus?.focus();
  }
});
function trapModal(event: KeyboardEvent) {
  if (event.key !== "Tab") return;
  const nodes = [
    ...document.querySelectorAll<HTMLElement>(
      ".modal button:not(:disabled), .modal input, .modal a[href]",
    ),
  ];
  if (!nodes.length) return;
  const first = nodes[0],
    last = nodes[nodes.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}
const title = computed(
  () =>
    ({
      home: "市场情绪总览",
      index: "大盘指数分析",
      sector: "板块情绪分析",
      stock: "个股情绪分析",
      rankings: "情绪排行榜",
      signals: "情绪异动研究",
      alerts: "预警中心",
      backtest: "历史情绪回测",
      portfolio: "自选组合",
    })[view.value],
);
const scope = computed(() =>
  mode.value === "market"
    ? "多指数股吧 + 全市场涨跌与涨跌停样本"
    : mode.value === "index"
      ? "多指数股吧舆论 + 全A宽度代理，展示指数相关情绪"
      : mode.value === "sector"
        ? "板块聚合展示：当前为独立模拟快照，真实成分聚合待接入"
        : mode.value === "portfolio"
          ? "同一账号同步自选标的，横向比较各自的情绪变化"
          : "对应个股股吧样本；不会混入同代码指数",
);
const isAnalysis = computed(() =>
  ["home", "index", "sector", "stock", "backtest"].includes(view.value),
);
let searchTimer: ReturnType<typeof setTimeout>,
  toastTimer: ReturnType<typeof setTimeout>,
  searchVersion = 0,
  analysisVersion = 0,
  rankVersion = 0;
function message(s: string) {
  toast.value = s;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toast.value = ""), 4500);
}
function fail(e: unknown) {
  error.value = e instanceof Error ? e.message : "操作失败，请重试";
}
function expire() {
  username.value = "";
  watches.value = [];
  watchAnalyses.value = [];
  alerts.value = [];
}
function beforeInstall(e: Event) {
  e.preventDefault();
  installPrompt.value = e;
}
async function install() {
  if (installPrompt.value) {
    await installPrompt.value.prompt();
    installPrompt.value = null;
  }
}
async function loadCandidates() {
  const version = ++searchVersion;
  searching.value = true;
  candidates.value = [];
  try {
    const rows = await request<Instrument[]>(
      `/instruments?${new URLSearchParams({ q: query.value, kind: query.value ? "all" : mode.value === "portfolio" ? "stock" : mode.value })}`,
    );
    if (version === searchVersion) candidates.value = rows;
  } catch (e) {
    if (version === searchVersion) fail(e);
  } finally {
    if (version === searchVersion) searching.value = false;
  }
}
function searchInput() {
  selected.value = null;
  candidates.value = [];
  searching.value = true;
  pickerOpen.value = true;
  clearTimeout(searchTimer);
  ++searchVersion;
  searchTimer = setTimeout(loadCandidates, 180);
}
async function pick(i: Instrument) {
  clearTimeout(searchTimer);
  selected.value = i;
  query.value = i.name;
  mode.value = i.kind;
  if (!["alerts", "backtest"].includes(view.value)) {
    view.value = i.kind === "market" ? "home" : (i.kind as View);
  }
  pickerOpen.value = false;
  searchVersion++;
}
async function changeMode(kind: Kind) {
  mode.value = kind;
  query.value = "";
  selected.value = null;
  pickerOpen.value = false;
  error.value = "";
  if (kind === "portfolio") {
    await navigate("portfolio");
    return;
  }
  await loadCandidates();
  if (candidates.value[0]) await pick(candidates.value[0]);
}
async function run() {
  if (!selected.value) {
    pickerOpen.value = true;
    await loadCandidates();
    return;
  }
  const version = ++analysisVersion,
    id = selected.value.id,
    n = period.value;
  busy.value = true;
  error.value = "";
  backtest.value = null;
  try {
    const a = await request<Analysis>(
      `/analysis?${new URLSearchParams({ instrument_id: id, period: String(n) })}`,
    );
    if (version === analysisVersion) {
      analysis.value = a;
      if (view.value === "backtest") await runBacktest();
    }
  } catch (e) {
    if (version === analysisVersion) fail(e);
  } finally {
    if (version === analysisVersion) busy.value = false;
  }
}
async function loadRanks() {
  const version = ++rankVersion;
  try {
    const rows = await request<Ranking[]>(`/rankings?sort=${sort.value}`);
    if (version === rankVersion) ranks.value = rows;
  } catch (e) {
    if (version === rankVersion) fail(e);
  }
}
async function navigate(v: View) {
  view.value = v;
  menuOpen.value = false;
  error.value = "";
  if (showLive.value || showMx.value) return;
  try {
    if (["home", "index", "sector", "stock"].includes(v)) {
      await changeMode(v === "home" ? "market" : (v as Kind));
      await run();
    }
    if (v === "rankings") await loadRanks();
    if (v === "portfolio") {
      mode.value = "portfolio";
      await loadPrivate();
    }
    if (v === "alerts") {
      if (selected.value) {
        mode.value = selected.value.kind;
        query.value = selected.value.name;
      } else await changeMode("stock");
      await loadPrivate();
    }
    if (v === "signals") {
      const s = await request<{
        items: (Signal & { instrument: Instrument })[];
      }>("/signals");
      signalItems.value = s.items;
    }
    if (v === "backtest") {
      if (!selected.value) await changeMode("market");
      await run();
    }
  } catch (e) {
    fail(e);
  }
}
async function openInstrument(i: Instrument) {
  view.value =
    i.kind === "stock"
      ? "stock"
      : i.kind === "index"
        ? "index"
        : i.kind === "sector"
          ? "sector"
          : "home";
  await pick(i);
  await run();
  window.scrollTo({ top: 0, behavior: "smooth" });
}
async function authenticate() {
  authBusy.value = true;
  authError.value = "";
  try {
    const res = await post<{ token: string; username: string }>(
      `/auth/${authMode.value}`,
      { username: authName.value, password: authPassword.value },
    );
    sessionStorage.setItem("sentiment-token", res.token);
    sessionStorage.setItem("sentiment-user", res.username);
    username.value = res.username;
    authPassword.value = "";
    modal.value = null;
    await loadPrivate();
    message("账号已连接，自选与预警保存在统一后端。");
  } catch (e) {
    authError.value = e instanceof Error ? e.message : "登录失败";
  } finally {
    authBusy.value = false;
  }
}
async function logout() {
  try {
    await post("/auth/logout", {});
  } catch (e) {
    fail(e);
  } finally {
    sessionStorage.removeItem("sentiment-token");
    sessionStorage.removeItem("sentiment-user");
    expire();
  }
}
async function loadPrivate() {
  if (!username.value) return;
  const owner = username.value;
  try {
    const [w, a] = await Promise.all([
      request<Instrument[]>("/watchlist"),
      request<Alert[]>("/alerts"),
    ]);
    const analyses = await Promise.all(
      w.map((i) =>
        request<Analysis>(
          `/analysis?instrument_id=${encodeURIComponent(i.id)}&period=10`,
        ),
      ),
    );
    if (owner !== username.value) return;
    watches.value = w;
    alerts.value = a;
    watchAnalyses.value = analyses;
  } catch (e) {
    if (owner === username.value) fail(e);
  }
}
async function addWatch() {
  if (!username.value) {
    modal.value = "auth";
    return;
  }
  if (!analysis.value) return;
  try {
    await post(
      "/watchlist",
      { instrument_id: analysis.value.instrument.id },
      "PUT",
    );
    await loadPrivate();
    message(`已添加 ${analysis.value.instrument.name} 到自选`);
  } catch (e) {
    fail(e);
  }
}
async function removeWatch(i: Instrument) {
  try {
    await request(`/watchlist?instrument_id=${encodeURIComponent(i.id)}`, {
      method: "DELETE",
    });
    await loadPrivate();
  } catch (e) {
    fail(e);
  }
}
async function createAlert() {
  if (!selected.value) {
    message("请先选择一个分析对象");
    return;
  }
  try {
    await post("/alerts", {
      instrument_id: selected.value.id,
      metric: alertMetric.value,
      threshold: alertThreshold.value,
    });
    await loadPrivate();
    message("预警条件已保存；当前为站内查询，不发送推送。");
  } catch (e) {
    fail(e);
  }
}
async function removeAlert(id: number) {
  try {
    await request(`/alerts/${id}`, { method: "DELETE" });
    await loadPrivate();
  } catch (e) {
    fail(e);
  }
}
async function runBacktest() {
  if (!analysis.value) return;
  backtestBusy.value = true;
  error.value = "";
  try {
    backtest.value = await request<Backtest>(
      `/backtest?${new URLSearchParams({ instrument_id: analysis.value.instrument.id, threshold: String(threshold.value) })}`,
    );
  } catch (e) {
    fail(e);
  } finally {
    backtestBusy.value = false;
  }
}
async function classify() {
  if (!username.value) {
    modal.value = "auth";
    return;
  }
  classifyBusy.value = true;
  try {
    classified.value = await post("/classify", { text: classifyText.value });
  } catch (e) {
    fail(e);
  } finally {
    classifyBusy.value = false;
  }
}
async function showReport() {
  try {
    report.value = await request("/report");
    modal.value = "report";
  } catch (e) {
    fail(e);
  }
}
onMounted(async () => {
  window.addEventListener("session-expired", expire);
  window.addEventListener("beforeinstallprompt", beforeInstall);
  await navigate("home");
  await loadRanks();
  if (username.value) {
    try {
      await request("/auth/me");
      await loadPrivate();
    } catch {
      expire();
    }
  }
});
onUnmounted(() => {
  clearTimeout(searchTimer);
  clearTimeout(toastTimer);
  window.removeEventListener("session-expired", expire);
  window.removeEventListener("beforeinstallprompt", beforeInstall);
});
</script>
<template>
  <header class="app-header">
    <button
      class="mobile-menu icon-button"
      aria-label="展开导航"
      @click="menuOpen = !menuOpen"
    >
      <Menu :size="21" /></button
    ><a class="brand" href="#" @click.prevent="navigate('home')"
      >散户情绪量化<span>RETAIL SENTIMENT LAB</span></a
    >
    <div class="header-right">
      <span class="data-status"><i></i>{{ showMx ? '妙想数据 · 按需查询' : showLive ? '真实个股采样 · 手动更新' : '演示数据 · 与真实采样隔离' }}</span
      ><button v-if="installPrompt" class="secondary compact" @click="install">
        安装应用</button
      ><button
        v-if="!username"
        class="secondary compact"
        @click="modal = 'auth'"
      >
        <LogIn :size="15" />登录 / 注册</button
      ><button v-else class="secondary compact" @click="logout">
        {{ username }} · 退出
      </button>
    </div>
  </header>
  <aside :class="['sidebar', { open: menuOpen }]">
    <nav aria-label="主导航">
      <button
        v-for="[id, label, icon] in nav"
        :key="id"
        :class="{ active: view === id }"
        @click="navigate(id)"
      >
        <component :is="icon" :size="19" /><span>{{ label }}</span>
      </button>
    </nav>
    <div class="sidebar-footer">
      <span class="version-mark">2.0</span>
      <p>观点留痕 · 信号验证</p>
      <small>独立研究工具<br />非东方财富官方产品</small>
    </div>
  </aside>
  <button
    v-if="menuOpen"
    class="nav-scrim"
    aria-label="关闭导航"
    @click="menuOpen = false"
  ></button>
  <main>
    <div class="page-heading">
      <div>
        <h1>{{ showMx ? '行情与板块数据' : showLive ? '散户情绪 · 真实数据采样' : title }}</h1>
        <p>
          {{
            view === "home"
              ? "让观点有迹可循，让信号经得起验证。"
              : view === "signals"
                ? "先定义条件，再验证信号。当前为规则筛选，AI模型尚未启用。"
                : "统一数据口径，保留每一次情绪变化。"
          }}
        </p>
      </div>
      <div v-if="!showLive && !showMx" class="heading-actions">
        <button class="secondary" aria-label="情绪复盘" @click="showReport">
          <FileText :size="16" /><span>情绪复盘</span></button
        ><button class="secondary" aria-label="数据口径" @click="modal = 'method'">
          <Settings2 :size="16" /><span>数据口径</span>
        </button>
      </div>
    </div>
    <p class="mobile-data-status">{{ showMx ? '妙想数据 · 按需查询与历史留存' : showLive ? '个股真实采样 · 标题/正文分开统计 · 规则分类' : '演示数据 · 与真实采样隔离 · AI尚未接入' }}</p>
    <div v-if="view === 'home'" class="page-filters" role="group" aria-label="数据来源">
      <button :class="['secondary', { selected: homeData === 'live' }]" :aria-pressed="homeData === 'live'" @click="switchHomeData('live')">真实采样 · 个股</button>
      <button :class="['secondary', { selected: homeData === 'mx' }]" :aria-pressed="homeData === 'mx'" @click="switchHomeData('mx')">妙想数据 · 行情与板块</button>
      <button :class="['secondary', { selected: homeData === 'demo' }]" :aria-pressed="homeData === 'demo'" @click="switchHomeData('demo')">全功能演示 · 模拟样本</button>
    </div>
    <LivePanel v-if="showLive" />
    <MiaoxiangPanel v-if="showMx" :username="username" @login="modal = 'auth'" />
    <template v-if="!showLive && !showMx">
    <div v-if="error" class="error-banner" role="alert">
      {{ error }}<button class="text-button" @click="error = ''">关闭</button>
    </div>
    <section v-if="isAnalysis || view === 'alerts'" class="panel analysis-form">
      <div class="mode-tabs" role="group" aria-label="分析模式">
        <button
          v-for="m in modes"
          :key="m.id"
          :class="{ selected: mode === m.id }"
          :aria-pressed="mode === m.id"
          @click="changeMode(m.id)"
        >
          {{ m.label }}
        </button>
      </div>
      <form @submit.prevent="view !== 'alerts' && run()">
        <div class="form-main">
          <div class="instrument-picker">
            <label for="instrument-search" class="sr-only"
              >输入股票、指数或板块代码与名称</label
            ><Search :size="17" /><input
              id="instrument-search"
              v-model="query"
              autocomplete="off"
              placeholder="输入代码或名称，如 000001"
              aria-controls="candidate-list"
              :aria-expanded="pickerOpen"
              @input="searchInput"
              @focus="
                pickerOpen = true;
                loadCandidates();
              "
              @keydown.esc="pickerOpen = false"
            /><button
              type="button"
              class="picker-toggle icon-button"
              aria-label="显示候选对象"
              @click="
                pickerOpen = !pickerOpen;
                loadCandidates();
              "
            >
              <ChevronDown :size="17" />
            </button>
            <div v-if="pickerOpen" id="candidate-list" class="candidate-list">
              <p>请选择具体对象 · 不自动猜测代码</p>
              <button
                v-for="i in candidates"
                :key="i.id"
                type="button"
                @click="pick(i)"
              >
                <span
                  ><b>{{ i.name }}</b
                  ><small>{{
                    i.kind === "index"
                      ? "指数"
                      : i.kind === "stock"
                        ? "股票"
                        : i.kind === "sector"
                          ? "板块"
                          : "全市场"
                  }}</small></span
                ><span>{{ i.id }}</span>
              </button>
              <div v-if="searching" class="empty-note" role="status">
                正在匹配分析对象…
              </div>
              <div v-else-if="!candidates.length" class="empty-note">
                未找到对象。当前目录为有限演示样本。
              </div>
              <button
                type="button"
                class="text-button close-picker"
                @click="pickerOpen = false"
              >
                收起候选
              </button>
            </div>
          </div>
          <div
            v-if="view !== 'alerts'"
            class="period-options"
            role="group"
            aria-label="统计周期"
          >
            <button
              v-for="n in [1, 3, 5, 10, 20]"
              :key="n"
              type="button"
              :class="{ selected: period === n }"
              :aria-pressed="period === n"
              @click="period = n"
            >
              {{ n === 1 ? "今日" : `近${n}日` }}
            </button>
          </div>
          <button
            v-if="view !== 'alerts'"
            class="primary analyze-button"
            :disabled="busy || !selected"
          >
            <RefreshCw v-if="busy" :size="16" class="spin" />{{
              busy ? "分析中…" : "开始分析"
            }}<ArrowRight v-if="!busy" :size="17" />
          </button>
        </div>
        <p class="scope-note">{{ scope }}</p>
      </form>
    </section>
    <div v-if="busy && !analysis" class="panel loading-state" role="status">
      正在读取历史快照…
    </div>
    <template v-if="isAnalysis && analysis"
      ><div
        v-if="
          !selected ||
          analysis.instrument.id !== selected.id ||
          analysis.requested_period !== period
        "
        class="pending-note"
      >
        筛选条件已修改。下方仍为 {{ analysis.instrument.name }} /
        {{ analysis.period }}日结果，请点击“开始分析”更新。
      </div>
      <div :aria-busy="busy" :class="{ updating: busy }">
        <AnalysisResult
          :analysis="analysis"
          @watch="addWatch"
          @backtest="
            view = 'backtest';
            runBacktest();
          "
        />
      </div>
      <BacktestPanel
        v-if="view === 'backtest'"
        v-model:threshold="threshold"
        :data="backtest"
        :busy="backtestBusy"
        @run="runBacktest" /><RankTable
        v-else
        :rows="ranks.slice(0, 4)"
        @select="openInstrument"
    /></template>
    <template v-if="view === 'rankings'"
      ><div class="page-filters">
        <button
          v-for="[key, label] in [
            ['score', '综合情绪'],
            ['bullish_pct', '看多榜'],
            ['bearish_pct', '看空榜'],
            ['heat_change', '热度异动'],
          ]"
          :key="key"
          :class="['secondary', { selected: sort === key }]"
          @click="
            sort = key;
            loadRanks();
          "
        >
          {{ label }}
        </button>
      </div>
      <RankTable
        :rows="ranks"
        title="个股情绪排行榜"
        @select="openInstrument"
      />
      <p class="subtle section-note">
        仅展示演示证券目录，不是全A排名。讨论热度变化与涨跌幅分开统计。
      </p></template
    >
    <template v-if="view === 'signals'"
      ><section class="panel">
        <h2>异动条件筛选</h2>
        <p class="subtle section-note">
          已实现：一致看多 / 极端悲观 / 恐慌后修复 / 情绪升温 / 亢奋但滞涨 /
          价格与情绪背离。资金背离、低位识别和板块扩散等待真实数据接入。
        </p>
        <div
          v-for="(s, i) in signalItems"
          :key="`${s.instrument.id}-${i}`"
          class="screen-row"
        >
          <div>
            <button class="table-link" @click="openInstrument(s.instrument)">
              <strong>{{ s.instrument.name }}</strong></button
            ><span class="tag">{{ s.name }}</span>
            <p>{{ s.reason }}</p>
          </div>
          <button
            class="icon-button"
            :aria-label="`查看${s.instrument.name}`"
            @click="openInstrument(s.instrument)"
          >
            <ArrowRight :size="18" />
          </button>
        </div>
        <p v-if="!signalItems.length" class="empty-note">
          当前样本未触发所列条件。
        </p>
      </section>
      <section class="panel classifier">
        <h2>帖子情绪识别工作台</h2>
        <p class="subtle">
          当前引擎：规则辅助分类（非AI）。否定、反讽、引述需要人工复核，不输出伪造置信度。
        </p>
        <form @submit.prevent="classify">
          <label for="post-text">输入待识别帖子</label
          ><textarea
            id="post-text"
            v-model="classifyText"
            required
            maxlength="10000"
            placeholder="例如：最近讨论多了，但还是先观望。"
          ></textarea
          ><button class="primary" :disabled="classifyBusy">
            {{ classifyBusy ? "识别中…" : "识别文本" }}
          </button>
        </form>
        <div v-if="classified" class="classification-result">
          <strong>识别：{{ directionNames[classified.label] }}</strong>
          <p>{{ classified.reason }}</p>
          <p>行为标签：{{ classified.tags.join("、") || "无" }}</p>
        </div>
      </section></template
    >
    <template v-if="view === 'portfolio'"
      ><section v-if="!username" class="panel empty-state">
        <Star :size="32" />
        <h2>把关注的变化留在一起</h2>
        <p>登录后保存自选，同一后端下的网页、安卓与Windows共用账号数据。</p>
        <button class="primary" @click="modal = 'auth'">登录 / 注册</button>
      </section>
      <template v-else
        ><section class="panel">
          <div class="panel-heading">
            <h2>我的自选 · {{ watches.length }}个对象</h2>
            <button class="secondary" @click="navigate('stock')">
              <Plus :size="16" />添加标的
            </button>
          </div>
          <p class="subtle section-note">
            展示近10日各自指标，不把不同覆盖率的对象直接合成一个组合分。
          </p>
          <div class="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>分析对象</th>
                  <th>10日情绪</th>
                  <th>看多 / 看空</th>
                  <th>覆盖率</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="a in watchAnalyses" :key="a.instrument.id">
                  <td>
                    <button
                      class="table-link"
                      @click="openInstrument(a.instrument)"
                    >
                      <strong>{{ a.instrument.name }}</strong
                      ><span>{{ a.instrument.id }}</span>
                    </button>
                  </td>
                  <td>{{ a.metrics.score }}</td>
                  <td>
                    <span class="bull">{{ a.metrics.bullish_pct }}%</span> /
                    <span class="bear">{{ a.metrics.bearish_pct }}%</span>
                  </td>
                  <td>{{ a.metrics.coverage }}%</td>
                  <td>
                    <button
                      class="icon-button"
                      :aria-label="`移除${a.instrument.name}`"
                      @click="removeWatch(a.instrument)"
                    >
                      <Trash2 :size="16" />
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-if="!watches.length" class="empty-note">
            尚无自选。进入个股分析，点击结果右上角的收藏图标。
          </p>
        </section></template
      ></template
    >
    <template v-if="view === 'alerts'"
      ><section v-if="!username" class="panel empty-state">
        <Bell :size="32" />
        <h2>保存你的观察条件</h2>
        <p>登录后创建预警条件。当前支持站内触发状态查询，未接入后台推送。</p>
        <button class="primary" @click="modal = 'auth'">登录 / 注册</button>
      </section>
      <section v-else class="panel">
        <h2>自定义情绪预警 · {{ selected?.name || "请选择对象" }}</h2>
        <p class="subtle section-note">
          选择上方分析对象，再保存条件。仅比较最新单日模拟快照；不发送系统通知。
        </p>
        <form class="inline-form alert-form" @submit.prevent="createAlert">
          <label for="metric">触发指标</label
          ><select id="metric" v-model="alertMetric">
            <option value="bullish_pct">看多占比</option>
            <option value="bearish_pct">看空占比</option>
            <option value="score">综合情绪分</option></select
          ><label for="alert-threshold">≥</label
          ><input
            id="alert-threshold"
            v-model.number="alertThreshold"
            type="number"
            min="0"
            max="100"
            required
          /><button class="primary" :disabled="!selected">保存预警</button>
        </form>
        <div v-for="a in alerts" :key="a.id" class="screen-row">
          <div>
            <strong>{{ a.instrument.name }}</strong>
            <p>
              {{ metricNames[a.metric] }} ≥ {{ a.threshold }} · 当前
              {{ a.value }}
              <span :class="a.triggered ? 'bull' : 'subtle'">{{
                a.triggered ? "已触发（模拟）" : "未触发"
              }}</span>
            </p>
          </div>
          <button
            class="icon-button"
            aria-label="删除预警"
            @click="removeAlert(a.id)"
          >
            <Trash2 :size="17" />
          </button>
        </div>
        <p v-if="!alerts.length" class="empty-note">还没有保存的预警条件。</p>
      </section></template
    >
    </template>
    <footer class="page-footer">
      <span>{{ showMx ? '查询结果按获取时间留存；请核对证券、数据日期与解析条件。' : showLive ? '限量采样与规则统计，仅用于研究；不代表全体散户观点。' : '当前分析为可重复的模拟样本，不代表实时市场。' }}</span
      ><span>统一引擎 · 历史留痕</span>
    </footer>
  </main>
  <div v-if="toast" class="toast" role="status">{{ toast }}</div>
  <div
    v-if="modal"
    class="modal-backdrop"
    @click.self="modal = null"
    @keydown.esc="modal = null"
    @keydown="trapModal"
  >
    <section
      class="modal panel"
      role="dialog"
      aria-modal="true"
      :aria-label="
        modal === 'auth'
          ? '账号登录'
          : modal === 'method'
            ? '数据口径'
            : '情绪复盘'
      "
    >
      <button
        class="modal-close icon-button"
        aria-label="关闭对话框"
        @click="modal = null"
      >
        <X :size="20" /></button
      ><template v-if="modal === 'auth'"
        ><h2>
          {{ authMode === "login" ? "连接你的研究工作台" : "创建统一账号" }}
        </h2>
        <p class="subtle">账号与自选保存在当前连接的后端。股吧采样无需登录；妙想查询、自选和预警需登录。</p>
        <form class="auth-form" @submit.prevent="authenticate">
          <label for="username">用户名</label
          ><input
            id="username"
            v-model="authName"
            autocomplete="username"
            required
            minlength="3"
            maxlength="40"
            placeholder="3–40位中文、字母、数字或下划线"
          /><label for="password">密码</label
          ><input
            id="password"
            v-model="authPassword"
            type="password"
            :autocomplete="
              authMode === 'login' ? 'current-password' : 'new-password'
            "
            required
            minlength="10"
            maxlength="128"
            placeholder="至少10位"
          />
          <p v-if="authError" class="bull" role="alert">{{ authError }}</p>
          <button class="primary" :disabled="authBusy">
            {{
              authBusy
                ? "连接中…"
                : authMode === "login"
                  ? "登录"
                  : "注册并登录"
            }}</button
          ><button
            type="button"
            class="text-button"
            @click="
              authMode = authMode === 'login' ? 'register' : 'login';
              authError = '';
            "
          >
            {{ authMode === "login" ? "没有账号？注册" : "已有账号？登录" }}
          </button>
        </form></template
      ><template v-if="modal === 'method'"
        ><h2>数据口径与接入状态</h2>
        <p>
          当前为研发版本，所有市场值来自固定的20个模拟交易日；不抓取或冒充实时股吧、行情或资金数据。
        </p>
        <ul>
          <li>
            股吧多空35% · 热度20% · 赚钱效应20% · 资金活跃15% · 变化速度10%。
          </li>
          <li>
            无方向帖子不进入多空净比例，仍计入中性分布；无多空样本返回缺失。
          </li>
          <li>热度本身不等于乐观，资金活跃度尚未接入，不识别“主力身份”。</li>
          <li>指数结合多个股吧和全A宽度代理；板块为演示成分组。</li>
          <li>
            历史记录包含发布时间、首次发现时间、内容修订和模型版本。生产回测须按实际可用时间取数。
          </li>
          <li>当前分类是规则辅助；异动页面也为条件筛选，未启用AI模型。</li>
          <li>手机网页支持PWA；APK、EXE需原生工具链构建并连接同一公网API。</li>
        </ul>
        <button class="secondary" @click="modal = null">
          了解口径
        </button></template
      ><template v-if="modal === 'report' && report"
        ><h2>{{ report.title }}</h2>
        <p v-for="p in report.paragraphs" :key="p">{{ p }}</p>
        <p class="subtle">
          这是可重复生成的规则模板摘要。尚未启用自动收盘复盘或通知。
        </p></template
      >
    </section>
  </div>
</template>
