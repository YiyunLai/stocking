import json, os, time
from datetime import datetime, timedelta
import requests

ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..'))
DOCS=os.path.join(ROOT,'docs')
DATA=os.path.join(DOCS,'data')
HEAD={'User-Agent':'Mozilla/5.0'}

def n(v):
    try: return float(v) if v not in ('',None) else 0
    except Exception: return 0

def latest_json():
    fs=sorted([f for f in os.listdir(DATA) if f.endswith('.json')]) if os.path.exists(DATA) else []
    return os.path.join(DATA,fs[-1]) if fs else None

def days(k=8):
    out=[]; d=datetime.today()
    while len(out)<k:
        if d.weekday()<5: out.append(d.strftime('%Y%m%d'))
        d-=timedelta(days=1)
    return out

def to_int(v):
    try: return int(str(v).replace(',','').replace('--','0').strip() or 0)
    except Exception: return 0

def norm(x): return str(x).replace(' ','').replace('　','')

def pick(row,fields,keys):
    for c,v in zip(fields,row):
        if all(k in norm(c) for k in keys): return to_int(v)
    return 0

def lots(row,fields,who):
    val=pick(row,fields,[who,'買賣超'])
    if val: return val//1000
    return (pick(row,fields,[who,'買進'])-pick(row,fields,[who,'賣出']))//1000

def fetch(date):
    url='https://www.twse.com.tw/rwd/zh/fund/T86'
    try:
        r=requests.get(url,headers=HEAD,params={'response':'json','date':date,'selectType':'ALL'},timeout=18)
        data=r.json()
        if data.get('stat')!='OK': return None,None
        return data.get('fields',[]), data.get('data',[])
    except Exception:
        return None,None

def enrich(stocks):
    hist={}
    for d in days(8):
        fields, rows=fetch(d)
        if not rows: continue
        for row in rows:
            code=str(row[0]).strip()
            if len(code)!=4: continue
            f=lots(row,fields,'外陸資'); t=lots(row,fields,'投信'); de=lots(row,fields,'自營商')
            hist.setdefault(code,[]).append({'f':f,'t':t,'a':f+t+de})
        time.sleep(.25)
    for s in stocks:
        hs=hist.get(str(s.get('代號')),[])
        if not hs: continue
        def streak(key):
            c=0
            for x in hs:
                if x[key]>0: c+=1
                else: break
            return c
        s['外資連買天數']=streak('f')
        s['投信連買天數']=streak('t')
        s['三大法人連買天數']=streak('a')
        s['外資今日(張)']=hs[0]['f']; s['投信今日(張)']=hs[0]['t']; s['三大法人今日(張)']=hs[0]['a']
        s['外資5日累計(張)']=sum(x['f'] for x in hs[:5])
        s['投信5日累計(張)']=sum(x['t'] for x in hs[:5])
        s['三大法人5日累計(張)']=sum(x['a'] for x in hs[:5])
    return stocks

def card(s):
    return f'<a class="card" href="stocks/{s.get("代號")}.html"><b>{s.get("代號")} {s.get("名稱")}</b><span>{s.get("籌碼類型","")} · {s.get("類型","")}</span><small>外資連{int(n(s.get("外資連買天數")))}天｜投信連{int(n(s.get("投信連買天數")))}天｜三大連{int(n(s.get("三大法人連買天數")))}天</small></a>'

