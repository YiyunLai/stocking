import json
import os
import time
from datetime import datetime, timedelta

import requests

FUGLE_API_KEY = os.environ.get("FUGLE_API_KEY", "")
FUGLE_BASE = "https://api.fugle.tw/marketdata/v1.0/stock"
FUGLE_HEADERS = {"X-API-KEY": FUGLE_API_KEY}

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DOCS = os.path.join(ROOT, "docs")
DATA_DIR = os.path.join(DOCS, "data")
STOCK_DIR = os.path.join(DOCS, "stocks")


def safe_num(v, default=0):
    try:
        if v == "" or v is None:
            return default
        return float(v)
    except Exception:
        return default


def latest_json_file():
    if not os.path.exists(DATA_DIR):
        return None
    files = sorted([f for f in os.listdir(DATA_DIR) if f.endswith(".json")])
    return os.path.join(DATA_DIR, files[-1]) if files else None


def fugle_get(path, params=None):
    if not FUGLE_API_KEY:
        return None
    try:
        r = requests.get(f"{FUGLE_BASE}{path}", headers=FUGLE_HEADERS, params=params or {}, timeout=15)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def fetch_candles(code, days=130):
    today = datetime.today()
    data = fugle_get(f"/historical/candles/{code}", {
        "from": (today - timedelta(days=days)).strftime("%Y-%m-%d"),
        "to": today.strftime("%Y-%m-%d"),
        "timeframe": "D",
        "fields": "open,high,low,close,volume",
        "sort": "asc",
    })
    rows = data.get("data", []) if data else []
    return rows if len(rows) >= 10 else []


def pct(a, b):
    if not b:
        return 0
    return (a - b) / b * 100


def support_resistance(candles, close):
    if not candles:
        return {
            "pressure1": None, "pressure2": None,
            "support1": None, "support2": None,
            "text": "目前沒有足夠 K 線資料，暫不估算支撐壓力。",
        }
    last20 = candles[-20:] if len(candles) >= 20 else candles
    last60 = candles[-60:] if len(candles) >= 60 else candles
    pressure1 = max([c["high"] for c in last20])
    pressure2 = max([c["high"] for c in last60])
    support1 = min([c["low"] for c in last20])
    support2 = min([c["low"] for c in last60])
    text = []
    if close >= pressure1 * 0.98:
        text.append("股價已接近短線壓力區，追價需要留意震盪。")
    elif close <= support1 * 1.03:
        text.append("股價接近短線支撐區，若量縮守穩可觀察是否止跌。")
    else:
        text.append("股價位於短線支撐與壓力之間，仍需觀察量價與法人是否延續。")
    return {
        "pressure1": pressure1, "pressure2": pressure2,
        "support1": support1, "support2": support2,
        "text": "".join(text),
    }


def svg_candles(candles, width=760, height=260):
    if not candles:
        return '<div style="padding:40px;text-align:center;color:#888;background:#fff;border-radius:14px">無 K 線資料</div>'
    candles = candles[-60:]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    vmax, vmin = max(highs), min(lows)
    vr = vmax - vmin if vmax != vmin else 1
    cw = width / len(candles)
    bw = max(cw * 0.58, 2)

    def y(v):
        return height - ((v - vmin) / vr) * (height - 18) - 9

    parts = [f'<svg width="100%" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" style="background:#111827;border-radius:14px">']
    for i, c in enumerate(candles):
        x = i * cw + cw / 2
        color = "#ef4444" if c["close"] >= c["open"] else "#22c55e"
        yo, yc = y(c["open"]), y(c["close"])
        yh, yl = y(c["high"]), y(c["low"])
        parts.append(f'<line x1="{x:.1f}" y1="{yh:.1f}" x2="{x:.1f}" y2="{yl:.1f}" stroke="{color}" stroke-width="1"/>')
        parts.append(f'<rect x="{x-bw/2:.1f}" y="{min(yo,yc):.1f}" width="{bw:.1f}" height="{max(abs(yc-yo),1):.1f}" fill="{color}"/>')
    parts.append('</svg>')
    return "".join(parts)


def signal_tags(s):
    labels = ["均線多頭", "MA20翻揚", "KD黃金交叉", "MACD翻多", "布林突破", "爆量長紅"]
    html = ""
    for label in labels:
        active = s.get(label) == "是"
        bg = "#111827" if active else "#f3f4f6"
        fg = "#fff" if active else "#9ca3af"
        html += f'<span style="display:inline-block;margin:3px;padding:4px 8px;border-radius:999px;background:{bg};color:{fg};font-size:12px">{label}</span>'
    return html


