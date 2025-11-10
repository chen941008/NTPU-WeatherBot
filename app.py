import os
import requests
import certifi
# import sqlite3  # ⭐️ 移除：不再使用 sqlite3
import datetime
from flask import Flask, request
from dotenv import load_dotenv

# ⭐️ 新增：Flask-SQLAlchemy
from flask_sqlalchemy import SQLAlchemy

# ⭐️ 新增：Google AI (Gemini)
import google.generativeai as genai

# LINE Bot SDK v3
from linebot.v3.webhook import WebhookParser
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage,
    QuickReply, QuickReplyItem, MessageAction  # ✅ 修正：使用 QuickReplyItem
)

load_dotenv()
app = Flask(__name__)

# ---- 1. 金鑰與設定 ----
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CHANNEL_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CWA_API_KEY = os.getenv("CWA_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
# DB_NAME = "bot.db" # ⭐️ 移除：不再需要

# ⭐️ ---- 1.1 ⭐️ 新增：SQLAlchemy 資料庫設定 ----
# 這會自動讀取你在 Render 上設定的 DATABASE_URL 環境變數
database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith("postgres://"):
    # Render 的 URL 是 'postgres://' 開頭，SQLAlchemy 1.4+ 需要 'postgresql://'
    database_url = database_url.replace("postgres://", "postgresql://", 1)

# 如果在本地執行 (沒有 DATABASE_URL)，則使用一個本地的 sqlite 檔案 (方便測試)
if not database_url:
    app.logger.warning("DATABASE_URL not set, using local sqlite.db for development.")
    # 注意：本地測試用的檔案會叫做 local_bot.db
    database_url = "sqlite:///local_bot.db"

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
# -----------------------------------------------


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


# ⭐️ ---- 2. ⭐️ 新增：SQLAlchemy 資料庫模型 (Models) ----
# 這會取代你原本的 CREATE TABLE
class User(db.Model):
    __tablename__ = 'users'
    # 欄位定義
    line_user_id = db.Column(db.String, primary_key=True)
    preferences = db.Column(db.Text, nullable=True)
    last_updated = db.Column(db.DateTime, onupdate=datetime.datetime.now)
    home_city = db.Column(db.String, nullable=True)

class ChatHistory(db.Model):
    __tablename__ = 'chat_history'
    # 欄位定義
    message_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    line_user_id = db.Column(db.String, index=True) # ⭐️ 加上 index 查詢會更快
    role = db.Column(db.String)
    content = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.now)


# ⭐️⭐️⭐️ ↓↓ 終極修正：Gunicorn 啟動時自動建立資料表 ↓↓ ⭐️⭐️⭐️
# 這樣 Gunicorn 啟動時就會執行，解決 'UndefinedTable' 錯誤
try:
    with app.app_context():
        db.create_all()
    app.logger.info("SQLAlchemy tables checked/created successfully.")
except Exception as e:
    app.logger.error(f"Error creating SQLAlchemy tables on startup: {e}")
# ⭐️⭐️⭐️ ↑↑ 終極修正 ↑↑ ⭐️⭐️⭐️


# ⭐️ ---- 2.1 ⭐️ 資料庫 (SQLAlchemy) 相關功能 ----
# 所有的函式都重寫了，不再使用 sqlite3

def save_user_preference(user_id: str, new_pref: str) -> str:
    """
    ⭐️ 儲存或更新使用者的「固定偏好」 (使用 SQLAlchemy)
    """
    if not user_id: return "無法識別使用者 ID。"
    
    try:
        # 1. 先取得使用者物件 (如果不存在，等等會建立)
        # ⭐️ db.session.get() 是 SQLAlchemy 取代 SELECT ... WHERE id=? 的方法
        user = db.session.get(User, user_id)
        
        final_prefs = ""
        if not user:
            # ⭐️ 如果使用者不存在，建立一個新的
            final_prefs = new_pref
            user = User(
                line_user_id=user_id, 
                preferences=final_prefs, 
                last_updated=datetime.datetime.now()
            )
            db.session.add(user) # ⭐️ 加入到 session 準備新增
        else:
            # ⭐️ 如果使用者存在，附加偏好
            current_prefs = user.preferences
            if not current_prefs:
                final_prefs = new_pref
            else:
                final_prefs = current_prefs + "\n" + new_pref
            
            user.preferences = final_prefs # ⭐️ 更新物件
            user.last_updated = datetime.datetime.now()
            
        db.session.commit() # ⭐️ 執行資料庫交易
        
        app.logger.info(f"Appended preference for user {user_id}")
        return f"我記住了：「{new_pref}」\n\n（點選「我的偏好」查看全部）"
        
    except Exception as e:
        db.session.rollback() # ⭐️ 發生錯誤時回滾
        app.logger.error(f"Error saving preference for user {user_id}: {e}")
        return "抱歉，儲存喜好時發生錯誤。"

