/** Local-only estimates. Fees, cash, existing exposure and lot sizes all constrain size. */
export type RiskInput={instrumentId:string;equity:number;cash:number;entry:number;stop:number;current:number;riskPct:number;accountRiskPct:number;otherRisk:number;positionCapPct:number;existingQuantity:number;sellableQuantity:number;cost:number;commissionPct:number;minimumCommission:number;sellTaxPct:number;slippagePct:number};
export function lotRule(id:string){
  if(/^XSHG:68\d{4}$/.test(id))return {minimum:200,step:1,label:'科创板：至少200股，递增1股'};
  if(/^XBSE:920\d{3}$/.test(id))return {minimum:100,step:1,label:'北交所：至少100股，递增1股'};
  if(/^(XSHG:6\d{5}|XSHE:(00|30)\d{4})$/.test(id))return {minimum:100,step:100,label:'沪深主板/创业板：100股整数倍'};
  throw new Error('请选择已核验的沪深北A股。');
}
export function riskPosition(p:RiskInput){
  const lot=lotRule(p.instrumentId);
  const values=Object.entries(p).filter(([k])=>k!=='instrumentId');
  if(values.some(([,v])=>typeof v!=='number'||!Number.isFinite(v)||v<0))throw new Error('所有金额和数量必须为有限的非负数。');
  const holdingOnly=p.existingQuantity>0&&p.entry===0;
  if(p.equity<=0||p.stop<=0||(!holdingOnly&&(p.entry<=0||p.entry<=p.stop)))throw new Error('账户权益和止损价须大于0；计划加仓时买入价须高于止损价。');
  if(p.cash>p.equity||p.current<0||p.riskPct>100||p.accountRiskPct>100||p.positionCapPct>100||p.commissionPct>5||p.sellTaxPct>5||p.slippagePct>10||p.minimumCommission>10000)throw new Error('资金、比例或费用超出允许范围。');
  if(!Number.isInteger(p.existingQuantity)||!Number.isInteger(p.sellableQuantity)||p.sellableQuantity>p.existingQuantity)throw new Error('持仓和可卖数量须为整数，可卖数量不得超过持仓。');
  if(p.existingQuantity&&(p.current<=0||p.cost<=0))throw new Error('持仓模式须填写已核验的当前参考价和持仓成本。');
  const slip=p.slippagePct/100,fee=p.commissionPct/100,tax=p.sellTaxPct/100;
  const buy=p.entry*(1+slip),sell=p.stop*(1-slip);
  const commission=(amount:number)=>Math.max(p.minimumCommission,amount*fee);
  const buyCost=(q:number)=>q?buy*q+commission(buy*q):0;
  const stopProceeds=(q:number)=>q?sell*q-commission(sell*q)-sell*q*tax:0;
  const loss=(q:number)=>buyCost(q)-stopProceeds(q);
  const existingValue=p.existingQuantity*p.current;
  const existingRisk=p.existingQuantity?Math.max(0,existingValue-stopProceeds(p.existingQuantity)):0;
  const tradeBudget=Math.max(0,p.equity*p.riskPct/100-existingRisk);
  const accountBudget=Math.max(0,p.equity*p.accountRiskPct/100-p.otherRisk-existingRisk);
  const budget=Math.min(tradeBudget,accountBudget);
  const cap=Math.max(0,p.equity*p.positionCapPct/100-existingValue);
  // A conservative per-calculation ceiling, not a claim about exchange maximum orders.
  const maximum=holdingOnly?0:Math.min(100000,Math.floor(p.cash/buy),Math.floor(cap/buy),Math.floor(budget/(buy-sell)));
  let lo=0,hi=maximum>=lot.minimum?Math.floor((maximum-lot.minimum)/lot.step)+1:0;
  const quantity=(units:number)=>units?lot.minimum+(units-1)*lot.step:0;
  while(lo<hi){const mid=Math.ceil((lo+hi)/2),q=quantity(mid);if(buyCost(q)<=p.cash&&buyCost(q)<=cap&&loss(q)<=budget)lo=mid;else hi=mid-1;}
  const q=quantity(lo);
  const holdingNet=(price:number)=>{const gross=price*p.existingQuantity;return gross-commission(gross)-gross*tax;};
  let breakEven:number|null=null;
  if(p.existingQuantity){let low=0,high=Math.max(p.cost*2+100,p.current*2+100);for(let i=0;i<60;i++){const m=(low+high)/2;if(holdingNet(m)<p.cost*p.existingQuantity)low=m;else high=m;}breakEven=Math.ceil(high*100)/100;}
  return {quantity:q,cashRequired:buyCost(q),plannedLoss:loss(q),riskBudget:budget,existingRisk,accountRisk:existingRisk+p.otherRisk+loss(q),positionPct:(existingValue+buyCost(q))/p.equity*100,lot,
    theoreticalQuantity:holdingOnly?0:Math.floor(p.equity*p.riskPct/100/(p.entry-p.stop)),
    holding:p.existingQuantity?{marketValue:existingValue,unrealized:(p.current-p.cost)*p.existingQuantity,estimatedStopPnl:stopProceeds(p.existingQuantity)-p.cost*p.existingQuantity,breakEven,sellable:p.sellableQuantity,locked:p.existingQuantity-p.sellableQuantity}:null,
    reasons:holdingOnly?['未设加仓价，仅计算已有持仓']:q?[]:[...(budget<=0?['单笔或账户风险预算已用尽']:[]),...(cap<=0?['已达到单股仓位上限']:[]),...(budget>0&&cap>0?['预算、现金或仓位上限不足以覆盖最低交易单位与费用']:[])]};
}
