"""Export the same real observations shown on screen, without synthetic returns."""
import io
from datetime import datetime
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from .database import get_db
from .eastmoney import IID, stock
from .live import live_analysis, snapshot_timeline

router=APIRouter(prefix='/api/live')


@router.get('/export')
def export(instrument_id:str=Query(IID,max_length=64),days:int=Query(3,ge=1,le=30),
           content:Literal['titles','body']='titles',db=Depends(get_db)):
    try: stock(instrument_id)
    except ValueError as exc: raise HTTPException(422,str(exc))
    a=live_analysis(db,days=days,iid=instrument_id,content=content)
    timeline=snapshot_timeline(db,iid=instrument_id,content=content,as_of=datetime.fromisoformat(a['as_of']))
    book=Workbook();sheet=book.active;sheet.title='真实采样摘要'
    for key,value in [('数据模式','live · 真实有限采样，非全量股吧'),('名称',a['name']),('证券ID',instrument_id),
                      ('查询截止',a['as_of']),('内容口径',content),('自然日窗口',days),('日期范围',' 至 '.join(a['range'])),
                      ('最近帖子观察',a['last_post_observation']),('采集标题或正文',a['sampled_posts']),
                      ('有效样本',a['effective_posts']),('有效作者',a['authors']),
                      ('看多',a['counts']['bullish']),('看空',a['counts']['bearish']),('中性/未识别',a['counts']['neutral']),
                      ('舆论分（无方向留空）',a['opinion_score']),('累计评论',a['comments']),('累计阅读',a['reads']),
                      ('分类方法','rules-v1，未接AI；中性包含未识别与冲突'),
                      ('范围说明','只导出当前统计、最近5条来源样本、最近日线及最多200个真实快照；无收益预测')]:
        sheet.append([key,value])
    sheet=book.create_sheet('按发帖日期分组');sheet.append(['日期','有效样本','舆论分（无方向留空）'])
    for p in a['trend']:sheet.append([p['date'],p['posts'],p['opinion_score']])
    sheet=book.create_sheet('最近5条有效样本');sheet.append(['源帖ID','摘录','规则分类','发布时间','首次发现','修订观察','来源URL'])
    for p in a['examples']:sheet.append([p[k] for k in ('source_id','excerpt','label','published_at','first_seen_at','observed_at','url')])
    sheet=book.create_sheet('日线观察');sheet.append(['观察时间','日期','开盘','收盘','复权口径'])
    if a['quote']:
        for p in a['quote']['bars']:sheet.append([a['quote']['observed_at'],p['date'],p['open'],p['close'],'未复权'])
    sheet=book.create_sheet('采集时点快照');sheet.append(['快照ID','可用时间','版本','统计窗口','有效样本','作者','方向样本','舆论分（无方向留空）'])
    for p in timeline['points']:
        sheet.append([p[k] for k in ('id','available_at','version','window_days','effective_posts','authors','directional_posts','opinion_score')])
    for sheet in book:
        sheet.freeze_panes='A2'
        for cells in sheet:
            for cell in cells:
                if isinstance(cell.value,str):
                    # Untrusted post/name strings are literal cells, never formulas.
                    cell.value=''.join(c for c in cell.value if ord(c)>=32 or c in '\t\n\r')[:30000]
                    cell.data_type='s'
    output=io.BytesIO();book.save(output);output.seek(0)
    return StreamingResponse(output,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition':f'attachment; filename="live-{instrument_id.replace(":","-")}-{content}.xlsx"'})