def get_user_preference(user_id: str) -> str:
    """
    從資料庫讀取使用者的「固定偏好」 (使用 SQLAlchemy)
    """
    if not user_id: return ""
    try:
        # ⭐️ 透過 Primary Key (user_id) 取得使用者
        user = db.session.get(User, user_id)
        
        # ⭐️ 如果 user 存在且 preferences 有值
        return user.preferences if user and user.preferences else "尚未設定"
        
    except Exception as e:
        app.logger.error(f"Error getting preference for user {user_id}: {e}")
        return "讀取偏好時發生錯誤"

def clear_user_preference(user_id: str) -> str:
    """
    ⭐️ 清除使用者的「固定偏好」 (使用 SQLAlchemy)
    """
    if not user_id: return "無法識別使用者 ID。"
    
    try:
        user = db.session.get(User, user_id)
        
        if user:
            user.preferences = None # ⭐️ 設為 None (即資料庫中的 NULL)
            user.last_updated = datetime.datetime.now()
            db.session.commit() # ⭐️ 儲存變更
            
        app.logger.info(f"Cleared preferences for user {user_id}")
        return "我已經忘記你所有的偏好了。"
        
    except Exception as e:
        db.session.rollback() # ⭐️ 回滾
        app.logger.error(f"Error clearing preference for user {user_id}: {e}")
        return "抱歉，清除偏好時發生錯誤。"

def add_chat_history(user_id: str, role: str, content: str):
    """
    新增一筆對話紀錄到資料庫 (使用 SQLAlchemy)
    """
    if not user_id or not content: return
    try:
        # ⭐️ 建立一個新的 ChatHistory 物件
        new_chat = ChatHistory(
            line_user_id=user_id,
            role=role,
            content=content,
            timestamp=datetime.datetime.now()
        )
        db.session.add(new_chat) # ⭐️ 加入
        db.session.commit() # ⭐️ 儲存
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error adding chat history for user {user_id}: {e}")

def get_chat_history(user_id: str, limit: int = 10) -> list:
    """
    取得使用者最近的 N 筆聊天紀錄 (使用 SQLAlchemy 2.0 語法)
    """
    if not user_id: return []
    try:
        # ⭐️ 這是 SQLAlchemy 2.0 的查詢語法
        # SELECT * FROM chat_history WHERE line_user_id=? ORDER BY timestamp DESC LIMIT ?
        stmt = (
            db.select(ChatHistory)
            .filter_by(line_user_id=user_id)
            .order_by(ChatHistory.timestamp.desc())
            .limit(limit)
        )
        # .all() 會回傳一個 ChatHistory 物件的 list
        rows = db.session.scalars(stmt).all()
        
        history = [(row.role, row.content) for row in rows]
        return list(reversed(history)) # 保持你原本的 (反轉) 邏輯
        
    except Exception as e:
        app.logger.error(f"Error getting chat history for user {user_id}: {e}")
        return []

# ---- 2.2 ⭐️ 地區設定相關函式 (使用 SQLAlchemy) ----

# (CITY_ALIASES 和 normalize_city 函式不變，因為它們與資料庫無關)
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
    text = (text or "").strip()
    if not text:
        return "臺北市"
    normalized = CITY_ALIASES.get(text)
    if normalized:
        return normalized
    if text in CITY_ALIASES.values():
        return text
    return None

