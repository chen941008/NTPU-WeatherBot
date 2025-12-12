import os
import logging
import datetime
from flask import Flask, request, abort

# LINE Bot SDK
from linebot.v3.webhook import WebhookParser
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, MessagingApiBlob,
    ReplyMessageRequest, TextMessage,
    QuickReply, QuickReplyItem, MessageAction, URIAction
)
from linebot.v3.webhooks import (
    MessageEvent, TextMessageContent, LocationMessageContent, ImageMessageContent
)

# ---- 引入模組 (模組化後的關鍵) ----
# 1. 設定與擴充
from config import CHANNEL_SECRET, CHANNEL_TOKEN, DATABASE_URL
from extensions import db

# 2. 資料庫模型
from models import User, ChatHistory

# 3. AI 服務 (大腦)
from services_ai import (
    startup_load_recipes, analyze_intent, search_recipe_by_ai,
    get_clothing_advice, get_fortune, suggest_recipe_by_ingredients,
    get_random_recipe, get_substitute_suggestion, generate_content_safe,
    generate_tour_guide_text  # <--- 記得這個新函式
)

# 4. 基礎服務 (手腳)
from services_basic import (
    save_user_preference, get_user_preference, clear_user_preference,
    save_user_home_city, get_user_home_city, add_chat_history, normalize_city,
    get_weather_36h, get_nearby_places
)

# 設定日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 🔥 初始化 DB 與 APP 的連結
db.init_app(app)

# 初始化 LINE Bot
if CHANNEL_TOKEN and CHANNEL_SECRET:
    configuration = Configuration(access_token=CHANNEL_TOKEN)
    parser = WebhookParser(CHANNEL_SECRET)
else:
    logger.error("未設定 LINE_CHANNEL_TOKEN 或 LINE_CHANNEL_SECRET")

# 啟動時載入食譜 (這會建立向量索引)
startup_load_recipes()

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
        logger.error(f"Webhook 錯誤: {e}")
        abort(400)
        return "Invalid Signature"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_blob_api = MessagingApiBlob(api_client)
        
        # 定義選單
        feature_quick_reply = QuickReply(items=[
            QuickReplyItem(action=MessageAction(label="🌤️ 查詢天氣", text="天氣")),
            QuickReplyItem(action=MessageAction(label="👕 客製穿搭", text="今天穿什麼")),
            QuickReplyItem(action=MessageAction(label="🗺️ 附近景點", text="附近哪裡好玩")),
            QuickReplyItem(action=MessageAction(label="🔮 今日運勢", text="今日運勢")),
            QuickReplyItem(action=MessageAction(label="🍽️ 食譜建議", text="今天吃什麼")),
            QuickReplyItem(action=MessageAction(label="⚙️ 設定穿搭", text="設定穿搭偏好")),
            QuickReplyItem(action=MessageAction(label="🔑 設定地區", text="設定地區")),
        ])

        for event in events:
            if isinstance(event, MessageEvent):
                if isinstance(event.message, TextMessageContent):
                    handle_text_message(event, line_bot_api, feature_quick_reply)
                elif isinstance(event.message, LocationMessageContent):
                    handle_location_message(event, line_bot_api)
                elif isinstance(event.message, ImageMessageContent):
                    handle_image_message(event, line_bot_api, line_bot_blob_api)

    return "OK"