def chip_comment(s):
    f_today = safe_num(s.get("外資今日(張)"))
    t_today = safe_num(s.get("投信今日(張)"))
    f_days = safe_num(s.get("外資連買天數"))
    t_days = safe_num(s.get("投信連買天數"))
    f5 = safe_num(s.get("外資5日累計(張)"))
    t5 = safe_num(s.get("投信5日累計(張)"))

    parts = []
    if f_today > 0 and f_days >= 2:
        parts.append(f"外資已連買 {int(f_days)} 天，今日仍買超 {int(f_today):,} 張，短線外資資金持續流入。")
    elif f_today > 0:
        parts.append(f"外資今日買超 {int(f_today):,} 張，但連續性仍需要再觀察。")
    elif f_today < 0:
        parts.append(f"外資今日賣超 {int(abs(f_today)):,} 張，外資籌碼短線轉弱。")

    if t_today > 0 and t_days >= 2:
        parts.append(f"投信連買 {int(t_days)} 天，5 日累計 {int(t5):,} 張，代表本土法人買盤有延續性。")
    elif t_today > 0:
        parts.append(f"投信今日買超 {int(t_today):,} 張，屬於初步偏多訊號。")
    elif t_today < 0:
        parts.append(f"投信今日賣超 {int(abs(t_today)):,} 張，需留意本土法人是否轉為調節。")

    if f_today > 0 and t_today > 0:
        parts.append("今日外資與投信同步買超，屬於籌碼面較強的訊號。")
    if not parts:
        parts.append("目前法人籌碼訊號不明顯，建議搭配價量與支撐壓力觀察。")
    return "".join(parts)


def tech_comment(s):
    count = len([x for x in ["均線多頭", "KD黃金交叉", "MACD翻多", "布林突破", "爆量長紅"] if s.get(x) == "是"])
    if count >= 3:
        return "技術面同時出現多個偏多訊號，短線動能強，但若已急漲也要留意拉回。"
    if count == 2:
        return "技術面已有兩個訊號轉強，屬於值得觀察的轉強型態。"
    if count == 1:
        return "技術面只有單一訊號成立，還不算全面轉強。"
    return "技術面尚未出現明確共振，籌碼若偏多也仍需等待價格確認。"


def money(v):
    if v is None:
        return "—"
    return f"{v:.2f}"


def stock_detail_page(s, date_str, candles):
    close = safe_num(s.get("收盤價"))
    sr = support_resistance(candles, close)
    code, name = s.get("代號"), s.get("名稱")
    y, m, d = date_str[:4], date_str[4:6], date_str[6:]
    chg = safe_num(s.get("漲跌%"))
    chg_color = "#dc2626" if chg >= 0 else "#16a34a"
    sign = "+" if chg >= 0 else ""

    return f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{code} {name} 個股分析</title></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f5f5;color:#111827;margin:0;padding:0">
