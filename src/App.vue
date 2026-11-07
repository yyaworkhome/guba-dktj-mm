<script setup lang="ts">
import {ref,watch,onMounted,onUnmounted,nextTick} from 'vue';
import {ChartNoAxesColumnIncreasing,X,LogIn,Sun,Moon} from 'lucide-vue-next';
import {post} from './api';
import LivePanel from './components/LivePanel.vue';
import MarketScanPanel from './components/MarketScanPanel.vue';
import MiaoxiangPanel from './components/MiaoxiangPanel.vue';
import LiveResearchPanel from './components/LiveResearchPanel.vue';
import DecisionCenter from './components/DecisionCenter.vue';

type View='home'|'stock'|'decision'|'rankings'|'index'|'sector'|'backtest'|'holdings';
const nav=[['home','总览'],['stock','个股分析'],['decision','智能决策'],['rankings','多空排名'],['index','大盘行情'],['sector','板块行情'],['backtest','历史数据'],['holdings','持仓管理']] as const;
const view=ref<View>('home');
watch(view,async()=>{await nextTick();window.scrollTo({top:0});});
const light=ref(localStorage.getItem('sentiment-theme')==='light');
watch(light,value=>{document.documentElement.dataset.theme=value?'light':'dark';localStorage.setItem('sentiment-theme',value?'light':'dark');},{immediate:true});
const requested=new URLSearchParams(location.search).get('stock')||sessionStorage.getItem('sentiment-live-instrument')||'XSHE:300059';
const iid=ref(/^(XSHE|XSHG|XBSE):\d{6}$/.test(requested)?requested:'XSHE:300059');
watch(iid,id=>sessionStorage.setItem('sentiment-live-instrument',id));
function openStock(id:string){iid.value=id;view.value='stock';}
const username=ref(sessionStorage.getItem('sentiment-user')||'');
const auth=ref(false),authMode=ref('login'),authName=ref(''),password=ref(''),authBusy=ref(false),authError=ref('');
let returnFocus:HTMLElement|null=null;
watch(auth,async value=>{if(value){returnFocus=document.activeElement as HTMLElement;await nextTick();document.querySelector<HTMLInputElement>('#auth-name')?.focus();}else returnFocus?.focus();});
function trap(event:KeyboardEvent){if(event.key!=='Tab')return;const nodes=[...document.querySelectorAll<HTMLElement>('.account-dialog input,.account-dialog button:not(:disabled)')];if(event.shiftKey&&document.activeElement===nodes[0]){event.preventDefault();nodes.at(-1)?.focus();}else if(!event.shiftKey&&document.activeElement===nodes.at(-1)){event.preventDefault();nodes[0]?.focus();}}
function expire(){username.value='';}
async function authenticate(){authBusy.value=true;authError.value='';try{const result=await post<{token:string;username:string}>(`/auth/${authMode.value}`,{username:authName.value,password:password.value});sessionStorage.setItem('sentiment-token',result.token);sessionStorage.setItem('sentiment-user',result.username);username.value=result.username;password.value='';auth.value=false;}catch(e){authError.value=e instanceof Error?e.message:'登录失败';}finally{authBusy.value=false;}}
async function logout(){try{await post('/auth/logout',{});}finally{sessionStorage.removeItem('sentiment-token');sessionStorage.removeItem('sentiment-user');expire();}}
onMounted(()=>window.addEventListener('session-expired',expire));
onUnmounted(()=>window.removeEventListener('session-expired',expire));
</script>

<template>
  <header class="workspace-header">
    <a class="workspace-brand" href="#" @click.prevent="view='home'" aria-label="多空研究台首页"><ChartNoAxesColumnIncreasing :size="30" :stroke-width="3"/><strong>多空研究台</strong></a>
    <nav aria-label="主导航"><button v-for="[id,label] in nav" :key="id" :class="{active:view===id}" :aria-current="view===id?'page':undefined" @click="view=id">{{label}}</button></nav>
    <div class="workspace-account"><button class="theme-toggle" :aria-label="light?'切换深色主题':'切换浅色主题'" @click="light=!light"><Moon v-if="light" :size="18"/><Sun v-else :size="18"/></button><button v-if="!username" class="account-link" @click="auth=true"><LogIn :size="16"/><span>登录</span></button><button v-else class="account-link" @click="logout">{{username}} · 退出</button></div>
  </header>
  <main class="workspace-main">
    <LivePanel v-if="view==='home'||view==='stock'" :key="view" :initial-instrument="iid" :home="view==='home'" @select="iid=$event">
      <template #scan><MarketScanPanel mode="controls" @select="openStock"/></template>
      <template #rankings><MarketScanPanel mode="rankings" @select="openStock"/></template>
    </LivePanel>
    <MarketScanPanel v-else-if="view==='rankings'" mode="full" @select="openStock"/>
    <DecisionCenter v-else-if="view==='decision'||view==='holdings'" :key="view" :initial-instrument="iid" :holdings="view==='holdings'" @select="iid=$event"/>
    <template v-else-if="view==='index'||view==='sector'">
      <div class="page-heading"><h1>{{view==='index'?'大盘行情':'板块行情'}}</h1><p class="subtle">妙想数据查询 · 与个股股吧情绪分别展示</p></div>
      <MiaoxiangPanel :key="view" :context="view" :username="username" @login="auth=true"/>
    </template>
    <LiveResearchPanel v-else view="backtest" :initial-instrument="iid" @select="openStock"/>
    <footer class="workspace-footer"><span>规则统计 · 来源可追溯</span><span>东方财富公开样本 · 时间统一为北京时间</span></footer>
  </main>
  <div v-if="auth" class="account-overlay" @click.self="auth=false" @keydown.esc="auth=false">
    <section class="account-dialog panel" role="dialog" aria-modal="true" aria-labelledby="auth-title" @keydown="trap">
      <button class="dialog-close" aria-label="关闭登录" @click="auth=false"><X :size="20"/></button>
      <h2 id="auth-title">{{authMode==='login'?'登录研究账号':'创建研究账号'}}</h2>
      <p class="subtle">用于保存妙想行情查询。个股分析与本机扫描无需登录。</p>
      <form class="auth-form" @submit.prevent="authenticate">
        <label for="auth-name">用户名</label><input id="auth-name" v-model="authName" autocomplete="username" required minlength="3" maxlength="40"/>
        <label for="auth-password">密码</label><input id="auth-password" v-model="password" type="password" :autocomplete="authMode==='login'?'current-password':'new-password'" required minlength="10" maxlength="128" placeholder="至少10位"/>
        <p v-if="authError" class="error-banner" role="alert">{{authError}}</p>
        <button class="primary" :disabled="authBusy">{{authBusy?'连接中…':authMode==='login'?'登录':'注册并登录'}}</button>
        <button type="button" class="text-button" @click="authMode=authMode==='login'?'register':'login';authError=''">{{authMode==='login'?'没有账号？注册':'已有账号？登录'}}</button>
      </form>
    </section>
  </div>
</template>
