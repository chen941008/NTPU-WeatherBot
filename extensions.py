# extensions.py
from flask_sqlalchemy import SQLAlchemy
from sentence_transformers import SentenceTransformer
import opencc

# 這裡只宣告，先不綁定 app
db = SQLAlchemy()

# 初始化 AI 模型 (這樣 services_ai.py 就可以 import 它)
print("正在載入 BGE-M3 模型...", flush=True)
embedding_model = SentenceTransformer('BAAI/bge-m3')

# 初始化 OpenCC
cc = opencc.OpenCC('s2t')