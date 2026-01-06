import google.generativeai as genai
import os
from dotenv import load_dotenv

load_dotenv()

# .envから読み込むか、直接ここに貼り付けてテストしてください
api_key = os.getenv("GEMINI_API_KEY") 
genai.configure(api_key=api_key)

print("--- 利用可能なモデル一覧 ---")
try:
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(m.name)
except Exception as e:
    print(f"エラーが発生しました: {e}")