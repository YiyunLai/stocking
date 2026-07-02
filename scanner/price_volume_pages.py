import json, os, time
from datetime import datetime, timedelta
import requests
try:
    from holdings import HOLDINGS
except Exception:
    HOLDINGS=[]

KEY=os.environ.get('FUGLE_API_KEY','')
BASE='https://api.fugle.tw/marketdata/v1.0/stock'
HEAD={'X-API-KEY':KEY}
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..'))
DOCS=os.path.join(ROOT,'docs')
DATA=os.path.join(DOCS,'data')
STOCKS=os.path.join(DOCS,'stocks')

def num(x):
    try: return float(x) if x not in ('',None) else 0.0
    except Exception: return 0.0

def latest_json():
    fs=sorted([f for f in os.listdir(DATA) if f.endswith('.json')]) if os.path.exists(DATA) else []
    return os.path.join(DATA,fs[-1]) if fs else None

def candles(code):
    if not KEY: return []
    today=datetime.today()
    try:
        r=requests.get(BASE+f'/historical/candles/{code}',headers=HEAD,params={'from':(today-timedelta(days=190)).strftime('%Y-%m-%d'),'to':today.strftime('%Y-%m-%d'),'timeframe':'D','fields':'open,high,low,close,volume','sort':'asc'},timeout=15)
        return r.json().get('data',[]) if r.status_code==200 else []
    except Exception: return []

def ret(cs,days):
    if not cs: return 0
    close=num(cs[-1]['close']); base=num(cs[0]['close']) if len(cs)<=days else num(cs[-days-1]['close'])
    return (close-base)/base*100 if base else 0

def pv(cs):
    if len(cs)<2: return {'kind':'資料不足','txt':'K線資料不足，暫時無法判讀價量。','v':0,'v5':0,'v20':0,'state':'—'}
    close=num(cs[-1]['close']); prev=num(cs[-2]['close']); v=num(cs[-1]['volume'])
    vs5=[num(x['volume']) for x in cs[-6:-1]] if len(cs)>=6 else []
    vs20=[num(x['volume']) for x in cs[-21:-1]] if len(cs)>=21 else []
    v5=sum(vs5)/len(vs5) if vs5 else 0; v20=sum(vs20)/len(vs20) if vs20 else 0
    up=close>=prev; big=v5 and v>=v5
    if up and big: kind='價漲量增'; txt='股價上漲且量能放大，短線動能較積極。'
    elif up: kind='價漲量縮'; txt='股價上漲但量能未同步放大，需觀察續航力。'
    elif big: kind='價跌量增'; txt='股價下跌且量能放大，短線賣壓需要留意。'
    else: kind='價跌量縮'; txt='股價下跌但量能收斂，賣壓暫未明顯擴大。'
    if v5 and v>v5*1.5: state='爆量'
    elif v5 and v>v5: state='放量'
    elif v5 and v<v5*.7: state='量縮'
    else: state='量能普通'
    return {'kind':kind,'txt':txt,'state':state,'v':v,'v5':v5,'v20':v20}

def sr(cs,close):
    if not cs: return None
    a=cs[-20:] if len(cs)>=20 else cs; b=cs[-60:] if len(cs)>=60 else cs
    p20=max(num(x['high']) for x in a); s20=min(num(x['low']) for x in a); p60=max(num(x['high']) for x in b); s60=min(num(x['low']) for x in b)
    if close>=p20*.98: txt='目前接近近20日壓力區，若要續攻需要量能配合。'
    elif close<=s20*1.03: txt='目前接近近20日支撐區，可觀察是否量縮守穩。'
    else: txt='目前位於短線支撐與壓力中間，等待突破或回測較明確。'
    return p20,p60,s20,s60,txt

