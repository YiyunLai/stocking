import csv
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(__file__))
from holdings import HOLDINGS

HEADERS = {"User-Agent": "Mozilla/5.0"}
FUGLE_API_KEY = os.environ.get("FUGLE_API_KEY", "")
FUGLE_BASE = "https://api.fugle.tw/marketdata/v1.0/stock"
FUGLE_HEADERS = {"X-API-KEY": FUGLE_API_KEY}


def parse_int(v):
    try:
        return int(str(v).replace(",", "").replace("--", "0").strip() or 0)
    except Exception:
        return 0


def parse_float(v):
    try:
        return float(str(v).replace(",", "").replace("--", "0").strip() or 0)
    except Exception:
        return 0.0


def norm(col):
    return str(col).replace(" ", "").replace("　", "").strip()


def pick(row, keywords):
    for col in row.index:
        if all(k in norm(col) for k in keywords):
            return parse_int(row.get(col, 0))
    return 0


def net_lots(row, buy_kw, sell_kw, net_kw):
    net = pick(row, net_kw)
    if net:
        return net // 1000
    return (pick(row, buy_kw) - pick(row, sell_kw)) // 1000


def calc_institutional_row(row):
    foreign = net_lots(row, ["外陸資", "買進"], ["外陸資", "賣出"], ["外陸資", "買賣超"])
    trust = net_lots(row, ["投信", "買進"], ["投信", "賣出"], ["投信", "買賣超"])
    dealer = net_lots(row, ["自營商", "買進"], ["自營商", "賣出"], ["自營商", "買賣超"])
    return foreign, trust, dealer


def get_trading_days(n=10):
    out = []
    d = datetime.today()
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return out


def fetch_institutional(date_str):
    url = f"https://www.twse.com.tw/rwd/zh/fund/T86?response=json&date={date_str}&selectType=ALL"
    try:
        data = requests.get(url, headers=HEADERS, timeout=20).json()
        if data.get("stat") != "OK" or not data.get("data"):
            print(f"  ⚠️ {date_str} 三大法人無資料：{data.get('stat')}")
            return None, None
        return pd.DataFrame(data["data"], columns=data["fields"]), data.get("date", date_str)
    except Exception as e:
        print(f"  ⚠️ {date_str} 三大法人例外：{e}")
        return None, None


def fetch_price(date_str):
    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&date={date_str}&type=ALL"
    try:
        data = requests.get(url, headers=HEADERS, timeout=20).json()
    except Exception as e:
        print(f"  ⚠️ 股價例外：{e}")
        return {}

    if data.get("stat") != "OK":
        print(f"  ⚠️ 股價 stat 異常：{data.get('stat')}")
        return {}

    price_map = {}
    for table in data.get("tables", []):
        fields = table.get("fields", []) if isinstance(table, dict) else []
        rows = table.get("data", []) if isinstance(table, dict) else []
        needed = ["證券代號", "證券名稱", "收盤價", "漲跌價差"]
        if not all(x in fields for x in needed):
            continue
        ci, ni, pi, di = [fields.index(x) for x in needed]
        for row in rows:
            code = str(row[ci]).strip()
            if len(code) != 4:
                continue
            price = parse_float(row[pi])
            chg = parse_float(row[di])
            if price <= 0:
                continue
            price_map[code] = {
                "name": str(row[ni]).strip(),
                "price": price,
                "chg": chg,
                "chg_pct": chg / (price - chg) * 100 if price != chg else 0,
            }
    print(f"✅ 股價取得 {len(price_map)} 檔")
    return price_map


def fugle_get(path, params=None):
    if not FUGLE_API_KEY:
        return None
    try:
        r = requests.get(f"{FUGLE_BASE}{path}", headers=FUGLE_HEADERS, params=params or {}, timeout=15)
        return r.json() if r.status_code == 200 else None
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
    return rows if len(rows) >= 25 else None


def sma(values, n):
    return sum(values[-n:]) / n if len(values) >= n else None


