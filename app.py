import os
import requests
import certifi
import sqlite3
import datetime
from flask import Flask, request
from dotenv import load_dotenv

# ⭐️ 新增：Google AI (Gemini)
import google.generativeai as genai

# LINE Bot SDK v3
from linebot.v3.webhook import WebhookParser
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage,
)

load_dotenv()
app = Flask(__name__)

# ---- 1. 金鑰與設定 ----
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CHANNEL_TOKEN  = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CWA_API_KEY    = os.getenv("CWA_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY") # ⭐️ 新增：Gemini 金鑰
DB_NAME = "bot.db"

# LINE Bot 初始化
configuration = Configuration(access_token=CHANNEL_TOKEN)
parser = WebhookParser(CHANNEL_SECRET)

# Gemini AI 模型初始化
gemini_model = None
if GOOGLE_API_KEY:
    try:
        genai.configure(api_key=GOOGLE_API_KEY)
        gemini_model = genai.GenerativeModel('gemini-2.5-flash')
        app.logger.info("Google Gemini model initialized.")
    except Exception as e:
        app.logger.error(f"Error initializing Gemini: {e}")
else:
    app.logger.warning("GOOGLE_API_KEY not set. AI functions will be disabled.")


# ---- 2. 資料庫 (SQLite) 相關功能 ----

def init_db():
    """
    初始化資料庫，建立資料表並新增 home_city 欄位 (如果不存在)
    """
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            # 建立使用者偏好表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    line_user_id TEXT PRIMARY KEY,
                    preferences TEXT,
                    last_updated TIMESTAMP
                )
            """)
            
            # ⭐️ 檢查並新增 home_city 欄位 (安全的新增)
            try:
                cursor.execute("ALTER TABLE users ADD COLUMN home_city TEXT")
                conn.commit()
                app.logger.info("Added 'home_city' column to 'users' table.")
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e):
                    app.logger.info("'home_city' column already exists, skipping.")
                else:
                    raise # 拋出其他 SQL 錯誤
            
            # 建立聊天紀錄表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_history (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    line_user_id TEXT,
                    role TEXT,
                    content TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            app.logger.info("Database initialized (users, chat_history tables).")
    except Exception as e:
        app.logger.error(f"Error initializing database: {e}")

def save_user_preference(user_id: str, new_pref: str) -> str:
    """
    ⭐️ 儲存或更新使用者的「固定偏好」 (來自 "記住我" 指令)
    ⭐️ 新邏輯：用 "換行" 來附加新偏好，而不是覆蓋
    """
    if not user_id: return "無法識別使用者 ID。"
    
    # 1. 先取得舊的偏好
    current_prefs = get_user_preference(user_id)
    
    # 2. 組合新的偏好字串
    final_prefs = ""
    if current_prefs == "尚未設定" or current_prefs == "讀取偏好時發生錯誤":
        # 如果是空的或錯誤，就用新的偏好
        final_prefs = new_pref
    else:
        # 否則，用換行符號附加
        final_prefs = current_prefs + "\n" + new_pref
        
    # 3. 儲存回資料庫
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO users (line_user_id, preferences, last_updated)
                VALUES (?, ?, ?)
                ON CONFLICT(line_user_id) DO UPDATE SET
                    preferences = excluded.preferences,
                    last_updated = excluded.last_updated
            """, (user_id, final_prefs, datetime.datetime.now())) # 儲存組合後的 final_prefs
            conn.commit()
        app.logger.info(f"Appended preference for user {user_id}")
        return f"我記住了：「{new_pref}」\n\n（點選「我的偏好」查看全部）"
    except Exception as e:
        app.logger.error(f"Error saving preference for user {user_id}: {e}")
        return "抱歉，儲存喜好時發生錯誤。"

def get_user_preference(user_id: str) -> str:
    """
    從資料庫讀取使用者的「固定偏好」
    """
    if not user_id: return ""
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT preferences FROM users WHERE line_user_id = ?", (user_id,))
            row = cursor.fetchone()
            # ⭐️ 如果 row[0] (preferences) 有值，就回傳；否則回傳 "尚未設定"
            return row[0] if row and row[0] else "尚未設定"
    except Exception as e:
        app.logger.error(f"Error getting preference for user {user_id}: {e}")
        return "讀取偏好時發生錯誤"

def clear_user_preference(user_id: str) -> str:
    """
    ⭐️ 新增：清除使用者的「固定偏好」
    """
    if not user_id: return "無法識別使用者 ID。"
    
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            # ⭐️ 將 preferences 欄位設為 NULL (空)
            cursor.execute("""
                UPDATE users
                SET preferences = NULL, last_updated = ?
                WHERE line_user_id = ?
            """, (datetime.datetime.now(), user_id))
            conn.commit()
        app.logger.info(f"Cleared preferences for user {user_id}")
        return "我已經忘記你所有的偏好了。"
    except Exception as e:
        app.logger.error(f"Error clearing preference for user {user_id}: {e}")
        return "抱歉，清除偏好時發生錯誤。"

