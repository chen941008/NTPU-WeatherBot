# app.py
import os
import requests
import certifi
from flask import Flask, request
from dotenv import load_dotenv

# LINE Bot SDK v3
from linebot.v3.webhook import WebhookParser
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage,
)

# 讀 .env（本地會有；雲端 Render 即使沒有也不影響）
load_dotenv()

app = Flask(__name__)

CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CHANNEL_TOKEN  = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CWA_API_KEY    = os.getenv("CWA_API_KEY")  # 中央氣象署金鑰

configuration = Configuration(access_token=CHANNEL_TOKEN)
parser = WebhookParser(CHANNEL_SECRET)

# ---- 地名別名對照 ----
CITY_ALIASES = {
    "台北": "臺北市", "臺北": "臺北市", "北市": "臺北市",
    "新北": "新北市", "台中": "臺中市", "臺中": "臺中市",
    "台南": "臺南市", "臺南": "臺南市", "高雄": "高雄市",
    "桃園": "桃園市", "新竹": "新竹市", "基隆": "基隆市",
    "嘉義": "嘉義市", "宜蘭": "宜蘭縣", "花蓮": "花蓮縣",
    "台東": "臺東縣", "臺東": "臺東縣", "屏東": "屏東縣",
    "苗栗": "苗栗縣", "彰化": "彰化縣", "雲林": "雲林縣",
    "南投": "南投縣", "嘉義縣": "嘉義縣", "新竹縣": "新竹縣",
    "連江": "連江縣", "金門": "金門縣", "澎湖": "澎湖縣",
    "臺北市":"臺北市","新北市":"新北市","高雄市":"高雄市","桃園市":"桃園市",
    "臺中市":"臺中市","臺南市":"臺南市","基隆市":"基隆市","新竹市":"新竹市","嘉義市":"嘉義市",
}

def normalize_city(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "臺北市"
    return CITY_ALIASES.get(text, text)

# ---- CWA 36 小時預報（穩健 SSL 策略：默認驗證 → 失敗再降級；本地可環境變數強制跳過） ----
def get_weather_36h(location="臺北市") -> str:
    if not CWA_API_KEY:
        return "尚未設定 CWA_API_KEY，請先在環境變數加入中央氣象署金鑰。"

    url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"
    params = {"Authorization": CWA_API_KEY, "locationName": location}

    # 用 session 並關閉對系統環境變數的信任，避免被外部 CA 變數干擾
    s = requests.Session()
    s.trust_env = False

    force_insecure = bool(os.getenv("CWA_INSECURE"))  # 本地可設 1
    attempts = []

    if force_insecure:
        # 本地開發：直接不驗證，最穩（雲端不要設這個）
        attempts = [(False, False)]
    else:
        # 雲端/正式：先用 certifi 驗證；若遇到特定 SSL 錯再降級一次
        attempts = [(True, certifi.where()), (False, False)]

    last_err = None
    for do_verify, verify_arg in attempts:
        try:
            r = s.get(url, params=params, timeout=12, verify=verify_arg)
            r.raise_for_status()
            data = r.json()
            locs = data.get("records", {}).get("location", [])
            if not locs:
                return f"查不到「{location}」的天氣資訊。"

            loc = locs[0]
            wx   = loc["weatherElement"][0]["time"][0]["parameter"]["parameterName"]  # 天氣現象
            pop  = loc["weatherElement"][1]["time"][0]["parameter"]["parameterName"]  # 降雨機率
            minT = loc["weatherElement"][2]["time"][0]["parameter"]["parameterName"]
            ci   = loc["weatherElement"][3]["time"][0]["parameter"]["parameterName"]  # 舒適度
            maxT = loc["weatherElement"][4]["time"][0]["parameter"]["parameterName"]

            return (f"{location} 今明短期預報：\n"
                    f"・天氣：{wx}\n"
                    f"・降雨機率：{pop}%\n"
                    f"・溫度：{minT}°C ~ {maxT}°C\n"
                    f"・體感/舒適度：{ci}")
        except requests.exceptions.SSLError as e:
            app.logger.warning(f"CWA SSL verify failed (verify={do_verify}). fallback if possible. err={e}")
            last_err = e
            continue
        except requests.exceptions.RequestException as e:
            app.logger.error(f"CWA request error: {e}")
            return "氣象資料連線失敗，稍後再試。"
        except Exception as e:
            app.logger.error(f"CWA parse error: {e}")
            return "天氣資料解析失敗，稍後再試。"

    app.logger.error(f"CWA SSL still failing after fallback: {last_err}")
    return "氣象資料連線失敗，稍後再試。"

# ✅ 健康檢查（Render 會定期打）
@app.get("/health")
def health():
    return "OK"

@app.route("/webhook", methods=['POST'])
def webhook():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True) or ""

    # 讓 LINE 後台 Verify / 健康檢查通過（無簽章或空 body 直接 200）
    if not signature or not body.strip():
        return "OK"

    try:
        events = parser.parse(body, signature)
    except Exception as e:
        app.logger.warning(f"parse error: {e}")
        return "OK"  # 避免 Verify 失敗

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        for event in events:
            if event.type == "message" and getattr(event, "message", None) and event.message.type == "text":
                text = (event.message.text or "").strip()
                reply_token = event.reply_token

                if text.startswith("天氣"):
                    city = text.replace("天氣", "", 1).strip()
                    reply = get_weather_36h(normalize_city(city))
                else:
                    reply = (
                        f"Hello 👋 你說：{text}\n\n你也可以試試：\n"
                        f"・天氣（預設臺北市）\n・天氣 台中\n・天氣 高雄"
                    )

                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=reply_token,
                        messages=[TextMessage(text=reply)]
                    )
                )
    return "OK"


if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=False)
