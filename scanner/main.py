import requests
import pandas as pd
from datetime import datetime, timedelta
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import os
import time

# ── 設定 ──────────────────────────────────────────────
SENDER_EMAIL    = os.environ["GMAIL_USER"]      # 你的 Gmail
RECEIVER_EMAIL  = os.environ["GMAIL_TO"]        # 收信人（可以是同一個）
GMAIL_APP_PWD   = os.environ["GMAIL_APP_PWD"]   # Gmail 應用程式密碼
# ──────────────────────────────────────────────────────

HEADERS = {"User-Agent": "Mozilla/5.0"}

def get_today_str():
    """取得今天日期，若今天是假日則往前找最近交易日（簡易版）"""
    d = datetime.today()
    # 週六 → 週五, 週日 → 週五
    if d.weekday() == 5:
        d -= timedelta(days=1)
    elif d.weekday() == 6:
        d -= timedelta(days=2)
    return d.strftime("%Y%m%d")

def fetch_institutional(date_str):
    url = (
        f"https://www.twse.com.tw/rwd/zh/fund/T86"
        f"?response=json&date={date_str}&selectType=ALL"
    )
    r = requests.get(url, headers=HEADERS, timeout=20)
    data = r.json()
    if data.get("stat") != "OK" or not data.get("data"):
        return None, None
    fields = data["fields"]
    rows   = data["data"]
    date_used = data.get("date", date_str)
    return pd.DataFrame(rows, columns=fields), date_used

def fetch_price(date_str):
    url = (
        f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
        f"?response=json&date={date_str}&type=ALL"
    )
    r = requests.get(url, headers=HEADERS, timeout=20)
    data = r.json()
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
            code  = row[ci].strip()
            name  = row[ni].strip()
            price = float(row[pi].replace(",", "") or 0)
            chg   = float(row[di].replace(",", "") or 0)
            if price > 0:
                chg_pct = chg / (price - chg) * 100 if (price - chg) != 0 else 0
                price_map[code] = {"name": name, "price": price, "chg": chg, "chg_pct": chg_pct}
    return price_map

def parse_int(s):
    try:
        return int(str(s).replace(",", ""))
    except:
        return 0

def build_stock_list(inst_df, price_map):
    stocks = []
    for _, row in inst_df.iterrows():
        code = str(row.get("證券代號", "")).strip()
        if len(code) != 4:
            continue
        pd_info = price_map.get(code)
        if not pd_info:
            continue

        name = pd_info["name"]
        price = pd_info["price"]
        chg_pct = pd_info["chg_pct"]

        foreign = (parse_int(row.get("外陸資買進股數", 0)) - parse_int(row.get("外陸資賣出股數", 0))) // 1000
        trust   = (parse_int(row.get("投信買進股數", 0))   - parse_int(row.get("投信賣出股數", 0)))   // 1000
        dealer  = (parse_int(row.get("自營商買進股數", 0)) - parse_int(row.get("自營商賣出股數", 0))) // 1000
        net3    = foreign + trust + dealer

        # 基本過濾：外資或投信至少其中一個有實質買超
        if foreign < 50 and trust < 10:
            continue
        if foreign <= 0 and trust <= 0:
            continue

        # 分類
        if chg_pct > 4 and foreign > 500:
            category = "burst"
        elif chg_pct < 3 and foreign > 0 and trust > 0 and price < 100:
            category = "launch"
        else:
            category = "trend"

        # 訊號
        signals = []
        if foreign > 0:   signals.append("外資買")
        if trust > 0:     signals.append("投信買")
        if foreign > 0 and trust > 0: signals.append("雙主力")
        if chg_pct > 3:  signals.append("強勢")
        if chg_pct > 6:  signals.append("噴出")

        score = (
            (3 if foreign > 1000 else 1 if foreign > 0 else 0) +
            (3 if trust  >  200  else 1 if trust  > 0 else 0) +
            (3 if foreign > 0 and trust > 0 else 0) +
            (2 if chg_pct > 3 else 0) +
            (2 if chg_pct > 6 else 0)
        )

        stocks.append({
            "code": code, "name": name, "price": price,
            "chg_pct": chg_pct, "foreign": foreign,
            "trust": trust, "dealer": dealer, "net3": net3,
            "category": category, "signals": signals, "score": score,
        })

    return sorted(stocks, key=lambda x: -x["score"])