def page(title,date,groups):
    nav='　'.join([f'<a href="#{gid}">{label}</a>' for gid,label,_,_ in groups])
    body=''.join([f'<section id="{gid}"><h2>{label} <small>{len(items)}檔</small></h2><p>{desc}</p><div class="cards">{"".join(card(x) for x in items)}</div></section>' for gid,label,desc,items in groups])
    return f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>body{{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:#f5f5f5;color:#111827}}.wrap{{max-width:1100px;margin:auto;padding:28px 16px}}.nav{{position:sticky;top:0;background:#f5f5f5;padding:10px 0}}.nav a{{display:inline-block;background:#111827;color:white;text-decoration:none;border-radius:999px;padding:8px 12px;margin:4px}}.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;margin-bottom:28px}}.card{{display:block;background:white;border:1px solid #e5e7eb;border-radius:14px;padding:14px;text-decoration:none;color:#111827}}.card span,.card small{{display:block;color:#6b7280;margin-top:4px}}</style></head><body><div class="wrap"><h1>{title}</h1><p>{date} 盤後 · 可篩選全部、連買3天、連買5天 · <a href="index.html">回首頁</a></p><div class="nav">{nav}</div>{body}</div></body></html>'

def make_groups(stocks,kind):
    if kind=='f': key='外資連買天數'; today='外資今日(張)'; five='外資5日累計(張)'; label='外資'
    elif kind=='t': key='投信連買天數'; today='投信今日(張)'; five='投信5日累計(張)'; label='投信'
    else: key='三大法人連買天數'; today='三大法人今日(張)'; five='三大法人5日累計(張)'; label='三大法人'
    base=[s for s in stocks if n(s.get(key))>0 or n(s.get(today))>0 or n(s.get(five))>0]
    base=sorted(base,key=lambda x:(-n(x.get(key)),-n(x.get(five))))
    return [('all','全部','含今日買超、連買或5日累計偏多的股票。',base),('d3',f'{label}連買 ≥ 3 天','連買天數至少3天。',[s for s in base if n(s.get(key))>=3]),('d5',f'{label}連買 ≥ 5 天','連買天數至少5天。',[s for s in base if n(s.get(key))>=5])]

def add_home_counts(stocks,date):
    f=len({s.get('代號') for _,_,_,g in make_groups(stocks,'f') for s in g})
    t=len({s.get('代號') for _,_,_,g in make_groups(stocks,'t') for s in g})
    a=len({s.get('代號') for _,_,_,g in make_groups(stocks,'a') for s in g})
    return f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>台股籌碼日報</title><style>body{{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:#f5f5f5;color:#111827}}.wrap{{max-width:1000px;margin:auto;padding:32px 16px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}}.card{{background:white;border:1px solid #e5e7eb;border-radius:16px;padding:20px;text-decoration:none;color:#111827}}.top{{display:flex;justify-content:space-between;align-items:center}}.num{{font-size:28px;font-weight:900}}</style></head><body><div class="wrap"><h1>📊 台股籌碼日報</h1><p>每日盤後自動更新 · 可篩選連買3天/5天 · 最新資料 {date}</p><div class="grid"><a class="card" href="foreign.html"><div class="top"><h2>🔵 外資連買</h2><div class="num">{f} 檔</div></div><p>全部 / 連買≥3天 / 連買≥5天</p></a><a class="card" href="trust.html"><div class="top"><h2>🟣 投信連買</h2><div class="num">{t} 檔</div></div><p>全部 / 連買≥3天 / 連買≥5天</p></a><a class="card" href="institutional.html"><div class="top"><h2>🔶 三大法人連買</h2><div class="num">{a} 檔</div></div><p>外資+投信+自營合計</p></a><a class="card" href="technical.html"><div class="top"><h2>🔥 技術面很強</h2><div class="num">—</div></div><p>強勢噴出、技術共振</p></a><a class="card" href="holdings.html"><div class="top"><h2>📋 我的持股追蹤</h2><div class="num">—</div></div><p>成本、現價、損益</p></a></div></div></body></html>'

def main():
    p=latest_json()
    if not p: print('no json'); return
    date=os.path.basename(p).replace('.json','')
    with open(p,encoding='utf-8') as f: stocks=json.load(f)
    stocks=enrich(stocks)
    with open(p,'w',encoding='utf-8') as f: json.dump(stocks,f,ensure_ascii=False)
    pages={'foreign.html':page('🔵 外資連買',date,make_groups(stocks,'f')),'trust.html':page('🟣 投信連買',date,make_groups(stocks,'t')),'institutional.html':page('🔶 三大法人連買',date,make_groups(stocks,'a')),'index.html':add_home_counts(stocks,date)}
    for name,html in pages.items():
        with open(os.path.join(DOCS,name),'w',encoding='utf-8') as f: f.write(html)
    print('streak filter pages done')

if __name__=='__main__': main()