def ema(values, n):
    if len(values) < n:
        return []
    k = 2 / (n + 1)
    out = [sum(values[:n]) / n]
    for v in values[n:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def analyze_technical(code, chg_pct):
    candles = fetch_candles(code)
    if not candles:
        return None

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    vols = [c["volume"] for c in candles]

    ma5, ma20, ma60 = sma(closes, 5), sma(closes, 20), sma(closes, 60)
    out = {
        "MA5": round(ma5, 2) if ma5 else "",
        "MA20": round(ma20, 2) if ma20 else "",
        "MA60": round(ma60, 2) if ma60 else "",
        "均線多頭": "是" if ma5 and ma20 and ma60 and ma5 > ma20 > ma60 else "否",
        "MA20翻揚": "否",
        "KD黃金交叉": "否",
        "MACD翻多": "否",
        "布林突破": "否",
        "爆量長紅": "否",
        "_candles": [{"date": c["date"], "o": c["open"], "h": c["high"], "l": c["low"], "c": c["close"]} for c in candles[-60:]],
    }

    if len(closes) >= 24:
        out["MA20翻揚"] = "是" if sum(closes[-20:]) / 20 > sum(closes[-24:-4]) / 20 else "否"

    if len(closes) >= 20:
        win = closes[-20:]
        mean = sum(win) / 20
        std = (sum((x - mean) ** 2 for x in win) / 20) ** 0.5
        out["布林突破"] = "是" if closes[-1] > mean + 2 * std else "否"

    if len(closes) >= 35:
        e12, e26 = ema(closes, 12), ema(closes, 26)
        macd = [a - b for a, b in zip(e12[len(e12)-len(e26):], e26)]
        sig = ema(macd, 9)
        if len(sig) >= 2:
            hist = [m - s for m, s in zip(macd[-len(sig):], sig)]
            out["MACD翻多"] = "是" if hist[-2] <= 0 < hist[-1] else "否"

    if len(candles) >= 12:
        k_prev = d_prev = 50.0
        kd = []
        for i in range(len(candles)):
            if i < 8:
                continue
            hh, ll = max(highs[i-8:i+1]), min(lows[i-8:i+1])
            rsv = 50 if hh == ll else (closes[i] - ll) / (hh - ll) * 100
            k = (2/3) * k_prev + (1/3) * rsv
            d = (2/3) * d_prev + (1/3) * k
            kd.append((k, d))
            k_prev, d_prev = k, d
        if len(kd) >= 2:
            out["KD黃金交叉"] = "是" if kd[-2][0] <= kd[-2][1] and kd[-1][0] > kd[-1][1] else "否"

    if len(vols) >= 6:
        avg5 = sum(vols[-6:-1]) / 5
        out["爆量長紅"] = "是" if avg5 > 0 and vols[-1] > avg5 * 1.5 and chg_pct >= 3 else "否"
    return out


def build_full_data(n_days=6):
    daily_inst = {}
    cutoff = (datetime.today() - timedelta(days=30)).strftime("%Y%m%d")

    for d in get_trading_days(n_days):
        df, actual = fetch_institutional(d)
        if df is not None and actual >= cutoff:
            daily_inst[actual] = df
            print(f"✅ {actual} 三大法人 OK（{len(df)} 筆）")
        time.sleep(1.2)

    if not daily_inst:
        return None, None, {}

    date_used = sorted(daily_inst.keys())[-1]
    sorted_days = sorted(daily_inst.keys(), reverse=True)
    price_map = fetch_price(date_used)

    inst_history = {}
    for day, df in daily_inst.items():
        print(f"ℹ️ {day} 欄位：{list(df.columns)}")
        for _, row in df.iterrows():
            code = str(row.get("證券代號", "")).strip()
            if len(code) != 4:
                continue
            f, t, d = calc_institutional_row(row)
            inst_history.setdefault(code, {})[day] = {"f": f, "t": t, "d": d}

    stocks = []
    for _, row in daily_inst[date_used].iterrows():
        code = str(row.get("證券代號", "")).strip()
        if len(code) != 4 or code not in price_map:
            continue

        hist = inst_history.get(code, {})
        today = hist.get(date_used, {"f": 0, "t": 0, "d": 0})
        f_consec = 0
        for day in sorted_days:
            if hist.get(day, {}).get("f", 0) > 0:
                f_consec += 1
            else:
                break

        t_consec = 0
        for day in sorted_days:
            if hist.get(day, {}).get("t", 0) > 0:
                t_consec += 1
            else:
                break

        if f_consec == 0 and t_consec == 0:
            continue

        p = price_map[code]
        chip_type = "雙主力連買" if f_consec and t_consec else ("外資連買" if f_consec else "投信連買")
        five = sorted_days[:5]
        stocks.append({
            "代號": code,
            "名稱": p["name"],
            "類型": "",
            "籌碼類型": chip_type,
            "收盤價": p["price"],
            "漲跌%": round(p["chg_pct"], 2),
            "外資今日(張)": today["f"],
            "投信今日(張)": today["t"],
            "自營今日(張)": today["d"],
            "三大法人合計(張)": today["f"] + today["t"] + today["d"],
            "外資連買天數": f_consec,
            "投信連買天數": t_consec,
            "外資5日累計(張)": sum(hist.get(x, {}).get("f", 0) for x in five),
            "投信5日累計(張)": sum(hist.get(x, {}).get("t", 0) for x in five),
            "自營5日累計(張)": sum(hist.get(x, {}).get("d", 0) for x in five),
            "MA5": "", "MA20": "", "MA60": "",
            "均線多頭": "", "MA20翻揚": "", "KD黃金交叉": "", "MACD翻多": "", "布林突破": "", "爆量長紅": "",
            "技術面標籤": "", "大戶400張以上%": "", "中實戶50~400張%": "", "散戶50張以下%": "",
            "_candles": [],
        })

    print(f"📊 補技術面：{len(stocks)} 檔")
    for i, s in enumerate(stocks):
        if FUGLE_API_KEY:
            tech = analyze_technical(s["代號"], s["漲跌%"])
            if tech:
                s.update(tech)
            time.sleep(0.35)

        tags = [k for k in ["均線多頭", "KD黃金交叉", "MACD翻多", "布林突破", "爆量長紅"] if s.get(k) == "是"]
        s["技術面標籤"] = "、".join(tags) if tags else "—"
        if len(tags) >= 3 or s["漲跌%"] > 5 or s["外資今日(張)"] > 300 or s["投信今日(張)"] > 200:
            s["類型"] = "強勢噴出"
        elif s["漲跌%"] < 2 and (s["外資今日(張)"] > 0 or s["投信今日(張)"] > 0) and s["收盤價"] < 150:
            s["類型"] = "低位啟動"
        else:
            s["類型"] = "趨勢持續"
        if (i + 1) % 10 == 0:
            print(f"  進度 {i+1}/{len(stocks)}")

    order = {"雙主力連買": 0, "外資連買": 1, "投信連買": 2}
    stocks.sort(key=lambda s: (order.get(s["籌碼類型"], 9), -(s["外資連買天數"] + s["投信連買天數"])))
    return stocks, date_used, daily_inst


def render_mini_candlestick_svg(candles, width=260, height=110):
    if not candles or len(candles) < 2:
        return '<div style="color:#888;font-size:11px;padding:20px;text-align:center">無K線資料</div>'
    highs, lows = [c["h"] for c in candles], [c["l"] for c in candles]
    vmax, vmin = max(highs), min(lows)
    vr = vmax - vmin if vmax != vmin else 1
    cw, bw = width / len(candles), max(width / len(candles) * 0.6, 1.5)

    def y(v):
        return height - ((v - vmin) / vr) * (height - 10) - 5

    bars = []
    for i, c in enumerate(candles):
        x = i * cw + cw / 2
        color = "#e34948" if c["c"] >= c["o"] else "#1baf7a"
        yo, yc, yh, yl = y(c["o"]), y(c["c"]), y(c["h"]), y(c["l"])
        bars.append(f'<line x1="{x:.1f}" y1="{yh:.1f}" x2="{x:.1f}" y2="{yl:.1f}" stroke="{color}" stroke-width="1"/>')
        bars.append(f'<rect x="{x-bw/2:.1f}" y="{min(yo,yc):.1f}" width="{bw:.1f}" height="{max(abs(yc-yo),1):.1f}" fill="{color}"/>')
    return f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" style="background:#1a1a1a;border-radius:6px">{"".join(bars)}</svg>'


def tech_count(s):
    return len([x for x in ["均線多頭", "KD黃金交叉", "MACD翻多", "布林突破", "爆量長紅"] if s.get(x) == "是"])


def filter_stocks(stocks, mode):
    if mode == "foreign":
        return [s for s in stocks if s["外資連買天數"] > 0 or s["外資今日(張)"] > 0 or s["外資5日累計(張)"] > 0]
    if mode == "trust":
        return [s for s in stocks if s["投信連買天數"] > 0 or s["投信今日(張)"] > 0 or s["投信5日累計(張)"] > 0]
    if mode == "technical":
        return [s for s in stocks if tech_count(s) >= 2 or s["類型"] == "強勢噴出"]
    return stocks


def stock_cards(group):
    cards = ""
    for s in group:
        chg_color = "#e34948" if s["漲跌%"] >= 0 else "#1baf7a"
        sign = "+" if s["漲跌%"] >= 0 else ""
        tags = "".join([f'<span style="font-size:10px;padding:2px 6px;border-radius:10px;background:#eee;color:#555">{k}</span>' for k in ["均線多頭", "KD黃金交叉", "MACD翻多", "布林突破", "爆量長紅"] if s.get(k) == "是"]) or '<span style="font-size:10px;color:#aaa">—</span>'
        cards += f"""
<div style="background:#fff;border:1px solid #e8e8e8;border-radius:12px;padding:14px;box-shadow:0 1px 3px rgba(0,0,0,0.04)">
  <div style="display:flex;justify-content:space-between;gap:8px;margin-bottom:8px">
    <div><div style="font-size:15px;font-weight:600">{s['代號']} {s['名稱']}</div><div style="font-size:12px;color:#888">{s['籌碼類型']} · 外資連{s['外資連買天數']}天｜投信連{s['投信連買天數']}天</div></div>
    <div style="text-align:right"><div style="font-size:17px;font-weight:600">{s['收盤價']:.2f}</div><div style="font-size:12px;color:{chg_color}">{sign}{s['漲跌%']:.2f}%</div></div>
  </div>
  <div style="margin-bottom:8px">{render_mini_candlestick_svg(s.get('_candles', []))}</div>
  <div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:10px">{tags}</div>
  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px;font-size:11px;text-align:center;background:#f8f8f8;border-radius:8px;padding:6px 0">
    <div><div style="color:#999">外今</div><b>{s['外資今日(張)']:+,}</b></div>
    <div><div style="color:#999">外5日</div><b>{s['外資5日累計(張)']:+,}</b></div>
    <div><div style="color:#999">投今</div><b>{s['投信今日(張)']:+,}</b></div>
    <div><div style="color:#999">投5日</div><b>{s['投信5日累計(張)']:+,}</b></div>
  </div>
</div>"""
    return cards


def make_dashboard_page(stocks, date_str, mode):
    y, m, d = date_str[:4], date_str[4:6], date_str[6:]
    cfg = {
        "foreign": ("🔵", "外資連買", "外資連續買超、當日外資買超或 5 日外資累計偏多"),
        "trust": ("🟣", "投信連買", "投信連續買超、當日投信買超或 5 日投信累計偏多"),
        "technical": ("🔥", "技術面很強", "至少兩個技術訊號成立，或分類為強勢噴出"),
        "report": ("📊", "台股籌碼日報", "外資、投信與技術面綜合清單"),
    }
    emoji, title, desc = cfg[mode]
    group = filter_stocks(stocks, mode)
    if mode == "foreign":
        group.sort(key=lambda s: (-s["外資連買天數"], -s["外資5日累計(張)"], -s["漲跌%"]))
    elif mode == "trust":
        group.sort(key=lambda s: (-s["投信連買天數"], -s["投信5日累計(張)"], -s["漲跌%"]))
    elif mode == "technical":
        group.sort(key=lambda s: (-tech_count(s), -s["漲跌%"]))

    cards = stock_cards(group) if group else '<div style="background:#fff;border:1px solid #e8e8e8;border-radius:12px;padding:20px;color:#888">目前沒有符合條件的股票。</div>'
    return f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} {y}/{m}/{d}</title></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f5f5;color:#1a1a1a;margin:0;padding:0">
