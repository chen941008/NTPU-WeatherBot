print("1. 程式開始... (如果有看到這行，代表 Python 沒壞)")
import os
print("2. 正在匯入基礎套件...")
import requests
import certifi
import datetime
import random
import json
from flask import Flask, request
from dotenv import load_dotenv

print("3. 正在匯入資料庫套件 (SQLAlchemy)...")
from flask_sqlalchemy import SQLAlchemy

print("4. 正在匯入 Google AI 套件...")
import google.generativeai as genai

print("5. 正在匯入 LINE Bot 套件...")
from linebot.v3.webhook import WebhookParser
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage,
    QuickReply, QuickReplyItem, MessageAction
)

print("6. 套件匯入完成！準備啟動伺服器...")

load_dotenv()
app = Flask(__name__)

# ---- 1. 金鑰與設定 ----
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CHANNEL_TOKEN  = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CWA_API_KEY    = os.getenv("CWA_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# 資料庫設定
database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

if not database_url:
    app.logger.warning("DATABASE_URL not set, using local bot.db")
    database_url = "sqlite:///bot.db"

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

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


# ---- 2. 資料庫模型 ----
class User(db.Model):
    __tablename__ = 'users'
    line_user_id = db.Column(db.String, primary_key=True)
    preferences = db.Column(db.Text, nullable=True)
    last_updated = db.Column(db.DateTime, onupdate=datetime.datetime.now)
    home_city = db.Column(db.String, nullable=True)
    session_state = db.Column(db.String, nullable=True, default=None) 

class ChatHistory(db.Model):
    __tablename__ = 'chat_history'
    message_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    line_user_id = db.Column(db.String, index=True)
    role = db.Column(db.String)
    content = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.now)

# 資料庫初始化
try:
    with app.app_context():
        db.create_all()  
    app.logger.info("SQLAlchemy tables checked/created successfully.")
except Exception as e:
    app.logger.error(f"Error creating SQLAlchemy tables: {e}")


# ---- 2.1 資料庫功能函式 ----
def save_user_preference(user_id: str, new_pref: str) -> str:
    if not user_id: return "無法識別使用者 ID。"
    try:
        user = db.session.get(User, user_id)
        final_prefs = ""
        if not user:
            final_prefs = new_pref
            user = User(line_user_id=user_id, preferences=final_prefs, last_updated=datetime.datetime.now())
            db.session.add(user)
        else:
            current_prefs = user.preferences
            if not current_prefs:
                final_prefs = new_pref
            else:
                final_prefs = current_prefs + "\n" + new_pref
            user.preferences = final_prefs
            user.last_updated = datetime.datetime.now()
        db.session.commit()
        return f"我記住了：「{new_pref}」\n\n（點選「我的偏好」查看全部）"
    except Exception as e:
        db.session.rollback()
        return "抱歉，儲存喜好時發生錯誤。"

def get_user_preference(user_id: str) -> str:
    if not user_id: return ""
    try:
        user = db.session.get(User, user_id)
        return user.preferences if user and user.preferences else "尚未設定"
    except Exception as e:
        return "讀取偏好時發生錯誤"

def clear_user_preference(user_id: str) -> str:
    if not user_id: return "無法識別使用者 ID。"
    try:
        user = db.session.get(User, user_id)
        if user:
            user.preferences = None
            user.last_updated = datetime.datetime.now()
            db.session.commit()
        return "我已經忘記你所有的偏好了。"
    except Exception as e:
        db.session.rollback()
        return "抱歉，清除偏好時發生錯誤。"

def add_chat_history(user_id: str, role: str, content: str):
    if not user_id or not content: return
    try:
        new_chat = ChatHistory(line_user_id=user_id, role=role, content=content, timestamp=datetime.datetime.now())
        db.session.add(new_chat)
        db.session.commit()
    except Exception as e:
        db.session.rollback()

