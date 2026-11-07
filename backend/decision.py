"""Versioned conditional research. Missing context is a veto, never an AI price."""
from datetime import datetime, timedelta, timezone
from math import isfinite
from statistics import mean
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from .database import get_db
from .models import DecisionRecord, Instrument
from .eastmoney import stock, CST
from .live import live_analysis
from .local_collection import enabled_for
from .research import utc

router=APIRouter(prefix='/api/live')
VERSION='decision-rules-v2'
WEIGHTS={'sentiment':25,'change':15,'trend':20,'volume':15,'environment':15,'quality':10}


def technical_levels(quote, now):
    reasons=[]
    if not quote: return None,['缺少已保存日线；请先采集行情']
    observed=datetime.fromisoformat(quote['observed_at'])
    if utc(observed)>now: reasons.append('日线观察时间在未来')
    if now-utc(observed)>timedelta(hours=24): reasons.append('日线观察超过24小时')
    local=now.astimezone(CST)
    if any(b['date']>local.date().isoformat() for b in quote['bars']): reasons.append('日线包含未来日期')
    bars=[b for b in quote['bars'] if b['date']<local.date().isoformat() or (b['date']==local.date().isoformat() and local.hour>=16)]
    if len(bars)<21: return None,[*reasons,'至少需要21根完整日线']
    bars=bars[-30:]
    required=('open','close','high','low','volume_lots')
    if any(any(not isinstance(b.get(k),(int,float)) or not isfinite(b[k]) for k in required) for b in bars):
        return None,[*reasons,'日线价格或成交字段缺失']
    if any(not 0<b['low']<=min(b['open'],b['close'])<=max(b['open'],b['close'])<=b['high'] or b['volume_lots']<0 or (b.get('amount_cny') is not None and (not isinstance(b['amount_cny'],(int,float)) or not isfinite(b['amount_cny']) or b['amount_cny']<0)) for b in bars):
        return None,[*reasons,'日线价格或成交字段异常']
    if any(a['date']>=b['date'] for a,b in zip(bars,bars[1:])): return None,[*reasons,'日线日期重复或顺序异常']
    if (local.date()-datetime.fromisoformat(bars[-1]['date']).date()).days>4: reasons.append('最近日线距今超过4自然日；节假日须另行核对交易日历')
    if any(abs(b['close']/a['close']-1)>.45 for a,b in zip(bars,bars[1:])): reasons.append('未复权价格存在大幅跳变，需核对除权或特殊交易状态')
    latest=bars[-1]
    atr=mean(max(b['high']-b['low'],abs(b['high']-a['close']),abs(b['low']-a['close'])) for a,b in zip(bars[-15:-1],bars[-14:]))
    baseline=mean(b['volume_lots'] for b in bars[-21:-1])
    if not baseline or not atr: reasons.append('成交量或波动幅度不足以建立价位参考')
    support=min(b['low'] for b in bars[-10:]);resistance=max(b['high'] for b in bars[-21:-1])
    entry=round(max(resistance,latest['close'])+.01,2)
    stop=round(support-.5*atr,2)
    risk=entry-stop
    trade_reasons=[]
    if stop<=0 or risk<=0 or risk/entry>.2: trade_reasons.append('结构止损距离无效或超过20%的研究阈值，不生成交易价位')
    amount=mean(b['amount_cny'] for b in bars[-20:]) if all(b.get('amount_cny') is not None for b in bars[-20:]) else None
    if amount is None: trade_reasons.append('行情源未提供历史成交额，流动性尚未核验，不生成交易价位')
    elif amount<20_000_000: trade_reasons.append('近20根日线平均成交额低于2000万元研究门槛，不生成交易价位')
    regime='趋势偏多' if latest['close']>mean(b['close'] for b in bars[-5:])>mean(b['close'] for b in bars[-20:]) else '高于20日均线，等待确认' if latest['close']>mean(b['close'] for b in bars[-20:]) else '趋势偏弱'
    value={'date':latest['date'],'close':latest['close'],'observed_at':quote['observed_at'],
           'ma5':round(mean(b['close'] for b in bars[-5:]),3),'ma20':round(mean(b['close'] for b in bars[-20:]),3),
           'atr14':round(atr,3),'support10':round(support,2),'resistance20':round(resistance,2),
           'volume_ratio':round(latest['volume_lots']/baseline,3) if baseline else None,'average_amount20':round(amount,2) if amount is not None else None,
           'source':quote.get('source','东方财富未复权日线'),'bar_count':len(bars),'trade_reasons':trade_reasons,
           'regime':regime if not reasons else '数据待核验','regime_method':'完整日线收盘/MA5/MA20规则；价格趋势不代表股吧情绪'}
    # Trading prices are withheld on stale, discontinuous, illiquid or malformed data.
    if not reasons and not trade_reasons:
        value['conditional_levels']={'entry':entry,'entry_upper':round(entry+.25*atr,2),'stop':stop,
                                     'target_2r':round(entry+2*risk,2),'target_3r':round(entry+3*risk,2),
                                     'volume_confirmation':1.5,'trailing_stop_distance':round(2*atr,3)}
    return value,reasons