<div style="max-width:980px;margin:0 auto;padding:26px 16px 60px">
  <div style="margin-bottom:18px;color:#6b7280;font-size:13px"><a href="../index.html" style="color:#6b7280">首頁</a> · <a href="../foreign.html" style="color:#6b7280">外資連買</a> · <a href="../trust.html" style="color:#6b7280">投信連買</a> · <a href="../technical.html" style="color:#6b7280">技術面很強</a></div>
  <div style="display:flex;justify-content:space-between;align-items:flex-end;border-bottom:2px solid #111827;padding-bottom:14px;margin-bottom:20px">
    <div><div style="font-size:28px;font-weight:800">{code} {name}</div><div style="font-size:13px;color:#6b7280;margin-top:4px">{y}/{m}/{d} 盤後 · {s.get('籌碼類型','')} · {s.get('類型','')}</div></div>
    <div style="text-align:right"><div style="font-size:26px;font-weight:800">{close:.2f}</div><div style="font-size:14px;color:{chg_color}">{sign}{chg:.2f}%</div></div>
  </div>

  <section style="background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px;margin-bottom:18px">
    <h2 style="font-size:18px;margin:0 0 12px">一、現行圖</h2>
    {svg_candles(candles)}
  </section>

  <section style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;margin-bottom:18px">
    <div style="background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px">
      <h2 style="font-size:18px;margin:0 0 12px">二、技術分析</h2>
      <div style="margin-bottom:10px">{signal_tags(s)}</div>
      <p style="font-size:14px;line-height:1.7;color:#374151">{tech_comment(s)}</p>
      <table style="width:100%;border-collapse:collapse;font-size:13px"><tbody>
        <tr><td style="padding:6px;color:#6b7280">MA5</td><td style="padding:6px;text-align:right">{s.get('MA5','—')}</td></tr>
        <tr><td style="padding:6px;color:#6b7280">MA20</td><td style="padding:6px;text-align:right">{s.get('MA20','—')}</td></tr>
        <tr><td style="padding:6px;color:#6b7280">MA60</td><td style="padding:6px;text-align:right">{s.get('MA60','—')}</td></tr>
      </tbody></table>
    </div>

    <div style="background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px">
      <h2 style="font-size:18px;margin:0 0 12px">三、支撐壓力</h2>
      <table style="width:100%;border-collapse:collapse;font-size:13px"><tbody>
        <tr><td style="padding:7px;color:#6b7280">壓力一：近 20 日高點</td><td style="padding:7px;text-align:right;font-weight:700">{money(sr['pressure1'])}</td></tr>
        <tr><td style="padding:7px;color:#6b7280">壓力二：近 60 日高點</td><td style="padding:7px;text-align:right;font-weight:700">{money(sr['pressure2'])}</td></tr>
        <tr><td style="padding:7px;color:#6b7280">支撐一：近 20 日低點</td><td style="padding:7px;text-align:right;font-weight:700">{money(sr['support1'])}</td></tr>
        <tr><td style="padding:7px;color:#6b7280">支撐二：近 60 日低點</td><td style="padding:7px;text-align:right;font-weight:700">{money(sr['support2'])}</td></tr>
      </tbody></table>
      <p style="font-size:14px;line-height:1.7;color:#374151">{sr['text']}</p>
    </div>
  </section>

  <section style="background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px;margin-bottom:18px">
    <h2 style="font-size:18px;margin:0 0 12px">四、籌碼分析</h2>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:12px">
      <div style="background:#f9fafb;border-radius:12px;padding:12px"><div style="font-size:12px;color:#6b7280">外資今日</div><div style="font-size:20px;font-weight:800">{int(safe_num(s.get('外資今日(張)'))):+,}</div></div>
      <div style="background:#f9fafb;border-radius:12px;padding:12px"><div style="font-size:12px;color:#6b7280">外資連買</div><div style="font-size:20px;font-weight:800">{int(safe_num(s.get('外資連買天數')))} 天</div></div>
      <div style="background:#f9fafb;border-radius:12px;padding:12px"><div style="font-size:12px;color:#6b7280">外資 5 日</div><div style="font-size:20px;font-weight:800">{int(safe_num(s.get('外資5日累計(張)'))):+,}</div></div>
      <div style="background:#f9fafb;border-radius:12px;padding:12px"><div style="font-size:12px;color:#6b7280">投信今日</div><div style="font-size:20px;font-weight:800">{int(safe_num(s.get('投信今日(張)'))):+,}</div></div>
      <div style="background:#f9fafb;border-radius:12px;padding:12px"><div style="font-size:12px;color:#6b7280">投信連買</div><div style="font-size:20px;font-weight:800">{int(safe_num(s.get('投信連買天數')))} 天</div></div>
      <div style="background:#f9fafb;border-radius:12px;padding:12px"><div style="font-size:12px;color:#6b7280">投信 5 日</div><div style="font-size:20px;font-weight:800">{int(safe_num(s.get('投信5日累計(張)'))):+,}</div></div>
    </div>
    <p style="font-size:15px;line-height:1.8;color:#374151">{chip_comment(s)}</p>
  </section>

  <div style="font-size:12px;color:#9ca3af;border-top:1px solid #e5e7eb;padding-top:14px">僅供研究與追蹤，不構成投資建議。</div>