def get_chat_history(user_id: str, limit: int = 10) -> list:
    if not user_id: return []
    try:
        stmt = db.select(ChatHistory).filter_by(line_user_id=user_id).order_by(ChatHistory.timestamp.desc()).limit(limit)
        rows = db.session.scalars(stmt).all()
        history = [(row.role, row.content) for row in rows]
        return list(reversed(history))
    except Exception as e:
        return []

# ---- 2.2 地區設定相關函式 ----
CITY_ALIASES = {
    "台北": "臺北市", "臺北": "臺北市", "北市": "臺北市","臺北市":"臺北市", "台北市":"臺北市",
    "新北": "新北市", "新北市":"新北市", "台中": "臺中市", "臺中": "臺中市", "臺中市":"臺中市", "台中市":"臺中市",
    "台南": "臺南市", "臺南": "臺南市", "臺南市":"臺南市", "台南市":"臺南市", "高雄": "高雄市", "高雄市":"高雄市",
    "桃園": "桃園市", "桃園市":"桃園市", "新竹": "新竹市", "新竹市":"新竹市", "基隆": "基隆市", "基隆市":"基隆市",
    "嘉義": "嘉義市", "嘉義市":"嘉義市", "宜蘭": "宜蘭縣", "宜蘭縣": "宜蘭縣", "花蓮": "花蓮縣", "花蓮縣": "花蓮縣",
    "台東": "臺東縣", "臺東": "臺東縣", "臺東縣": "臺東縣", "台東縣": "臺東縣", "屏東": "屏東縣", "屏東縣": "屏東縣",
    "苗栗": "苗栗縣", "苗栗縣": "苗栗縣", "彰化": "彰化縣", "彰化縣": "彰化縣", "雲林": "雲林縣", "雲林縣": "雲林縣",
    "南投": "南投縣", "南投縣": "南投縣", "嘉義縣": "嘉義縣", "嘉義": "嘉義縣", "新竹縣": "新竹縣",
    "連江": "連江縣", "連江縣": "連江縣", "金門": "金門縣", "金門縣": "金門縣", "澎湖": "澎湖縣", "澎湖縣": "澎湖縣",
}

def normalize_city(text: str) -> str:
    text = (text or "").strip()
    if not text: return "臺北市"
    normalized = CITY_ALIASES.get(text)
    if normalized: return normalized
    if text in CITY_ALIASES.values(): return text
    return None

def save_user_home_city(user_id: str, city_name: str) -> str:
    if not user_id: return "無法識別使用者 ID。"
    normalized_city = normalize_city(city_name)
    if not normalized_city: return f"抱歉，我不認識「{city_name}」。"
    try:
        user = db.session.get(User, user_id)
        if not user:
            user = User(line_user_id=user_id, home_city=normalized_city, last_updated=datetime.datetime.now())
            db.session.add(user)
        else:
            user.home_city = normalized_city
            user.last_updated = datetime.datetime.now()
        db.session.commit()
        return f"您的預設地區已設定為：「{normalized_city}」"
    except Exception as e:
        db.session.rollback()
        return "抱歉，儲存地區時發生錯誤。"

def get_user_home_city(user_id: str) -> str:
    if not user_id: return "臺北市"
    try:
        user = db.session.get(User, user_id)
        return user.home_city if user and user.home_city else "臺北市"
    except Exception as e:
        return "臺北市"


