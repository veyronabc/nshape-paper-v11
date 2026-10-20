#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘前股票池：用昨日及以前的数据，生成今日 09:45 观察名单。"""
import argparse, csv, datetime, json, os, statistics
import paper_engine as pe

BASE=os.path.dirname(os.path.abspath(__file__))
STATE=os.path.join(BASE,'paper_account_live.json')

def build(date):
    dates=pe.get_trade_dates(date)
    if date not in dates:
        return {'ok':False,'message':f'{date} 不是交易日'}
    ti=dates.index(date)
    prev=dates[ti-1]
    market=pe.hs300_context(prev,dates)
    events=pe.first_board_events(dates,ti)
    list_dates=pe.stock_list_dates()
    series=pe.load_window_rows({e['code'] for e in events},dates[:ti])
    pool=[]
    for e in events:
        code=e['code']; rows=series.get(code,[])
        if not rows:
            pool.append({**e,'status':'淘汰','reason':'历史数据不足'});continue
        listed=list_dates.get(code)
        if listed:
            try:
                if (datetime.datetime.strptime(date,'%Y%m%d')-datetime.datetime.strptime(str(listed),'%Y%m%d')).days<365:
                    pool.append({**e,'status':'淘汰','reason':'上市不足一年'});continue
            except Exception:pass
        en=pe.enrich_candidate(e,rows,market['market_up'])
        if not en:
            pool.append({**e,'status':'淘汰','reason':'流动性或历史数据不足'});continue
        fi=next((i for i,r in enumerate(rows) if r['date']==e['first_date']),None)
        if fi is None:
            pool.append({**en,'status':'淘汰','reason':'首板数据缺失'});continue
        if e['gap_days']==1:
            pool.append({**en,'status':'等待回调','reason':'首板后还没有回调日'});continue
        pb=rows[fi+1:]
        if not pb:
            pool.append({**en,'status':'等待回调','reason':'还没有回调日'});continue
        if any(r['close']>=en['first_close'] for r in pb):
            pool.append({**en,'status':'淘汰','reason':'回调收盘未低于首板收盘'});continue
        pb_low=min(r['low'] for r in pb)
        sy=sum(1 for r in pb if r['close']>r['open'])
        if pb_low<=en['first_low']:
            pool.append({**en,'status':'淘汰','reason':'跌破首板最低价（单阳破位）'});continue
        if sy>1:
            pool.append({**en,'status':'淘汰','reason':f'回调小阳{sy}根，超过1根'});continue
        okb,branch=pe.branch_ok(en)
        if not okb:
            pool.append({**en,'status':'淘汰','reason':'分支 A/B 前置条件不符'});continue
        prev_close=rows[-1]['close']
        pool.append({**en,'branch':branch,'small_yang':sy,'entry_date':date,'prev_close':prev_close,
                     'threshold':prev_close*1.03,'status':'待触发','reason':'等待09:45前涨超3%'})
    # 排序：待触发优先
    order={'待触发':0,'等待回调':1,'淘汰':2}
    pool.sort(key=lambda x:(order.get(x.get('status'),9),-x.get('prior20',0)))
    return {'ok':True,'date':date,'prev_trade_date':prev,'market':market,'pool':pool}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--date',required=True);args=ap.parse_args()
    r=build(args.date)
    if not r.get('ok'):
        print(json.dumps(r,ensure_ascii=False,indent=2));return
    state=json.load(open(STATE,encoding='utf-8'))
    state['stock_pool']=r['pool'];state['pool_date']=r['date'];state['pool_updated_at']=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S');state['market']=r['market']
    pe.write_json(STATE,state)
    with open(os.path.join(BASE,f'preopen_pool_{args.date}.json'),'w',encoding='utf-8') as f:json.dump(r,f,ensure_ascii=False,indent=2)
    with open(os.path.join(BASE,f'preopen_pool_{args.date}.csv'),'w',newline='',encoding='utf-8') as f:
        fields=['status','branch','code','name','first_date','first_time','gap_days','prior20','vol_ratio_5_20','first_open_times','small_yang','prev_close','threshold','reason']
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(r['pool'])
    print(json.dumps({'ok':True,'date':r['date'],'prev':r['prev_trade_date'],'market':r['market'],
        'counts':{s:sum(1 for x in r['pool'] if x['status']==s) for s in ('待触发','等待回调','淘汰')},
        'watch':[{k:x.get(k) for k in ('status','branch','code','name','first_date','gap_days','prior20','vol_ratio_5_20','first_open_times','small_yang','prev_close','threshold','reason')} for x in r['pool'] if x['status'] in ('待触发','等待回调')]},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