def save_user_home_city(user_id: str, city_name: str) -> str:
    """
    儲存或更新使用者的「預設地區」 (使用 SQLAlchemy)
    """
    if not user_id:
        return "無法識別使用者 ID。"
    
    normalized_city = normalize_city(city_name)
    if not normalized_city:
        return f"抱歉，我不認識「{city_name}」。我目前只支援臺灣的縣市。"
    
    try:
        user = db.session.get(User, user_id)
        
        if not user:
            # ⭐️ 建立新使用者，並設定 home_city
            user = User(
                line_user_id=user_id, 
                home_city=normalized_city, 
                last_updated=datetime.datetime.now()
            )
            db.session.add(user)
        else:
            # ⭐️ 更新現有使用者的 home_city
            user.home_city = normalized_city
            user.last_updated = datetime.datetime.now()
            
        db.session.commit() # ⭐️ 儲存
        
        app.logger.info(f"Saved home city for user {user_id}: {normalized_city}")
        return f"您的預設地區已設定為：「{normalized_city}」"
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error saving home city for user {user_id}: {e}")
        return "抱歉，儲存地區時發生錯誤。"

def get_user_home_city(user_id: str) -> str:
    """
    從資料庫讀取使用者的「預設地區」 (使用 SQLAlchemy)
    """
    if not user_id:
        return "臺北市" # 預設
    try:
        user = db.session.get(User, user_id)
        
        # ⭐️ 如果 user 存在且 home_city 有值
        return user.home_city if user and user.home_city else "臺北市"
        
    except Exception as e:
        app.logger.error(f"Error getting home city for user {user_id}: {e}")
        return "臺北市" # 發生錯誤時也回傳預設


# ---- 3. 既有的天氣功能 (CWA API) ----
# (此區塊完全不變，因為它不碰資料庫)
def get_weather_36h(location="臺北市") -> dict:
    if not CWA_API_KEY:
        return {"error": "尚未設定 CWA_API_KEY..."}

    url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"
    params = {"Authorization": CWA_API_KEY, "locationName": location}
    s = requests.Session()
    s.trust_env = False
    
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
                return {"error": f"查不到「{location}」的天氣資訊，請確認是否為臺灣的縣市。"}
            
            loc = locs[0]
            wx  = loc["weatherElement"][0]["time"][0]["parameter"]["parameterName"]
            pop = loc["weatherElement"][1]["time"][0]["parameter"]["parameterName"]
            minT = loc["weatherElement"][2]["time"][0]["parameter"]["parameterName"]
            ci  = loc["weatherElement"][3]["time"][0]["parameter"]["parameterName"]
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
            app.logger.error(f"CWAs parse error: {e}")
            return {"error": "天氣資料解析失敗，稍後再試。"}

    app.logger.error(f"CWA SSL still failing after fallback: {last_err}")
    return {"error": "氣象資料連線失敗，稍後再試。"}