# ---- 3. 天氣功能 (CWA API) ----
def get_weather_36h(location="臺北市") -> dict:
    if not CWA_API_KEY: return {"error": "尚未設定 CWA_API_KEY..."}
    url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"
    params = {"Authorization": CWA_API_KEY, "locationName": location}
    s = requests.Session()
    s.trust_env = False
    
    force_insecure = bool(os.getenv("CWA_INSECURE"))
    attempts = [(False, False)] if force_insecure else [(True, certifi.where()), (False, False)]

    for do_verify, verify_arg in attempts:
        try:
            r = s.get(url, params=params, timeout=12, verify=verify_arg)
            r.raise_for_status()
            data = r.json()
            locs = data.get("records", {}).get("location", [])
            if not locs: return {"error": f"查不到「{location}」的天氣資訊。"}
            
            loc = locs[0]
            wx = loc["weatherElement"][0]["time"][0]["parameter"]["parameterName"]
            pop = loc["weatherElement"][1]["time"][0]["parameter"]["parameterName"]
            minT = loc["weatherElement"][2]["time"][0]["parameter"]["parameterName"]
            ci = loc["weatherElement"][3]["time"][0]["parameter"]["parameterName"]
            maxT = loc["weatherElement"][4]["time"][0]["parameter"]["parameterName"]
            
            return {
                "location": location, "wx": wx, "pop": pop, "minT": minT, "maxT": maxT, "ci": ci,
                "full_text": (f"{location} 今明短期預報：\n・天氣：{wx}\n・降雨機率：{pop}%\n・溫度：{minT}°C ~ {maxT}°C\n・體感：{ci}")
            }
        except Exception:
            continue
    return {"error": "氣象資料連線失敗，稍後再試。"}


# ---- 4. AI 穿搭建議功能 ----
def get_clothing_advice(user_id: str, location: str) -> str:
    if not gemini_model: return "抱歉，AI 建議功能目前無法使用。"
    app.logger.info(f"Generating clothing advice for {user_id} in {location}...")
    try:
        weather_data = get_weather_36h(location)
        if "error" in weather_data: return f"抱歉，我拿不到「{location}」的天氣資訊。"
        user_prefs = get_user_preference(user_id)
        history_rows = get_chat_history(user_id, limit=10)

        prompt_parts = [
            "你是「生活智慧管家」，一個專業且體貼的AI助理。",
            f"天氣：{weather_data['location']}, {weather_data['full_text']}",
            f"使用者偏好：{user_prefs}",
            "請給予簡潔、體貼的穿搭建議："
        ]
        
        response = gemini_model.generate_content("\n".join(prompt_parts))
        return response.text
    except Exception as e:
        app.logger.error(f"Error generating clothing advice: {e}")
        return "抱歉，AI 在思考建議時發生錯誤，請稍後再試。"


# ⭐️ ---- 5. 食譜 RAG 功能 (搜尋 + AI 講解) ----
RECIPES_URL = 'https://mp-bc8d1f0a-3356-4a4e-8592-f73a3371baa2.cdn.bspapp.com/all_recipes.json'
CACHED_RECIPES = []

def ensure_recipes_loaded():
    """確保食譜已經下載到記憶體"""
    global CACHED_RECIPES
    if not CACHED_RECIPES:
        print("正在下載食譜資料庫...")
        try:
            r = requests.get(RECIPES_URL, timeout=15)
            if r.status_code == 200:
                CACHED_RECIPES = r.json()
                print(f"✅ 食譜下載成功！共有 {len(CACHED_RECIPES)} 道菜")
            else:
                print("❌ 食譜下載失敗")
        except Exception as e:
            print(f"❌ 下載錯誤: {e}")