</div></body></html>"""


def card(s):
    code = s.get("代號")
    chg = safe_num(s.get("漲跌%"))
    color = "#dc2626" if chg >= 0 else "#16a34a"
    sign = "+" if chg >= 0 else ""
    return f"""<a href="stocks/{code}.html" style="display:block;background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:14px;text-decoration:none;color:#111827">
      <div style="display:flex;justify-content:space-between;gap:8px">
        <div><div style="font-size:16px;font-weight:800">{code} {s.get('名稱')}</div><div style="font-size:12px;color:#6b7280;margin-top:3px">{s.get('籌碼類型')} · {s.get('類型')}</div></div>
        <div style="text-align:right"><div style="font-weight:800">{safe_num(s.get('收盤價')):.2f}</div><div style="font-size:12px;color:{color}">{sign}{chg:.2f}%</div></div>
      </div>
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-top:12px;font-size:11px;text-align:center;background:#f9fafb;border-radius:10px;padding:8px 0">
        <div><div style="color:#9ca3af">外連</div><b>{int(safe_num(s.get('外資連買天數')))}天</b></div>
        <div><div style="color:#9ca3af">外5日</div><b>{int(safe_num(s.get('外資5日累計(張)'))):+,}</b></div>
        <div><div style="color:#9ca3af">投連</div><b>{int(safe_num(s.get('投信連買天數')))}天</b></div>
        <div><div style="color:#9ca3af">訊號</div><b>{len([x for x in ['均線多頭','KD黃金交叉','MACD翻多','布林突破','爆量長紅'] if s.get(x)=='是'])}</b></div>
      </div>
    </a>"""


def section(title, desc, group):
    if not group:
        return ""
    cards = "".join(card(s) for s in group)
    return f"""<section style="margin-bottom:28px"><div style="font-size:20px;font-weight:800;margin-bottom:4px">{title} <span style="font-size:13px;color:#6b7280;font-weight:400">{len(group)} 檔</span></div><div style="font-size:13px;color:#6b7280;margin-bottom:12px">{desc}</div><div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px">{cards}</div></section>"""


def category_page(title, date_str, mode, stocks):
    y, m, d = date_str[:4], date_str[4:6], date_str[6:]
    if mode == "foreign":
        groups = [
            ("⭐ 雙主力連買", "外資與投信同步連買，籌碼共振最明顯。", [s for s in stocks if s.get("籌碼類型") == "雙主力連買"]),
            ("🔵 外資連買天數較高", "依外資連買天數排序，觀察外資是否持續累積。", sorted([s for s in stocks if safe_num(s.get("外資連買天數")) > 0], key=lambda x: -safe_num(x.get("外資連買天數")))[:40]),
            ("💰 外資 5 日累計買超較大", "依外資 5 日累計買超排序。", sorted([s for s in stocks if safe_num(s.get("外資5日累計(張)")) > 0], key=lambda x: -safe_num(x.get("外資5日累計(張)")))[:40]),
            ("🔥 外資買超且技術轉強", "外資偏多，同時至少兩個技術訊號成立。", [s for s in stocks if safe_num(s.get("外資今日(張)")) > 0 and len([x for x in ["均線多頭","KD黃金交叉","MACD翻多","布林突破","爆量長紅"] if s.get(x)=="是"]) >= 2]),
        ]
    elif mode == "trust":
        groups = [
            ("⭐ 雙主力連買", "外資與投信同步連買，籌碼共振最明顯。", [s for s in stocks if s.get("籌碼類型") == "雙主力連買"]),
            ("🟣 投信連買天數較高", "依投信連買天數排序，觀察投信是否持續加碼。", sorted([s for s in stocks if safe_num(s.get("投信連買天數")) > 0], key=lambda x: -safe_num(x.get("投信連買天數")))[:40]),
            ("💰 投信 5 日累計買超較大", "依投信 5 日累計買超排序。", sorted([s for s in stocks if safe_num(s.get("投信5日累計(張)")) > 0], key=lambda x: -safe_num(x.get("投信5日累計(張)")))[:40]),
            ("🔥 投信買超且技術轉強", "投信偏多，同時至少兩個技術訊號成立。", [s for s in stocks if safe_num(s.get("投信今日(張)")) > 0 and len([x for x in ["均線多頭","KD黃金交叉","MACD翻多","布林突破","爆量長紅"] if s.get(x)=="是"]) >= 2]),
        ]
    else:
        groups = [
            ("🔥 強勢噴出", "分類為強勢噴出，表示價量或法人動能較強。", [s for s in stocks if s.get("類型") == "強勢噴出"]),
            ("📈 技術訊號共振", "至少兩個技術訊號成立。", [s for s in stocks if len([x for x in ["均線多頭","KD黃金交叉","MACD翻多","布林突破","爆量長紅"] if s.get(x)=="是"]) >= 2]),
            ("🟡 低位啟動", "分類為低位啟動，偏向初期轉強觀察名單。", [s for s in stocks if s.get("類型") == "低位啟動"]),
        ]
    body = "".join(section(a, b, c) for a, b, c in groups)
    if not body:
        body = '<div style="background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:20px;color:#6b7280">目前沒有符合條件的股票。</div>'
    return f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f5f5;color:#111827;margin:0;padding:0"><div style="max-width:1100px;margin:0 auto;padding:28px 16px 60px">
