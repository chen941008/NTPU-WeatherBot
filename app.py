import os
import logging
import datetime
import random
import json
from typing import List, Dict, Any, Optional, Union
from urllib.parse import quote
import opencc

import requests
import certifi
import urllib3
from sentence_transformers import SentenceTransformer, util
import torch
import google.generativeai as genai
from google.api_core import exceptions
from flask import Flask, request, abort
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv

from linebot.v3.webhook import WebhookParser
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, MessagingApiBlob,
    ReplyMessageRequest, TextMessage,
    QuickReply, QuickReplyItem, MessageAction,
    URIAction
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    LocationMessageContent,
    ImageMessageContent
)

# urllib3 警告關閉
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 設定日誌記錄 (Logging Setup)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 載入環境變數
load_dotenv()

# 初始化 Flask 應用程式
app = Flask(__name__)

# ---- 🚀 BGE-M3 向量搜尋引擎初始化 ----
print("正在載入 BGE-M3 模型 (第一次執行需下載，請耐心等候)...", flush=True)
# 載入模型 (會自動使用 GPU，如果沒有則用 CPU)
embedding_model = SentenceTransformer('BAAI/bge-m3')

# 定義意圖與標準問句 (Knowledge Base)
# 這裡定義你希望機器人聽懂的句子
# 定義意圖與標準問句 (Knowledge Base) - AI 增強版
INTENT_KNOWLEDGE_BASE = {
    # ----------------------------------------------------
    # 打招呼與閒聊防護網 (最重要的防誤判層)
    # ----------------------------------------------------
    "greeting": [
        "你好", "哈囉", "嗨", "早安", "午安", "晚安", "Hi", "Hello", 
        "有人在嗎", "安安", "嘿", "Yo", "早", "機器人你好", "測試", 
        "在嗎", "出來面對", "哈囉你好", "晚安囉"
    ],

    # ----------------------------------------------------
    # 天氣相關 (加入帶傘、曬衣、溫度感受)
    # ----------------------------------------------------
    "weather": [
        # 直問
        "天氣如何", "查詢天氣", "氣溫幾度", "現在幾度", "台北天氣", "下雨機率",
        # 帶傘情境
        "外面有下雨嗎", "要帶傘嗎", "會下雨嗎", "出門要不要帶雨具", "有沒有降雨機率",
        # 穿衣/冷熱情境
        "明天會冷嗎", "熱死人了", "今天好冷喔", "週末天氣好嗎", "適合出遊嗎",
        # 簡短
        "天氣", "氣象", "下雨", "溫度"
    ],

    # ----------------------------------------------------
    # 穿搭建議 (加入場合、洋蔥式、保暖)
    # ----------------------------------------------------
    "clothing_advice": [
        # 直問
        "今天穿什麼", "穿搭建議", "怎麼穿比較好", "給點穿搭意見",
        # 情境
        "外面冷嗎要穿外套嗎", "適合穿短袖嗎", "穿這樣會冷嗎", "需不需要穿大衣",
        "要穿長袖還是短袖", "洋蔥式穿法", "今天適合穿裙子嗎",
        # 需求
        "怕冷怎麼穿", "騎車要穿什麼", "今天風大嗎要穿什麼", "有沒有穿搭靈感"
    ],

    # ----------------------------------------------------
    # 食譜查詢 (加入想做菜、教學、特定菜名)
    # ----------------------------------------------------
    "search_recipe": [
        # 意圖
        "教我做菜", "食譜查詢", "作法教學", "怎麼煮", "料理教學", "我想學做菜",
        # 具體菜色範例 (讓向量知道這類句型)
        "怎麼煮紅燒肉", "我想學做義大利麵", "番茄炒蛋作法", "宮保雞丁怎麼弄",
        "教我煮咖哩", "我想吃麻婆豆腐", "牛肉麵食譜", "三杯雞作法", 
        # 關鍵字
        "紅燒魚", "炒高麗菜", "玉米濃湯", "食譜"
    ],

    # ----------------------------------------------------
    # 冰箱剩菜/食材推薦 (加入清冰箱、剩下什麼)
    # ----------------------------------------------------
    "suggest_by_ingredients": [
        # 冰箱情境
        "冰箱只剩雞蛋", "清冰箱料理", "冰箱有番茄可以做什麼", "家裡只有麵條",
        "剩菜怎麼煮", "幫我消耗食材", "只有豆腐怎麼辦", "冰箱剩這些",
        # 推薦請求
        "食材推薦", "我有洋蔥和蛋", "用雞胸肉做一道菜", "這些材料能煮什麼",
        "推薦一道用馬鈴薯的菜", "幫我想料理"
    ],

    # ----------------------------------------------------
    # 隨機食譜 (加入飢餓、選擇困難、三餐)
    # ----------------------------------------------------
    "random_recipe": [
        # 選擇困難
        "晚餐吃什麼", "午餐吃什麼", "早餐吃什麼", "幫我決定晚餐", "不知道吃什麼",
        "隨便推薦一道菜", "我想不到要吃啥", "有什麼好吃的", "推薦晚餐",
        # 飢餓表達
        "肚子好餓", "快餓扁了", "餓了", "想吃好料的", "隨便來一道", 
        "今晚吃什麼", "介紹一道菜"
    ],

    # ----------------------------------------------------
    # 食材替代 (加入缺料、沒有X怎麼辦)
    # ----------------------------------------------------
    "substitute_ingredient": [
        # 缺料情境
        "沒有醬油怎麼辦", "奶油可以用什麼代替", "缺少調味料", "家裡沒糖了",
        "沒有米酒", "可以用牛奶代替鮮奶油嗎", "沒有太白粉",
        # 替代詢問
        "替代食材", "這可以用什麼換", "有什麼替代品", "如果不加這個會怎樣",
        "沒買到洋蔥"
    ],

    # ----------------------------------------------------
    # 運勢 (加入星座、運氣、占卜)
    # ----------------------------------------------------
    "fortune": [
        "今日運勢", "運氣如何", "抽籤", "占卜", "好運嗎", "星座運勢",
        "我今天運氣好嗎", "水逆了嗎", "幫我算命", "今日宜忌", "我的運勢",
        "處女座運勢", "今天會幸運嗎"
    ],

    # ----------------------------------------------------
    # 附近景點 (加入無聊、出去玩、導覽)
    # ----------------------------------------------------
    "search_nearby": [
        # 直問
        "附近有什麼好玩的", "推薦景點", "這附近哪裡好玩", "旅遊推薦", "導覽",
        # 玩樂需求
        "好無聊喔", "我想出去玩", "假日去哪裡", "帶我去玩", "附近有什麼地標",
        "哪裡適合約會", "附近景點", "好玩的", "我要去玩"
    ]
}