def decision_report(data,previous=None,now=None):
    now=now or datetime.now(timezone.utc)
    quality=data['interpretation']['quality_reasons']
    technical,price_reasons=technical_levels(data.get('quote'),now)
    components=[]
    def add(key,label,score,reason,covered=None):
        components.append({'key':key,'label':label,'weight':WEIGHTS[key],'score':round(score,1) if score is not None else None,
                           'covered_weight':(WEIGHTS[key] if covered is None else covered) if score is not None else 0,'reason':reason})
    add('sentiment','散户情绪',data['opinion_score'] if not quality else None,
        '已识别方向的帖子多空分；不等于上涨概率' if not quality else '；'.join(quality))
    delta=None
    if previous and not previous['interpretation']['quality_reasons'] and not quality:
        delta=round(data['percentages']['bullish']-previous['percentages']['bullish'],2)
    add('change','情绪变化与背离',max(0,min(100,50+delta*3)) if delta is not None else None,
        f'同长度前置自然日窗口，看多占比变化{delta:+}个百分点；背离尚待同步价格验证' if delta is not None else '缺少同长度、当时可获得且质量达标的前置窗口；不补造变化率',7.5)
    trend_score=None
    if technical and not price_reasons:
        trend_score=80 if technical['close']>technical['ma5']>technical['ma20'] else 60 if technical['close']>technical['ma20'] else 25
    add('trend','价格趋势',trend_score,'收盘/MA5/MA20规则：顺序向上80分，仅高于MA20为60分，其他25分；待回测校准' if trend_score is not None else '；'.join(price_reasons))
    volume_score=None
    if technical and not price_reasons and technical['volume_ratio'] is not None: volume_score=min(100,technical['volume_ratio']/1.5*70)
    add('volume','成交量与资金',volume_score,'仅计算最新完整日线/此前20日均量；资金流向未接入，覆盖本维度一半',7.5)
    add('environment','板块及大盘环境',None,'未保存同步的指数/板块环境输入')
    add('quality','风险与样本质量',max(0,min(100,data['authors']/20*40+(data['interpretation']['direction_coverage'] or 0)*.6)) if data['effective_posts'] else None,
        '样本覆盖及作者数的开发评分；交易状态、涨跌停和公司行为尚未核验')
    covered=round(sum(c['covered_weight'] for c in components),1)
    complete=covered==100 and all(c['score'] is not None for c in components)
    score=round(sum(c['score']*c['weight']/100 for c in components),1) if complete else None
    vetoes=[*quality,*price_reasons,*(technical or {}).get('trade_reasons',[]),'尚未核验停牌、当日涨跌停、除权和可成交状态','缺少完整资金、大盘及板块环境，不启用买入或加仓建议']
    if score is None: vetoes.append(f'六维数据仅覆盖{covered}%权重，不合成为完整100分')
    status='等待信号'
    if technical and not price_reasons and technical['close']<technical['ma20']: status='风险预警'
    plan=None
    levels=technical.get('conditional_levels') if technical else None
    if levels and not quality:
        plan={**levels,'status':'条件参考，尚未允许执行','horizon':'未来3～5个交易日，须每日重新核验',
              'entry_condition':'完整交易日收盘站上参考压力；成交量至少达到前20日均量的1.5倍；回踩后仍守住突破位',
              'invalidation':'跌破结构参考位、行情过期或交易状态不明时失效；隔夜跳空可能无法在参考止损价成交',
              'position':'由风险预算计算器另行计算，不输出固定仓位比例'}
    scenarios=[{'name':'乐观情景','condition':f'完整日线收盘突破{technical["resistance20"]:.2f}，量能≥前20日均量1.5倍且情绪样本达标' if technical and not price_reasons else '取得完整价格、成交及情绪数据后，确认放量突破',
                'action':'先核验市场环境与交易状态，再按风险预算评估分批参与；未确认前不执行'},
               {'name':'中性情景','condition':'价格未突破压力，量能不足或情绪方向不明确','action':'继续观察，不用情绪分数替代价格确认'},
               {'name':'悲观情景','condition':f'完整日线跌破{technical["support10"]:.2f}或情绪/样本质量恶化' if technical and not price_reasons else '价格跌破核验后的支撑或数据不可用',
                'action':'放弃新买入条件；持仓按可卖数量与风险预算评估减仓，不生成做空指令'}]
    return {'instrument_id':data['instrument_id'],'code':data['code'],'name':data['name'],'as_of':data['as_of'],'days':data['days'],
            'version':VERSION,'method':'确定性研究规则；AI解释尚未接入；阈值与权重尚未回测校准',
            'score':score,'coverage':covered,'status':status,'risk_level':'数据待核验' if vetoes else '待策略验证',
            'components':components,'vetoes':vetoes,'technical':technical,'price_reasons':price_reasons,'plan':plan,'scenarios':scenarios,
            'data_status':{'sentiment':'样本待补充' if quality else '样本达标','price':'行情待核验' if price_reasons else '技术数据可用'},
            'evidence':data['examples'],'sample':{'effective_posts':data['effective_posts'],'authors':data['authors'],'counts':data['counts'],'observed_at':data['last_post_observation']},
            'limitations':['技术位仅来自未复权日线，不构成实时委托报价。','止损是风险估算，不保证成交；T+1、跳空、停牌和涨跌停会使实际损失更大。','当前未完成真实策略回测，不输出胜率、预测收益或自动下单。']}