def add_chat_history(user_id: str, role: str, content: str):
    """
    新增一筆對話紀錄到資料庫
    role 應為 'user' (使用者) 或 'bot' (AI)
    """
    if not user_id or not content: return
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO chat_history (line_user_id, role, content, timestamp)
                VALUES (?, ?, ?, ?)
            """, (user_id, role, content, datetime.datetime.now()))
            conn.commit()
    except Exception as e:
        app.logger.error(f"Error adding chat history for user {user_id}: {e}")

def get_chat_history(user_id: str, limit: int = 10) -> list:
    """
    取得使用者最近的 N 筆聊天紀錄
    """
    if not user_id: return []
    try:
        with sqlite3.connect(DB_NAME) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT role, content FROM chat_history
                WHERE line_user_id = ? ORDER BY timestamp DESC LIMIT ?
            """, (user_id, limit))
            rows = cursor.fetchall()
            history = [(row['role'], row['content']) for row in rows]
            return list(reversed(history)) 
    except Exception as e:
        app.logger.error(f"Error getting chat history for user {user_id}: {e}")
        return []

# ⭐️ ---- 2.1 ⭐️ 新增：地區設定相關函式 ----

# (你的 CITY_ALIASES 和 normalize_city 函式移到這裡，因為多處需要)
CITY_ALIASES = {
    "台北": "臺北市", "臺北": "臺北市", "北市": "臺北市","臺北市":"臺北市", "台北市":"臺北市",
    "新北": "新北市", "新北市":"新北市",
    "台中": "臺中市", "臺中": "臺中市", "臺中市":"臺中市", "台中市":"臺中市",
    "台南": "臺南市", "臺南": "臺南市", "臺南市":"臺南市", "台南市":"臺南市",
    "高雄": "高雄市", "高雄市":"高雄市",
    "桃園": "桃園市", "桃園市":"桃園市",
    "新竹": "新竹市", "新竹市":"新竹市",
    "基隆": "基隆市", "基隆市":"基隆市",
    "嘉義": "嘉義市", "嘉義市":"嘉義市",
    "宜蘭": "宜蘭縣", "宜蘭縣": "宜蘭縣",
    "花蓮": "花蓮縣", "花蓮縣": "花蓮縣",
    "台東": "臺東縣", "臺東": "臺東縣", "臺東縣": "臺東縣", "台東縣": "臺東縣",
    "屏東": "屏東縣", "屏東縣": "屏東縣",
    "苗栗": "苗栗縣", "苗栗縣": "苗栗縣",
    "彰化": "彰化縣", "彰化縣": "彰化縣",
    "雲林": "雲林縣", "雲林縣": "雲林縣",
    "南投": "南投縣", "南投縣": "南投縣",
    "嘉義縣": "嘉義縣", "嘉義": "嘉義縣",
    "新竹縣": "新竹縣", 
    "連江": "連江縣", "連江縣": "連江縣",
    "金門": "金門縣", "金門縣": "金門縣",
    "澎湖": "澎湖縣", "澎湖縣": "澎湖縣",
}

def normalize_city(text: str) -> str:
    """
    正規化城市名稱，並檢查是否存在於別名列表中
    """
    text = (text or "").strip()
    if not text:
        return "臺北市" # 保留預設
    
    normalized = CITY_ALIASES.get(text)
    if normalized:
        return normalized
    
    # 如果不在別名中，檢查是否為標準名稱 (例如 "臺北市")
    if text in CITY_ALIASES.values():
        return text
        
    return None # 回傳 None 代表查無此地

def save_user_home_city(user_id: str, city_name: str) -> str:
    """
    儲存或更新使用者的「預設地區」
    """
    if not user_id:
        return "無法識別使用者 ID。"
    
    # 驗證地區
    normalized_city = normalize_city(city_name)
    if not normalized_city:
        return f"抱歉，我不認識「{city_name}」。我目前只支援臺灣的縣市。"
    
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            # ⭐️ 把正規化後的城市存入 home_city 欄位
            cursor.execute("""
                INSERT INTO users (line_user_id, home_city, last_updated)
                VALUES (?, ?, ?)
                ON CONFLICT(line_user_id) DO UPDATE SET
                    home_city = excluded.home_city,
                    last_updated = excluded.last_updated
            """, (user_id, normalized_city, datetime.datetime.now()))
            conn.commit()
        app.logger.info(f"Saved home city for user {user_id}: {normalized_city}")
        return f"您的預設地區已設定為：「{normalized_city}」"
    except Exception as e:
        app.logger.error(f"Error saving home city for user {user_id}: {e}")
        return "抱歉，儲存地區時發生錯誤。"

