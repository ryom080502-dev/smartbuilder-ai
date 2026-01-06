import os, time, json, shutil, io
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Depends, status
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer
import google.generativeai as genai
from dotenv import load_dotenv
from jose import JWTError, jwt
from passlib.context import CryptContext
import pandas as pd
from fpdf import FPDF

load_dotenv()

# --- 認証設定 ---
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-123") # 本番ではランダムな文字列に
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 # 24時間有効
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Gemini / LINE 設定
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-2.5-pro')

app = FastAPI()
UPLOAD_DIR, DB_FILE, USERS_FILE = "uploads", "records.json", "users.json"
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# --- ユーティリティ ---
def load_json(path):
    if not os.path.exists(path): return [] if "records" in path else {}
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
        return json.loads(content) if content else ([] if "records" in path else {})

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# --- 認証ロジック ---
def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(request: Request):
    token = request.headers.get("Authorization")
    if not token or not token.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        payload = jwt.decode(token.split(" ")[1], SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub") # user_id
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# --- 初期ユーザー作成 (初回起動時のみ) ---
def init_admin():
    users = load_json(USERS_FILE)
    if "admin" not in users:
        # 初期ID: admin / パスワード: password (適宜変更してください)
        users["admin"] = {
            "password": pwd_context.hash("password"),
            "plan": "premium",
            "limit": 100,
            "used": 0
        }
        save_json(USERS_FILE, users)
init_admin()

# --- エンドポイント ---

@app.get("/", response_class=HTMLResponse)
async def index():
    with open("index.html", "r", encoding="utf-8") as f: return f.read()

@app.post("/login")
async def login(data: dict):
    users = load_json(USERS_FILE)
    user_id = data.get("id")
    password = data.get("password")
    
    if user_id in users and pwd_context.verify(password, users[user_id]["password"]):
        token = create_access_token(data={"sub": user_id})
        return {"token": token}
    raise HTTPException(status_code=401, detail="IDまたはパスワードが違います")

@app.get("/api/status")
async def get_status(user_id: str = Depends(get_current_user)):
    return {"records": load_json(DB_FILE), "users": load_json(USERS_FILE)}

@app.post("/upload")
async def upload_receipt(file: UploadFile = File(...), user_id: str = Depends(get_current_user)):
    path = os.path.join(UPLOAD_DIR, file.filename)
    with open(path, "wb") as b: shutil.copyfileobj(file.file, b)
    
    # 解析プロンプト (25年/26年問題を解決済み)
    prompt = """領収書を解析し [ { "date": "YYYY-MM-DD", "vendor_name": "...", "total_amount": 0 } ] のJSON配列で返せ。
    ※ 年が2桁(25, 26等)の場合は2025年, 2026年と解釈すること。和暦は禁止。"""
    
    genai_file = genai.upload_file(path=path)
    while genai_file.state.name == "PROCESSING": time.sleep(1); genai_file = genai.get_file(genai_file.name)
    response = model.generate_content([genai_file, prompt])
    
    # カウントアップ
    users = load_json(USERS_FILE)
    users[user_id]["used"] += 1
    save_json(USERS_FILE, users)
    
    data_list = json.loads(response.text.strip().replace('```json', '').replace('```', ''))
    records = load_json(DB_FILE)
    for item in (data_list if isinstance(data_list, list) else [data_list]):
        item.update({"image_url": f"/uploads/{os.path.basename(path)}", "id": int(time.time()*1000)})
        records.append(item)
    save_json(DB_FILE, records)
    return {"data": data_list}

# PDF/Excel/Webhookなどはそのまま（認証を追加する場合は Depends(get_current_user) を付与）