<div style="border-bottom:2px solid #111827;padding-bottom:14px;margin-bottom:24px"><div style="font-size:26px;font-weight:900">{title}</div><div style="font-size:13px;color:#6b7280;margin-top:4px">{y}/{m}/{d} 盤後 · 點選個股可進入分析頁 · <a href="index.html" style="color:#6b7280">← 回首頁</a></div></div>{body}<div style="border-top:1px solid #e5e7eb;padding-top:14px;font-size:12px;color:#9ca3af">僅供研究與追蹤，不構成投資建議。</div></div></body></html>"""


def index_page(date_str):
    return f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>台股籌碼日報</title></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f5f5;color:#111827;margin:0;padding:0"><div style="max-width:760px;margin:0 auto;padding:32px 16px 60px"><div style="font-size:26px;font-weight:900;margin-bottom:6px">📊 台股籌碼日報</div><div style="font-size:13px;color:#6b7280;margin-bottom:24px">每日盤後自動更新 · 分類清單 → 個股分析 · 最新資料 {date_str}</div><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px;margin-bottom:28px">
<a href="foreign.html" style="background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px;text-decoration:none;color:#111827"><div style="font-size:24px;margin-bottom:8px">🔵</div><div style="font-size:18px;font-weight:900">外資連買</div><div style="font-size:12px;color:#6b7280;margin-top:4px">外資連買天數、5 日累計、技術轉強</div></a>
<a href="trust.html" style="background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px;text-decoration:none;color:#111827"><div style="font-size:24px;margin-bottom:8px">🟣</div><div style="font-size:18px;font-weight:900">投信連買</div><div style="font-size:12px;color:#6b7280;margin-top:4px">投信連買天數、5 日累計、技術轉強</div></a>
<a href="technical.html" style="background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px;text-decoration:none;color:#111827"><div style="font-size:24px;margin-bottom:8px">🔥</div><div style="font-size:18px;font-weight:900">技術面很強</div><div style="font-size:12px;color:#6b7280;margin-top:4px">強勢噴出、技術共振、低位啟動</div></a>
<a href="holdings.html" style="background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px;text-decoration:none;color:#111827"><div style="font-size:24px;margin-bottom:8px">📋</div><div style="font-size:18px;font-weight:900">我的持股追蹤</div><div style="font-size:12px;color:#6b7280;margin-top:4px">成本、現價、損益、法人籌碼</div></a>
</div><div style="background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:16px;font-size:13px;color:#6b7280">操作方式：先進入分類頁，查看各種分類與股票；再點個股，進入現行圖、技術分析、支撐壓力與籌碼分析。</div></div></body></html>"""


def main():
    path = latest_json_file()
    if not path:
        print("no data json")
        return
    date_str = os.path.basename(path).replace(".json", "")
    with open(path, "r", encoding="utf-8") as f:
        stocks = json.load(f)
    os.makedirs(STOCK_DIR, exist_ok=True)
    for i, s in enumerate(stocks):
        candles = fetch_candles(s.get("代號"))
        with open(os.path.join(STOCK_DIR, f"{s.get('代號')}.html"), "w", encoding="utf-8") as f:
            f.write(stock_detail_page(s, date_str, candles))
        if FUGLE_API_KEY:
            time.sleep(0.15)
    pages = {
        "foreign.html": category_page("🔵 外資連買", date_str, "foreign", stocks),
        "trust.html": category_page("🟣 投信連買", date_str, "trust", stocks),
        "technical.html": category_page("🔥 技術面很強", date_str, "technical", stocks),
        "index.html": index_page(date_str),
    }
    for name, html in pages.items():
        with open(os.path.join(DOCS, name), "w", encoding="utf-8") as f:
            f.write(html)
    print(f"postprocess done: {len(stocks)} stocks, date={date_str}")


if __name__ == "__main__":
    main()