def get_user_home_city(user_id: str) -> str:
    """
    從資料庫讀取使用者的「預設地區」，若無則回傳 '臺北市'
    """
    if not user_id:
        return "臺北市" # 預設
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT home_city FROM users WHERE line_user_id = ?", (user_id,))
            row = cursor.fetchone()
            # ⭐️ 如果 row[0] (home_city) 有值，就回傳；否則回傳預設
            return row[0] if row and row[0] else "臺北市"
    except Exception as e:
        app.logger.error(f"Error getting home city for user {user_id}: {e}")
        return "臺北市" # 發生錯誤時也回傳預設


# ---- 3. 既有的天氣功能 (CWA API) ----
def get_weather_36h(location="臺北市") -> dict:
    if not CWA_API_KEY:
        return {"error": "尚未設定 CWA_API_KEY..."}

    url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"
    params = {"Authorization": CWA_API_KEY, "locationName": location}
    s = requests.Session()
    s.trust_env = False
    
    # (SSL 驗證邏輯...)
    force_insecure = bool(os.getenv("CWA_INSECURE"))
    attempts = []
    if force_insecure:
        attempts = [(False, False)]
    else:
        attempts = [(True, certifi.where()), (False, False)]

    last_err = None
    for do_verify, verify_arg in attempts:
        try:
            r = s.get(url, params=params, timeout=12, verify=verify_arg)
            r.raise_for_status()
            data = r.json()
            locs = data.get("records", {}).get("location", [])
            if not locs:
                # ⭐️ 如果 API 查不到 (例如 normalize_city 漏了)，給出明確錯誤
                return {"error": f"查不到「{location}」的天氣資訊，請確認是否為臺灣的縣市。"}
            
            loc = locs[0]
            wx   = loc["weatherElement"][0]["time"][0]["parameter"]["parameterName"]
            pop  = loc["weatherElement"][1]["time"][0]["parameter"]["parameterName"]
            minT = loc["weatherElement"][2]["time"][0]["parameter"]["parameterName"]
            ci   = loc["weatherElement"][3]["time"][0]["parameter"]["parameterName"]
            maxT = loc["weatherElement"][4]["time"][0]["parameter"]["parameterName"]
            
            return {
                "location": location, "wx": wx, "pop": pop, "minT": minT, "maxT": maxT, "ci": ci,
                "full_text": (f"{location} 今明短期預報：\n"
                              f"・天氣：{wx}\n"
                              f"・降雨機率：{pop}%\n"
                              f"・溫度：{minT}°C ~ {maxT}°C\n"
                              f"・體感/舒適度：{ci}")
            }
        except requests.exceptions.SSLError as e:
            app.logger.warning(f"CWA SSL verify failed (verify={do_verify}). err={e}")
            last_err = e
            continue
        except requests.exceptions.RequestException as e:
            app.logger.error(f"CWA request error: {e}")
            return {"error": "氣象資料連線失敗，稍後再試。"}
        except Exception as e:
            app.logger.error(f"CWA parse error: {e}")
            return {"error": "天氣資料解析失敗，稍後再試。"}

    app.logger.error(f"CWA SSL still failing after fallback: {last_err}")
    return {"error": "氣象資料連線失敗，稍後再試。"}


# ---- 4. AI 穿搭建議功能 ----
def get_clothing_advice(user_id: str, location: str) -> str:
    if not gemini_model:
        return "抱歉，AI 建議功能目前無法使用 (Gemini 未啟動)。"
    
    app.logger.info(f"Generating clothing advice for {user_id} in {location}...")
    
    try:
        # 1. 撈天氣 (API)
        weather_data = get_weather_36h(location)
        if "error" in weather_data:
            return f"抱歉，我拿不到「{location}」的天氣資訊，無法給您建議。"

        # 2. 撈偏好 (SQLite)
        user_prefs = get_user_preference(user_id)

        # 3. 撈聊天紀錄 (SQLite)
        history_rows = get_chat_history(user_id, limit=10)

        # 4. 組合 Prompt (指令) 送給 AI
        prompt_parts = [
            "你是「生活智慧管家」，一個專業且體貼的AI助理。",
            "你的任務是根據以下所有資訊，給予一個簡潔、體貼、個人化的「穿搭建議」。",
            f"\n--- 1. 即時天氣資訊 ({weather_data['location']}) ---",
            weather_data["full_text"],
            
            "\n--- 2. 使用者「固定」穿搭偏好 (來自 '記住我' 指令) ---",
            user_prefs,
            
            "\n--- 3. 使用者「最近」聊天紀錄 (AI 會從中學習隱含的偏好) ---"
        ]
        
        if history_rows:
            for role, content in history_rows:
                role_text = "使用者" if role == "user" else "你(AI)"
                prompt_parts.append(f"{role_text}: {content}")
        else:
            prompt_parts.append("尚無聊天紀錄")
            
        prompt_parts.append("\n--- 你的建議 ---")
        prompt_parts.append(f"請根據 {weather_data['location']} 的天氣({weather_data['minT']}~{weather_data['maxT']}度，{weather_data['wx']})，以及使用者的偏好和聊天紀錄，直接開始提供建議：")

        final_prompt = "\n".join(prompt_parts)
        
        response = gemini_model.generate_content(final_prompt)
        return response.text

    except Exception as e:
        app.logger.error(f"Error generating clothing advice: {e}")
        return "抱歉，AI 在思考建議時發生錯誤，請稍後再試。"