class Body(BaseModel):
    model_config={'extra':'forbid'}
    instrument_id:str=Field(max_length=64)
    days:int=Field(default=3,ge=1,le=20)


def build(db,iid,days):
    try: stock(iid)
    except ValueError as exc: raise HTTPException(422,str(exc))
    data=live_analysis(db,days=days,iid=iid)
    previous=live_analysis(db,days=days,iid=iid,as_of=datetime.fromisoformat(data['as_of'])-timedelta(days=days))
    return decision_report(data,previous)


@router.get('/decision')
def read(instrument_id:str=Query('XSHE:300059',max_length=64),days:int=Query(3,ge=1,le=20),db=Depends(get_db)):
    return build(db,instrument_id,days)


@router.post('/decision')
def save(body:Body,request:Request,db=Depends(get_db)):
    if not enabled_for(request): raise HTTPException(403,'保存研究方案仅对本机服务开放。')
    if not db.get(Instrument,body.instrument_id): raise HTTPException(404,'请先搜索并选择已核验证券。')
    result=build(db,body.instrument_id,body.days)
    previous=db.scalar(select(DecisionRecord).where(DecisionRecord.instrument_id==body.instrument_id).order_by(DecisionRecord.id.desc()))
    if previous and utc(previous.created_at)>datetime.now(timezone.utc)-timedelta(minutes=10) and all(previous.result.get(k)==result.get(k) for k in ('days','sample','technical','vetoes','version')):
        return {**previous.result,'record_id':previous.id,'reused':True}
    row=DecisionRecord(instrument_id=body.instrument_id,version=VERSION,result=result)
    db.add(row);db.commit()
    return {**result,'record_id':row.id,'reused':False}


@router.get('/decision/history')
def history(instrument_id:str=Query('XSHE:300059',max_length=64),db=Depends(get_db)):
    return [{'id':r.id,'created_at':utc(r.created_at).isoformat(),'version':r.version,'result':r.result}
            for r in db.scalars(select(DecisionRecord).where(DecisionRecord.instrument_id==instrument_id).order_by(DecisionRecord.id.desc()).limit(20))]
