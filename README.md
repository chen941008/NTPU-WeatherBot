# NTPU-WeatherBot

一個基於 **LINE Messaging API + Flask** 的天氣查詢聊天機器人。  
目前部署於 [Render](https://dashboard.render.com/)，可 24/7 在線（⚠️ 免費方案會在閒置後休眠，重新喚醒需額外等待約 50 秒）。

---

## 🚀 部署到 Render

### 0. 需求
- Python 3.10+
- 一個 LINE Official Account 與 **Messaging API Channel**
- Render 帳號（可用 GitHub 登入）

---

### 1. 推送專案到 GitHub
確保專案包含以下檔案：
```
├── app.py
├── requirements.txt
├── .gitignore
└── README.md
```

`.env` 請不要上傳，敏感資訊改用 Render 的 Environment Variables。

---

### 2. 在 Render 建立服務
1. 登入 [Render Dashboard](https://dashboard.render.com/)，點選 **New → Web Service**  
2. 連結你的 GitHub Repo  
3. 基本設定：
   - **Name**: `NTPU-WeatherBot`
   - **Language**: Python 3
   - **Branch**: main
   - **Region**: Oregon (US West)

---

### 3. Build & Start Command
在 Render 建立服務時，請設定：

- **Build Command**
  ```bash
  pip install -r requirements.txt
  ```

- **Start Command**
  ```bash
  gunicorn app:app --workers 1 --threads 8 --timeout 30
  ```

---

### 4. 設定環境變數
進入 Render → 你的服務 → **Environment**，新增：

```
LINE_CHANNEL_SECRET=你的Channel secret
LINE_CHANNEL_ACCESS_TOKEN=你的Channel access token
```

（這些值可在 LINE Developers Console → Messaging API → Channel settings 找到）

---

### 5. Health Check
Render 預設會打 `/healthz` 來確認服務狀態，請在 `app.py` 加入：

```python
@app.get("/healthz")
def health():
    return "OK"
```

---

### 6. 設定 LINE Webhook
1. 回到 **LINE Developers Console** → Messaging API  
2. Webhook URL 填入 Render 分配的網址，例如：
   ```
   https://ntpu-weatherbot.onrender.com/webhook
   ```
3. 點 **Verify** → 成功會回傳 200  
4. 開啟 **Use webhook: Enabled**  
5. 關閉「自動回應訊息」「歡迎訊息」避免重複回覆  

---

### 7. 測試
- 在 LINE 加好友你的官方帳號  
- 傳送訊息 → Render Logs 應該會出現 `/webhook` 紀錄  
- 你會收到機器人的回覆 🎉  

---

## ⚠️ 注意事項
- **Free Plan 限制**：若超過 15 分鐘沒有請求，Render 免費服務會休眠，第一次再喚醒時需要等待 30–60 秒。  
- **長期穩定運行**：若要 24/7 不間斷，建議升級付費方案。  
- **安全性**：不要把 `.env` 上傳到 GitHub，敏感資訊請放 Render Environment Variables。  

---

## 📄 License
MIT
