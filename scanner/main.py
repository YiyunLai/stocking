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
# ──────────────────────────────────────────────────────

HEADERS = {"User-Agent": "Mozilla/5.0"}

def get_trading_days(n=10):
    """往前取得 n 個工作日的日期字串列表（最近的在前）"""
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
            return None, None
        date_used = data.get("date", date_str)
        return pd.DataFrame(data["data"], columns=data["fields"]), date_used
    except:
        return None, None

def fetch_price(date_str):
    url = (f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
           f"?response=json&date={date_str}&type=ALL")
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        data = r.json()
    except:
        return {}
    price_map = {}
    for table in data.get("tables", []):
        fields = table.get("fields", [])
        rows   = table.get("data", [])
        if "收盤價" not in fields:
            continue
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
            except (ValueError, AttributeError):
                continue
            if price > 0:
                chg_pct = chg / (price - chg) * 100 if (price - chg) != 0 else 0
                price_map[code] = {"name": name, "price": price, "chg": chg, "chg_pct": chg_pct}
    return price_map

def parse_int(s):
    try:
        return int(str(s).replace(",", ""))
    except:
        return 0

def parse_float(s):
    try:
        return float(str(s).replace(",", "").replace("--","").strip() or 0)
    except:
        return 0.0

def fetch_ownership(code):
    """抓股權分散表（週頻，最新一週）"""
    url = (f"https://www.twse.com.tw/rwd/zh/holding/STOCK_DAY_AVG"
           f"?response=json&stockNo={code}")
    # 改用正確的股權分散 API
    url = f"https://www.twse.com.tw/rwd/zh/stockHolder/SHAREHOLDING?response=json&stockNo={code}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        data = r.json()
        if data.get("stat") != "OK" or not data.get("data"):
            return None
        fields = data.get("fields", [])
        rows   = data.get("data", [])
        if not rows:
            return None
        # 取最新一週資料（最後一筆）
        latest = rows[-1]
        row_dict = dict(zip(fields, latest))

        # 持股級距分析
        # TWSE 股權分散表欄位：持股/單位數分級、人數、持股/單位數、占集保庫存數%
        # 我們用持股張數分級來判斷散戶/中實戶/大戶
        return row_dict
    except:
        return None

def fetch_ownership_distribution(code):
    """
    抓 TWSE 股權分散週報，計算大戶/中實戶/散戶持股比例
    大戶：400張以上
    中實戶：50~400張
    散戶：50張以下
    """
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

        # 找持股比例欄位 index
        pct_idx = None
        for i, f in enumerate(fields):
            if "%" in f or "比例" in f or "占" in f:
                pct_idx = i
                break
        if pct_idx is None and len(fields) >= 4:
            pct_idx = 3  # 通常第4欄是占比

        # 級距關鍵字對應
        big    = 0.0  # 大戶 400張以上
        mid    = 0.0  # 中實戶 50~400張
        small  = 0.0  # 散戶 50張以下

        for row in rows:
            label = str(row[0]).replace(",","").strip()
            try:
                pct = float(str(row[pct_idx]).replace(",","").replace("%","").strip())
            except:
                continue

            # 大戶：400張以上各級距
            if any(x in label for x in ["400", "600", "800", "1,000", "1000", "以上"]):
                big += pct
            # 散戶：1~50張
            elif any(x in label for x in ["1～", "1~", "~5", "5～", "10～", "15～", "20～", "30～", "40～",
                                            "1至", "5至", "10至", "15至", "20至", "30至", "40至"]):
                small += pct
            # 中實戶：50~400張
            elif any(x in label for x in ["50", "100", "200"]):
                mid += pct

        return {"大戶400張以上%": round(big, 2),
                "中實戶50~400張%": round(mid, 2),
                "散戶50張以下%": round(small, 2)}
    except Exception as e:
        return None