# ---- 5.1 食材推薦功能 (New!) ----
def suggest_recipe_by_ingredients(user_id: str, ingredients: str) -> str:
    """根據使用者提供的食材，建議合適的食譜"""
    if not gemini_model: return "抱歉，AI 建議功能目前無法使用。"
    
    ensure_recipes_loaded()
    if not CACHED_RECIPES: return "食譜資料庫連線失敗。"

    # 選擇一些食譜資料給 AI 參考 (為了速度，只取前 20 個作為參考資料)
    # 優化：我們只傳遞菜名和類別，減少 token 量
    sample_recipes = CACHED_RECIPES[:20] 
    recipe_names = "\n".join([f"・{r['name']} ({r.get('category', '未分類')})" for r in sample_recipes])
    
    # 準備 Prompt
    prompt = f"""
    你是「聰明主廚 AI」，專門根據現有食材推薦料理。
    
    使用者現有的食材清單：【{ingredients}】
    
    參考食譜資料庫 (部分)：
    {recipe_names}
    
    任務：
    1. 從參考食譜中，找出最適合用這些食材製作的 1~2 道菜。
    2. 說明為什麼這道菜適合，以及需要多買哪些簡單的調味料。
    3. 如果沒有任何適合的菜，請禮貌地推薦一道，並說明需要買哪些主食材。
    4. 最後鼓勵使用者輸入「食譜 [菜名]」來查詢作法。
    5. 請使用親切、幽默的語氣，並使用繁體中文。
    """
    
    # 生成並強化錯誤處理
    try:
        response = gemini_model.generate_content(prompt)
        
        # ⭐️ 關鍵修改：檢查 response 是否有內容
        if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
            # 確保內容是可讀的
            return response.text
        else:
            # 如果 response.text 失敗，我們嘗試從 finish_reason 取得更多資訊
            reason = response.candidates[0].finish_reason.name if response.candidates else "未知原因"
            app.logger.error(f"AI Response failed but reason is {reason}")
            
            if reason == "SAFETY":
                 return "抱歉，AI 認為這個請求可能違反了安全規範，無法提供建議。"
            elif reason == "RECITATION":
                 return "抱歉，AI 發生記憶錯誤，無法提供建議。"
            else:
                 return "AI 忙碌中，請換個食材再試一次。" # 給使用者一個友善的重試訊息
                 
    except Exception as e:
        app.logger.error(f"AI Suggestion Error: {e}")
        return "AI 在分析食材時發生錯誤，請稍後再試。"

def get_random_recipe():
    """隨機推薦一道菜"""
    ensure_recipes_loaded()
    if not CACHED_RECIPES: return "食譜資料庫連線失敗，請稍後再試。"
    
    dish = random.choice(CACHED_RECIPES)
    name = dish.get('name', '神秘料理')
    category = dish.get('category', '未分類')
    desc = dish.get('description', '')[:100]
    return f"🍳 隨機推薦：{name}\n📂 分類：{category}\n📝 簡介：{desc}...\n\n(想知道怎麼做嗎？請輸入「食譜 {name}」)"

def analyze_intent(user_text):
    """
    使用 AI 來判斷使用者的意圖 (Intent Classification) (已新增食材推薦意圖)
    """
    if not gemini_model:
        return {"intent": "chat", "reply": "AI 維修中"}
        
    prompt = f"""
    你是 LINE Bot 的大腦。請分析使用者的輸入：「{user_text}」
    
    請判斷使用者的意圖，並嚴格依照以下 JSON 格式回傳，不要有任何其他廢話：
    
    1. 如果使用者想找食譜、學做菜、問作法 (例如：教我煮三杯雞、我想吃宮保雞丁、番茄炒蛋怎麼弄)：
       回傳：{{"intent": "search_recipe", "keyword": "擷取出的菜名"}}
       
    2. 如果使用者想隨機抽食譜 (例如：今天吃什麼、晚餐吃什麼、隨便推薦一道)：
       回傳：{{"intent": "random_recipe"}}
       
    3. 如果使用者想問天氣 (例如：台北天氣如何、外面會下雨嗎)：
       回傳：{{"intent": "weather", "location": "擷取出的縣市名稱(若無則回傳null)"}}
       
    4. 如果使用者想問穿搭 (例如：今天穿什麼、好冷要穿這嗎)：
       回傳：{{"intent": "clothing_advice"}}
       
    5. 如果使用者想根據現有食材推薦菜色 (例如：我只有雞蛋和番茄可以做什麼、冰箱只剩豆腐)：
       回傳：{{"intent": "suggest_by_ingredients", "ingredients": "擷取出的食材清單 (以逗號分隔)"}}

    6. 其他閒聊或無法判斷：
       回傳：{{"intent": "chat"}}
    """
    
    try:
        response = gemini_model.generate_content(prompt)
        # 清理回應，確保是乾淨的 JSON (有時候 AI 會包 markdown 符號)
        clean_text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_text)
    except Exception as e:
        print(f"意圖辨識失敗: {e}")
        return {"intent": "chat"}