# ---- 5. Flask Webhook 路由 ----

@app.get("/health")
def health():
    return "OK"

@app.route("/webhook", methods=['POST'])
def webhook():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True) or "" # 修正了之前的 as_text.True 錯誤

    if not signature or not body.strip():
        return "OK"

    try:
        events = parser.parse(body, signature)
    except Exception as e:
        app.logger.warning(f"parse error: {e}")
        return "OK"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        for event in events:
            if event.type == "message" and getattr(event, "message", None) and event.message.type == "text":
                
                text = (event.message.text or "").strip()
                reply_token = event.reply_token
                user_id = ""
                
                if event.source and event.source.type == "user":
                    user_id = event.source.user_id
                
                if not user_id:
                    continue 

                add_chat_history(user_id, "user", text)
                reply = "" 

                # ⭐️⭐️ 關鍵：新的指令路由 ⭐️⭐️
                
                if text.startswith("天氣"):
                    # 1. 天氣功能
                    city_text = text.replace("天氣", "", 1).strip()
                    city_norm = ""
                    reply_prefix = ""
                    
                    if not city_text:
                        # ⭐️ 如果只打「天氣」，使用預設地區
                        city_norm = get_user_home_city(user_id)
                        reply_prefix = f"（您設定的地區：{city_norm}）\n\n" # 加上提示
                    else:
                        # ⭐️ 否則，使用指定的地區
                        city_norm = normalize_city(city_text)
                    
                    if not city_norm:
                        reply = f"抱歉，我不認識「{city_text}」。我目前只支援臺灣的縣市。"
                    else:
                        weather_data = get_weather_36h(city_norm)
                        if "error" in weather_data:
                            reply = weather_data["error"]
                        else:
                            reply = reply_prefix + weather_data["full_text"]

                elif text.startswith("記住我"):
                    # 2. 儲存偏好
                    prefs = text.replace("記住我", "", 1).strip()
                    if not prefs:
                        reply = "請告訴我你的喜好，例如：「記住我 穿搭偏好：喜歡穿短褲」"
                    else:
                        # ⭐️ 呼叫更新後的 "附加" 函式
                        reply = save_user_preference(user_id, prefs)
                
                elif text == "我的偏好":
                    # 3. ⭐️ 新增：查看偏好
                    prefs = get_user_preference(user_id)
                    reply = f"您目前的偏好設定：\n\n{prefs}"

                elif text == "忘記我":
                    # 4. ⭐️ 新增：清除偏好
                    reply = clear_user_preference(user_id)

                elif text.startswith("設定地區"):
                    # 5. 設定地區
                    city_text = text.replace("設定地區", "", 1).strip()
                    if not city_text:
                        reply = "請輸入地區，例如：「設定地區 新北市」"
                    else:
                        reply = save_user_home_city(user_id, city_text)

                elif text == "今天穿什麼" or text == "穿搭建議" or text == "給我穿搭建議":
                    # 6. AI 穿搭建議
                    city = get_user_home_city(user_id)
                    reply = get_clothing_advice(user_id, city)

                else:
                    # 7. 預設回覆 (⭐️ 更新提示文字)
                    reply = (
                        f"Hello 👋 你說：{text}\n\n"
                        f"我現在支援：\n"
                        f"・天氣 (預設/指定地區)\n"
                        f"・今天穿什麼 (AI穿搭建議)\n"
                        f"・設定地區 [你的縣市]\n"
                        f"・記住我 [你的偏好] (可多次新增)\n"
                        f"・我的偏好 (查看)\n"
                        f"・忘記我 (清除偏好)"
                    )
                
                if reply:
                    add_chat_history(user_id, "bot", reply)
                else:
                    reply = "抱歉，我不知道怎麼回應。"

                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=reply_token,
                        messages=[TextMessage(text=reply)]
                    )
                )
    return "OK"


if __name__ == "__main__":
    init_db() # ⭐️ 啟動時呼叫 (會自動更新資料表)
    port = int(os.getenv("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=False)