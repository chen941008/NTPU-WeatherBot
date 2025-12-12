import datetime
from extensions import db

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