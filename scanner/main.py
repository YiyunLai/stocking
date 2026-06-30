import requests
import pandas as pd
from datetime import datetime, timedelta
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import os
import time
import csv
import io

# ── 設定 ──────────────────────────────────────────────
SENDER_EMAIL   = os.environ["GMAIL_USER"]
RECEIVER_EMAIL = os.environ["GMAIL_TO"]
GMAIL_APP_PWD  = os.environ["GMAIL_APP_PWD"]
FUGLE_API_KEY  = os.environ.get("FUGLE_API_KEY", "")
# ──────────────────────────────────────────────────────

HEADERS = {"User-Agent": "Mozilla/5.0"}
FUGLE_BASE = "https://api.fugle.tw/marketdata/v1.0/stock"
FUGLE_HEADERS = {"X-API-KEY": FUGLE_API_KEY}


# ════════════════════════════════════════════════════════
# TWSE：三大法人 + 當日股價（免費公開）
# ════════════════════════════════════════════════════════

def get_trading_days(n=10):
    days = []
    d = datetime.today()
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return days

def fetch_institutional(date_str):
    url = (f"https://www.twse.com.tw/rwd/zh/fund/T86"
           f"?response=json&date={date_str}&selectType=ALL")
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        data = r.json()
        if data.get("stat") != "OK" or not data.get("data"):
            print(f"    （{date_str} stat={data.get('stat')}，data筆數={len(data.get('data',[]))}）")
            return None, None
        date_used = data.get("date", date_str)
        return pd.DataFrame(data["data"], columns=data["fields"]), date_used
    except Exception as e:
        print(f"    （{date_str} 例外：{e}）")
        return None, None

def fetch_price(date_str):
    url = (f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
           f"?response=json&date={date_str}&type=ALL")
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        data = r.json()
    except Exception as e:
        print(f"  ⚠️ fetch_price 例外：{e}")
        return {}

    if data.get("stat") != "OK":
        print(f"  ⚠️ fetch_price stat 異常：{data.get('stat')}")
        return {}

    tables = data.get("tables", [])
    print(f"  ℹ️ fetch_price 共有 {len(tables)} 個 table")

    price_map = {}
    for idx, table in enumerate(tables):
        if not isinstance(table, dict):
            continue
        fields = table.get("fields", [])
        rows   = table.get("data", [])
        if "收盤價" not in fields:
            continue
        print(f"  ℹ️ table[{idx}] 命中「收盤價」欄位，共 {len(rows)} 列，標題：{table.get('title','')[:30]}")
        ci = fields.index("證券代號")
        ni = fields.index("證券名稱")
        pi = fields.index("收盤價")
        di = fields.index("漲跌價差")
        for row in rows:
            code = row[ci].strip()
            name = row[ni].strip()
            try:
                price = float(row[pi].replace(",","").replace("--","").strip() or 0)
                chg   = float(row[di].replace(",","").replace("--","").strip() or 0)
            except (ValueError, AttributeError, IndexError):
                continue
            if price > 0:
                chg_pct = chg / (price - chg) * 100 if (price - chg) != 0 else 0
                price_map[code] = {"name": name, "price": price, "chg": chg, "chg_pct": chg_pct}
    print(f"  ℹ️ fetch_price 最終取得 {len(price_map)} 檔股價")
    return price_map

def parse_int(s):
    try:
        return int(str(s).replace(",", ""))
    except:
        return 0


# ════════════════════════════════════════════════════════
# TWSE：股權分散表（大戶/中實戶/散戶）
# ════════════════════════════════════════════════════════