# 初始化簡轉繁轉換器
cc = opencc.OpenCC('s2t')
CACHED_RECIPES = [] 
RECIPE_EMBEDDINGS = None

# ==========================================
# 🚀 系統啟動流程修正
# 邏輯：檢查本地 -> (無)強制下載 -> 轉繁體 -> 注入意圖 -> 向量化
# ==========================================

def startup_load_recipes():
    """
    [修正版] 啟動載入：讀取 -> 轉繁體 -> 建立兩階段向量索引
    """
    global CACHED_RECIPES, RECIPE_EMBEDDINGS
    
    recipe_json_path = "recipes.json"
    data = []
    cleaned = [] # 🔥 關鍵修正：先初始化為空列表，防止 NameError

    # 1. 嘗試讀取本地
    if os.path.exists(recipe_json_path):
        print(f"📂 發現本地食譜檔案，正在讀取...", flush=True)
        try:
            with open(recipe_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"❌ 本地讀取失敗: {e}，將嘗試網路下載。", flush=True)
    
    # 2. 如果本地沒有，強制下載
    if not data:
        print(f"🌐 正在從網路下載食譜資料庫...", flush=True)
        try:
            response = requests.get(RECIPES_URL, timeout=60)
            if response.status_code == 200:
                data = response.json()
                with open(recipe_json_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
            else:
                print(f"❌ 下載失敗 (Status: {response.status_code})", flush=True)
        except Exception as e:
            print(f"❌ 下載錯誤: {e}", flush=True)

    # 3. 執行簡轉繁與清洗
    if data:
        for dish in data:
            new_dish = dish.copy()
            # 安全轉換：先檢查欄位是否存在
            if "name" in new_dish:
                new_dish["name"] = cc.convert(new_dish["name"])
            if "description" in new_dish:
                new_dish["description"] = cc.convert(new_dish["description"])
            if "ingredients" in new_dish:
                new_dish["ingredients"] = cc.convert(str(new_dish["ingredients"]))
            cleaned.append(new_dish)
        
        CACHED_RECIPES = cleaned
        print(f"✅ 食譜載入並繁體化完成！共 {len(CACHED_RECIPES)} 道。", flush=True)
        
        # ==== 🔥 新增：第二層向量化 (針對菜名) ====
        # 只有在有食譜時才建立索引
        if CACHED_RECIPES:
            print("🍳 正在為食譜名稱建立專屬向量索引...", flush=True)
            try:
                # 提取所有菜名
                recipe_names = [r['name'] for r in CACHED_RECIPES]
                
                # 轉成 Tensor
                RECIPE_EMBEDDINGS = embedding_model.encode(recipe_names, convert_to_tensor=True)
                print(f"✅ 食譜向量索引建立完成！(Shape: {RECIPE_EMBEDDINGS.shape})", flush=True)

                # 動態注入意圖 (讓第一層分類更準)
                if "search_recipe" in INTENT_KNOWLEDGE_BASE:
                    INTENT_KNOWLEDGE_BASE["search_recipe"].extend(recipe_names)
                    print(f"💉 已注入 {len(recipe_names)} 個菜名到意圖系統。", flush=True)
            except Exception as e:
                print(f"❌ 建立向量索引時發生錯誤: {e}", flush=True)

    else:
        print("⚠️ 警告：無法載入食譜，機器人將無法辨識特定菜名。", flush=True)

# ---- 執行啟動載入 ----
startup_load_recipes()

# ---- 接著才做向量化 ----
print("🧠 正在將意圖資料庫轉為向量 (BGE-M3)...", flush=True)
corpus_sentences = []
intent_map = [] 

for intent, examples in INTENT_KNOWLEDGE_BASE.items():
    for example in examples:
        corpus_sentences.append(example)
        intent_map.append(intent)

corpus_embeddings = embedding_model.encode(corpus_sentences, convert_to_tensor=True)
print("✅ BGE-M3 初始化完成！向量空間已建立。", flush=True)

# ---- 配置與常數 (Configuration & Constants) ----

# 頻道與 API 金鑰設定
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CHANNEL_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CWA_API_KEY = os.getenv("CWA_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
CWA_INSECURE = os.getenv("CWA_INSECURE")

# 資料庫連線設定
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if not DATABASE_URL:
    logger.warning("未設定 DATABASE_URL，將使用本地 SQLite 資料庫 (bot.db)。")
    DATABASE_URL = "sqlite:///bot.db"

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 初始化資料庫
db = SQLAlchemy(app)

# 初始化 LINE Bot
if CHANNEL_TOKEN and CHANNEL_SECRET:
    configuration = Configuration(access_token=CHANNEL_TOKEN)
    parser = WebhookParser(CHANNEL_SECRET)
else:
    logger.error("未設定 LINE_CHANNEL_TOKEN 或 LINE_CHANNEL_SECRET，Bot 無法運作。")

# 初始化 Google Gemini
if GOOGLE_API_KEY:
    try:
        genai.configure(api_key=GOOGLE_API_KEY)
        logger.info("Google Gemini 模型初始化成功。")
    except Exception as e:
        logger.error(f"初始化 Gemini 時發生錯誤: {e}")
else:
    logger.warning("未設定 GOOGLE_API_KEY，AI 功能將無法使用。")

# 模型優先順序清單
# 邏輯：優先使用穩定且快速的模型 (Flash)，其次是強大的模型 (Pro)，
# 若都失敗則使用實驗性或輕量模型。
MODEL_PRIORITY: List[str] = [
    # 第一梯隊：最強大腦 (High Intelligence)
    "gemini-2.5-pro",         # 次強模型

    # 第二梯隊：速度與品質平衡 (Balanced / Flash)
    "gemini-2.5-flash",       # 最新版 Flash，速度快且聰明
    "gemini-2.0-flash",       # 上一代 Flash，穩定
    "gemini-2.0-flash-exp",   # 實驗版 Flash

    # 第三梯隊：極致輕量與速度 (Lite)
    "gemini-2.5-flash-lite",  # 2.5 的輕量版
    "gemini-2.0-flash-lite",  # 2.0 的輕量版

    # 第四梯隊：開源模型 (Gemma - 當作最後防線)
    "gemma-3-27b",            # Gemma 系列中最大的
    "gemma-3-12b",            # Gemma 系列中型的
]

# 城市名稱對照表
CITY_ALIASES: Dict[str, str] = {
    "台北": "臺北市", "臺北": "臺北市", "北市": "臺北市", "臺北市": "臺北市", "台北市": "臺北市",
    "新北": "新北市", "新北市": "新北市", "台中": "臺中市", "臺中": "臺中市", "臺中市": "臺中市", "台中市": "臺中市",
    "台南": "臺南市", "臺南": "臺南市", "臺南市": "臺南市", "台南市": "臺南市", "高雄": "高雄市", "高雄市": "高雄市",
    "桃園": "桃園市", "桃園市": "桃園市", "新竹": "新竹市", "新竹市": "新竹市", "基隆": "基隆市", "基隆市": "基隆市",
    "嘉義": "嘉義市", "嘉義市": "嘉義市", "宜蘭": "宜蘭縣", "宜蘭縣": "宜蘭縣", "花蓮": "花蓮縣", "花蓮縣": "花蓮縣",
    "台東": "臺東縣", "臺東": "臺東縣", "臺東縣": "臺東縣", "台東縣": "臺東縣", "屏東": "屏東縣", "屏東縣": "屏東縣",
    "苗栗": "苗栗縣", "苗栗縣": "苗栗縣", "彰化": "彰化縣", "彰化縣": "彰化縣", "雲林": "雲林縣", "雲林縣": "雲林縣",
    "南投": "南投縣", "南投縣": "南投縣", "嘉義縣": "嘉義縣", "新竹縣": "新竹縣",
    "連江": "連江縣", "連江縣": "連江縣", "金門": "金門縣", "金門縣": "金門縣", "澎湖": "澎湖縣", "澎湖縣": "澎湖縣",
}

# 食譜資料來源 URL
RECIPES_URL = 'https://mp-bc8d1f0a-3356-4a4e-8592-f73a3371baa2.cdn.bspapp.com/all_recipes.json'
# 全域食譜快取
CACHED_RECIPES: List[Dict[str, Any]] = []


# ---- 資料庫模型 (Database Models) ----

class User(db.Model):
    """
    使用者資料表模型
    """
    __tablename__ = 'users'
    line_user_id = db.Column(db.String, primary_key=True)
    preferences = db.Column(db.Text, nullable=True)
    last_updated = db.Column(db.DateTime, onupdate=datetime.datetime.now)
    home_city = db.Column(db.String, nullable=True)
    session_state = db.Column(db.String, nullable=True, default=None)


class ChatHistory(db.Model):
    """
    對話紀錄資料表模型
    """
    __tablename__ = 'chat_history'
    message_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    line_user_id = db.Column(db.String, index=True)
    role = db.Column(db.String)
    content = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.now)


# ---- 輔助函式 (Helper Functions) ----

def generate_content_safe(prompt_parts: Union[str, List[Any]]) -> Any:
    """
    依序嘗試 MODEL_PRIORITY 中的模型來生成內容。
    包含完整的錯誤處理，特別是針對模型不存在 (404) 的情況。

    Args:
        prompt_parts: 提示詞內容，可以是字串、字串列表，或包含圖片資料的混合列表。

    Returns:
        Gemini API 的回應物件。

    Raises:
        Exception: 當所有模型都嘗試失敗時拋出異常。
    """
    if not GOOGLE_API_KEY:
        raise Exception("API Key 未設定")

    last_error = None

    for model_name in MODEL_PRIORITY:
        try:
            # 建立當前要嘗試的模型物件
            current_model = genai.GenerativeModel(model_name)
            
            # 嘗試生成內容
            response = current_model.generate_content(prompt_parts)
            return response

        except exceptions.ResourceExhausted:
            logger.warning(f"模型 {model_name} 額度已滿或被限制 (ResourceExhausted)，切換下一個...")
            last_error = "Quota Exceeded"
            continue

        except exceptions.ServiceUnavailable:
            logger.warning(f"模型 {model_name} 暫時無法連線 (ServiceUnavailable)，切換下一個...")
            last_error = "Service Unavailable"
            continue

        except (exceptions.NotFound, exceptions.InvalidArgument) as e:
            # 這是關鍵修復：捕捉 404 (NotFound) 或 400 (InvalidArgument)
            # 這通常發生在模型名稱錯誤或該模型版本尚未對此 API Key 開放
            logger.warning(f"模型 {model_name} 不存在或名稱無效 ({type(e).__name__})，跳過。錯誤訊息: {e}")
            last_error = f"Model Not Found/Invalid: {e}"
            continue

        except Exception as e:
            logger.error(f"模型 {model_name} 發生非預期錯誤: {e}")
            # 若發生未知錯誤，為避免無限迴圈或邏輯錯誤，這裡選擇拋出異常
            last_error = str(e)
            continue 

    raise Exception(f"所有模型都嘗試失敗。最後錯誤原因: {last_error}")


def ensure_recipes_loaded() -> None:
    """
    [修正版] 讀取本地 recipes.json，並強制將內容轉換為繁體中文。
    """
    global CACHED_RECIPES
    
    # 如果已經有資料，就不用再讀了 (避免重複讀取覆蓋)
    if CACHED_RECIPES:
        return

    # 1. 先嘗試讀取本地檔案
    if os.path.exists("recipes.json"):
        logger.info("發現本地食譜檔案，正在讀取並進行繁體化...")
        try:
            with open("recipes.json", "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            
            # ==== 關鍵修改：在這裡進行簡轉繁 ====
            cleaned_recipes = []
            for dish in raw_data:
                new_dish = dish.copy()
                # 針對可能出現簡體的欄位進行轉換
                if "name" in new_dish:
                    new_dish["name"] = cc.convert(new_dish["name"])
                if "description" in new_dish:
                    new_dish["description"] = cc.convert(new_dish["description"])
                if "ingredients" in new_dish:
                    new_dish["ingredients"] = cc.convert(str(new_dish["ingredients"]))
                
                cleaned_recipes.append(new_dish)
            # =================================
            
            CACHED_RECIPES = cleaned_recipes
            logger.info(f"本地食譜載入並轉繁體成功！共有 {len(CACHED_RECIPES)} 道菜")
            return
            
        except Exception as e:
            logger.error(f"讀取本地食譜失敗: {e}，將嘗試網路下載...")

    # 2. 如果本地沒有，才去網路下載 (網路下載的也要轉繁體)
    logger.info("正在從網路下載食譜資料庫...")
    try:
        response = requests.get(RECIPES_URL, timeout=60)
        if response.status_code == 200:
            raw_data = response.json()
            
            # ==== 網路下載的也要轉 ====
            cleaned_recipes = []
            for dish in raw_data:
                new_dish = dish.copy()
                if "name" in new_dish:
                    new_dish["name"] = cc.convert(new_dish["name"])
                if "description" in new_dish:
                    new_dish["description"] = cc.convert(new_dish["description"])
                cleaned_recipes.append(new_dish)
            # =======================

            CACHED_RECIPES = cleaned_recipes
            logger.info(f"網路食譜下載成功！共有 {len(CACHED_RECIPES)} 道菜")
            
            # 順便存檔 (建議存轉好的繁體版，下次讀取就不用轉了)
            with open("recipes.json", "w", encoding="utf-8") as f:
                json.dump(CACHED_RECIPES, f, ensure_ascii=False)
        else:
            logger.error(f"食譜下載失敗，狀態碼: {response.status_code}")
    except Exception as e:
        logger.error(f"下載食譜時發生錯誤: {e}")


def normalize_city(text: str) -> Optional[str]:
    """
    將使用者輸入的地區名稱標準化。
    """
    text = (text or "").strip()
    if not text:
        return "臺北市"
    
    normalized = CITY_ALIASES.get(text)
    if normalized:
        return normalized
    
    if text in CITY_ALIASES.values():
        return text
        
    return None


# ---- 資料庫操作函式 (Database Operations) ----

def save_user_preference(user_id: str, new_pref: str) -> str:
    if not user_id:
        return "無法識別使用者 ID。"
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
        logger.error(f"儲存偏好失敗: {e}")
        return "抱歉，儲存喜好時發生錯誤。"


def get_user_preference(user_id: str) -> str:
    if not user_id:
        return ""
    try:
        user = db.session.get(User, user_id)
        return user.preferences if user and user.preferences else "尚未設定"
    except Exception as e:
        logger.error(f"讀取偏好失敗: {e}")
        return "讀取偏好時發生錯誤"


def clear_user_preference(user_id: str) -> str:
    if not user_id:
        return "無法識別使用者 ID。"
    try:
        user = db.session.get(User, user_id)
        if user:
            user.preferences = None
            user.last_updated = datetime.datetime.now()
            db.session.commit()
        return "我已經忘記你所有的偏好了。"
    except Exception as e:
        db.session.rollback()
        logger.error(f"清除偏好失敗: {e}")
        return "抱歉，清除偏好時發生錯誤。"


def add_chat_history(user_id: str, role: str, content: str) -> None:
    if not user_id or not content:
        return
    try:
        new_chat = ChatHistory(line_user_id=user_id, role=role, content=content, timestamp=datetime.datetime.now())
        db.session.add(new_chat)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"新增對話紀錄失敗: {e}")


def get_chat_history(user_id: str, limit: int = 10) -> List[tuple]:
    if not user_id:
        return []
    try:
        stmt = db.select(ChatHistory).filter_by(line_user_id=user_id).order_by(ChatHistory.timestamp.desc()).limit(limit)
        rows = db.session.scalars(stmt).all()
        history = [(row.role, row.content) for row in rows]
        return list(reversed(history))
    except Exception as e:
        logger.error(f"讀取對話紀錄失敗: {e}")
        return []


def save_user_home_city(user_id: str, city_name: str) -> str:
    if not user_id:
        return "無法識別使用者 ID。"
    normalized_city = normalize_city(city_name)
    if not normalized_city:
        return f"抱歉，我不認識「{city_name}」。"
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
        logger.error(f"儲存地區失敗: {e}")
        return "抱歉，儲存地區時發生錯誤。"


def get_user_home_city(user_id: str) -> str:
    if not user_id:
        return "臺北市"
    try:
        user = db.session.get(User, user_id)
        return user.home_city if user and user.home_city else "臺北市"
    except Exception as e:
        logger.error(f"讀取地區失敗: {e}")
        return "臺北市"


# ---- 功能邏輯函式 (Business Logic) ----

def get_weather_36h(location: str = "臺北市") -> Dict[str, Any]:
    """
    取得未來 36 小時天氣預報。
    """
    if not CWA_API_KEY:
        return {"error": "尚未設定 CWA_API_KEY..."}
    
    url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"
    params = {"Authorization": CWA_API_KEY, "locationName": location}
    session = requests.Session()
    session.trust_env = False
    
    force_insecure = bool(CWA_INSECURE)
    attempts = [(False, False)] if force_insecure else [(True, certifi.where()), (False, False)]

    for _, verify_arg in attempts:
        try:
            response = session.get(url, params=params, timeout=12, verify=verify_arg)
            response.raise_for_status()
            data = response.json()
            locs = data.get("records", {}).get("location", [])
            
            if not locs:
                return {"error": f"查不到「{location}」的天氣資訊。"}
            
            loc = locs[0]
            # 解析 CWA 資料結構
            wx = loc["weatherElement"][0]["time"][0]["parameter"]["parameterName"]
            pop = loc["weatherElement"][1]["time"][0]["parameter"]["parameterName"]
            min_t = loc["weatherElement"][2]["time"][0]["parameter"]["parameterName"]
            ci = loc["weatherElement"][3]["time"][0]["parameter"]["parameterName"]
            max_t = loc["weatherElement"][4]["time"][0]["parameter"]["parameterName"]
            
            return {
                "location": location,
                "wx": wx,
                "pop": pop,
                "minT": min_t,
                "maxT": max_t,
                "ci": ci,
                "full_text": (f"{location} 今明短期預報：\n・天氣：{wx}\n・降雨機率：{pop}%\n・溫度：{min_t}°C ~ {max_t}°C\n・體感：{ci}")
            }
        except Exception:
            continue
            
    return {"error": "氣象資料連線失敗，稍後再試。"}


def get_clothing_advice(user_id: str, location: str) -> str:
    """
    取得 AI 穿搭建議。
    """
    if not GOOGLE_API_KEY:
        return "抱歉，AI 建議功能目前無法使用。"
        
    logger.info(f"正在為 {user_id} 生成位於 {location} 的穿搭建議...")
    
    try:
        weather_data = get_weather_36h(location)
        if "error" in weather_data:
            return f"抱歉，我拿不到「{location}」的天氣資訊。"
            
        user_prefs = get_user_preference(user_id)
        
        prompt_parts = [
            "你是「生活智慧管家」，一個專業且體貼的AI助理。",
            f"天氣：{weather_data['location']}, {weather_data.get('full_text', '')}",
            f"使用者偏好：{user_prefs}",
            "請給予簡潔、體貼的穿搭建議："
        ]
        
        response = generate_content_safe("\n".join(prompt_parts))
        return response.text
    except Exception as e:
        logger.error(f"生成穿搭建議時發生錯誤: {e}")
        return "抱歉，AI 在思考建議時發生錯誤，請稍後再試。"


def suggest_recipe_by_ingredients(user_id: str, ingredients: str) -> str:
    """
    根據食材推薦食譜 (RAG + Generative AI)。
    """
    if not GOOGLE_API_KEY:
        return "抱歉，AI 建議功能目前無法使用。"
    
    ensure_recipes_loaded()
    if not CACHED_RECIPES:
        return "食譜資料庫連線失敗。"

    sample_recipes = CACHED_RECIPES[:20] 
    recipe_names = "\n".join([f"・{r['name']} ({r.get('category', '未分類')})" for r in sample_recipes])
    
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
    
    try:
        response = generate_content_safe(prompt)
        if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
            return response.text
        else:
            return "AI 思考後沒有回應，請再試一次。"
    except Exception as e:
        logger.error(f"AI 食材推薦發生錯誤: {e}")
        return "AI 在分析食材時發生錯誤，請稍後再試。"


def get_random_recipe() -> str:
    """
    隨機取得一道食譜。
    """
    ensure_recipes_loaded()
    if not CACHED_RECIPES:
        return "食譜資料庫連線失敗，請稍後再試。"
    
    dish = random.choice(CACHED_RECIPES)
    name = dish.get('name', '神秘料理')
    category = dish.get('category', '未分類')
    desc = dish.get('description', '')[:100]
    return f"🍳 隨機推薦：{name}\n📂 分類：{category}\n📝 簡介：{desc}...\n\n(想知道怎麼做嗎？請輸入「食譜 {name}」)"


def get_fortune(user_id: str, user_mood: str) -> str:
    """
    取得運勢分析。
    """
    if not GOOGLE_API_KEY:
        return "抱歉，AI 運勢功能目前無法使用。"
    
    user_location = get_user_home_city(user_id)
    weather_data = get_weather_36h(user_location)
    
    if "error" in weather_data:
        weather_info = f"（無法取得 {user_location} 的天氣，請提供通用運勢）"
    else:
        weather_info = weather_data.get('full_text', '')
        
    system_prompt = (
       "你是「貼心生活氣象台」AI，專門提供情緒化、有趣的運勢報告。 "
        "請根據提供的天氣和心情資訊，生成一份運勢報告。\n"
        
        "**報告必須包含以下四項，且必須使用繁體中文、表情符號和條列式呈現：**\n"
        "1. **今日情緒天氣**：用一個天氣詞彙比喻使用者狀態。\n"
        "2. **今日美食吉籤**：給予一個適合今日心情/天氣的美食建議。\n"
        "3. **今日穿搭提醒**：提供基於天氣的簡短穿搭建議。\n"
        "4. **今日幸運小物 (必填)**：請務必指定一個簡單的、容易攜帶的「幸運小物」。\n"
        
        "請將所有資訊整合為一個簡潔的回覆，總長度不超過 150 字。"
    )
    
    final_prompt = f"{system_prompt}\n\n請幫我生成一份運勢報告。今日天氣是：{weather_info}。我的心情是：{user_mood}"

    try:
        response = generate_content_safe(final_prompt)
        return response.text
    except Exception as e:
        logger.error(f"運勢生成失敗: {e}")
        return "運勢生成器故障了！請稍後再試試看。"


def get_substitute_suggestion(target_ingredient: str) -> str:
    """
    取得食材替代建議。
    """
    if not GOOGLE_API_KEY:
        return "抱歉，AI 建議功能目前無法使用。"
    
    prompt = f"""
    你是「聰明主廚 AI」，專門提供專業且實用的食材替代方案。
    
    使用者想知道：【{target_ingredient}】的最佳替代品是什麼？
    
    任務：
    1. **提供 3 個最佳替代方案**（例如：如果你要找雞蛋的替代品，可以提供香蕉泥、亞麻籽粉、或市售蛋替代品）。
    2. 針對每個替代品，**簡要說明**它在料理中的作用（例如：提供黏性、增加甜度、維持濕度）。
    3. 說明使用替代品時，**份量應該如何調整**（例如：1 顆雞蛋約等於半根香蕉泥）。
    4. 最後鼓勵使用者在緊急時試試看。
    5. 請使用親切、幽默的語氣，並使用繁體中文和條列式呈現，總長度不超過 150 字。
    """
    
    try:
        response = generate_content_safe(prompt)
        return response.text
    except Exception as e:
        logger.error(f"替代建議生成失敗: {e}")
        return "AI 忙碌中，請稍後再試。"


def get_nearby_places(lat: float, lng: float) -> Union[Dict[str, Any], Dict[str, str]]:
    """
    取得附近景點與 AI 導覽。
    """
    if not GOOGLE_MAPS_API_KEY:
        return {"error": "錯誤：找不到 Google Maps API Key。"}

    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    params = {
        "location": f"{lat},{lng}",
        "radius": 1500,
        "type": "tourist_attraction",
        "language": "zh-TW",
        "key": GOOGLE_MAPS_API_KEY
    }

    try:
        response = requests.get(url, params=params)
        data = response.json()
        
        if data.get("status") == "OK":
            results = data.get("results", [])[:5]
            if not results:
                return {"error": "附近好像沒有特別著名的景點耶。"}

            places_for_ai = []
            places_for_line = []
            
            for i, place in enumerate(results):
                name = place.get("name")
                rating = place.get("rating", "無評分")
                place_id = place.get("place_id")
                
                encoded_name = quote(name)
                # 建構 Google Maps 連結
                maps_url = f"https://www.google.com/maps/search/?api=1&query={encoded_name}&query_place_id={place_id}"
                
                places_for_line.append({
                    "name": name,
                    "maps_url": maps_url
                })
                
                places_for_ai.append(
                    f"{i + 1}. {name} (⭐{rating})"
                )
            
            places_str = "\n".join(places_for_ai)

            prompt = f"""
            使用者現在位於某個地點，附近有以下 5 個景點編號與名稱：
            {places_str}

            請扮演一位「熱情活潑的在地導遊」，根據以上清單：
            1. 挑選 3 個你認為最值得去的地方。
            2. 用生動的語言介紹它們。
            3. **回覆內容只需要生成介紹文字，但必須明確提到你推薦的景點名稱或編號，以便使用者知道要點選哪個按鈕。**
            4. 加上 Emoji。
            """
            
            try:
                ai_response = generate_content_safe(prompt)
                return {
                    "ai_text": ai_response.text, 
                    "places_data": places_for_line,
                    "error": None
                }
            except Exception as e:
                logger.error(f"AI 導遊生成失敗: {e}")
                return {"error": "AI 景點介紹生成失敗。"}
        else:
            logger.warning(f"Google Maps API 回傳狀態非 OK: {data.get('status')}")
            return {"error": "Google Maps 暫時無法回應，請稍後再試。"}
    except Exception as e:
        logger.error(f"Maps API Error: {e}")
        return {"error": "搜尋景點時發生錯誤。"}


def analyze_intent(user_text: str) -> Dict[str, Any]:
    """
    [新版] 使用 BGE-M3 向量相似度來判斷使用者意圖，並提取關鍵字。
    """
    # 1. 將使用者輸入轉為向量
    query_embedding = embedding_model.encode(user_text, convert_to_tensor=True)
    
    # 2. 計算相似度 (Cosine Similarity)
    cos_scores = util.cos_sim(query_embedding, corpus_embeddings)[0]
    
    # 3. 找出最高分的那個
    best_score = torch.max(cos_scores)
    best_idx = torch.argmax(cos_scores).item()
    predicted_intent = intent_map[best_idx]
    
    logger.info(f"輸入: '{user_text}' | 匹配: '{corpus_sentences[best_idx]}' | 意圖: {predicted_intent} | 分數: {best_score:.4f}")
    
    # 4. 設定門檻值 (建議 0.5 ~ 0.55)
    # 如果分數太低，代表使用者說的話不在我們的守備範圍，轉交給閒聊模式
    if best_score < 0.65:
        return {"intent": "chat"}

    # ---- 5. 參數萃取 (Parameter Extraction) ----
    # 這裡是透過簡單的規則 (Rule-based) 從句子中挖出參數
    
    result = {"intent": predicted_intent}

    # (A) 地點相關：天氣、穿搭、附近景點
    # 邏輯：掃描全域變數 CITY_ALIASES 看有沒有命中
    if predicted_intent in ["weather", "clothing_advice", "search_nearby"]:
        found_city = None
        for alias, real_name in CITY_ALIASES.items():
            if alias in user_text:
                found_city = real_name
                break
        result["location"] = found_city  # 如果沒抓到會是 None，主程式會去抓使用者的預設地區

    # (B) 查詢食譜
    # 邏輯：把「食譜、教我、怎麼做」這些功能詞刪掉，剩下的就是菜名
    elif predicted_intent == "search_recipe":
        stop_words = [
            "食譜", "教我", "做法", "作法", "怎麼做", "怎麼煮", "製作", 
            "我想吃", "有沒有", "幫我找", "查詢", "教學"
        ]
        clean_text = user_text
        for word in stop_words:
            clean_text = clean_text.replace(word, "")
        
        # 如果刪完變空字串 (例如使用者只打"食譜")，就回傳原文以免報錯
        result["keyword"] = clean_text.strip() if clean_text.strip() else user_text

    # (C) 冰箱剩菜/食材推薦
    # 邏輯：把「冰箱、只剩、我有」刪掉，剩下的就是食材清單
    elif predicted_intent == "suggest_by_ingredients":
        stop_words = [
            "冰箱", "只剩", "剩下", "只有", "我有", "可以做什麼", 
            "料理", "推薦", "幫我想", "食材"
        ]
        clean_text = user_text
        for word in stop_words:
            clean_text = clean_text.replace(word, "")
        result["ingredients"] = clean_text.strip()

    # (D) 食材替代
    # 邏輯：把「替代、沒有」刪掉
    elif predicted_intent == "substitute_ingredient":
        stop_words = [
            "沒有", "缺", "少了", "可以用", "什麼", "代替", "替代", 
            "換成", "怎麼辦"
        ]
        clean_text = user_text
        for word in stop_words:
            clean_text = clean_text.replace(word, "")
        result["target"] = clean_text.strip()

    # (E) 隨機食譜 (目前不需要參數，但也預留擴充空間)
    elif predicted_intent == "random_recipe":
        # 未來可以抓取 "晚餐"、"午餐" 等關鍵字
        pass

    return result


def search_recipe_by_ai(user_text: str) -> str:
    """
    [升級版] 第二階段檢索：使用向量相似度搜尋食譜。
    優點：抗錯字、抗簡繁差異、懂語意 (辣辣的雞 -> 宮保雞丁)。
    """
    global CACHED_RECIPES, RECIPE_EMBEDDINGS
    
    if not GOOGLE_API_KEY:
        return "抱歉，AI 功能目前無法使用。"
    
    # 確保資料已載入
    ensure_recipes_loaded()
    if not CACHED_RECIPES or RECIPE_EMBEDDINGS is None:
        return "食譜資料庫尚未建立索引。"

    # 1. 將使用者的輸入 (例如: "教我做宫保鸡丁") 轉成向量
    # 注意：這裡不需要刻意清洗關鍵字，直接丟整句也可以，BGE-M3 很強
    # 但如果前面有 analyze_intent 洗出來的 keyword 更好
    query_embedding = embedding_model.encode(user_text, convert_to_tensor=True)
    
    # 2. 計算相似度 (Query vs 所有菜名)
    # cos_sim 回傳的是一個矩陣，我們取 [0] 代表第一個 query 的結果
    cos_scores = util.cos_sim(query_embedding, RECIPE_EMBEDDINGS)[0]
    
    # 3. 找出分數最高的那個
    best_score = torch.max(cos_scores)
    best_idx = torch.argmax(cos_scores).item()
    
    target_dish = CACHED_RECIPES[best_idx]
    dish_name = target_dish['name']
    
    logger.info(f"食譜搜尋: '{user_text}' -> 匹配: '{dish_name}' | 分數: {best_score:.4f}")
    
    # 4. 設定門檻值 (Threshold)
    # 建議設在 0.6 ~ 0.7 之間，因為是針對特定領域的搜尋
    if best_score < 0.65:
        return f"抱歉，我找不到跟「{user_text}」相關的食譜。要不要換個說法試試？"

    # ==========================================
    # 以下是原本的 Gemini RAG 生成邏輯 (完全不用動)
    # ==========================================
    dish_data_str = json.dumps(target_dish, ensure_ascii=False)
    
    prompt = f"""
    你現在是一位專業的五星級大廚。
    使用者想知道「{dish_name}」的作法。
    
    以下是這道菜的詳細原始資料 (JSON 格式)：
    {dish_data_str}
    
    任務：
    請根據上面的原始資料，執行以下步驟：
    1. 將所有內容翻譯為高質量、流暢的繁體中文。
    2. 用親切、易懂的方式，寫一份完整的食譜教學。
    
    格式要求：
    1. 開頭先介紹這道菜。
    2. 列出「食材清單」。
    3. 列出「詳細步驟」。
    4. 最後給一個「大廚小撇步」。
    """
    
    try:
        response = generate_content_safe(prompt)
        return response.text
    except Exception as e:
        logger.error(f"AI 食譜教學生成失敗: {e}")
        return "AI 在讀取食譜時頭暈了，請稍後再試。"


# ---- Flask 路由與主要處理 (Routes & Main Handler) ----

@app.get("/health")
def health() -> str:
    return "OK"


@app.route("/webhook", methods=['POST'])
def webhook() -> str:
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True) or ""
    
    if not signature or not body.strip():
        return "OK"
    
    try:
        events = parser.parse(body, signature)
    except Exception as e:
        logger.error(f"Webhook 簽章驗證失敗: {e}")
        abort(400)
        return "Invalid Signature"

    # 使用 ApiClient 初始化 MessagingApi 與 MessagingApiBlob (處理多媒體)
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_blob_api = MessagingApiBlob(api_client) # 新增 Blob API 支援
        
        # 定義功能選單 (Quick Reply)
        feature_quick_reply = QuickReply(
            items=[
                QuickReplyItem(action=MessageAction(label="🌤️ 查詢天氣", text="天氣")),
                QuickReplyItem(action=MessageAction(label="👕 客製穿搭建議", text="今天穿什麼")),
                QuickReplyItem(action=MessageAction(label="🗺️ 附近景點", text="附近哪裡好玩")),
                QuickReplyItem(action=MessageAction(label="🔮 今日運勢", text="今日運勢")),
                QuickReplyItem(action=MessageAction(label="🍽️ 食譜建議", text="今天吃什麼")),
                QuickReplyItem(action=MessageAction(label="⚙️ 設定：穿搭偏好", text="設定穿搭偏好")), 
                QuickReplyItem(action=MessageAction(label="🔑 設定地區", text="設定地區")),
            ]
        )
        
        for event in events:
            # 處理文字訊息
            if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
                handle_text_message(event, line_bot_api, feature_quick_reply)

            # 處理位置訊息
            elif isinstance(event, MessageEvent) and isinstance(event.message, LocationMessageContent):
                handle_location_message(event, line_bot_api)
            
            # 處理圖片訊息 (新增)
            elif isinstance(event, MessageEvent) and isinstance(event.message, ImageMessageContent):
                handle_image_message(event, line_bot_api, line_bot_blob_api)

    return "OK"


