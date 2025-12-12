# services_basic.py
import requests
import datetime
import certifi
from urllib.parse import quote
from typing import Dict, Any, List, Optional, Union

# 引入專案內的模組
from extensions import db
from models import User, ChatHistory
from config import (
    CWA_API_KEY, CWA_INSECURE, GOOGLE_MAPS_API_KEY, 
    CITY_ALIASES, GOOGLE_API_KEY
)
import logging

logger = logging.getLogger(__name__)

# ---- 資料庫操作輔助函式 ----

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

def normalize_city(text: str) -> Optional[str]:
    text = (text or "").strip()
    if not text:
        return "臺北市"
    normalized = CITY_ALIASES.get(text)
    if normalized:
        return normalized
    if text in CITY_ALIASES.values():
        return text
    return None

# ---- 天氣與地圖功能 ----

def get_weather_36h(location: str = "臺北市") -> Dict[str, Any]:
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

def get_nearby_places(lat: float, lng: float) -> Union[Dict[str, Any], Dict[str, str]]:
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

            places_for_line = []
            places_for_ai = [] # 用於回傳給 AI 產生文案
            
            for i, place in enumerate(results):
                name = place.get("name")
                rating = place.get("rating", "無評分")
                place_id = place.get("place_id")
                encoded_name = quote(name)
                maps_url = f"https://www.google.com/maps/search/?api=1&query={encoded_name}&query_place_id={place_id}"
                
                places_for_line.append({"name": name, "maps_url": maps_url})
                places_for_ai.append(f"{i + 1}. {name} (⭐{rating})")
            
            # 回傳結構修改以符合邏輯
            return {
                "places_ai_str": "\n".join(places_for_ai),
                "places_data": places_for_line,
                "error": None
            }
        else:
            return {"error": "Google Maps 暫時無法回應，請稍後再試。"}
    except Exception as e:
        return {"error": "搜尋景點時發生錯誤。"}