def svg(cs,W=860,PH=240,VH=80):
    if not cs: return '<div class="empty">沒有足夠走勢資料</div>'
    cs=cs[-120:]; H=PH+VH+25; hi=max(num(x['high']) for x in cs); lo=min(num(x['low']) for x in cs); vr=hi-lo or 1
    vmax=max(num(x['volume']) for x in cs) or 1; cw=W/len(cs); bw=max(cw*.55,1.5)
    def y(v): return PH-((v-lo)/vr)*(PH-18)+8
    def vy(v): return PH+18+VH-(v/vmax)*(VH-8)
    out=[f'<svg width="100%" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" class="chart">','<text x="12" y="20" fill="#9ca3af" font-size="12">近120日價量走勢 黃線=MA20</text>']
    closes=[num(x['close']) for x in cs]
    if len(closes)>=20:
        pts=[]
        for k in range(19,len(closes)):
            ma=sum(closes[k-19:k+1])/20; pts.append(f'{k*cw+cw/2:.1f},{y(ma):.1f}')
        out.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="#facc15" stroke-width="1.8"/>')
    for k,c in enumerate(cs):
        x=k*cw+cw/2; o=num(c['open']); cl=num(c['close']); h=num(c['high']); l=num(c['low']); v=num(c['volume']); col='#ef4444' if cl>=o else '#22c55e'; yo=y(o); yc=y(cl)
        out.append(f'<line x1="{x:.1f}" y1="{y(h):.1f}" x2="{x:.1f}" y2="{y(l):.1f}" stroke="{col}"/>')
        out.append(f'<rect x="{x-bw/2:.1f}" y="{min(yo,yc):.1f}" width="{bw:.1f}" height="{max(abs(yc-yo),1):.1f}" fill="{col}"/>')
        out.append(f'<rect x="{x-bw/2:.1f}" y="{vy(v):.1f}" width="{bw:.1f}" height="{max(PH+18+VH-vy(v),1):.1f}" fill="{col}" opacity=".5"/>')
    out.append('</svg>'); return ''.join(out)

def tech_count(s): return sum(1 for x in ['均線多頭','KD黃金交叉','MACD翻多','布林突破','爆量長紅'] if s.get(x)=='是')
def is_foreign(s): return num(s.get('外資連買天數'))>0 or num(s.get('外資5日累計(張)'))>0 or num(s.get('外資今日(張)'))>0
def is_trust(s): return num(s.get('投信連買天數'))>0 or num(s.get('投信5日累計(張)'))>0 or num(s.get('投信今日(張)'))>0
def is_technical(s): return s.get('類型') in ('強勢噴出','低位啟動') or tech_count(s)>=2

def chip(s):
    f=num(s.get('外資今日(張)')); t=num(s.get('投信今日(張)')); fd=num(s.get('外資連買天數')); td=num(s.get('投信連買天數')); f5=num(s.get('外資5日累計(張)')); t5=num(s.get('投信5日累計(張)'))
    parts=[]
    if f>0: parts.append(f'外資今日買超 {int(f):,} 張，連買 {int(fd)} 天，5日累計 {int(f5):,} 張。')
    elif f<0: parts.append(f'外資今日賣超 {abs(int(f)):,} 張。')
    if t>0: parts.append(f'投信今日買超 {int(t):,} 張，連買 {int(td)} 天，5日累計 {int(t5):,} 張。')
    elif t<0: parts.append(f'投信今日賣超 {abs(int(t)):,} 張。')
    if f>0 and t>0: parts.append('外資與投信同步偏多，籌碼面較強。')
    return ''.join(parts) or '法人籌碼沒有明顯同步偏多。'

def tech(s):
    labels=['均線多頭','MA20翻揚','KD黃金交叉','MACD翻多','布林突破','爆量長紅']
    tags=''.join([f'<span class="tag {"on" if s.get(x)=="是" else ""}">{x}</span>' for x in labels])
    c=tech_count(s); txt='技術面多訊號共振，短線動能較強。' if c>=3 else ('技術面已有轉強跡象。' if c>=1 else '技術面尚未明顯轉強。')
    return tags,txt