def handle_text_message(event, line_bot_api, quick_reply):
    text = (event.message.text or "").strip()
    reply_token = event.reply_token
    user_id = event.source.user_id if event.source else None
    if not user_id: return

    # 1. 記錄使用者訊息
    add_chat_history(user_id, "user", text)

    with app.app_context():
        # 檢查/建立使用者
        user = db.session.get(User, user_id)
        if not user:
            user = User(line_user_id=user_id)
            db.session.add(user)
            try: db.session.commit()
            except: db.session.rollback()
        
        user_state = user.session_state
        reply_msg_obj = None
        reply_text = ""
        
        # 2. 處理 Session 狀態 (等待輸入中)
        if user_state:
            user.session_state = None # 重置狀態
            if user_state == "awaiting_region":
                reply_text = save_user_home_city(user_id, text)
            elif user_state == "awaiting_preference":
                reply_text = save_user_preference(user_id, text)
            elif user_state == "awaiting_mood":
                # 運勢分析
                reply_text = get_fortune(user_id, text)
            db.session.commit()
            
        # 3. 處理明確指令
        elif text == "記住我" or text == "設定穿搭偏好":
            user.session_state = "awaiting_preference"
            db.session.commit()
            reply_text = "好的，請告訴我您的「穿搭偏好」：\n（例如：我怕冷、我喜歡穿短褲）"
        elif text == "設定地區":
            user.session_state = "awaiting_region"
            db.session.commit()
            reply_text = "好的，請輸入您要設定的「預設地區」：\n（例如：臺北市）"
        elif text == "我的偏好":
            reply_text = f"您目前的偏好：\n{get_user_preference(user_id)}"
        elif text == "忘記我":
            reply_text = clear_user_preference(user_id)
        
        # 4. AI 意圖判斷 (BGE-M3)
        else:
            ai_result = analyze_intent(text)
            intent = ai_result.get("intent")
            logger.info(f"User: {text} -> Intent: {intent}")

            if intent == "search_recipe":
                # 直接使用 AI 兩段式搜尋 (不需要參數萃取了，因為是向量對向量)
                reply_text = search_recipe_by_ai(text)

            elif intent == "random_recipe":
                reply_text = get_random_recipe()

            elif intent == "suggest_by_ingredients":
                ingredients = ai_result.get("ingredients") or text
                reply_text = suggest_recipe_by_ingredients(user_id, ingredients)

            elif intent == "weather":
                city = ai_result.get("location")
                if not city: city = get_user_home_city(user_id)
                norm_city = normalize_city(city)
                if norm_city:
                    w_data = get_weather_36h(norm_city)
                    reply_text = w_data.get("full_text", "查詢失敗")
                else:
                    reply_text = f"抱歉，我不確定您問的是哪個縣市 ({city})。"

            elif intent == "clothing_advice":
                city = get_user_home_city(user_id)
                reply_text = get_clothing_advice(user_id, city)

            elif intent == "fortune":
                user.session_state = "awaiting_mood"
                db.session.commit()
                reply_text = "在分析運勢前，請告訴我你現在的心情如何？😊"

            elif intent == "substitute_ingredient":
                target = ai_result.get("target") or text
                reply_text = get_substitute_suggestion(target)

            elif intent == "search_nearby":
                reply_msg_obj = TextMessage(
                    text="沒問題！請點擊下方按鈕，傳送您的位置給我，我來幫您找找附近好玩的地方！👇",
                    quick_reply=QuickReply(items=[QuickReplyItem(action=MessageAction(label="📍 傳送我的位置", type="location"))])
                )

            else: # chat
                reply_text = f"你說了：「{text}」\n需要我幫你做什麼嗎？您可以試試看下方的快速選單："
                reply_msg_obj = TextMessage(text=reply_text, quick_reply=quick_reply)

        # 統一回覆建構
        if reply_text and not reply_msg_obj:
            reply_msg_obj = TextMessage(text=reply_text)

        if reply_msg_obj:
            add_chat_history(user_id, "bot", str(reply_msg_obj))
            line_bot_api.reply_message(ReplyMessageRequest(reply_token=reply_token, messages=[reply_msg_obj]))

def handle_location_message(event, line_bot_api):
    """
    處理位置訊息：結合 services_basic (查資料) 與 services_ai (寫文案)
    """
    # 1. 先去查 Google Maps 資料 (Basic Service)
    result = get_nearby_places(event.message.latitude, event.message.longitude)
    
    if result.get("error"):
        reply_msg = TextMessage(text=result["error"])
    else:
        # 2. 將查到的景點資料丟給 AI 產生生動介紹 (AI Service)
        places_str = result.get("places_ai_str", "")
        ai_text = generate_tour_guide_text(places_str)

        # 3. 建立導航按鈕
        quick_reply_items = []
        for p in result["places_data"]:
            label = f"📍 {p['name'][:10]}"
            quick_reply_items.append(QuickReplyItem(action=URIAction(label=label, uri=p['maps_url'])))
        
        reply_msg = TextMessage(text=ai_text + "\n\n點擊下方按鈕直接導航：", quick_reply=QuickReply(items=quick_reply_items))

    line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[reply_msg]))

def handle_image_message(event, line_bot_api, line_bot_blob_api):
    try:
        content = line_bot_blob_api.get_message_content(event.message.id)
        image_part = {'mime_type': 'image/jpeg', 'data': content}
        
        prompt = """
        請辨識圖中食材，推薦 1 道適合的料理。
        請簡述食材清單與 3 個簡易步驟。
        """
        response = generate_content_safe([prompt, image_part])
        reply_text = response.text
    except Exception as e:
        logger.error(f"圖片辨識錯誤: {e}")
        reply_text = "抱歉，我看不太清楚這張圖片裡的食材，可以再拍清楚一點嗎？😅"
    
    line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))

if __name__ == "__main__":
    # 初始化資料庫
    with app.app_context():
        db.create_all()
        
    port = int(os.getenv("PORT", 3000))
    logger.info(f"伺服器即將啟動於 Port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)