def fetch_ownership_distribution(code):
    url = f"https://www.twse.com.tw/rwd/zh/stockHolder/SHAREHOLDING?response=json&stockNo={code}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        data = r.json()
        if data.get("stat") != "OK":
            return None
        rows   = data.get("data", [])
        fields = data.get("fields", [])
        if not rows:
            return None

        pct_idx = None
        for i, f in enumerate(fields):
            if "%" in f or "比例" in f or "占" in f:
                pct_idx = i
                break
        if pct_idx is None and len(fields) >= 4:
            pct_idx = 3

        big, mid, small = 0.0, 0.0, 0.0
        for row in rows:
            label = str(row[0]).replace(",", "").strip()
            try:
                pct = float(str(row[pct_idx]).replace(",", "").replace("%", "").strip())
            except:
                continue
            if any(x in label for x in ["400", "600", "800", "1,000", "1000", "以上"]):
                big += pct
            elif any(x in label for x in ["1～", "1~", "~5", "5～", "10～", "15～", "20～", "30～", "40～",
                                            "1至", "5至", "10至", "15至", "20至", "30至", "40至"]):
                small += pct
            elif any(x in label for x in ["50", "100", "200"]):
                mid += pct

        return {"大戶400張以上%": round(big, 2),
                "中實戶50~400張%": round(mid, 2),
                "散戶50張以下%": round(small, 2)}
    except:
        return None


# ════════════════════════════════════════════════════════
# 富果 API：52週區間、均線（技術面）
# ════════════════════════════════════════════════════════

def fugle_get(path, params=None):
    if not FUGLE_API_KEY:
        return None
    try:
        r = requests.get(f"{FUGLE_BASE}{path}", headers=FUGLE_HEADERS, params=params or {}, timeout=15)
        if r.status_code != 200:
            return None
        return r.json()
    except:
        return None

def fetch_52w_stats(code):
    """取得 52 週高低點，計算目前股價在區間的百分位"""
    data = fugle_get(f"/historical/stats/{code}")
    if not data:
        return None
    try:
        high = data.get("week52High")
        low  = data.get("week52Low")
        close = data.get("closePrice")
        if not high or not low or high == low:
            return None
        percentile = (close - low) / (high - low) * 100
        return {
            "52週高": high,
            "52週低": low,
            "52週位階%": round(percentile, 1),
        }
    except:
        return None

def fetch_candles(code, days=130):
    """抓歷史日K（OHLCV），免費方案可用，一次呼叫取得完整序列"""
    today = datetime.today()
    date_from = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    date_to   = today.strftime("%Y-%m-%d")
    data = fugle_get(f"/historical/candles/{code}", {
        "from": date_from, "to": date_to, "timeframe": "D",
        "fields": "open,high,low,close,volume", "sort": "asc"
    })
    if not data or not data.get("data"):
        return None
    series = data["data"]
    if len(series) < 25:
        return None
    return series  # list of {date, open, high, low, close, volume}, 由舊到新排序

def compute_sma(closes, period):
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period

def compute_sma_series(closes, period):
    if len(closes) < period:
        return []
    return [sum(closes[i-period+1:i+1]) / period for i in range(period-1, len(closes))]

def compute_kdj(candles, r_period=9, k_period=3, d_period=3):
    """RSV → K → D，回傳最後兩筆 K/D 供判斷黃金交叉"""
    if len(candles) < r_period + d_period:
        return None
    closes = [c["close"] for c in candles]
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]

    k_vals, d_vals = [], []
    k_prev, d_prev = 50.0, 50.0
    for i in range(len(candles)):
        if i < r_period - 1:
            k_vals.append(None); d_vals.append(None)
            continue
        period_high = max(highs[i-r_period+1:i+1])
        period_low  = min(lows[i-r_period+1:i+1])
        rsv = 50.0 if period_high == period_low else (closes[i] - period_low) / (period_high - period_low) * 100
        k = (2/3)*k_prev + (1/3)*rsv
        d = (2/3)*d_prev + (1/3)*k
        k = max(0, min(100, k))
        d = max(0, min(100, d))
        k_vals.append(k); d_vals.append(d)
        k_prev, d_prev = k, d

    valid = [(k,d) for k,d in zip(k_vals, d_vals) if k is not None]
    if len(valid) < 2:
        return None
    (pk, pd_), (ck, cd) = valid[-2], valid[-1]
    golden_cross = (pk <= pd_) and (ck > cd)
    return {"K值": round(ck,1), "D值": round(cd,1), "KD黃金交叉": "是" if golden_cross else "否"}