<div style="max-width:1100px;margin:0 auto;padding:28px 16px 60px">
<div style="border-bottom:2px solid #1a1a1a;padding-bottom:14px;margin-bottom:24px"><div style="font-size:24px;font-weight:700">{emoji} {title}</div><div style="font-size:13px;color:#888;margin-top:4px">{y}/{m}/{d} 盤後 · {desc} · 共 {len(group)} 檔 · <a href="index.html" style="color:#888">← 回首頁</a></div></div>
<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px">{cards}</div>
<div style="border-top:1px solid #ddd;padding-top:16px;margin-top:28px;font-size:12px;color:#aaa">僅供參考，不構成投資建議 · 每日約 17:30 自動更新</div>
</div></body></html>"""


def make_holdings_page(price_map, inst_map, date_str):
    y, m, d = date_str[:4], date_str[4:6], date_str[6:]
    groups = []
    for h in HOLDINGS:
        if h["group"] not in groups:
            groups.append(h["group"])

    def color(v):
        return "#e34948" if v > 0 else ("#1baf7a" if v < 0 else "#888")

    sections = ""
    pnl_list, up, dn, total = [], 0, 0, 0
    for h in HOLDINGS:
        p = price_map.get(h["code"])
        if p:
            total += 1
            up += 1 if p["chg"] > 0 else 0
            dn += 1 if p["chg"] < 0 else 0
            pnl_list.append((p["price"] - h["cost"]) / h["cost"] * 100)
    avg_pnl = sum(pnl_list) / len(pnl_list) if pnl_list else 0

    for group in groups:
        rows = ""
        for h in [x for x in HOLDINGS if x["group"] == group]:
            p = price_map.get(h["code"])
            if not p:
                rows += f'<tr><td style="padding:8px 6px;font-weight:500">{h["code"]}<br><span style="font-size:11px;color:#888">{h["name"]}</span></td><td colspan="5" style="font-size:12px;color:#888">資料暫無</td></tr>'
                continue
            pnl = (p["price"] - h["cost"]) / h["cost"] * 100
            f, t = inst_map.get(h["code"], {}).get("foreign", 0), inst_map.get(h["code"], {}).get("trust", 0)
            chip = "⭐ 雙買" if f > 0 and t > 0 else (f"外資 +{f}" if f > 0 else (f"投信 +{t}" if t > 0 else "—"))
            rows += f"""<tr style="border-bottom:1px solid #eee">