def handle_text_message(event: MessageEvent, line_bot_api: MessagingApi, quick_reply: QuickReply) -> None:
    """
    處理文字訊息的邏輯分流。
    """
    text = (event.message.text or "").strip()
    reply_token = event.reply_token
    user_id = event.source.user_id if event.source else None

    if not user_id:
        return

    # 1. 記錄使用者對話
    add_chat_history(user_id, "user", text)
    
    # 2. 狀態管理與指令處理
    with app.app_context():
        user = db.session.get(User, user_id)
        if not user:
            user = User(line_user_id=user_id)
            db.session.add(user)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
        
        user_state = user.session_state
        reply_msg_obj = None
        reply_text = ""

        # 問候語生成
        current_hour = datetime.datetime.now().hour
        if 5 <= current_hour < 12:
            greeting = "早安！☀️"
        elif 12 <= current_hour < 18:
            greeting = "午安！☕️"
        else:
            greeting = "晚安！🌙"

        # 優先處理 Session 狀態 (例如：正在等待使用者輸入偏好)
        if user_state:
            user.session_state = None
            if user_state == "awaiting_region":
                reply_text = save_user_home_city(user_id, text)
            elif user_state == "awaiting_preference":
                reply_text = save_user_preference(user_id, text)
            elif user_state == "awaiting_mood":
                reply_text = get_fortune(user_id, text)
            else:
                reply_text = "發生錯誤，請再試一次。"
            
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            reply_msg_obj = TextMessage(text=reply_text)

        # 處理明確指令
        elif text == "記住我": 
            user.session_state = "awaiting_preference"
            db.session.commit()
            reply_text = "好的，請告訴我您的「穿搭偏好」：\n（例如：我怕冷、我喜歡穿短褲）"
            reply_msg_obj = TextMessage(text=reply_text)
            
        elif text == "設定地區": 
            user.session_state = "awaiting_region"
            db.session.commit()
            reply_text = "好的，請輸入您要設定的「預設地區」：\n（例如：臺北市）"
            reply_msg_obj = TextMessage(text=reply_text)

        elif text == "我的偏好":
            prefs = get_user_preference(user_id)
            reply_text = f"您目前的偏好設定：\n\n{prefs}"
            reply_msg_obj = TextMessage(text=reply_text)

        elif text == "忘記我":
            reply_text = clear_user_preference(user_id)
            reply_msg_obj = TextMessage(text=reply_text)
        
        # AI 意圖判斷與處理
        else:
            ai_result = analyze_intent(text)
            intent = ai_result.get("intent")
            logger.info(f"User: {text} -> Intent: {intent}")

            if intent == "search_recipe":
                keyword = ai_result.get("keyword") or text
                reply_text = search_recipe_by_ai(keyword)
                
            elif intent == "random_recipe":
                reply_text = get_random_recipe()

            elif intent == "suggest_by_ingredients":
                ingredients = ai_result.get("ingredients") or ""
                reply_text = suggest_recipe_by_ingredients(user_id, ingredients)
                
            elif intent == "weather":
                city = ai_result.get("location")
                if not city:
                    city = get_user_home_city(user_id)
                norm_city = normalize_city(city)
                if norm_city:
                    w_data = get_weather_36h(norm_city)
                    reply_text = w_data.get("full_text", "查詢失敗")
                else:
                    reply_text = f"抱歉，我不確定您問的是哪個縣市 ({city})。"

            elif intent == "clothing_advice":
                city = get_user_home_city(user_id)
                reply_text = get_clothing_advice(user_id, city)
                if reply_text and "抱歉" not in reply_text:
                    reply_text += "\n\n---\n💡 **貼心提醒：** 輸入「記住我」可設定個人偏好喔！"
                reply_msg_obj = TextMessage(text=reply_text or "抱歉，目前無法提供穿搭建議。")

            elif intent == "fortune":
                user.session_state = "awaiting_mood" 
                db.session.commit()
                reply_text = f"{greeting} 在為你分析今日運勢之前，請用幾個字告訴我你現在的心情如何呢？😊"
                reply_msg_obj = TextMessage(text=reply_text)
            
            elif intent == "substitute_ingredient":
                target = ai_result.get("target") or text
                reply_text = get_substitute_suggestion(target)

            elif intent == "search_nearby":
                reply_text = "沒問題！請點擊下方按鈕，傳送您的位置給我，我來幫您找找附近好玩的地方！👇"
                qr_buttons = QuickReply(
                    items=[
                        QuickReplyItem(action=MessageAction(label="📍 傳送我的位置", type="location"))
                    ]
                )
                reply_msg_obj = TextMessage(text=reply_text, quick_reply=qr_buttons)
            
            else: # intent == "chat"
                reply_text = f"你說了：「{text}」\n需要我幫你做什麼嗎？您可以試試看下方的快速選單："
                reply_msg_obj = TextMessage(text=reply_text, quick_reply=quick_reply)

        # 統一回覆
        if reply_text and not reply_msg_obj:
            reply_msg_obj = TextMessage(text=reply_text)

        if reply_msg_obj:
            add_chat_history(user_id, "bot", reply_text or "image/template")
            line_bot_api.reply_message(
                ReplyMessageRequest(reply_token=reply_token, messages=[reply_msg_obj])
            )