def stock_page(s,cs,date):
    code=s.get('代號'); name=s.get('名稱'); close=num(s.get('收盤價')); chg=num(s.get('漲跌%')); pvv=pv(cs); srr=sr(cs,close); tags,tt=tech(s); color='#dc2626' if chg>=0 else '#16a34a'
    sr_html='資料不足' if not srr else f'壓力一(20日高)：{srr[0]:.2f}｜壓力二(60日高)：{srr[1]:.2f}<br>支撐一(20日低)：{srr[2]:.2f}｜支撐二(60日低)：{srr[3]:.2f}<p>{srr[4]}</p>'
    return f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{code} {name}</title><style>body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f5f5;color:#111827;margin:0}}.wrap{{max-width:1050px;margin:auto;padding:26px 16px 60px}}.box{{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px;margin-bottom:16px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}.mini{{background:#f9fafb;border-radius:12px;padding:12px}}.tag{{display:inline-block;margin:3px;padding:5px 9px;border-radius:999px;background:#f3f4f6;color:#9ca3af;font-size:12px}}.tag.on{{background:#111827;color:white}}.chart{{background:#111827;border-radius:14px}}a{{color:#6b7280}}</style></head><body><div class="wrap"><div style="font-size:13px;color:#6b7280;margin-bottom:16px"><a href="../index.html">首頁</a> · <a href="../foreign.html">外資連買</a> · <a href="../trust.html">投信連買</a> · <a href="../technical.html">技術面很強</a></div><div style="display:flex;justify-content:space-between;border-bottom:2px solid #111827;padding-bottom:14px;margin-bottom:20px"><div><h1 style="margin:0">{code} {name}</h1><div style="color:#6b7280;font-size:13px">{date} · {s.get('籌碼類型','')} · {s.get('類型','')}</div></div><div style="text-align:right"><div style="font-size:28px;font-weight:800">{close:.2f}</div><div style="color:{color}">{chg:+.2f}%</div></div></div><section class="box"><h2>一、過去一段時間走勢與價量</h2>{svg(cs)}<div class="grid"><div class="mini">近20日<br><b>{ret(cs,20):+.2f}%</b></div><div class="mini">近60日<br><b>{ret(cs,60):+.2f}%</b></div><div class="mini">近120日<br><b>{ret(cs,120):+.2f}%</b></div><div class="mini">量能狀態<br><b>{pvv.get('state','—')}</b></div></div></section><section class="box"><h2>二、價量判讀</h2><div class="grid"><div class="mini">今日量<br><b>{int(pvv.get('v',0)):,}</b></div><div class="mini">5日均量<br><b>{int(pvv.get('v5',0)):,}</b></div><div class="mini">20日均量<br><b>{int(pvv.get('v20',0)):,}</b></div><div class="mini">價量型態<br><b>{pvv.get('kind')}</b></div></div><p>{pvv.get('txt')}</p></section><section class="box"><h2>三、技術分析</h2>{tags}<p>{tt}</p><p>MA5：{s.get('MA5','—')}｜MA20：{s.get('MA20','—')}｜MA60：{s.get('MA60','—')}</p></section><section class="box"><h2>四、支撐壓力</h2>{sr_html}</section><section class="box"><h2>五、籌碼分析</h2><div class="grid"><div class="mini">外資今日<br><b>{int(num(s.get('外資今日(張)'))):+,}</b></div><div class="mini">外資5日<br><b>{int(num(s.get('外資5日累計(張)'))):+,}</b></div><div class="mini">投信今日<br><b>{int(num(s.get('投信今日(張)'))):+,}</b></div><div class="mini">投信5日<br><b>{int(num(s.get('投信5日累計(張)'))):+,}</b></div></div><p>{chip(s)}</p></section></div></body></html>'''

def card(s):
    return f'<a class="card" href="stocks/{s.get("代號")}.html"><b>{s.get("代號")} {s.get("名稱")}</b><span>{s.get("籌碼類型")} · {s.get("類型")}</span><small>外資連{s.get("外資連買天數",0)}天｜投信連{s.get("投信連買天數",0)}天｜技術訊號{tech_count(s)}</small></a>'