<td style="padding:8px 6px;font-weight:500">{h['code']}<br><span style="font-size:11px;color:#888">{h['name']}</span></td>
<td style="padding:8px 6px;text-align:right;color:#888">{h['cost']:.1f}</td><td style="padding:8px 6px;text-align:right;font-weight:500">{p['price']:.2f}</td>
<td style="padding:8px 6px;text-align:right;color:{color(p['chg'])}">{p['chg']:+.2f}<br><span style="font-size:10px">{p['chg_pct']:+.2f}%</span></td>
<td style="padding:8px 6px;text-align:right;font-weight:500;color:{color(pnl)}">{pnl:+.1f}%</td><td style="padding:8px 6px;font-size:11px">{chip}</td></tr>"""
        sections += f'<div style="margin-bottom:24px"><div style="font-size:13px;font-weight:600;margin:1rem 0 8px;padding-left:8px;border-left:3px solid #1a1a1a">{group}</div><table style="width:100%;border-collapse:collapse;font-size:13px;background:#fff"><thead><tr style="border-bottom:1px solid #ddd;color:#888"><th style="padding:7px 6px;text-align:left">代號/名稱</th><th style="padding:7px 6px;text-align:right">成本</th><th style="padding:7px 6px;text-align:right">現價</th><th style="padding:7px 6px;text-align:right">今日漲跌</th><th style="padding:7px 6px;text-align:right">損益%</th><th style="padding:7px 6px">今日籌碼</th></tr></thead><tbody>{rows}</tbody></table></div>'

    summary = f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:1.5rem"><div style="background:#fff;border-radius:12px;padding:12px 14px">持股數<br><b>{total} 檔</b></div><div style="background:#fff;border-radius:12px;padding:12px 14px">平均損益<br><b style="color:{color(avg_pnl)}">{avg_pnl:+.1f}%</b></div><div style="background:#fff;border-radius:12px;padding:12px 14px">今日上漲<br><b style="color:#e34948">{up} 檔</b></div><div style="background:#fff;border-radius:12px;padding:12px 14px">今日下跌<br><b style="color:#1baf7a">{dn} 檔</b></div></div>'
    return f'<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>持股追蹤 {y}/{m}/{d}</title></head><body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:#f5f5f5;color:#1a1a1a;margin:0;padding:0"><div style="max-width:900px;margin:0 auto;padding:24px 16px 60px"><div style="display:flex;align-items:baseline;gap:12px;margin-bottom:20px;border-bottom:2px solid #1a1a1a;padding-bottom:12px"><div style="font-size:22px;font-weight:700">📋 持股追蹤</div><div style="font-size:13px;color:#888">{y}/{m}/{d} 盤後 · <a href="index.html" style="color:#888">← 回首頁</a></div></div>{summary}{sections}</div></body></html>'


def make_index_html(docs_dir):
    reports_dir = os.path.join(docs_dir, "reports")
    files = sorted([f for f in os.listdir(reports_dir) if f.endswith(".html")], reverse=True) if os.path.exists(reports_dir) else []
    items = ""
    for f in files:
        ds = f.replace(".html", "")
        if len(ds) == 8:
            items += f'<li style="padding:10px 0;border-bottom:1px solid #f0f0f0"><a href="reports/{f}" style="color:#1a1a1a;text-decoration:none;font-size:15px">📊 {ds[:4]}/{ds[4:6]}/{ds[6:]} 籌碼日報</a></li>'
    items = items or '<li style="padding:10px 0;color:#888;font-size:13px">尚無歷史報告，請先執行一次 GitHub Actions。</li>'
    buttons = [
        ("foreign.html", "🔵", "外資連買", "外資連續買超、5 日累計偏多"),
        ("trust.html", "🟣", "投信連買", "投信連續買超、法人籌碼穩定"),
        ("technical.html", "🔥", "技術面很強", "均線、KD、MACD、布林、爆量長紅"),
        ("holdings.html", "📋", "我的持股追蹤", "成本、現價、損益、法人買賣超"),
    ]
    btn_html = "".join([f'<a href="{href}" style="background:#fff;border:1px solid #e5e5e5;border-radius:14px;padding:18px;text-decoration:none;color:#1a1a1a;box-shadow:0 1px 4px rgba(0,0,0,.04)"><div style="font-size:22px;margin-bottom:8px">{emoji}</div><div style="font-size:17px;font-weight:700">{title}</div><div style="font-size:12px;color:#888;margin-top:4px">{desc}</div></a>' for href, emoji, title, desc in buttons])
    return f'<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>台股籌碼日報</title></head><body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:#f5f5f5;margin:0;padding:0;color:#1a1a1a"><div style="max-width:760px;margin:0 auto;padding:32px 16px 60px"><div style="font-size:24px;font-weight:700;margin-bottom:6px">📊 台股籌碼日報</div><div style="font-size:13px;color:#888;margin-bottom:24px">每日盤後自動更新 · 外資 / 投信 / 技術面 / 持股追蹤</div><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px;margin-bottom:28px">{btn_html}</div><div style="background:#fff;border-radius:14px;border:1px solid #e5e5e5;padding:16px"><div style="font-size:14px;font-weight:700;margin-bottom:10px">歷史報告</div><ul style="list-style:none;padding:0;margin:0">{items}</ul></div></div></body></html>'


def make_csv(stocks):
    out = io.StringIO()
    if stocks:
        fieldnames = [k for k in stocks[0] if not k.startswith("_")]
        w = csv.DictWriter(out, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows([{k: v for k, v in s.items() if not k.startswith("_")} for s in stocks])
    return out.getvalue()


def main():
    docs_dir = os.path.join(os.path.dirname(__file__), "..", "docs")
    data_dir = os.path.join(docs_dir, "data")
    reports_dir = os.path.join(docs_dir, "reports")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(reports_dir, exist_ok=True)

    stocks, date_used, daily_inst = build_full_data(n_days=6)
    if not stocks:
        print("❌ 無資料，結束。")
        return

    ymd = date_used
    today_price_map = fetch_price(date_used)

    today_inst_map = {}
    today_df = daily_inst.get(date_used)
    if today_df is not None:
        for _, row in today_df.iterrows():
            code = str(row.get("證券代號", "")).strip()
            f, t, _ = calc_institutional_row(row)
            today_inst_map[code] = {"foreign": f, "trust": t}

    with open(os.path.join(reports_dir, f"{ymd}.html"), "w", encoding="utf-8") as f:
        f.write(make_dashboard_page(stocks, ymd, "report"))

    with open(os.path.join(data_dir, f"{ymd}.json"), "w", encoding="utf-8") as f:
        json.dump([{k: v for k, v in s.items() if k != "_candles"} for s in stocks], f, ensure_ascii=False)

    pages = {
        "foreign.html": make_dashboard_page(stocks, ymd, "foreign"),
        "trust.html": make_dashboard_page(stocks, ymd, "trust"),
        "technical.html": make_dashboard_page(stocks, ymd, "technical"),
        "holdings.html": make_holdings_page(today_price_map, today_inst_map, ymd),
        "index.html": make_index_html(docs_dir),
    }
    for name, html in pages.items():
        with open(os.path.join(docs_dir, name), "w", encoding="utf-8") as f:
            f.write(html)
        print(f"✅ {name} 已更新")

    csv_path = os.path.join(data_dir, f"{ymd}.csv")
    with open(csv_path, "w", encoding="utf-8-sig") as f:
        f.write(make_csv(stocks))
    print(f"✅ CSV 已寫入：{csv_path}")


if __name__ == "__main__":
    main()
