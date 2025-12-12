# services_ai.py
import os
import json
import logging
import requests
import torch
import random
from typing import Dict, Any, List, Union
import google.generativeai as genai
from google.api_core import exceptions
from sentence_transformers import util

# 引用專案內模組
from extensions import embedding_model, cc, db
from models import User

# 從 config 引入必要的變數
from config import (
    INTENT_KNOWLEDGE_BASE, 
    CITY_ALIASES, 
    RECIPES_URL, 
    GOOGLE_API_KEY, 
    MODEL_PRIORITY
)

logger = logging.getLogger(__name__)

# 全域變數
CACHED_RECIPES = []
RECIPE_EMBEDDINGS = None
corpus_embeddings = None
corpus_sentences = []
intent_map = []

# 初始化 Gemini
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)

# ==========================================
# 1. 核心 AI 呼叫函式 (原本缺少的!)
# ==========================================
def generate_content_safe(prompt_parts: Union[str, List[Any]]) -> Any:
    """
    依序嘗試 MODEL_PRIORITY 中的模型來生成內容。
    """
    if not GOOGLE_API_KEY:
        raise Exception("API Key 未設定")

    last_error = None

    for model_name in MODEL_PRIORITY:
        try:
            current_model = genai.GenerativeModel(model_name)
            response = current_model.generate_content(prompt_parts)
            return response

        except exceptions.ResourceExhausted:
            logger.warning(f"模型 {model_name} 額度已滿，切換下一個...")
            continue
        except exceptions.ServiceUnavailable:
            logger.warning(f"模型 {model_name} 暫時無法連線，切換下一個...")
            continue
        except (exceptions.NotFound, exceptions.InvalidArgument) as e:
            logger.warning(f"模型 {model_name} 不存在或無效，跳過。")
            continue
        except Exception as e:
            logger.error(f"模型 {model_name} 發生錯誤: {e}")
            last_error = str(e)
            continue 

    raise Exception(f"所有模型都嘗試失敗。最後錯誤: {last_error}")

# ==========================================
# 2. 啟動與載入邏輯
# ==========================================
def startup_load_recipes():
    """
    啟動載入：讀取 -> 轉繁體 -> 建立兩階段向量索引
    """
    global CACHED_RECIPES, RECIPE_EMBEDDINGS, corpus_embeddings, corpus_sentences, intent_map
    
    recipe_json_path = "recipes.json"
    data = []
    cleaned = []

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
            if "name" in new_dish:
                new_dish["name"] = cc.convert(new_dish["name"])
            if "description" in new_dish:
                new_dish["description"] = cc.convert(new_dish["description"])
            if "ingredients" in new_dish:
                new_dish["ingredients"] = cc.convert(str(new_dish["ingredients"]))
            cleaned.append(new_dish)
        
        CACHED_RECIPES = cleaned
        print(f"✅ 食譜載入並繁體化完成！共 {len(CACHED_RECIPES)} 道。", flush=True)
        
        # 4. 建立食譜名稱向量索引
        if CACHED_RECIPES:
            print("🍳 正在為食譜名稱建立專屬向量索引...", flush=True)
            try:
                recipe_names = [r['name'] for r in CACHED_RECIPES]
                RECIPE_EMBEDDINGS = embedding_model.encode(recipe_names, convert_to_tensor=True)
                print(f"✅ 食譜向量索引建立完成！", flush=True)

                # 動態注入意圖
                if "search_recipe" in INTENT_KNOWLEDGE_BASE:
                    INTENT_KNOWLEDGE_BASE["search_recipe"].extend(recipe_names)
                    print(f"💉 已注入 {len(recipe_names)} 個菜名到意圖系統。", flush=True)
            except Exception as e:
                print(f"❌ 建立向量索引時發生錯誤: {e}", flush=True)

    # 5. 建立意圖向量索引 (Knowledge Base)
    print("🧠 正在將意圖資料庫轉為向量...", flush=True)
    corpus_sentences = []
    intent_map = [] 

    for intent, examples in INTENT_KNOWLEDGE_BASE.items():
        for example in examples:
            corpus_sentences.append(example)
            intent_map.append(intent)

    corpus_embeddings = embedding_model.encode(corpus_sentences, convert_to_tensor=True)
    print("✅ BGE-M3 意圖索引建立完成！", flush=True)

def ensure_recipes_loaded():
    if not CACHED_RECIPES:
        startup_load_recipes()

# ==========================================
# 3. 意圖分析與搜尋邏輯
# ==========================================
def analyze_intent(user_text: str) -> Dict[str, Any]:
    global corpus_embeddings
    if corpus_embeddings is None:
        startup_load_recipes()

    query_embedding = embedding_model.encode(user_text, convert_to_tensor=True)
    cos_scores = util.cos_sim(query_embedding, corpus_embeddings)[0]
    best_score = torch.max(cos_scores)
    best_idx = torch.argmax(cos_scores).item()
    predicted_intent = intent_map[best_idx]
    
    logger.info(f"輸入: '{user_text}' | 意圖: {predicted_intent} | 分數: {best_score:.4f}")
    
    if best_score < 0.65:
        return {"intent": "chat"}

    result = {"intent": predicted_intent}

    # 參數萃取邏輯
    if predicted_intent in ["weather", "clothing_advice", "search_nearby"]:
        found_city = None
        for alias, real_name in CITY_ALIASES.items():
            if alias in user_text:
                found_city = real_name
                break
        result["location"] = found_city

    elif predicted_intent == "search_recipe":
        result["keyword"] = user_text 

    elif predicted_intent == "suggest_by_ingredients":
        stop_words = ["冰箱", "只剩", "剩下", "只有", "我有", "可以做什麼", "料理", "推薦", "食材"]
        clean_text = user_text
        for word in stop_words:
            clean_text = clean_text.replace(word, "")
        result["ingredients"] = clean_text.strip()

    elif predicted_intent == "substitute_ingredient":
        stop_words = ["沒有", "缺", "少了", "可以用", "什麼", "代替", "替代", "換成", "怎麼辦"]
        clean_text = user_text
        for word in stop_words:
            clean_text = clean_text.replace(word, "")
        result["target"] = clean_text.strip()

    return result