def search_recipe_by_ai(keyword):
    """
    RAG 核心邏輯 (強化翻譯與資料清洗)：
    1. 檢索 (Retrieval): 搜尋符合關鍵字的食譜
    2. 增強 (Augmentation): 把食譜的原始 JSON 資料當作背景知識
    3. 生成 (Generation): 叫 Gemini 講人話
    """
    if not gemini_model: return "抱歉，AI 功能目前無法使用。"
    
    ensure_recipes_loaded()
    if not CACHED_RECIPES: return "食譜資料庫連線失敗。"

    # 1. 檢索 (模糊搜尋)
    found_dishes = [r for r in CACHED_RECIPES if keyword in r.get('name', '')]
    
    if not found_dishes:
        return f"抱歉，我在食譜資料庫裡找不到「{keyword}」。試試看別的關鍵字？（例如：雞肉、番茄）"
    
    # 如果找到太多，先取第一個最像的
    target_dish = found_dishes[0]
    
    # 2. 增強 (準備 Prompt)
    dish_data_str = json.dumps(target_dish, ensure_ascii=False)
    
    prompt = f"""
    你現在是一位專業的五星級大廚。
    
    使用者想知道「{target_dish['name']}」的作法。
    
    以下是這道菜的詳細原始資料 (JSON 格式)：
    {dish_data_str}
    
    任務：
    請根據上面的原始資料，執行以下步驟：
    1. **徹底執行資料清洗與標準化**，忽略資料中的亂碼或不一致的格式。
    2. 將所有內容（包括食材名稱、步驟說明）**翻譯為高質量、流暢的繁體中文**。
    3. 用親切、易懂的方式，寫一份完整的食譜教學給使用者。
    
    格式要求：
    1. 開頭先用一句話介紹這道菜。
    2. 列出「食材清單」(請整理好份量，統一單位)。
    3. 列出「詳細步驟」(請加上編號，並把步驟寫得清楚好操作)。
    4. 最後給一個「大廚小撇步」。
    """
    
    # 3. 生成
    try:
        response = gemini_model.generate_content(prompt)
        return response.text
    except Exception as e:
        app.logger.error(f"AI Recipe Error: {e}")
        return "AI 在讀取食譜時頭暈了，請稍後再試。"


# ---- 6. Flask Webhook 路由 ----
@app.get("/health")
def health(): return "OK"