def build_full_data(n_days=6):
    """
    主流程：
    1. 抓最近 n_days 天的三大法人資料
    2. 計算連買天數、5日累計
    3. 抓今日股價
    4. 篩選有效個股
    5. 逐檔抓股權分散
    """
    trading_days = get_trading_days(n_days)
    print(f"⏳ 抓取近 {n_days} 個交易日資料：{trading_days[:3]}...")

    # 抓各天三大法人
    daily_inst = {}
    date_used  = None
    for i, d in enumerate(trading_days):
        df, du = fetch_institutional(d)
        if df is not None:
            if i == 0:
                date_used = du
            daily_inst[du or d] = df
            print(f"  ✅ {du or d} 三大法人 OK（{len(df)} 筆）")
        else:
            print(f"  ⚠️  {d} 無資料")
        time.sleep(0.5)

    if not daily_inst:
        print("❌ 無法取得任何三大法人資料")
        return None, None

    today_key  = sorted(daily_inst.keys())[-1]
    date_used  = today_key
    today_df   = daily_inst[today_key]
    sorted_days = sorted(daily_inst.keys(), reverse=True)  # 新→舊

    # 抓今日股價
    time.sleep(1)
    price_map = fetch_price(date_used)
    print(f"✅ 今日股價 {date_used}：{len(price_map)} 筆")

    # 建立每支股票的每日買賣超 dict
    # inst_history[code][date] = {foreign, trust, dealer}
    inst_history = {}
    for day_key, df in daily_inst.items():
        for _, row in df.iterrows():
            code = str(row.get("證券代號","")).strip()
            if len(code) != 4:
                continue
            f = (parse_int(row.get("外陸資買進股數",0)) - parse_int(row.get("外陸資賣出股數",0))) // 1000
            t = (parse_int(row.get("投信買進股數",0))   - parse_int(row.get("投信賣出股數",0)))   // 1000
            d2= (parse_int(row.get("自營商買進股數",0)) - parse_int(row.get("自營商賣出股數",0))) // 1000
            if code not in inst_history:
                inst_history[code] = {}
            inst_history[code][day_key] = {"f": f, "t": t, "d": d2}

    # 篩選今日有效個股（外資或投信有買超）
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

        # 連買天數（外資）
        f_consec = 0
        for day in sorted_days:
            v = hist.get(day, {}).get("f", 0)
            if v > 0:
                f_consec += 1
            else:
                break

        # 連買天數（投信）
        t_consec = 0
        for day in sorted_days:
            v = hist.get(day, {}).get("t", 0)
            if v > 0:
                t_consec += 1
            else:
                break

        # 5日累計
        five_days = sorted_days[:5]
        f_5d = sum(hist.get(d,{}).get("f",0) for d in five_days)
        t_5d = sum(hist.get(d,{}).get("t",0) for d in five_days)
        d_5d = sum(hist.get(d,{}).get("d",0) for d in five_days)

        # 分類
        chg_pct = pd_info["chg_pct"]
        if chg_pct > 5 or (chg_pct > 3 and (foreign_today > 300 or trust_today > 200)):
            category = "強勢噴出"
        elif chg_pct < 2 and (foreign_today > 0 or trust_today > 0) and pd_info["price"] < 150:
            category = "低位啟動"
        else:
            category = "趨勢持續"

        stocks.append({
            "代號": code,
            "名稱": pd_info["name"],
            "類型": category,
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
            "大戶400張以上%": "",
            "中實戶50~400張%": "",
            "散戶50張以下%": "",
        })

    # 排序
    stocks.sort(key=lambda x: -(abs(x["外資今日(張)"]) + abs(x["投信今日(張)"]) * 3))

    # 逐檔抓股權分散（有速率限制，加延遲）
    print(f"\n📊 開始抓 {len(stocks)} 檔股權分散資料...")
    for i, s in enumerate(stocks):
        own = fetch_ownership_distribution(s["代號"])
        if own:
            s["大戶400張以上%"]   = own["大戶400張以上%"]
            s["中實戶50~400張%"] = own["中實戶50~400張%"]
            s["散戶50張以下%"]   = own["散戶50張以下%"]
        if (i+1) % 10 == 0:
            print(f"  進度：{i+1}/{len(stocks)}")
        time.sleep(0.8)

    return stocks, date_used

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
        "強勢噴出": "🔴 強勢噴出 — 漲幅強、法人大量買，適合追強短線",
        "低位啟動": "🟢 低位啟動 — 低價位法人剛介入，適合波段布局",
        "趨勢持續": "🟡 趨勢持續 — 法人持續買進，適合持有或輕追",
    }

    def cn(n):
        if n == "": return "—"
        n = int(n) if isinstance(n, float) and n == int(n) else n
        if isinstance(n, (int, float)):
            if n > 0: return f'<span style="color:#c0392b">+{n:,}</span>'
            if n < 0: return f'<span style="color:#27ae60">{n:,}</span>'
        return str(n) if n != 0 else "—"

    def cp(p):
        if p == "": return "—"
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
            rows_html += f"""<tr style="border-bottom:1px solid #f0f0f0">
  <td style="padding:8px 6px;font-weight:500;white-space:nowrap">{s['代號']}<br><span style="font-size:11px;color:#888;font-weight:400">{s['名稱']}</span></td>
  <td style="padding:8px 6px;text-align:right">{s['收盤價']:.2f}<br><span style="font-size:11px">{cp(s['漲跌%'])}</span></td>
  <td style="padding:8px 6px;text-align:right">{cn(s['外資今日(張)'])}<br><span style="font-size:11px;color:#888">連{s['外資連買天數']}天｜5日{cn(s['外資5日累計(張)'])}</span></td>
  <td style="padding:8px 6px;text-align:right">{cn(s['投信今日(張)'])}<br><span style="font-size:11px;color:#888">連{s['投信連買天數']}天｜5日{cn(s['投信5日累計(張)'])}</span></td>
  <td style="padding:8px 6px;text-align:right">{cn(s['自營今日(張)'])}</td>
  <td style="padding:8px 6px;text-align:right;font-size:12px">{s['大戶400張以上%'] or '—'}<br>{s['中實戶50~400張%'] or '—'}<br>{s['散戶50張以下%'] or '—'}</td>
</tr>"""
        sections += f"""<div style="margin-bottom:28px">
  <div style="font-size:15px;font-weight:600;margin-bottom:4px">{cat_desc[cat]} <span style="font-size:12px;font-weight:400;color:#888">{len(group)} 檔</span></div>
  <table style="width:100%;border-collapse:collapse;font-size:13px">
    <thead><tr style="background:#f8f8f8;color:#555">
      <th style="padding:7px 6px;text-align:left">代號/名稱</th>
      <th style="padding:7px 6px;text-align:right">收盤/漲跌</th>
      <th style="padding:7px 6px;text-align:right">外資(張)</th>
      <th style="padding:7px 6px;text-align:right">投信(張)</th>
      <th style="padding:7px 6px;text-align:right">自營(張)</th>
      <th style="padding:7px 6px;text-align:right">大戶%/中實戶%/散戶%</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>"""

    return f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,sans-serif;background:#fff;color:#222;margin:0;padding:0">
<div style="max-width:800px;margin:0 auto;padding:20px 16px">
  <div style="border-bottom:2px solid #222;padding-bottom:10px;margin-bottom:20px">
    <div style="font-size:19px;font-weight:700">📊 台股籌碼日報</div>
    <div style="font-size:12px;color:#888;margin-top:3px">{y}/{m}/{d} 盤後 · 共 {len(stocks)} 檔入選 · 詳細資料見附件 CSV</div>
  </div>
  {sections}
  <div style="border-top:1px solid #eee;padding-top:12px;font-size:11px;color:#aaa">
    資料來源：台灣證券交易所（TWSE）· 僅供參考，不構成投資建議
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

    # CSV 附件
    csv_bytes = csv_str.encode("utf-8-sig")  # utf-8-sig 讓 Excel 正確顯示中文
    attachment = MIMEBase("application", "octet-stream")
    attachment.set_payload(csv_bytes)
    encoders.encode_base64(attachment)
    attachment.add_header("Content-Disposition", "attachment",
                          filename=f"chip_{y}{m}{d}.csv")
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
    csv_str  = make_csv(stocks)
    html     = make_html_summary(stocks, date_used)
    send_email(html, csv_str, date_used, len(stocks))

if __name__ == "__main__":
    main()
