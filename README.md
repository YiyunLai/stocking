# 台股籌碼日報 — 自動每日 Email

每天收盤後自動抓取三大法人資料，分類篩選後寄到你的 Gmail。

## 設定步驟（10 分鐘內完成）

### 1. Fork 這個 repo

點右上角 **Fork**，複製到你自己的 GitHub 帳號。

---

### 2. 開啟 Gmail 應用程式密碼

> 注意：這不是你的 Gmail 登入密碼，是專門給程式用的 16 碼密碼。

1. 前往 [myaccount.google.com/security](https://myaccount.google.com/security)
2. 確認已開啟「兩步驟驗證」
3. 搜尋「應用程式密碼」→ 新增一個，名稱隨便填（例如「股票日報」）
4. 複製那 16 碼密碼（只顯示一次）

---

### 3. 在 GitHub 設定 Secrets

在你 fork 的 repo 頁面：
**Settings → Secrets and variables → Actions → New repository secret**

新增以下三個：

| Secret 名稱 | 填入內容 |
|---|---|
| `GMAIL_USER` | 你的 Gmail（`xxx@gmail.com`） |
| `GMAIL_TO` | 收信的 Email（可以跟上面一樣） |
| `GMAIL_APP_PWD` | 剛才複製的 16 碼應用程式密碼 |

---

### 4. 啟用 Actions

GitHub 對 fork 的 repo 預設關閉 Actions：
**Actions 頁籤 → 點「I understand my workflows, go ahead and enable them」**

---

### 5. 測試

**Actions → 台股籌碼日報 → Run workflow**

幾秒後應該會收到一封信。

---

## 執行時間

預設是每天**台灣時間 17:30**（週一到週五），對應盤後資料通常公布的時間。
如果想調整，編輯 `.github/workflows/daily.yml` 裡的 `cron`。

## 選股邏輯

| 類型 | 條件 |
|---|---|
| 🟢 低位啟動 | 股價 < 100、外資+投信同買、漲幅 < 3% |
| 🔴 強勢噴出 | 外資買超 > 500 張、當日漲幅 > 4% |
| 🟡 趨勢持續 | 外資或投信買超，不符合上兩類 |

資料來源：台灣證券交易所（TWSE）公開 API，免費、不需登入。