def make_html(stocks, date_str):
    y, m, d = date_str[:4], date_str[4:6], date_str[6:]
    date_label = f"{y}/{m}/{d}"

    cat_label = {"launch": "🟢 低位啟動", "burst": "🔴 強勢噴出", "trend": "🟡 趨勢持續"}
    cat_desc  = {
        "launch": "股價低位、法人剛介入、均線翻揚——適合波段布局",
        "burst":  "近期強勢、外資大量買超、量價配合——適合追強短線",
        "trend":  "多頭趨勢持續、法人買進但未過熱——適合持有或輕追",
    }

    def color_num(n):
        if n > 0: return f'<span style="color:#c0392b">+{n:,}</span>'
        if n < 0: return f'<span style="color:#27ae60">{n:,}</span>'
        return "—"

    def color_pct(p):
        if p > 0: return f'<span style="color:#c0392b">+{p:.2f}%</span>'
        if p < 0: return f'<span style="color:#27ae60">{p:.2f}%</span>'
        return f"{p:.2f}%"

    sections = ""
    for cat in ["launch", "burst", "trend"]:
        group = [s for s in stocks if s["category"] == cat]
        if not group:
            continue
        rows = ""
        for s in group[:15]:
            sig_html = " ".join(
                f'<span style="font-size:11px;padding:2px 6px;border-radius:10px;background:#f0f0f0;color:#555">{sg}</span>'
                for sg in s["signals"]
            )
            rows += f"""
            <tr style="border-bottom:1px solid #f0f0f0">
              <td style="padding:10px 8px;font-weight:500">{s['code']}<br>
                <span style="font-size:12px;color:#888;font-weight:400">{s['name']}</span>
              </td>
              <td style="padding:10px 8px;text-align:right">{s['price']:.2f}<br>
                <span style="font-size:12px">{color_pct(s['chg_pct'])}</span>
              </td>
              <td style="padding:10px 8px;text-align:right">{color_num(s['foreign'])}</td>
              <td style="padding:10px 8px;text-align:right">{color_num(s['trust'])}</td>
              <td style="padding:10px 8px;text-align:right">{color_num(s['dealer'])}</td>
              <td style="padding:10px 8px">{sig_html}</td>
            </tr>"""

        sections += f"""
        <div style="margin-bottom:32px">
          <div style="font-size:16px;font-weight:600;margin-bottom:4px">{cat_label[cat]}
            <span style="font-size:12px;font-weight:400;color:#888;margin-left:8px">{len(group)} 檔</span>
          </div>
          <div style="font-size:12px;color:#888;margin-bottom:12px">{cat_desc[cat]}</div>
          <table style="width:100%;border-collapse:collapse;font-size:14px">
            <thead>
              <tr style="background:#f8f8f8;color:#555">
                <th style="padding:8px;text-align:left;font-weight:500">代號 / 名稱</th>
                <th style="padding:8px;text-align:right;font-weight:500">收盤 / 漲跌</th>
                <th style="padding:8px;text-align:right;font-weight:500">外資（張）</th>
                <th style="padding:8px;text-align:right;font-weight:500">投信（張）</th>
                <th style="padding:8px;text-align:right;font-weight:500">自營（張）</th>
                <th style="padding:8px;font-weight:500">訊號</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>"""

    total = len(stocks)
    html = f"""
<!DOCTYPE html>
<html lang="zh-Hant">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#fff;color:#222;margin:0;padding:0">
<div style="max-width:680px;margin:0 auto;padding:24px 16px">

  <div style="border-bottom:2px solid #222;padding-bottom:12px;margin-bottom:24px">
    <div style="font-size:20px;font-weight:700">📊 台股籌碼日報</div>
    <div style="font-size:13px;color:#888;margin-top:4px">{date_label} 盤後三大法人 · 共 {total} 檔入選</div>
  </div>

  {sections}

  <div style="border-top:1px solid #eee;padding-top:16px;font-size:12px;color:#aaa">
    資料來源：台灣證券交易所（TWSE）盤後公告 · 僅供參考，不構成投資建議
  </div>
</div>
</body></html>"""
    return html

def send_email(html_body, date_str, stock_count):
    y, m, d = date_str[:4], date_str[4:6], date_str[6:]
    subject = f"📊 台股籌碼日報 {y}/{m}/{d}｜{stock_count} 檔入選"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SENDER_EMAIL
    msg["To"]      = RECEIVER_EMAIL
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SENDER_EMAIL, GMAIL_APP_PWD)
        server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
    print(f"✅ 已寄出：{subject}")

def main():
    date_str = get_today_str()
    print(f"⏳ 抓取 {date_str} 資料...")

    inst_df, date_used = fetch_institutional(date_str)
    if inst_df is None:
        print("❌ 今日三大法人資料尚未公布，結束。")
        return

    time.sleep(1)
    price_map = fetch_price(date_used)
    stocks    = build_stock_list(inst_df, price_map)

    print(f"✅ 篩選出 {len(stocks)} 檔")
    html = make_html(stocks, date_used)
    send_email(html, date_used, len(stocks))

if __name__ == "__main__":
    main()