def handle_location_message(event: MessageEvent, line_bot_api: MessagingApi) -> None:
    """
    處理位置訊息 (Google Maps 查詢)。
    """
    latitude = event.message.latitude
    longitude = event.message.longitude
    
    result = get_nearby_places(latitude, longitude)
    
    if "error" in result and result["error"]:
        reply_msg = TextMessage(text=result["error"])
    else:
        ai_text = result.get("ai_text", "")
        places_data = result.get("places_data", [])
        
        quick_reply_items = []
        for p in places_data:
            # LINE 按鈕長度限制
            button_label = f"📍 導航: {p['name'][:10]}..." 
            quick_reply_items.append(
                    QuickReplyItem(
                    action=URIAction(label=button_label, uri=p['maps_url'])
                    )
            )
        
        final_text = ai_text + "\n\n---\n\n點擊下方按鈕，直接導航至 AI 推薦的景點："
        
        reply_msg = TextMessage(
            text=final_text,
            quick_reply=QuickReply(items=quick_reply_items)
        )

    line_bot_api.reply_message(
        ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[reply_msg]
        )
    )


def handle_image_message(event: MessageEvent, line_bot_api: MessagingApi, line_bot_blob_api: MessagingApiBlob) -> None:
    """
    處理圖片訊息：辨識食材並推薦食譜 (Gemini Vision)。
    """
    user_id = event.source.user_id
    message_id = event.message.id
    reply_token = event.reply_token

    # 1. 取得圖片內容 (Binary)
    try:
        logger.info(f"正在下載圖片: {message_id}")
        message_content = line_bot_blob_api.get_message_content(message_id)
        # message_content 本身是 bytes
    except Exception as e:
        logger.error(f"下載圖片失敗: {e}")
        return

    # 2. 準備 Prompt 與 圖片資料
    # Gemini 接受 {'mime_type': '...', 'data': bytes} 的格式
    image_part = {
        'mime_type': 'image/jpeg', 
        'data': message_content
    }
    
    prompt = """
    請扮演一位專業的「食材辨識與料理顧問」。
    
    請仔細觀察這張圖片：
    1. **辨識食材**：列出你看到的所有食材。
    2. **推薦料理**：根據這些食材，推薦 1 道最適合的料理。
    3. **簡易作法**：用 3 個步驟簡單說明這道菜怎麼做。
    
    請用親切、活潑的語氣回覆，並加上表情符號。
    如果圖片中沒有食材，請幽默地回應使用者。
    """

    # 3. 呼叫 Gemini (Vision)
    # 我們可以直接使用 generate_content_safe，因為它支援傳入 list
    try:
        response = generate_content_safe([prompt, image_part])
        reply_text = response.text
    except Exception as e:
        logger.error(f"Gemini 圖片辨識失敗: {e}")
        reply_text = "抱歉，我看不太清楚這張圖片裡的食材，可以再拍清楚一點嗎？😅"

    # 4. 回覆使用者
    line_bot_api.reply_message(
        ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=reply_text)]
        )
    )


# ---- 程式進入點 (Entry Point) ----

if __name__ == "__main__":
    # 初始化資料庫表格 (若不存在)
    try:
        with app.app_context():
            db.create_all()  
        logger.info("SQLAlchemy 資料庫表格檢查/建立完成。")
    except Exception as e:
        logger.critical(f"建立資料庫表格時發生嚴重錯誤: {e}")

    # 啟動伺服器
    port = int(os.getenv("PORT", 3000))
    logger.info(f"伺服器即將啟動於 Port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)