def search_recipe_by_ai(user_text: str) -> str:
    global CACHED_RECIPES, RECIPE_EMBEDDINGS
    
    ensure_recipes_loaded()
    if not CACHED_RECIPES or RECIPE_EMBEDDINGS is None:
        return "食譜資料庫尚未建立索引。"

    query_embedding = embedding_model.encode(user_text, convert_to_tensor=True)
    cos_scores = util.cos_sim(query_embedding, RECIPE_EMBEDDINGS)[0]
    best_score = torch.max(cos_scores)
    best_idx = torch.argmax(cos_scores).item()
    
    target_dish = CACHED_RECIPES[best_idx]
    dish_name = target_dish['name']
    
    logger.info(f"食譜搜尋: '{user_text}' -> '{dish_name}' ({best_score:.4f})")
    
    if best_score < 0.65:
        return f"抱歉，我找不到跟「{user_text}」相關的食譜。"

    dish_data_str = json.dumps(target_dish, ensure_ascii=False)
    prompt = f"你是專業大廚。請將此食譜資料：{dish_data_str}，整理成繁體中文教學。包含介紹、食材、步驟、小撇步。"
    
    try:
        response = generate_content_safe(prompt)
        return response.text
    except Exception as e:
        logger.error(f"生成失敗: {e}")
        return "AI 生成食譜時發生錯誤。"

# ==========================================
# 4. 其他 AI 服務 (補齊原本 app.py 遺失的功能)
# ==========================================

def get_clothing_advice(user_id: str, location: str) -> str:
    """
    客製化穿搭建議
    """
    # 避免循環引用，在函式內引用 services_basic
    from services_basic import get_weather_36h, get_user_preference
    
    weather_data = get_weather_36h(location)
    if "error" in weather_data:
        return f"抱歉，我拿不到「{location}」的天氣資訊。"
    
    user_prefs = get_user_preference(user_id)
    prompt = f"你是管家。天氣：{weather_data['full_text']}。偏好：{user_prefs}。請給穿搭建議。"
    try:
        return generate_content_safe(prompt).text
    except:
        return "AI 暫時無法回應。"

def get_random_recipe() -> str:
    """
    隨機食譜
    """
    ensure_recipes_loaded()
    if not CACHED_RECIPES: return "資料庫未載入。"
    dish = random.choice(CACHED_RECIPES)
    return f"🍳 推薦：{dish['name']}\n{dish.get('description','')[:50]}...\n(想學做這道菜嗎？請輸入「食譜 {dish['name']}」)"

def suggest_recipe_by_ingredients(user_id: str, ingredients: str) -> str:
    """
    冰箱食材推薦
    """
    ensure_recipes_loaded()
    # 取前 30 道菜當作參考樣本給 AI
    sample_recipes = "\n".join([r['name'] for r in CACHED_RECIPES[:30]])
    prompt = f"""
    你是聰明主廚。使用者有食材：【{ingredients}】。
    
    請推薦 1~2 道適合的料理，並說明理由。
    如果資料庫裡的菜 ({sample_recipes}...) 適合，優先推薦，並引導使用者查詢。
    如果不適合，請發揮創意推薦簡單料理。
    """
    try:
        return generate_content_safe(prompt).text
    except:
        return "AI 思考食材中..."

def get_fortune(user_id: str, mood: str) -> str:
    """
    運勢分析
    """
    # 需要天氣資訊來增加運勢的豐富度
    from services_basic import get_user_home_city, get_weather_36h
    
    city = get_user_home_city(user_id)
    w_data = get_weather_36h(city)
    w_info = w_data.get("full_text", "天氣未知")

    prompt = f"""
    你是貼心生活氣象台 AI。
    今日天氣：{w_info}。
    使用者心情：{mood}。
    
    請生成一份運勢報告 (繁體中文)，包含：
    1. 今日情緒天氣
    2. 美食吉籤
    3. 穿搭提醒
    4. 幸運小物
    """
    try:
        return generate_content_safe(prompt).text
    except:
        return "運勢生成器連線中..."

def get_substitute_suggestion(target: str) -> str:
    """
    食材替代建議
    """
    prompt = f"使用者想知道【{target}】的替代品。請列出 3 個最佳替代方案，並說明比例與口感差異。"
    try:
        return generate_content_safe(prompt).text
    except:
        return "AI 查詢替代食材中..."

def generate_tour_guide_text(places_str: str) -> str:
    """
    [新增] 生成導遊介紹文案 (配合 services_basic 的 get_nearby_places)
    """
    prompt = f"""
    使用者附近有以下景點：
    {places_str}

    請扮演一位「熱情活潑的在地導遊」：
    1. 挑選 3 個值得去的地方。
    2. 用生動語言介紹。
    3. 加上 Emoji。
    """
    try:
        return generate_content_safe(prompt).text
    except:
        return "附近有不少好玩的景點喔！(AI 導遊暫時休息中)"