def compute_ema_series(values, period):
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    ema = [sum(values[:period]) / period]
    for v in values[period:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema

def compute_macd(candles, fast=12, slow=26, signal=9):
    closes = [c["close"] for c in candles]
    if len(closes) < slow + signal:
        return None
    ema_fast = compute_ema_series(closes, fast)
    ema_slow = compute_ema_series(closes, slow)
    offset = len(ema_fast) - len(ema_slow)
    macd_line = [f - s for f, s in zip(ema_fast[offset:], ema_slow)]
    if len(macd_line) < signal + 1:
        return None
    signal_line = compute_ema_series(macd_line, signal)
    macd_aligned = macd_line[-len(signal_line):]
    hist = [m - s for m, s in zip(macd_aligned, signal_line)]
    if len(hist) < 2:
        return None
    prev_hist, curr_hist = hist[-2], hist[-1]
    macd_turn_bullish = (prev_hist <= 0) and (curr_hist > 0)
    return {"MACD翻多": "是" if macd_turn_bullish else "否"}

def compute_bbands(closes, period=20, num_std=2):
    if len(closes) < period:
        return None
    window = closes[-period:]
    mean = sum(window) / period
    variance = sum((x - mean) ** 2 for x in window) / period
    std = variance ** 0.5
    upper = mean + num_std * std
    return {"布林上軌": round(upper, 2)}

def analyze_technical(code, today_chg_pct, today_volume=None):
    """一次抓K線、自算全部技術面指標，回傳 5 個訊號標籤"""
    candles = fetch_candles(code)
    if not candles:
        return None

    closes  = [c["close"]  for c in candles]
    volumes = [c["volume"] for c in candles]

    ma5  = compute_sma(closes, 5)
    ma20 = compute_sma(closes, 20)
    ma60 = compute_sma(closes, 60)
    ma20_series = compute_sma_series(closes, 20)

    result = {
        "MA5": round(ma5,2) if ma5 else "",
        "MA20": round(ma20,2) if ma20 else "",
        "MA60": round(ma60,2) if ma60 else "",
        "均線多頭": "否", "MA20翻揚": "否",
        "KD黃金交叉": "否", "MACD翻多": "否", "布林突破": "否", "爆量長紅": "否",
    }

    if ma5 and ma20 and ma60:
        result["均線多頭"] = "是" if (ma5 > ma20 > ma60) else "否"
    if len(ma20_series) >= 5:
        result["MA20翻揚"] = "是" if ma20_series[-1] > ma20_series[-5] else "否"

    kd = compute_kdj(candles)
    if kd:
        result["KD黃金交叉"] = kd["KD黃金交叉"]

    macd = compute_macd(candles)
    if macd:
        result["MACD翻多"] = macd["MACD翻多"]

    bb = compute_bbands(closes)
    if bb and closes:
        result["布林突破"] = "是" if closes[-1] > bb["布林上軌"] else "否"
        result["布林上軌"] = bb["布林上軌"]

    # 爆量長紅：今日量 > 近5日均量的1.5倍，且漲幅>=3%
    if len(volumes) >= 6:
        avg_vol5 = sum(volumes[-6:-1]) / 5
        vol_today = volumes[-1]
        vol_spike = avg_vol5 > 0 and vol_today > avg_vol5 * 1.5
        result["爆量長紅"] = "是" if (vol_spike and today_chg_pct >= 3) else "否"
    else:
        result["爆量長紅"] = "是" if today_chg_pct >= 5 else "否"

    return result


# ════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════

def build_full_data(n_days=6):
    trading_days = get_trading_days(n_days)
    print(f"⏳ 抓取近 {n_days} 個交易日資料：{trading_days[:3]}...")

    daily_inst = {}
    for i, d in enumerate(trading_days):
        df, du = fetch_institutional(d)
        if df is not None:
            daily_inst[du or d] = df
            print(f"  ✅ {du or d} 三大法人 OK（{len(df)} 筆）")
        else:
            print(f"  ⚠️  {d} 無資料")
        time.sleep(1.5)

    if not daily_inst:
        print("❌ 無法取得任何三大法人資料")
        return None, None

    today_key   = sorted(daily_inst.keys())[-1]
    date_used   = today_key
    today_df    = daily_inst[today_key]
    sorted_days = sorted(daily_inst.keys(), reverse=True)

    time.sleep(1)
    price_map = fetch_price(date_used)
    print(f"✅ 今日股價 {date_used}：{len(price_map)} 筆")

    inst_history = {}
    for day_key, df in daily_inst.items():
        for _, row in df.iterrows():
            code = str(row.get("證券代號","")).strip()
            if len(code) != 4:
                continue
            f  = (parse_int(row.get("外陸資買進股數",0)) - parse_int(row.get("外陸資賣出股數",0))) // 1000
            t  = (parse_int(row.get("投信買進股數",0))   - parse_int(row.get("投信賣出股數",0)))   // 1000
            d2 = (parse_int(row.get("自營商買進股數",0)) - parse_int(row.get("自營商賣出股數",0))) // 1000
            inst_history.setdefault(code, {})[day_key] = {"f": f, "t": t, "d": d2}

    stocks = []
    for _, row in today_df.iterrows():
        code = str(row.get("證券代號","")).strip()
        if len(code) != 4:
            continue
        pd_info = price_map.get(code)
        if not pd_info:
            continue

        hist = inst_history.get(code, {})
        today_data = hist.get(today_key, {"f":0,"t":0,"d":0})
        foreign_today = today_data["f"]
        trust_today   = today_data["t"]
        dealer_today  = today_data["d"]

        if foreign_today < 50 and trust_today < 10:
            continue
        if foreign_today <= 0 and trust_today <= 0:
            continue

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

        five_days = sorted_days[:5]
        f_5d = sum(hist.get(d,{}).get("f",0) for d in five_days)
        t_5d = sum(hist.get(d,{}).get("t",0) for d in five_days)
        d_5d = sum(hist.get(d,{}).get("d",0) for d in five_days)

        stocks.append({
            "代號": code,
            "名稱": pd_info["name"],
            "類型": "",  # 待技術面資料補齊後分類
            "收盤價": pd_info["price"],
            "漲跌%": round(pd_info["chg_pct"], 2),
            "外資今日(張)": foreign_today,
            "投信今日(張)": trust_today,
            "自營今日(張)": dealer_today,
            "三大法人合計(張)": foreign_today + trust_today + dealer_today,
            "外資連買天數": f_consec,
            "投信連買天數": t_consec,
            "外資5日累計(張)": f_5d,
            "投信5日累計(張)": t_5d,
            "自營5日累計(張)": d_5d,
            "52週高": "", "52週低": "", "52週位階%": "",
            "MA5": "", "MA20": "", "MA60": "",
            "均線多頭": "", "MA20翻揚": "",
            "KD黃金交叉": "", "MACD翻多": "", "布林突破": "", "爆量長紅": "",
            "技術面標籤": "",
            "大戶400張以上%": "", "中實戶50~400張%": "", "散戶50張以下%": "",
        })

    stocks.sort(key=lambda x: -(abs(x["外資今日(張)"]) + abs(x["投信今日(張)"]) * 3))

    # 技術面 + 股權分散逐檔補齊
    has_fugle = bool(FUGLE_API_KEY)
    print(f"\n📊 開始補齊 {len(stocks)} 檔技術面 / 籌碼面資料（富果 API：{'啟用' if has_fugle else '未設定，跳過技術面'}）...")

    for i, s in enumerate(stocks):
        code = s["代號"]

        if has_fugle:
            stats = fetch_52w_stats(code)
            if stats:
                s["52週高"]    = stats["52週高"]
                s["52週低"]    = stats["52週低"]
                s["52週位階%"] = stats["52週位階%"]
            time.sleep(0.3)

            tech = analyze_technical(code, s["漲跌%"])
            if tech:
                s["MA5"]        = tech["MA5"]
                s["MA20"]       = tech["MA20"]
                s["MA60"]       = tech["MA60"]
                s["均線多頭"]    = tech["均線多頭"]
                s["MA20翻揚"]   = tech["MA20翻揚"]
                s["KD黃金交叉"] = tech["KD黃金交叉"]
                s["MACD翻多"]   = tech["MACD翻多"]
                s["布林突破"]   = tech["布林突破"]
                s["爆量長紅"]   = tech["爆量長紅"]

                tags = []
                if tech["均線多頭"] == "是":   tags.append("均線多頭")
                if tech["KD黃金交叉"] == "是": tags.append("KD黃金交叉")
                if tech["MACD翻多"] == "是":   tags.append("MACD翻多")
                if tech["布林突破"] == "是":   tags.append("布林突破")
                if tech["爆量長紅"] == "是":   tags.append("爆量長紅")
                s["技術面標籤"] = "、".join(tags) if tags else "—"
            time.sleep(0.3)

        own = fetch_ownership_distribution(code)
        if own:
            s["大戶400張以上%"]   = own["大戶400張以上%"]
            s["中實戶50~400張%"] = own["中實戶50~400張%"]
            s["散戶50張以下%"]   = own["散戶50張以下%"]
        time.sleep(0.6)

        # ── 分類邏輯（相對位階 + 均線 + 法人動向 + 技術面標籤數）──
        chg_pct    = s["漲跌%"]
        percentile = s["52週位階%"]
        tag_count  = len([t for t in [s["均線多頭"],s["KD黃金交叉"],s["MACD翻多"],s["布林突破"],s["爆量長紅"]] if t == "是"])

        if has_fugle and percentile != "":
            if percentile >= 75 and (chg_pct > 3 or s["外資今日(張)"] > 300 or s["投信今日(張)"] > 200 or tag_count >= 3):
                category = "強勢噴出"
            elif percentile <= 35 and (s["均線多頭"] == "是" or s["MA20翻揚"] == "是") and (foreign_today > 0 or trust_today > 0):
                category = "低位啟動"
            else:
                category = "趨勢持續"
        else:
            if chg_pct > 5 or (chg_pct > 3 and (foreign_today > 300 or trust_today > 200)):
                category = "強勢噴出"
            elif chg_pct < 2 and (foreign_today > 0 or trust_today > 0) and pd_info["price"] < 150:
                category = "低位啟動"
            else:
                category = "趨勢持續"

        s["類型"] = category

        if (i+1) % 10 == 0:
            print(f"  進度：{i+1}/{len(stocks)}")

    return stocks, date_used


# ════════════════════════════════════════════════════════
# 輸出：CSV + Email
# ════════════════════════════════════════════════════════

def make_csv(stocks):
    output = io.StringIO()
    if not stocks:
        return output.getvalue()
    fieldnames = list(stocks[0].keys())
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(stocks)
    return output.getvalue()

def make_html_summary(stocks, date_str):
    y, m, d = date_str[:4], date_str[4:6], date_str[6:]
    cats = ["強勢噴出", "低位啟動", "趨勢持續"]
    cat_desc = {
        "強勢噴出": "🔴 強勢噴出 — 股價接近52週高點、近期漲幅強、法人買超，適合追強短線",
        "低位啟動": "🟢 低位啟動 — 股價位於52週相對低位、剛站上/翻揚MA20，適合波段布局",
        "趨勢持續": "🟡 趨勢持續 — 介於兩者之間，法人持續買進，適合持有或輕追",
    }

    def cn(n):
        if n == "" or n is None: return "—"
        if isinstance(n, (int, float)):
            if n > 0: return f'<span style="color:#c0392b">+{n:,.0f}</span>' if n == int(n) else f'<span style="color:#c0392b">+{n:,}</span>'
            if n < 0: return f'<span style="color:#27ae60">{n:,.0f}</span>' if n == int(n) else f'<span style="color:#27ae60">{n:,}</span>'
            return "—"
        return str(n)

    def cp(p):
        if p == "" or p is None: return "—"
        try:
            p = float(p)
            if p > 0: return f'<span style="color:#c0392b">+{p:.2f}%</span>'
            if p < 0: return f'<span style="color:#27ae60">{p:.2f}%</span>'
            return f"{p:.2f}%"
        except: return str(p)

    sections = ""
    for cat in cats:
        group = [s for s in stocks if s["類型"] == cat]
        if not group: continue
        rows_html = ""
        for s in group:
            pct_label = f'{s["52週位階%"]}%' if s["52週位階%"] != "" else "—"
            tag_label = s["技術面標籤"] if s["技術面標籤"] else "—"
            rows_html += f"""<tr style="border-bottom:1px solid #f0f0f0">
  <td style="padding:8px 6px;font-weight:500;white-space:nowrap">{s['代號']}<br><span style="font-size:11px;color:#888;font-weight:400">{s['名稱']}</span></td>
  <td style="padding:8px 6px;text-align:right">{s['收盤價']:.2f}<br><span style="font-size:11px">{cp(s['漲跌%'])}</span></td>
  <td style="padding:8px 6px;text-align:right;font-size:12px">{pct_label}</td>
  <td style="padding:8px 6px;font-size:11px;color:#555">{tag_label}</td>
  <td style="padding:8px 6px;text-align:right">{cn(s['外資今日(張)'])}<br><span style="font-size:11px;color:#888">連{s['外資連買天數']}天｜5日{cn(s['外資5日累計(張)'])}</span></td>
  <td style="padding:8px 6px;text-align:right">{cn(s['投信今日(張)'])}<br><span style="font-size:11px;color:#888">連{s['投信連買天數']}天｜5日{cn(s['投信5日累計(張)'])}</span></td>
  <td style="padding:8px 6px;text-align:right;font-size:12px">{s['大戶400張以上%'] or '—'}<br>{s['中實戶50~400張%'] or '—'}<br>{s['散戶50張以下%'] or '—'}</td>
</tr>"""
        sections += f"""<div style="margin-bottom:28px">
  <div style="font-size:15px;font-weight:600;margin-bottom:4px">{cat_desc[cat]} <span style="font-size:12px;font-weight:400;color:#888">{len(group)} 檔</span></div>
  <table style="width:100%;border-collapse:collapse;font-size:13px">
    <thead><tr style="background:#f8f8f8;color:#555">
      <th style="padding:7px 6px;text-align:left">代號/名稱</th>
      <th style="padding:7px 6px;text-align:right">收盤/漲跌</th>
      <th style="padding:7px 6px;text-align:right">52週位階</th>
      <th style="padding:7px 6px;text-align:left">技術面標籤</th>
      <th style="padding:7px 6px;text-align:right">外資(張)</th>
      <th style="padding:7px 6px;text-align:right">投信(張)</th>
      <th style="padding:7px 6px;text-align:right">大戶%/中實戶%/散戶%</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>"""

    return f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,sans-serif;background:#fff;color:#222;margin:0;padding:0">
<div style="max-width:840px;margin:0 auto;padding:20px 16px">
  <div style="border-bottom:2px solid #222;padding-bottom:10px;margin-bottom:20px">
    <div style="font-size:19px;font-weight:700">📊 台股籌碼日報</div>
    <div style="font-size:12px;color:#888;margin-top:3px">{y}/{m}/{d} 盤後 · 共 {len(stocks)} 檔入選 · 詳細資料見附件 CSV</div>
  </div>
  {sections}
  <div style="border-top:1px solid #eee;padding-top:12px;font-size:11px;color:#aaa">
    資料來源：台灣證券交易所（TWSE）、富果行情 API · 僅供參考，不構成投資建議
  </div>
</div></body></html>"""

def send_email(html_body, csv_str, date_str, stock_count):
    y, m, d = date_str[:4], date_str[4:6], date_str[6:]
    subject = f"📊 台股籌碼日報 {y}/{m}/{d}｜{stock_count} 檔入選"

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"]    = SENDER_EMAIL
    msg["To"]      = RECEIVER_EMAIL

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)

    csv_bytes = csv_str.encode("utf-8-sig")
    attachment = MIMEBase("application", "octet-stream")
    attachment.set_payload(csv_bytes)
    encoders.encode_base64(attachment)
    attachment.add_header("Content-Disposition", "attachment", filename=f"chip_{y}{m}{d}.csv")
    msg.attach(attachment)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SENDER_EMAIL, GMAIL_APP_PWD)
        server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
    print(f"✅ 已寄出：{subject}")

def main():
    stocks, date_used = build_full_data(n_days=6)
    if not stocks:
        print("❌ 無資料，結束。")
        return
    print(f"\n✅ 共 {len(stocks)} 檔")
    csv_str = make_csv(stocks)
    html    = make_html_summary(stocks, date_used)
    send_email(html, csv_str, date_used, len(stocks))

if __name__ == "__main__":
    main()