# ---- 4. AI 穿搭建議功能 ----
# (此區塊完全不變，因為它呼叫的是 2.1 區塊的函式)
def get_clothing_advice(user_id: str, location: str) -> str:
    if not gemini_model:
        return "抱歉，AI 建議功能目前無法使用 (Gemini 未啟動)。"
    
    app.logger.info(f"Generating clothing advice for {user_id} in {location}...")
    
    try:
        # 1. 撈天氣 (API)
        weather_data = get_weather_36h(location)
        if "error" in weather_data:
            return f"抱歉，我拿不到「{location}」的天氣資訊，無法給您建議。"

        # 2. 撈偏好 (⭐️ 已更新為 SQLAlchemy 版本)
        user_prefs = get_user_preference(user_id)

        # 3. 撈聊天紀錄 (⭐️ 已更新為 SQLAlchemy 版本)
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
            
        prompt_parts.append("\n--- Suggere-me ---") # (你這裡拼錯了，但我先保留，以免影響你的 prompt)
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
    body = request.get_data(as_text=True) or "" 

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

                # ⭐️ 呼叫 SQLAlchemy 版本的 add_chat_history
                add_chat_history(user_id, "user", text)
                reply = "" 

                # ⭐️⭐️ 關鍵：新的指令路由 ⭐️⭐️
                
                if text.startswith("天氣"):
                    city_text = text.replace("天氣", "", 1).strip()
                    city_norm = ""
                    reply_prefix = ""
                    
                    if not city_text:
                        # ⭐️ 呼叫 SQLAlchemy 版本的 get_user_home_city
                        city_norm = get_user_home_city(user_id)
                        reply_prefix = f"（您設定的地區：{city_norm}）\n\n"
                    else:
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
                    prefs = text.replace("記住我", "", 1).strip()
                    if not prefs:
                        reply = "請告訴我你的喜好，例如：「記住我 穿搭偏好：喜歡穿短褲」"
                    else:
                        # ⭐️ 呼叫 SQLAlchemy 版本的 save_user_preference
                        reply = save_user_preference(user_id, prefs)
                
                elif text == "我的偏好":
                    # ⭐️ 呼叫 SQLAlchemy 版本的 get_user_preference
                    prefs = get_user_preference(user_id)
                    reply = f"您目前的偏好設定：\n\n{prefs}"

                elif text == "忘記我":
                    # ⭐️ 呼叫 SQLAlchemy 版本的 clear_user_preference
                    reply = clear_user_preference(user_id)

                elif text.startswith("設定地區"):
                    city_text = text.replace("設定地區", "", 1).strip()
                    if not city_text:
                        reply = "請輸入地區，例如：「設定地區 新北市」"
                    else:
                        # ⭐️ 呼叫 SQLAlchemy 版本的 save_user_home_city
                        reply = save_user_home_city(user_id, city_text)

                elif text == "今天穿什麼" or text == "穿搭建議" or text == "給我穿搭建議":
                    # ⭐️ 呼叫 SQLAlchemy 版本的 get_user_home_city
                    city = get_user_home_city(user_id)
                    # ⭐️ 呼叫 SQLAlchemy 版本的 get_clothing_advice
                    reply = get_clothing_advice(user_id, city)

                # ✅✅✅ --- 這是「最終修正版」的 else 區塊 --- ✅✅✅
                else:
                    # ⭐️ 1. 建立「快速回覆」按鈕
                    qr_buttons = QuickReply(
                        items=[
                            QuickReplyItem(  # ✅ 修正：使用 QuickReplyItem
                                action=MessageAction(label="☀️ 看天氣", text="天氣")
                            ),
                            QuickReplyItem(  # ✅ 修正：使用 QuickReplyItem
                                action=MessageAction(label="👕 穿搭建議", text="今天穿什麼")
                            ),
                            QuickReplyItem(  # ✅ 修正：使用 QuickReplyItem
                                action=MessageAction(label="❤️ 我的偏好", text="我的偏好")
                            ),
                        ]
                    )

                    # ⭐️ 2. 準備回覆的文字
                    reply_text = f"哈囉！你說了：{text}\n\n需要我幫你做什麼嗎？"
                    
                    # ⭐️ 3. 建立帶有按鈕的 TextMessage
                    reply_msg_obj = TextMessage(
                        text=reply_text,
                        quick_reply=qr_buttons  # 關鍵！把按鈕加進來
                    )
                    
                    # ⭐️ 4. 儲存這筆 bot 的回覆到聊天紀錄
                    add_chat_history(user_id, "bot", reply_text)
                    
                    # ⭐️ 5. 馬上回覆訊息 (包含按鈕)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=reply_token,
                            messages=[reply_msg_obj] # 傳送我們剛建立的「帶按鈕的訊息」
                        )
                    )
                    
                    # ⭐️ 6. (重要) 因為我們已經手動回覆了，
                    # 我們要用 `continue` 來跳過這個 event，
                    # 避免程式跑到後面又試圖回覆一次
                    continue
                
                # ✅✅✅ --- 這是「舊的、有問題的」邏輯 --- ✅✅✅
                # (但它會被上面的 continue 跳過，所以不會再出錯)
                if reply:
                    # ⭐️ 呼叫 SQLAlchemy 版本的 add_chat_history
                    add_chat_history(user_id, "bot", reply)
                else:
                    # ❌ (這就是你 1:28 AM 看到的 bug 發生點)
                    reply = "抱歉，我不知道怎麼回應。"

                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=reply_token,
                        messages=[TextMessage(text=reply)]
                    )
                )
    return "OK"


# ⭐️ ---- 6. ⭐️ 移除多餘的函式 ----
# def create_all_tables(): ... 
# ⭐️ (已移除，功能移到檔案頂部)

if __name__ == "__main__":
    # ⭐️ (本地測試時，頂部的 db.create_all() 也會自動執行)
    port = int(os.getenv("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=False)