def page(title,groups,date):
    body=''.join([f'<h2>{t} <small>{len(g)}檔</small></h2><p>{d}</p><div class="cards">{"".join(card(x) for x in g)}</div>' for t,d,g in groups if g])
    return f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>body{{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:#f5f5f5;color:#111827}}.wrap{{max-width:1100px;margin:auto;padding:28px 16px}}.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;margin-bottom:28px}}.card{{display:block;background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:14px;text-decoration:none;color:#111827}}.card span,.card small{{display:block;color:#6b7280;margin-top:4px}}a{{color:#6b7280}}</style></head><body><div class="wrap"><h1>{title}</h1><p>{date} 盤後 · 點進個股看價量走勢、技術、支撐壓力與籌碼 · <a href="index.html">回首頁</a></p>{body or "目前沒有符合條件的股票"}</div></body></html>'

def category_groups(stocks):
    foreign=[('⭐ 雙主力連買','外資與投信同步連買。',[s for s in stocks if s.get('籌碼類型')=='雙主力連買']),('🔵 外資連買天數較高','依外資連買天數排序。',sorted([s for s in stocks if is_foreign(s)],key=lambda x:-num(x.get('外資連買天數')))[:40]),('💰 外資5日累計較大','依外資5日累計排序。',sorted([s for s in stocks if num(s.get('外資5日累計(張)'))>0],key=lambda x:-num(x.get('外資5日累計(張)')))[:40])]
    trust=[('⭐ 雙主力連買','外資與投信同步連買。',[s for s in stocks if s.get('籌碼類型')=='雙主力連買']),('🟣 投信連買天數較高','依投信連買天數排序。',sorted([s for s in stocks if is_trust(s)],key=lambda x:-num(x.get('投信連買天數')))[:40]),('💰 投信5日累計較大','依投信5日累計排序。',sorted([s for s in stocks if num(s.get('投信5日累計(張)'))>0],key=lambda x:-num(x.get('投信5日累計(張)')))[:40])]
    techg=[('🔥 強勢噴出','分類為強勢噴出。',[s for s in stocks if s.get('類型')=='強勢噴出']),('📈 技術訊號共振','至少兩個技術訊號成立。',[s for s in stocks if tech_count(s)>=2]),('🟡 低位啟動','初期轉強觀察名單。',[s for s in stocks if s.get('類型')=='低位啟動'])]
    return foreign,trust,techg

def make_pages(stocks,date):
    foreign,trust,techg=category_groups(stocks)
    return {'foreign.html':page('🔵 外資連買',foreign,date),'trust.html':page('🟣 投信連買',trust,date),'technical.html':page('🔥 技術面很強',techg,date)}

def count_unique(group):
    return len({s.get('代號') for _,_,items in group for s in items})

def index(date,stocks):
    foreign,trust,techg=category_groups(stocks); fcnt=count_unique(foreign); tcnt=count_unique(trust); techcnt=count_unique(techg); hcnt=len(HOLDINGS) if HOLDINGS else 0
    return f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>台股籌碼日報</title><style>body{{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:#f5f5f5;color:#111827}}.wrap{{max-width:900px;margin:auto;padding:32px 16px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}}a.card{{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:20px 24px;text-decoration:none;color:#111827;display:block}}.top{{display:flex;align-items:center;justify-content:space-between;gap:12px}}.count{{font-size:28px;font-weight:900;color:#111827}}.count small{{font-size:14px;color:#6b7280;font-weight:600}}.desc{{color:#6b7280;margin-top:8px}}</style></head><body><div class="wrap"><h1>📊 台股籌碼日報</h1><p>每日盤後自動更新 · 分類清單 → 個股分析 · 最新資料 {date}</p><div class="grid"><a class="card" href="foreign.html"><div class="top"><h2>🔵 外資連買</h2><div class="count">{fcnt}<small> 檔</small></div></div><div class="desc">外資連買天數、5 日累計、技術轉強</div></a><a class="card" href="trust.html"><div class="top"><h2>🟣 投信連買</h2><div class="count">{tcnt}<small> 檔</small></div></div><div class="desc">投信連買天數、5 日累計、技術轉強</div></a><a class="card" href="technical.html"><div class="top"><h2>🔥 技術面很強</h2><div class="count">{techcnt}<small> 檔</small></div></div><div class="desc">強勢噴出、技術共振、低位啟動</div></a><a class="card" href="holdings.html"><div class="top"><h2>📋 我的持股追蹤</h2><div class="count">{hcnt}<small> 檔</small></div></div><div class="desc">成本、現價、損益、法人籌碼</div></a></div></div></body></html>'

def main():
    p=latest_json()
    if not p: print('no json'); return
    date=os.path.basename(p).replace('.json','')
    with open(p,encoding='utf-8') as f: stocks=json.load(f)
    os.makedirs(STOCKS,exist_ok=True)
    for s in stocks:
        cs=candles(s.get('代號'))
        with open(os.path.join(STOCKS,f'{s.get("代號")}.html'),'w',encoding='utf-8') as f: f.write(stock_page(s,cs,date))
        if KEY: time.sleep(.12)
    pages=make_pages(stocks,date); pages['index.html']=index(date,stocks)
    for name,html in pages.items():
        with open(os.path.join(DOCS,name),'w',encoding='utf-8') as f: f.write(html)
    print(f'price volume pages done {len(stocks)} {date}')

if __name__=='__main__': main()