@app.route("/webhook", methods=['POST'])
def webhook():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True) or "" 
    if not signature or not body.strip(): return "OK"
    try: events = parser.parse(body, signature)
    except Exception: return "OK"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        for event in events:
            if event.type == "message" and getattr(event, "message", None) and event.message.type == "text":
                text = (event.message.text or "").strip()
                reply_token = event.reply_token
                if event.source and event.source.type == "user": user_id = event.source.user_id
                else: continue 

                add_chat_history(user_id, "user", text)
                
                # 使用者狀態管理
                user = db.session.get(User, user_id)
                if not user:
                    user = User(line_user_id=user_id)
                    db.session.add(user)
                    try: db.session.commit()
                    except: db.session.rollback()
                
                user_state = user.session_state
                reply_msg_obj = None 
                reply_text = ""      

                # ==========================================
                # 1. 最高優先級：處理「狀態」(強制流程)
                # ==========================================
                if user_state:
                    user.session_state = None
                    if user_state == "awaiting_region":
                        reply_text = save_user_home_city(user_id, text)
                    elif user_state == "awaiting_preference":
                        reply_text = save_user_preference(user_id, text)
                    else:
                        reply_text = "發生錯誤，請再試一次。"
                    try: db.session.commit()
                    except: db.session.rollback()
                    reply_msg_obj = TextMessage(text=reply_text)

                # ==========================================
                # 2. 次高優先級：處理「按鈕指令」(Exact Match)
                # ==========================================
                elif text == "記住我": 
                    user.session_state = "awaiting_preference"
                    db.session.commit()
                    reply_text = "好的，請告訴我您的「穿搭偏好」：\n（例如：我怕冷、我喜歡穿短褲）"
                    
                elif text == "設定地區": 
                    user.session_state = "awaiting_region"
                    db.session.commit()
                    reply_text = "好的，請輸入您要設定的「預設地區」：\n（例如：臺北市）"

                elif text == "我的偏好":
                    prefs = get_user_preference(user_id)
                    reply_text = f"您目前的偏好設定：\n\n{prefs}"

                elif text == "忘記我":
                    reply_text = clear_user_preference(user_id)
                
                # 這裡把原本硬寫的食譜/天氣也搬到 AI Router 處理，因此不再需要這裡的 elif text.startswith("天氣") 等硬規則。
                # 舊的硬規則已被移除。

                # ==========================================
                # 3. 剩下的所有文字 -> 交給 AI 判斷意圖！
                # ==========================================
                else:
                    # 呼叫我們剛寫的 AI 判斷函式
                    ai_result = analyze_intent(text)
                    intent = ai_result.get("intent")
                    
                    print(f"使用者輸入: {text} -> AI 判斷意圖: {intent}")

                    if intent == "search_recipe":
                        keyword = ai_result.get("keyword")
                        # 如果 AI 沒抓到關鍵字，就用整句去搜
                        if not keyword: keyword = text
                        reply_text = search_recipe_by_ai(keyword)
                        
                    elif intent == "random_recipe":
                        reply_text = get_random_recipe()

                    # ⭐️ 處理新的食材推薦意圖
                    elif intent == "suggest_by_ingredients":
                        ingredients = ai_result.get("ingredients")
                        reply_text = suggest_recipe_by_ingredients(user_id, ingredients)
                        
                    elif intent == "weather":
                        city = ai_result.get("location")
                        if not city:
                            city = get_user_home_city(user_id) # 如果沒說地點，就用預設的
                        
                        norm_city = normalize_city(city)
                        if norm_city:
                            w_data = get_weather_36h(norm_city)
                            reply_text = w_data.get("full_text", "查詢失敗")
                        else:
                            reply_text = f"抱歉，我不確定您問的是哪個縣市 ({city})，請先設定地區或明示地名。"

                    elif intent == "clothing_advice":
                        city = get_user_home_city(user_id)
                        reply_text = get_clothing_advice(user_id, city)
                        
                    else: # intent == "chat"
                        # AI 判定為閒聊，回覆預設選單
                        qr_buttons = QuickReply(
                            items=[
                                QuickReplyItem(action=MessageAction(label="☀️ 看天氣", text="天氣")),
                                QuickReplyItem(action=MessageAction(label="👕 穿搭建議", text="今天穿什麼")),
                                QuickReplyItem(action=MessageAction(label="🍳 今天吃什麼", text="今天吃什麼")),
                                QuickReplyItem(action=MessageAction(label="💡 食材推薦", text="我只有雞蛋、蔥、醬油")), # 新增推薦按鈕範例
                                QuickReplyItem(action=MessageAction(label="🔍 搜尋食譜", text="食譜 番茄炒蛋")), 
                                QuickReplyItem(action=MessageAction(label="⚙️ 設定地區", text="設定地區")),
                            ]
                        )
                        reply_text = f"你說了：「{text}」\n需要我幫你做什麼嗎？"
                        reply_msg_obj = TextMessage(text=reply_text, quick_reply=qr_buttons)

                # ==========================================
                # 4. 統一發送
                # ==========================================
                if reply_text and not reply_msg_obj:
                    reply_msg_obj = TextMessage(text=reply_text)

                if reply_msg_obj:
                    add_chat_history(user_id, "bot", reply_text or "image/template")
                    line_bot_api.reply_message(ReplyMessageRequest(reply_token=reply_token, messages=[reply_msg_obj]))

    return "OK"

if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=False)