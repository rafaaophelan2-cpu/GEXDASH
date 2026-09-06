import os
import json
import time
import requests
import hashlib
import threading
import streamlit as st
import pandas as pd
import numpy as np
from scipy.stats import norm
from scipy.ndimage import gaussian_filter
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import schwab
from google import genai
from supabase import create_client, Client

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="GEX Terminal Pro - Schwab", layout="wide", initial_sidebar_state="expanded")

# --- MANEJO SEGURO DE SECRETOS ---
CLIENT_ID = st.secrets.get("CLIENT_ID", "")
CLIENT_SECRET = st.secrets.get("CLIENT_SECRET", "")
JSONBIN_BIN_ID = st.secrets.get("JSONBIN_BIN_ID", "")
JSONBIN_API_KEY = st.secrets.get("JSONBIN_API_KEY", "")
GROQ_API_KEY = st.secrets.get("GROQ_API_KEY", st.secrets.get("GROQ_KEY", os.environ.get("GROQ_API_KEY", "")))
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))

SUPABASE_URL = st.secrets.get("SUPABASE_URL", os.environ.get("SUPABASE_URL", ""))
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", os.environ.get("SUPABASE_KEY", ""))

# --- PRECIOS POR DEFECTO PARA TICKERS ---
TICKER_DEFAULTS = {
    "QQQ": 480.0,
    "SPY": 560.0,
    "IWM": 220.0,
    "AAPL": 225.0,
    "NVDA": 125.0,
    "TSLA": 210.0,
    "AMZN": 185.0,
    "MSFT": 420.0,
    "META": 510.0,
    "GOOGL": 175.0
}

# --- INICIALIZACIÓN DE CLIENTE SUPABASE ---
@st.cache_resource
def get_supabase_client():
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            return create_client(SUPABASE_URL, SUPABASE_KEY)
        except Exception:
            return None
    return None

supabase: Client = get_supabase_client()

# --- LECTURA DE SNAPSHOTS DESDE SUPABASE ---
@st.cache_data(ttl=1)
def fetch_supabase_latest_snapshot(symbol="QQQ"):
    if not supabase:
        return None
    try:
        res = supabase.table("gex_snapshots") \
            .select("*") \
            .eq("symbol", symbol) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        if res.data and len(res.data) > 0:
            return res.data[0]
    except Exception as e:
        log_to_console("Supabase Snapshot Fetch Error", str(e))
    return None

@st.cache_data(ttl=2)
def fetch_supabase_gex_history(symbol="QQQ", limit=100):
    if not supabase:
        return []
    try:
        res = supabase.table("gex_snapshots") \
            .select("*") \
            .eq("symbol", symbol) \
            .order("created_at", desc=False) \
            .limit(limit) \
            .execute()
        if res.data:
            return res.data
    except Exception as e:
        log_to_console("Supabase History Fetch Error", str(e))
    return []

# --- INICIALIZACIÓN DE ESTADOS Y SISTEMA DE LOGS ---
if "console_logs" not in st.session_state:
    st.session_state.console_logs = []

def log_to_console(source: str, error_detail: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_entry = {
        "time": timestamp,
        "source": source,
        "error": str(error_detail)
    }
    st.session_state.console_logs.append(log_entry)
    
    if supabase:
        try:
            supabase.table("console_logs").insert({
                "source": source,
                "error": str(error_detail)
            }).execute()
        except Exception:
            pass

# --- ESTILOS CSS FINTECH INSTITUCIONAL ---
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700;800&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
        background-color: #06080D !important;
        color: #D1D5DB;
    }
    
    .stApp {
        background-color: #06080D !important;
    }

    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #090D16 0%, #05070B 100%) !important;
        border-right: 1px solid rgba(255, 255, 255, 0.07) !important;
    }
    
    [data-testid="stSidebar"] label {
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 0.70rem !important;
        font-weight: 700 !important;
        letter-spacing: 1.2px !important;
        color: #8B949E !important;
        text-transform: uppercase !important;
        margin-bottom: 6px !important;
    }

    [data-testid="stSidebar"] div[data-baseweb="input"] > div,
    [data-testid="stSidebar"] div[data-baseweb="select"] > div {
        background-color: #0E131F !important;
        border: 1px solid rgba(255, 255, 255, 0.1) !important;
        border-radius: 8px !important;
        color: #F0F6FC !important;
        font-family: 'JetBrains Mono', monospace !important;
    }

    [data-testid="stSidebar"] div[data-baseweb="slider"] div[role="slider"] {
        background-color: #3B82F6 !important;
        border: 2px solid #60A5FA !important;
        box-shadow: 0 0 10px rgba(59, 130, 246, 0.6) !important;
    }

    [data-testid="stSidebar"] .stButton > button, div.stButton > button {
        width: 100%;
        background: linear-gradient(135deg, #1E2640 0%, #0F172A 100%) !important;
        border: 1px solid rgba(59, 130, 246, 0.4) !important;
        border-radius: 8px !important;
        color: #60A5FA !important;
        font-family: 'JetBrains Mono', monospace !important;
        font-weight: 700 !important;
        font-size: 0.78rem !important;
        letter-spacing: 0.8px !important;
        padding: 10px 16px !important;
        transition: all 0.2s ease-in-out !important;
    }
    [data-testid="stSidebar"] .stButton > button:hover, div.stButton > button:hover {
        border-color: #60A5FA !important;
        background: linear-gradient(135deg, #2563EB 0%, #1D4ED8 100%) !important;
        color: #FFFFFF !important;
        box-shadow: 0 0 14px rgba(37, 99, 235, 0.5) !important;
    }

    .metric-card {
        background: rgba(13, 17, 26, 0.85);
        backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 8px;
        padding: 10px 14px;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
        transition: border-color 0.2s ease;
    }
    .metric-card:hover {
        border-color: rgba(59, 130, 246, 0.4);
    }
    .metric-label {
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.65rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 1px;
        color: #8B949E;
        margin-bottom: 2px;
    }
    .metric-value {
        font-family: 'JetBrains Mono', monospace;
        font-size: 1.10rem;
        font-weight: 800;
        color: #F0F6FC;
    }
    .metric-sub {
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.65rem;
        color: #6E7681;
        margin-top: 2px;
    }
    
    .status-card {
        background: rgba(14, 19, 31, 0.9);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 8px;
        padding: 10px 14px;
        display: flex;
        align-items: center;
        justify-content: space-between;
    }
    .pulse-dot {
        width: 7px;
        height: 7px;
        background-color: #10B981;
        border-radius: 50%;
        box-shadow: 0 0 8px #10B981;
        display: inline-block;
        margin-right: 6px;
    }

    .stTabs [data-baseweb="tab-list"] {
        gap: 6px;
        background-color: #0B0E17;
        padding: 5px;
        border-radius: 10px;
        border: 1px solid rgba(255, 255, 255, 0.06);
    }
    .stTabs [data-baseweb="tab"] {
        height: 36px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.75rem;
        font-weight: 700;
        color: #8B949E;
        border-radius: 6px;
        padding: 0 16px;
        border: none !important;
        transition: all 0.2s ease;
    }
    .stTabs [aria-selected="true"] {
        background: #1E293B !important;
        color: #60A5FA !important;
        box-shadow: 0 0 12px rgba(59, 130, 246, 0.2);
    }

    .depth-frame {
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 12px;
        padding: 18px;
        background: linear-gradient(180deg, rgba(14, 18, 27, 0.8) 0%, rgba(9, 12, 18, 0.95) 100%);
        box-shadow: 0 10px 40px rgba(0, 0, 0, 0.5);
    }
    
    .backtest-controls {
        background: rgba(14, 19, 31, 0.9);
        border: 1px solid rgba(59, 130, 246, 0.2);
        border-radius: 10px;
        padding: 14px 18px;
        margin-bottom: 15px;
    }

    .badge-online {
        background: rgba(16, 185, 129, 0.12);
        border: 1px solid rgba(16, 185, 129, 0.4);
        color: #10B981;
        padding: 5px 12px;
        border-radius: 6px;
        font-family: 'JetBrains Mono', monospace;
        font-weight: 800;
        font-size: 0.72rem;
        letter-spacing: 0.5px;
        display: inline-flex;
        align-items: center;
        gap: 6px;
    }

    .badge-offline {
        background: rgba(239, 68, 68, 0.12);
        border: 1px solid rgba(239, 68, 68, 0.4);
        color: #EF4444;
        padding: 5px 12px;
        border-radius: 6px;
        font-family: 'JetBrains Mono', monospace;
        font-weight: 800;
        font-size: 0.72rem;
        letter-spacing: 0.5px;
        display: inline-flex;
        align-items: center;
        gap: 6px;
    }
    
    .badge-warning {
        background: rgba(245, 158, 11, 0.12);
        border: 1px solid rgba(245, 158, 11, 0.4);
        color: #F59E0B;
        padding: 5px 12px;
        border-radius: 6px;
        font-family: 'JetBrains Mono', monospace;
        font-weight: 800;
        font-size: 0.72rem;
        letter-spacing: 0.5px;
        display: inline-flex;
        align-items: center;
        gap: 6px;
    }
    </style>
""", unsafe_allow_html=True)

# --- MÓDULO DE AUTENTICACIÓN SEGURA ---
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "user_email" not in st.session_state:
    st.session_state.user_email = ""

def login_user(username_in, password_in):
    user_clean = username_in.strip().lower()
    pass_clean = password_in.strip()
    
    if not user_clean or not pass_clean:
        return False, "Por favor ingresa un usuario y contraseña válidos."

    # 1. Intento via Supabase
    if supabase:
        try:
            pass_hash = hashlib.sha256(pass_clean.encode('utf-8')).hexdigest()
            res = supabase.table("app_users") \
                .select("*") \
                .eq("username", user_clean) \
                .execute()
                
            if res.data and len(res.data) > 0:
                user_record = res.data[0]
                db_hash = str(user_record.get('password_hash', '')).strip()
                
                # Permite validación por hash SHA256 o texto plano por retrocompatibilidad
                if db_hash == pass_hash or db_hash == pass_clean:
                    st.session_state.authenticated = True
                    st.session_state.user_email = user_record.get('username', user_clean)
                    
                    try:
                        supabase.table("active_sessions").upsert({
                            "username": user_clean,
                            "ip_address": "streamlit_cloud",
                            "session_token": st.session_state.get("session_id", "active"),
                            "last_seen": datetime.now().isoformat()
                        }).execute()
                    except Exception as e_sess:
                        log_to_console("Active Sessions Upsert Error", str(e_sess))
                        
                    return True, f"Bienvenido {user_record.get('name', user_clean)}"
        except Exception as e:
            log_to_console("Supabase Login Error", str(e))

    # 2. Intento via st.secrets [USERS]
    valid_users = st.secrets.get("USERS", {})
    if valid_users:
        try:
            users_lower = {str(k).strip().lower(): str(v).strip() for k, v in valid_users.items()}
            if user_clean in users_lower and users_lower[user_clean] == pass_clean:
                st.session_state.authenticated = True
                st.session_state.user_email = user_clean
                return True, "Inicio de sesión exitoso."
        except Exception as e_sec:
            log_to_console("Secrets USERS Login Error", str(e_sec))

    # 3. Credenciales de respaldo para desarrollo
    dev_users = {
        "admin": "admin123",
        "trader": "gex2026"
    }
    if user_clean in dev_users and dev_users[user_clean] == pass_clean:
        st.session_state.authenticated = True
        st.session_state.user_email = user_clean
        return True, "Inicio de sesión en modo desarrollo."

    return False, "Usuario o contraseña incorrectos."

# --- PANTALLA EXCLUSIVA DE LOGIN ---
if not st.session_state.authenticated:
    st.markdown("<div style='height:60px;'></div>", unsafe_allow_html=True)
    col_center = st.columns([1, 1.8, 1])[1]
    with col_center:
        st.markdown("""
            <div style='text-align:center; margin-bottom: 20px;'>
                <h2 style='font-weight:800; letter-spacing:-0.5px; background: linear-gradient(90deg, #F0F6FC 0%, #8B949E 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent;'>GEX QUANT TERMINAL</h2>
                <p style='color:#6E7681; font-family:"JetBrains Mono"; font-size:0.8rem;'>SISTEMA DE AUTENTICACIÓN INSTITUCIONAL</p>
            </div>
        """, unsafe_allow_html=True)
        
        with st.form("login_form"):
            email_in = st.text_input("Usuario", value="")
            pass_in = st.text_input("Contraseña", type="password", value="")
            btn_login = st.form_submit_button("INGRESAR AL TERMINAL", use_container_width=True)
            
            if btn_login:
                if not email_in or not pass_in:
                    st.warning("Por favor ingresa tu usuario y contraseña.")
                else:
                    ok, msg = login_user(email_in, pass_in)
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
    st.stop()

# --- INICIALIZACIÓN DE HISTORIAL DE CHAT ---
if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []
    if supabase:
        try:
            res = supabase.table("chat_messages").select("role, content").order("created_at", desc=False).limit(50).execute()
            if res.data:
                st.session_state.chat_messages = res.data
        except Exception:
            pass

def clean_ai_response(text: str) -> str:
    if not text:
        return ""
    cleaned = text.replace("$", "")
    cleaned = cleaned.replace(r"\(", "").replace(r"\)", "").replace(r"\[", "").replace(r"\]", "")
    return cleaned

def save_chat_message(role: str, content: str):
    cleaned_content = clean_ai_response(content) if role == "assistant" else content
    st.session_state.chat_messages.append({"role": role, "content": cleaned_content})
    if supabase:
        def push_chat_bg():
            try:
                supabase.table("chat_messages").insert({
                    "role": role,
                    "content": cleaned_content
                }).execute()
            except Exception as e:
                log_to_console("Supabase Chat Insert Error", str(e))
        threading.Thread(target=push_chat_bg, daemon=True).start()

TOKEN_PATH = "schwab_token.json"

if "SCHWAB_TOKEN" in st.secrets and not os.path.exists(TOKEN_PATH):
    raw_token = st.secrets["SCHWAB_TOKEN"]
    with open(TOKEN_PATH, "w") as f:
        if isinstance(raw_token, str):
            f.write(raw_token)
        else:
            json.dump(dict(raw_token), f)

@st.cache_resource
def get_schwab_client():
    if not os.path.exists(TOKEN_PATH):
        return None
    try:
        return schwab.auth.client_from_token_file(
            token_path=TOKEN_PATH,
            api_key=CLIENT_ID,
            app_secret=CLIENT_SECRET,
            enforce_enums=False
        )
    except Exception as e:
        log_to_console("Conexión Schwab API Init", str(e))
        return None

client = get_schwab_client()

# --- FUNCION LECTURA DE JSONBIN PARA RESPALDO HISTÓRICO ---
@st.cache_data(ttl=20)
def fetch_jsonbin_history(bin_id, api_key):
    if not bin_id or not api_key:
        return {}
    try:
        url = f"https://api.jsonbin.io/v3/b/{bin_id}/latest"
        headers = {"X-Master-Key": api_key}
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            return resp.json().get("record", {})
    except Exception as e:
        log_to_console("JSONBin Read Error", str(e))
    return {}

# --- CLIENTES DE IA (GROQ Y GEMINI) ---
@st.cache_resource
def get_gemini_client():
    if GEMINI_API_KEY:
        try:
            return genai.Client(api_key=GEMINI_API_KEY)
        except Exception as e:
            log_to_console("Inicialización Gemini Client", str(e))
            return None
    return None

ai_client = get_gemini_client()

def query_groq(system_prompt, user_prompt, api_key):
    try:
        from groq import Groq
        groq_client = Groq(api_key=api_key)
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3
        )
        return clean_ai_response(completion.choices[0].message.content)
    except Exception:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.3
        }
        resp = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=15)
        if resp.status_code == 200:
            return clean_ai_response(resp.json()["choices"][0]["message"]["content"])
        else:
            raise Exception(f"Groq API Error {resp.status_code}: {resp.text}")

# --- SIDEBAR CONFIG Y SESIÓN ---
st.sidebar.markdown(f"<p style='font-family:\"JetBrains Mono\"; font-size:0.75rem; color:#60A5FA; margin-bottom:8px;'>👤 USUARIO: <b>{st.session_state.user_email}</b></p>", unsafe_allow_html=True)
if st.sidebar.button("🚪 CERRAR SESIÓN", key="btn_logout", use_container_width=True):
    st.session_state.authenticated = False
    st.session_state.user_email = ""
    st.rerun()

st.sidebar.markdown("<hr style='border-color:rgba(255,255,255,0.06);'>", unsafe_allow_html=True)
st.sidebar.markdown("<p style='font-family:\"JetBrains Mono\"; font-size:0.85rem; font-weight:800; color:#F0F6FC; letter-spacing:1px; margin-bottom:15px;'>⚙️ CONFIGURACIÓN</p>", unsafe_allow_html=True)

ticker_symbol = st.sidebar.text_input("SYMBOL", value="QQQ").upper()
strike_range = st.sidebar.slider("STRIKE RANGE (± ATM)", min_value=10, max_value=50, value=20)
st.sidebar.markdown("<hr style='border-color:rgba(255,255,255,0.06);'>", unsafe_allow_html=True)

tz_choice = st.sidebar.selectbox("TIMEZONE", ["UTC-5 (Lima)", "UTC-4 (New York)"])
tz_target = "America/Lima" if "UTC-5" in tz_choice else "America/New_York"

st.sidebar.markdown("<hr style='border-color:rgba(255,255,255,0.06);'>", unsafe_allow_html=True)

auto_refresh = st.sidebar.toggle("AUTO-REFRESCO EN VIVO", value=True)
refresh_interval = st.sidebar.select_slider(
    "INTERVALO (SEGUNDOS)",
    options=[1, 2, 5, 10, 15, 30, 60],
    value=1,
    disabled=not auto_refresh
)

if auto_refresh:
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=refresh_interval * 1000, key="gex_auto_refresh")
    except ImportError:
        time.sleep(refresh_interval)
        st.rerun()

st.sidebar.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
if st.sidebar.button("🔄 ACTUALIZAR DATOS AHORA", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

# --- FUNCIONES DE MERCADO SCHWAB CACHEADAS ---
@st.cache_data(ttl=5)
def fetch_history_schwab(symbol):
    if not client:
        return pd.DataFrame()
    try:
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        freq_type = getattr(client.PriceHistory.FrequencyType, 'MINUTE', 'minute') if hasattr(client, 'PriceHistory') else 'minute'
        freq = getattr(client.PriceHistory.Frequency, 'EVERY_MINUTE', 'every_minute') if hasattr(client, 'PriceHistory') else 'every_minute'

        resp = client.get_price_history(
            symbol,
            start_datetime=today_start,
            frequency_type=freq_type,
            frequency=freq,
            need_extended_hours_data=False
        )
        if resp.status_code == 200:
            data = resp.json()
            candles = data.get("candles", []) if isinstance(data, dict) else []
            if candles:
                df = pd.DataFrame(candles)
                df['datetime'] = pd.to_datetime(df['datetime'], unit='ms', utc=True)
                df.set_index('datetime', inplace=True)
                df.rename(columns={
                    'open': 'Open', 'high': 'High',
                    'low': 'Low', 'close': 'Close', 'volume': 'Volume'
                }, inplace=True)
                return df
    except Exception as e:
        log_to_console("fetch_history_schwab", str(e))
    return pd.DataFrame()

@st.cache_data(ttl=5)
def fetch_option_chain_schwab(symbol, strikes_count):
    if not client:
        return {}
    try:
        today = datetime.now()
        contract_type = getattr(client.Options.ContractType, 'ALL', 'ALL') if hasattr(client, 'Options') else 'ALL'
        resp = client.get_option_chain(
            symbol=symbol,
            contract_type=contract_type,
            strike_count=strikes_count,
            from_date=today,
            to_date=today + timedelta(days=7)
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        log_to_console("fetch_option_chain_schwab", str(e))
    return {}

@st.cache_data(ttl=5)
def fetch_nq_price_schwab():
    if not client:
        return 0.0
    try:
        resp = client.get_quote("/NQ")
        if resp.status_code == 200:
            data = resp.json()
            nq_data = data.get("/NQ", {}) if isinstance(data, dict) else {}
            quote_data = nq_data.get("quote", {}) if isinstance(nq_data, dict) else {}
            price = float(quote_data.get("lastPrice", quote_data.get("closePrice", 0.0)))
            if price > 0:
                return price
    except Exception as e:
        log_to_console("fetch_nq_price_schwab", str(e))
    return 0.0

# --- PROCESAMIENTO Y DETECCIÓN ESTADO ONLINE / SUPABASE / OFFLINE ---
now_tz = pd.Timestamp.now(tz=tz_target)
ref_today = now_tz.floor('D').tz_localize(None)

latest_supabase_snap = fetch_supabase_latest_snapshot(ticker_symbol)
hist_raw = fetch_history_schwab(ticker_symbol)
chain_raw = fetch_option_chain_schwab(ticker_symbol, strike_range)

# Extracción inicial de spot_price
spot_price = 0.0
if isinstance(chain_raw, dict):
    raw_spot = chain_raw.get("underlyingPrice")
    if raw_spot is not None:
        try:
            spot_price = float(raw_spot)
        except (ValueError, TypeError):
            spot_price = 0.0

if (spot_price <= 0 or np.isnan(spot_price)) and latest_supabase_snap:
    spot_price = float(latest_supabase_snap.get("spot", 0.0))

if (spot_price <= 0 or np.isnan(spot_price)) and not hist_raw.empty and 'Close' in hist_raw:
    try:
        valid_closes = hist_raw['Close'].dropna()
        if not valid_closes.empty:
            spot_price = float(valid_closes.iloc[-1])
    except Exception:
        spot_price = 0.0

def parse_schwab_chain(chain_data):
    if not isinstance(chain_data, dict):
        return pd.DataFrame(), None
        
    call_map = chain_data.get('callExpDateMap') or {}
    put_map = chain_data.get('putExpDateMap') or {}
    
    all_exp_keys = sorted(list(set(list(call_map.keys()) + list(put_map.keys()))))
    if not all_exp_keys:
        return pd.DataFrame(), None
    
    selected_exp = all_exp_keys[0]
    calls_for_exp = call_map.get(selected_exp) or {}
    puts_for_exp = put_map.get(selected_exp) or {}
    
    records = {}
    
    def extract_iv(opt_dict):
        vol = float(opt_dict.get('volatility', opt_dict.get('impliedVolatility', 0.0)))
        if vol > 2.0:
            vol = vol / 100.0
        return max(vol, 0.001)

    for strike_str, opt_list in calls_for_exp.items():
        if not opt_list: continue
        opt = opt_list[0]
        strike = float(strike_str)
        if strike not in records:
            records[strike] = {
                'strike': strike,
                'openInterest_c': 0, 'openInterest_p': 0,
                'gamma_c': 0.0, 'gamma_p': 0.0,
                'delta_c': 0.0, 'delta_p': 0.0,
                'theta_c': 0.0, 'theta_p': 0.0,
                'vega_c': 0.0, 'vega_p': 0.0,
                'iv_c': 0.0, 'iv_p': 0.0
            }
        records[strike]['openInterest_c'] = opt.get('openInterest', 0)
        records[strike]['gamma_c'] = opt.get('gamma', 0.0)
        records[strike]['delta_c'] = opt.get('delta', 0.0)
        records[strike]['theta_c'] = opt.get('theta', 0.0)
        records[strike]['vega_c'] = opt.get('vega', 0.0)
        records[strike]['iv_c'] = extract_iv(opt)

    for strike_str, opt_list in puts_for_exp.items():
        if not opt_list: continue
        opt = opt_list[0]
        strike = float(strike_str)
        if strike not in records:
            records[strike] = {
                'strike': strike,
                'openInterest_c': 0, 'openInterest_p': 0,
                'gamma_c': 0.0, 'gamma_p': 0.0,
                'delta_c': 0.0, 'delta_p': 0.0,
                'theta_c': 0.0, 'theta_p': 0.0,
                'vega_c': 0.0, 'vega_p': 0.0,
                'iv_c': 0.0, 'iv_p': 0.0
            }
        records[strike]['openInterest_p'] = opt.get('openInterest', 0)
        records[strike]['gamma_p'] = opt.get('gamma', 0.0)
        records[strike]['delta_p'] = opt.get('delta', 0.0)
        records[strike]['theta_p'] = opt.get('theta', 0.0)
        records[strike]['vega_p'] = opt.get('vega', 0.0)
        records[strike]['iv_p'] = extract_iv(opt)

    df = pd.DataFrame(list(records.values())).sort_values('strike').reset_index(drop=True) if records else pd.DataFrame()
    return df, selected_exp

df_curr, exp_0dte = parse_schwab_chain(chain_raw)

# --- EVALUACIÓN DE ESTADO REAL (ONLINE / SUPABASE / OFFLINE) ---
is_online = False
is_cloud_backup = False

if client is not None and isinstance(chain_raw, dict) and len(chain_raw) > 0 and spot_price > 0 and not df_curr.empty:
    is_online = True

jsonbin_history_data = fetch_jsonbin_history(JSONBIN_BIN_ID, JSONBIN_API_KEY)

if not is_online:
    if latest_supabase_snap:
        is_cloud_backup = True
        spot_price = float(latest_supabase_snap.get("spot", spot_price))
        cloud_strikes = latest_supabase_snap.get("strikes", [])
        if cloud_strikes:
            df_curr = pd.DataFrame(cloud_strikes)
            for col in ['openInterest_c', 'openInterest_p']:
                if col not in df_curr.columns: df_curr[col] = 1000
            for col in ['iv_c', 'iv_p']:
                if col not in df_curr.columns: df_curr[col] = 0.20
            for col in ['delta_c', 'delta_p', 'theta_c', 'theta_p', 'vega_c', 'vega_p']:
                if col not in df_curr.columns: df_curr[col] = 0.0
            exp_0dte = now_tz.strftime('%Y-%m-%d')
    elif jsonbin_history_data:
        available_cloud_dates = sorted(list(jsonbin_history_data.keys()))
        if available_cloud_dates:
            latest_date_key = available_cloud_dates[-1]
            latest_day_snaps = jsonbin_history_data.get(latest_date_key, [])
            if latest_day_snaps:
                is_cloud_backup = True
                last_cloud_snap = latest_day_snaps[-1]
                spot_price = float(last_cloud_snap.get("spot", 0.0))
                
                cloud_strikes = last_cloud_snap.get("strikes", [])
                if cloud_strikes:
                    df_curr = pd.DataFrame(cloud_strikes)
                    for col in ['openInterest_c', 'openInterest_p']:
                        if col not in df_curr.columns: df_curr[col] = 1000
                    for col in ['iv_c', 'iv_p']:
                        if col not in df_curr.columns: df_curr[col] = 0.20
                    for col in ['delta_c', 'delta_p', 'theta_c', 'theta_p', 'vega_c', 'vega_p']:
                        if col not in df_curr.columns: df_curr[col] = 0.0
                    exp_0dte = latest_date_key

if spot_price <= 0:
    spot_price = TICKER_DEFAULTS.get(ticker_symbol, 480.00)

# --- ZONA HORARIA Y GENERACIÓN/FILTRADO INTRADÍA CORDENADO ---
h_1m = fetch_history_schwab(ticker_symbol)

today_date_str = now_tz.strftime('%Y-%m-%d')
if "UTC-5" in tz_choice:
    start_str = f"{today_date_str} 08:30:00"
    end_str = f"{today_date_str} 15:00:00"
else:
    start_str = f"{today_date_str} 09:30:00"
    end_str = f"{today_date_str} 16:00:00"

start_time = pd.Timestamp(start_str).tz_localize(tz_target)
end_time = pd.Timestamp(end_str).tz_localize(tz_target)

full_time_grid = pd.date_range(start_time, end_time, freq="1min")
full_timestamps = full_time_grid.strftime('%H:%M').tolist()

min_strike = int(np.floor(spot_price - strike_range)) if spot_price > 0 else 0
max_strike = int(np.ceil(spot_price + strike_range)) if spot_price > 0 else 100
fine_strikes = np.linspace(min_strike, max_strike, int((max_strike - min_strike) * 2 + 1))

if not h_1m.empty and is_online:
    h_1m = h_1m.tz_convert(tz_target)
    h_1m_today = h_1m[h_1m.index.strftime('%Y-%m-%d') == today_date_str].copy()
    if h_1m_today.empty:
        today_date_str = h_1m.index.max().strftime('%Y-%m-%d')
        h_1m_today = h_1m[h_1m.index.strftime('%Y-%m-%d') == today_date_str].copy()

    h_1m_today = h_1m_today[~h_1m_today.index.duplicated(keep='last')]
    h_1m_reindexed = h_1m_today.reindex(full_time_grid)
    spot_series = h_1m_reindexed['Close'].ffill().bfill()
    full_spots = spot_series.fillna(spot_price).tolist()
    
    if full_spots and full_spots[-1] > 0:
        spot_price = float(full_spots[-1])

elif is_cloud_backup and latest_supabase_snap:
    supabase_history = fetch_supabase_gex_history(ticker_symbol, limit=390)
    if supabase_history:
        full_timestamps = [pd.to_datetime(s.get("created_at")).strftime('%H:%M') for s in supabase_history]
        full_spots = [s.get("spot", spot_price) for s in supabase_history]
        if full_spots and full_spots[-1] > 0:
            spot_price = float(full_spots[-1])
        
        h_1m_reindexed = pd.DataFrame({
            'Open': full_spots, 'High': [v + 0.3 for v in full_spots],
            'Low': [v - 0.3 for v in full_spots], 'Close': full_spots,
            'Volume': [1500 for _ in full_spots]
        }, index=full_time_grid[:len(full_spots)])
    else:
        full_spots = [spot_price]
        h_1m_reindexed = pd.DataFrame()
elif is_cloud_backup and jsonbin_history_data:
    available_cloud_dates = sorted(list(jsonbin_history_data.keys()))
    latest_date_key = available_cloud_dates[-1]
    latest_day_snaps = jsonbin_history_data.get(latest_date_key, [])
    
    if latest_day_snaps:
        full_timestamps = [s.get("time") for s in latest_day_snaps]
        full_spots = [s.get("spot", spot_price) for s in latest_day_snaps]
        if full_spots and full_spots[-1] > 0:
            spot_price = float(full_spots[-1])
        
        cloud_strikes_set = set()
        for s in latest_day_snaps:
            for st_item in s.get("strikes", []):
                cloud_strikes_set.add(st_item["strike"])
        if cloud_strikes_set:
            fine_strikes = np.array(sorted(list(cloud_strikes_set)))
            
        Z_matrix_real = np.zeros((len(fine_strikes), len(latest_day_snaps)))
        strike_idx_map = {k: i for i, k in enumerate(fine_strikes)}
        for t_idx, s in enumerate(latest_day_snaps):
            for st_item in s.get("strikes", []):
                st_v = st_item["strike"]
                if st_v in strike_idx_map:
                    Z_matrix_real[strike_idx_map[st_v], t_idx] = st_item.get("net_gex", 0.0)
        
        h_1m_reindexed = pd.DataFrame({
            'Open': full_spots, 'High': [v + 0.3 for v in full_spots],
            'Low': [v - 0.3 for v in full_spots], 'Close': full_spots,
            'Volume': [1500 for _ in full_spots]
        }, index=full_time_grid[:len(full_spots)])
    else:
        full_spots = []
else:
    np.random.seed(42)
    n_mins = len(full_timestamps)
    price_changes = np.random.normal(0, 0.15, n_mins)
    cum_drift = np.cumsum(price_changes)
    cum_drift = cum_drift - cum_drift[-1]
    full_spots = (spot_price + cum_drift).tolist()
    
    syn_opens = full_spots.copy()
    syn_highs = [p + abs(np.random.normal(0.15, 0.05)) for p in full_spots]
    syn_lows = [p - abs(np.random.normal(0.15, 0.05)) for p in full_spots]
    syn_closes = full_spots.copy()
    syn_vols = np.random.randint(500, 5000, n_mins)

    h_1m_reindexed = pd.DataFrame({
        'Open': syn_opens,
        'High': syn_highs,
        'Low': syn_lows,
        'Close': syn_closes,
        'Volume': syn_vols
    }, index=full_time_grid)

# --- GENERADOR DE RESPALDO SINTÉTICO SI NO HAY CADENA DE OPCIONES ---
if df_curr.empty:
    np.random.seed(42)
    s_min = int(np.floor(spot_price - strike_range))
    s_max = int(np.ceil(spot_price + strike_range))
    synthetic_strikes = np.arange(s_min, s_max + 1, 1.0)
    
    syn_records = []
    for s in synthetic_strikes:
        dist = (s - spot_price) / spot_price
        oi_c = int(np.random.randint(800, 6000) * np.exp(-20 * max(0, -dist)**2))
        oi_p = int(np.random.randint(800, 6000) * np.exp(-20 * max(0, dist)**2))
        iv = 0.18 + 0.04 * abs(dist)
        
        d1 = (np.log(spot_price / s) + (0.045 + 0.5 * iv**2) * 0.005) / (iv * np.sqrt(0.005))
        gamma_val = norm.pdf(d1) / (spot_price * iv * np.sqrt(0.005)) if spot_price > 0 else 0.01
        delta_c_val = float(norm.cdf(d1))
        delta_p_val = delta_c_val - 1.0
        
        syn_records.append({
            'strike': float(s),
            'openInterest_c': max(oi_c, 100),
            'openInterest_p': max(oi_p, 100),
            'gamma_c': gamma_val,
            'gamma_p': gamma_val,
            'delta_c': delta_c_val,
            'delta_p': delta_p_val,
            'theta_c': -0.08,
            'theta_p': -0.08,
            'vega_c': 0.15,
            'vega_p': 0.15,
            'iv_c': iv,
            'iv_p': iv
        })
    df_curr = pd.DataFrame(syn_records)
    exp_0dte = now_tz.strftime('%Y-%m-%d') + ":0"

# --- ENCABEZADO Y BADGE DE ESTADO ONLINE / OFFLINE ---
col_head_title, col_head_badge, col_head_console = st.columns([5.5, 2.2, 2.3])

with col_head_title:
    st.markdown("<h2 style='margin:0; font-weight:800; letter-spacing:-0.5px; background: linear-gradient(90deg, #F0F6FC 0%, #8B949E 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent;'>GEX QUANT TERMINAL</h2>", unsafe_allow_html=True)
    st.markdown("<p style='color:#6E7681; margin:0 0 15px 0; font-size:0.78rem; font-family:\"JetBrains Mono\"; letter-spacing:0.5px;'>SCHWAB REAL-TIME GAMMA EXPOSURE & INTRADAY FLOW</p>", unsafe_allow_html=True)

with col_head_badge:
    st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
    if is_online:
        st.markdown('<div class="badge-online">🟢 ONLINE (EN VIVO)</div>', unsafe_allow_html=True)
    elif is_cloud_backup:
        st.markdown('<div class="badge-warning">⚡ SUPABASE REALTIME</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="badge-warning">🟡 SIMULADO (FALLBACK)</div>', unsafe_allow_html=True)

with col_head_console:
    with st.popover("💻 CONSOLA", use_container_width=True):
        st.markdown("<p style='font-family:\"JetBrains Mono\"; font-weight:800; font-size:0.9rem; color:#F59E0B; margin-bottom:8px;'>💻 CONSOLA DE REGISTROS Y ERRORES</p>", unsafe_allow_html=True)
        if st.button("🗑️ Limpiar Consola", key="btn_clear_console", use_container_width=True):
            st.session_state.console_logs = []
            if supabase:
                try:
                    supabase.table("console_logs").delete().neq("id", 0).execute()
                except Exception:
                    pass
            st.rerun()
        
        st.markdown("---")
        if st.session_state.console_logs:
            for item in reversed(st.session_state.console_logs):
                st.markdown(f"**[{item['time']}] {item['source']}**")
                st.code(item['error'], language="python")
        else:
            st.info("No hay errores registrados en la consola.")

nq_price = fetch_nq_price_schwab()
if nq_price > 0 and spot_price > 0:
    conversion_ratio = nq_price / spot_price
else:
    conversion_ratio = 41.125

st.sidebar.markdown("<hr style='border-color:rgba(255,255,255,0.06);'>", unsafe_allow_html=True)
st.sidebar.markdown(f"""
    <div class="status-card">
        <span style="font-family:'JetBrains Mono'; font-size:0.68rem; color:#8B949E; font-weight:700;">NQ/QQQ RATIO</span>
        <span style="font-family:'JetBrains Mono'; font-size:0.88rem; color:#10B981; font-weight:800;">
            <span class="pulse-dot"></span>{conversion_ratio:.4f}
        </span>
    </div>
""", unsafe_allow_html=True)

def fmt_val(val, show_sign=True):
    if val is None or np.isnan(val):
        return "$0.0"
    sign = ("+" if val > 0 else "") if show_sign else ""
    if abs(val) >= 1e9:
        return f"{sign}${val/1e9:.2f}B"
    elif abs(val) >= 1e6:
        return f"{sign}${val/1e6:.2f}M"
    elif abs(val) >= 1e3:
        return f"{sign}${val/1e3:.1f}K"
    else:
        return f"{sign}${val:.1f}"

def safe_strike_range(df_sub):
    if df_sub.empty or 'strike' not in df_sub.columns or df_sub['strike'].dropna().empty:
        return {}
    s_min = float(df_sub['strike'].min())
    s_max = float(df_sub['strike'].max())
    if np.isnan(s_min) or np.isnan(s_max):
        return {}
    if s_min == s_max:
        return {"range": [s_min - 5, s_max + 5]}
    x_mid = (s_min + s_max) / 2.0
    x_half_span = ((s_max - s_min) / 2.0) + 1.0
    return {"range": [x_mid - (x_half_span * 1.5), x_mid + (x_half_span * 1.5)]}

# --- RECALCULO DINÁMICO DE GAMMA ---
def recalculate_gex_for_spot(df_input, spot_t, T_exp, iv):
    if df_input.empty or spot_t <= 0:
        return df_input
    
    df_out = df_input.copy()
    iv = max(float(iv), 0.001)
    T_exp = max(float(T_exp), 1e-5)
    
    def calc_gamma_dyn(r):
        K = float(r['strike'])
        if K <= 0 or spot_t <= 0:
            return 0.0
        d1 = (np.log(spot_t / K) + (0.045 + 0.5 * iv**2) * T_exp) / (iv * np.sqrt(T_exp))
        return norm.pdf(d1) / (spot_t * iv * np.sqrt(T_exp))

    df_out['gamma'] = df_out.apply(calc_gamma_dyn, axis=1)
    df_out['call_gex'] = df_out['gamma'] * df_out['openInterest_c'] * (spot_t ** 2) * 0.01
    df_out['put_gex'] = df_out['gamma'] * df_out['openInterest_p'] * (spot_t ** 2) * (-0.01)
    df_out['net_gex'] = df_out['call_gex'] + df_out['put_gex']
    return df_out

# --- CÁLCULOS CUÁNTICOS DE OPCIONES ---
if not df_curr.empty and exp_0dte is not None and spot_price > 0:
    exp_date_part = exp_0dte.split(':')[0] if isinstance(exp_0dte, str) and ':' in exp_0dte else str(exp_0dte)
    try:
        exp_dt = pd.to_datetime(exp_date_part).tz_localize(None)
        days_to_exp = max((exp_dt - ref_today).days, 0)
    except Exception:
        days_to_exp = 0
    T_exp = max(days_to_exp / 365.0, 0.5 / 365.0)

    near_atm = df_curr[abs(df_curr['strike'] - spot_price) <= (spot_price * 0.025)]
    valid_ivs = []
    if not near_atm.empty:
        for _, r in near_atm.iterrows():
            if 0.02 < r.get('iv_c', 0) < 3.0: valid_ivs.append(r['iv_c'])
            if 0.02 < r.get('iv_p', 0) < 3.0: valid_ivs.append(r['iv_p'])
    atm_iv = float(np.median(valid_ivs)) if len(valid_ivs) > 0 else 0.20
    atm_iv = max(atm_iv, 0.08)

    df_curr = recalculate_gex_for_spot(df_curr, spot_price, T_exp, atm_iv)

    df_curr['call_dex'] = df_curr['delta_c'] * df_curr['openInterest_c'] * 100 * spot_price / 1e6
    df_curr['put_dex'] = df_curr['delta_p'] * df_curr['openInterest_p'] * 100 * spot_price / 1e6
    df_curr['net_dex'] = df_curr['call_dex'] + df_curr['put_dex']

    df_curr['call_tex'] = df_curr['theta_c'] * df_curr['openInterest_c'] * 100
    df_curr['put_tex'] = df_curr['theta_p'] * df_curr['openInterest_p'] * 100
    df_curr['net_tex'] = df_curr['call_tex'] + df_curr['put_tex']

    df_curr['call_vex'] = df_curr['vega_c'] * df_curr['openInterest_c'] * 100
    df_curr['put_vex'] = df_curr['vega_p'] * df_curr['openInterest_p'] * 100
    df_curr['net_vex'] = df_curr['call_vex'] + df_curr['put_vex']

    r_rate = 0.045
    valid_strikes = df_curr['strike'] > 0
    d1_charm = (np.log(spot_price / df_curr.loc[valid_strikes, 'strike']) + (r_rate + 0.5 * atm_iv**2) * T_exp) / (atm_iv * np.sqrt(T_exp))
    d2_charm = d1_charm - atm_iv * np.sqrt(T_exp)
    
    call_charm_annual = - norm.pdf(d1_charm) * (r_rate / (atm_iv * np.sqrt(T_exp)) - d2_charm / (2.0 * T_exp))
    put_charm_annual = call_charm_annual + (r_rate * np.exp(-r_rate * T_exp) * norm.cdf(-d1_charm))

    df_curr.loc[valid_strikes, 'charm_c'] = call_charm_annual / 365.0
    df_curr.loc[valid_strikes, 'charm_p'] = put_charm_annual / 365.0
    df_curr['charm_c'] = df_curr['charm_c'].fillna(0.0)
    df_curr['charm_p'] = df_curr['charm_p'].fillna(0.0)

    df_curr['call_chex'] = df_curr['charm_c'] * df_curr['openInterest_c'] * 100 * spot_price / 1e6
    df_curr['put_chex'] = df_curr['charm_p'] * df_curr['openInterest_p'] * 100 * spot_price / 1e6
    df_curr['net_chex'] = df_curr['call_chex'] - df_curr['put_chex']

    calls_dominant = df_curr[df_curr['net_gex'] > 0].sort_values('net_gex', ascending=False)
    top_calls = calls_dominant['strike'].tolist()
    cw1 = top_calls[0] if len(top_calls) > 0 else spot_price + 5
    cw2 = top_calls[1] if len(top_calls) > 1 else cw1 + 2
    cw3 = top_calls[2] if len(top_calls) > 2 else cw2 + 2

    puts_dominant = df_curr[df_curr['net_gex'] < 0].sort_values('net_gex', ascending=True)
    top_puts = puts_dominant['strike'].tolist()
    pw1 = top_puts[0] if len(top_puts) > 0 else spot_price - 5
    pw2 = top_puts[1] if len(top_puts) > 1 else pw1 - 2
    pw3 = top_puts[2] if len(top_puts) > 2 else pw2 - 2

    df_curr['cum_gex'] = df_curr['net_gex'].cumsum()
    zero_gamma_idx = (df_curr['cum_gex'].abs()).idxmin() if not df_curr.empty else None
    zero_gamma = df_curr.loc[zero_gamma_idx]['strike'] if zero_gamma_idx is not None and zero_gamma_idx in df_curr.index else spot_price

    net_gex_total = float(df_curr['net_gex'].sum())
    call_gex_sum = float(df_curr['call_gex'].sum())
    put_gex_sum = float(df_curr['put_gex'].sum())
    total_gex = float((df_curr['call_gex'].abs() + df_curr['put_gex'].abs()).sum())
    
    call_oi_sum = int(df_curr['openInterest_c'].sum())
    put_oi_sum = int(df_curr['openInterest_p'].sum())
    total_oi_sum = call_oi_sum + put_oi_sum

    net_dex_total = float(df_curr['net_dex'].sum())
    net_tex_total = float(df_curr['net_tex'].sum())
    net_vex_total = float(df_curr['net_vex'].sum())
    net_chex_total = float(df_curr['net_chex'].sum())

    regime_str = "positive regime" if net_gex_total >= 0 else "negative regime"
    condition_str = "Positive – dealers long gamma, hedging dampens volatility (mean-reverting)" if net_gex_total >= 0 else "Negative – dealers short gamma, hedging amplifies trending behavior"
    iv_str = f"{atm_iv * 100:.2f}%"
    iv_rank_str = f"{int(min(max((atm_iv / 0.35) * 100, 15), 85))}th percentile (moderate volatility environment)"
else:
    cw1, cw2, cw3 = spot_price + 5, spot_price + 10, spot_price + 15
    pw1, pw2, pw3 = spot_price - 5, spot_price - 10, spot_price - 15
    zero_gamma = spot_price
    atm_iv = 0.20
    net_gex_total = 0.0
    call_gex_sum, put_gex_sum, total_gex = 0.0, 0.0, 0.0
    call_oi_sum, put_oi_sum, total_oi_sum = 0, 0, 0
    net_dex_total, net_tex_total, net_vex_total, net_chex_total = 0.0, 0.0, 0.0, 0.0
    regime_str = "neutral regime"
    condition_str = "Neutral"
    iv_str = "20.00%"
    iv_rank_str = "N/A"

# --- MATRIZ DE HEATMAP REAL/SIMULADA ---
if 'Z_matrix_real' not in locals() or Z_matrix_real.shape[0] == 0:
    Z_matrix_real = np.zeros((len(fine_strikes), len(full_timestamps)))
    if not df_curr.empty and len(full_timestamps) > 0:
        sigma_k = 0.5
        for t_idx, S_t in enumerate(full_spots):
            if S_t <= 0 or np.isnan(S_t): continue
            for _, r in df_curr.iterrows():
                K = r['strike']
                if K < min_strike - 2 or K > max_strike + 2: continue
                net_oi = r['openInterest_c'] - r['openInterest_p']
                if net_oi == 0: continue

                d1_t = (np.log(S_t / K) + (0.045 + 0.5 * atm_iv**2) * T_exp) / (atm_iv * np.sqrt(T_exp))
                gamma_t = norm.pdf(d1_t) / (S_t * atm_iv * np.sqrt(T_exp))
                net_gex_t = net_oi * gamma_t * (S_t ** 2) * 0.01

                gauss_weight = np.exp(-0.5 * ((fine_strikes - K) / sigma_k) ** 2)
                Z_matrix_real[:, t_idx] += gauss_weight * net_gex_t

    if Z_matrix_real.size > 0 and Z_matrix_real.shape[1] > 1:
        Z_matrix_real = gaussian_filter(Z_matrix_real, sigma=(0.8, 1.4))

# CÁLCULO DE DRIFT DE PRIMA
closes_drift = np.array(full_spots)
vols_drift = h_1m_reindexed['Volume'].fillna(1000).values if not h_1m_reindexed.empty else np.full(len(full_timestamps), 1000)

if len(closes_drift) > 1:
    price_changes_drift = np.diff(closes_drift, prepend=closes_drift[0])
    call_drift_raw = np.cumsum(np.where(price_changes_drift >= 0, price_changes_drift * vols_drift * 0.12, price_changes_drift * vols_drift * 0.08))
    put_drift_raw = np.cumsum(np.where(price_changes_drift < 0, -price_changes_drift * vols_drift * 0.18, price_changes_drift * vols_drift * 0.05))
    net_drift_raw = call_drift_raw - put_drift_raw
    last_call_drift, last_put_drift, last_net_drift = float(call_drift_raw[-1]), float(put_drift_raw[-1]), float(net_drift_raw[-1])
else:
    call_drift_raw, put_drift_raw, net_drift_raw = np.zeros(len(full_timestamps)), np.zeros(len(full_timestamps)), np.zeros(len(full_timestamps))
    last_call_drift, last_put_drift, last_net_drift = 0.0, 0.0, 0.0

# GENERADOR SINTÉTICO DE HISTORIAL JSONBIN PARA BACKGAMMA SI ESTÁ VACÍO
if not jsonbin_history_data and not latest_supabase_snap:
    mock_date = now_tz.strftime('%Y-%m-%d')
    mock_snaps = []
    mock_times = [t.strftime('%H:%M') for t in pd.date_range("09:30", "16:00", freq="5min")]
    np.random.seed(42)
    base_s = spot_price if spot_price > 0 else 480.0
    p_path = base_s + np.cumsum(np.random.normal(0, 0.3, len(mock_times)))
    
    for idx, t_str in enumerate(mock_times):
        sp = float(p_path[idx])
        stks = []
        for st_val in np.arange(int(sp - 15), int(sp + 16), 1):
            gex_v = (np.random.randn() * 1e5) + (1e6 if st_val > sp else -1e6) * np.exp(-abs(st_val - sp)/5)
            stks.append({
                "strike": float(st_val),
                "net_gex": float(gex_v),
                "call_gex": float(abs(gex_v)*0.6),
                "put_gex": float(-abs(gex_v)*0.4)
            })
        mock_snaps.append({
            "time": t_str,
            "spot": sp,
            "net_gex": float(sum(s["net_gex"] for s in stks)),
            "strikes": stks
        })
    jsonbin_history_data = {mock_date: mock_snaps}

# --- ASISTENTE IA ---
@st.cache_data(ttl=1800, show_spinner=False)
def consultar_ia_cache(
    tipo_analisis, mensaje_usuario, ticker_symbol, spot_price, conversion_ratio,
    net_gex_total, regime_str, call_gex_sum, put_gex_sum, total_gex,
    call_oi_sum, put_oi_sum, total_oi_sum,
    cw1, cw2, cw3, pw1, pw2, pw3, zero_gamma,
    iv_str, iv_rank_str, condition_str,
    net_dex_total, net_tex_total, net_vex_total, net_chex_total,
    last_call_drift, last_put_drift, last_net_drift
):
    system_prompt = f"""
    Eres un analista cuantitativo experto en estructura de opciones, Gamma Exposure (GEX), Charm Exposure (CHEX), flujo de prima intradía y microestructura de mercado.
    Responde en español de forma analítica, directa y profesional usando viñetas.

    REGLAS ESTRICTAS DE FORMATO (OBLIGATORIO):
    - PROHIBIDO USAR NOTACIÓN LATEX O MATEMÁTICA: JAMÁS utilices el símbolo de dólar ($) ni delimitadores LaTeX ($$, \(, \)).
    - Escribe ÚNICAMENTE en texto plano legible en español con formato Markdown estándar (negritas y viñetas).
    - MANTÉN ESPACIOS NORMALES ENTRE PALABRAS: Nunca concatenes palabras sin espacios.
    - Para referirte a montos de dinero o precios, escribe la cifra seguida de 'USD' o 'dólares' (por ejemplo: 442.2K USD).

    Métricas en tiempo real de Net Drift ({ticker_symbol}):
    - Call Drift Acumulado: {fmt_val(last_call_drift).replace('$', '')} USD
    - Put Drift Acumulado: {fmt_val(last_put_drift).replace('$', '')} USD
    - Net Drift Total: {fmt_val(last_net_drift).replace('$', '')} USD

    Métricas actuales del mercado ({ticker_symbol}):
    - Spot Price: {spot_price:.2f} USD (Ratio NQ/QQQ: {conversion_ratio:.4f})
    - Interés Abierto (OI): Calls = {call_oi_sum:,} | Puts = {put_oi_sum:,} | Total = {total_oi_sum:,}
    - Net GEX Total: {fmt_val(net_gex_total).replace('$', '')} USD ({regime_str})
    - Desglose GEX: Call GEX = {fmt_val(call_gex_sum).replace('$', '')} USD | Put GEX = {fmt_val(put_gex_sum).replace('$', '')} USD | Total GEX = {fmt_val(total_gex, show_sign=False).replace('$', '')} USD
    - Call Walls: CW1 = {cw1:.0f} USD, CW2 = {cw2:.0f} USD, CW3 = {cw3:.0f} USD
    - Put Walls: PW1 = {pw1:.0f} USD, PW2 = {pw2:.0f} USD, PW3 = {pw3:.0f} USD
    - Zero Gamma (Flip Level): {zero_gamma:.2f} USD
    - Volatilidad: IV ATM = {iv_str} | Percentil IV Rank = {iv_rank_str}
    - Condición de Gamma: {condition_str}
    - Exposiciones Acumuladas de Grecas:
      * Delta Exposure (DEX): {net_dex_total:.2f}M USD
      * Theta Exposure (TEX): {net_tex_total:,.0f} USD/día
      * Vega Exposure (VEX): {net_vex_total:,.0f} USD/1% IV
      * Charm Exposure (CHEX): {net_chex_total:.2f}M USD/día
    """

    prompt_final = mensaje_usuario or f"Proporciona un diagnóstico estratégico del mercado integrando los niveles GEX, Charm Exposure (CHEX) y el Net Drift intradía para {tipo_analisis}."

    if GROQ_API_KEY:
        try:
            raw_res = query_groq(system_prompt, prompt_final, GROQ_API_KEY)
            return clean_ai_response(raw_res)
        except Exception as e_groq:
            log_to_console("Groq AI Engine", str(e_groq))

    if ai_client:
        try:
            response = ai_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=f"{system_prompt}\n\nPregunta del usuario: {prompt_final}"
            )
            return clean_ai_response(response.text)
        except Exception as e_gemini:
            log_to_console("Gemini AI Engine", str(e_gemini))

    return "Ocurrió un error al procesar la solicitud de IA."

def consultar_ia(tipo_analisis=None, mensaje_usuario=None):
    net_dex_val = float(df_curr['net_dex'].sum()) if not df_curr.empty and 'net_dex' in df_curr.columns else 0.0
    net_tex_val = float(df_curr['net_tex'].sum()) if not df_curr.empty and 'net_tex' in df_curr.columns else 0.0
    net_vex_val = float(df_curr['net_vex'].sum()) if not df_curr.empty and 'net_vex' in df_curr.columns else 0.0
    net_chex_val = float(df_curr['net_chex'].sum()) if not df_curr.empty and 'net_chex' in df_curr.columns else 0.0

    return consultar_ia_cache(
        tipo_analisis, mensaje_usuario, ticker_symbol, spot_price, conversion_ratio,
        net_gex_total, regime_str, call_gex_sum, put_gex_sum, total_gex,
        call_oi_sum, put_oi_sum, total_oi_sum,
        cw1, cw2, cw3, pw1, pw2, pw3, zero_gamma,
        iv_str, iv_rank_str, condition_str,
        net_dex_val, net_tex_val, net_vex_val, net_chex_val,
        last_call_drift, last_put_drift, last_net_drift
    )

# --- WIDGET CHATBOT EN SIDEBAR ---
st.sidebar.markdown("<hr style='border-color:rgba(255,255,255,0.06);'>", unsafe_allow_html=True)
with st.sidebar.popover("💬 ASISTENTE IA GEX", use_container_width=True):
    col_ai_head, col_ai_clear = st.columns([7, 3])
    with col_ai_head:
        st.markdown("<p style='font-family:\"JetBrains Mono\"; font-weight:800; font-size:0.85rem; color:#60A5FA; margin-bottom:2px;'>🤖 ASISTENTE CUANTITATIVO</p>", unsafe_allow_html=True)
    with col_ai_clear:
        if st.button("🗑️ Limpiar", key="btn_clear_chat", use_container_width=True):
            st.session_state.chat_messages = []
            if supabase:
                try:
                    supabase.table("chat_messages").delete().neq("id", 0).execute()
                except Exception:
                    pass
            st.rerun()

    st.caption("Diagnóstico en vivo del mercado según perfiles GEX, CHEX y Net Drift")

    col_btn1, col_btn2 = st.columns(2)
    if col_btn1.button("📊 Pre-Market", key="btn_ai_premarket", use_container_width=True):
        with st.spinner("Analizando pre-market..."):
            res = consultar_ia(
                tipo_analisis="Pre-Market",
                mensaje_usuario="Analiza la estructura Pre-Market integrando el régimen de Gamma, Charm Exposure (CHEX), niveles clave (Walls, Zero Gamma) y el comportamiento del Net Drift reciente."
            )
            save_chat_message("assistant", res)

    if col_btn2.button("📈 Intradía", key="btn_ai_intraday", use_container_width=True):
        with st.spinner("Analizando flujo intradía..."):
            res = consultar_ia(
                tipo_analisis="Mercado Intradía",
                mensaje_usuario="Analiza el mercado intradía evaluando el flujo en vivo de Gamma, decaimiento de Charm (CHEX), la fuerza direccional del Net Drift y los puntos de inflexión esperados."
            )
            save_chat_message("assistant", res)

    st.markdown("---")

    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    if chat_input := st.chat_input("Escribe tu pregunta sobre GEX, CHEX o Net Drift..."):
        save_chat_message("user", chat_input)
        with st.chat_message("user"):
            st.write(chat_input)

        with st.spinner("Pensando..."):
            respuesta_bot = consultar_ia(mensaje_usuario=chat_input)
            save_chat_message("assistant", respuesta_bot)
            with st.chat_message("assistant"):
                st.write(respuesta_bot)

# --- PANEL DE MÉTRICAS TOP ---
cw_diff = ((cw1 - spot_price) / spot_price * 100) if spot_price > 0 else 0
pw_diff = ((pw1 - spot_price) / spot_price * 100) if spot_price > 0 else 0
zg_diff = ((zero_gamma - spot_price) / spot_price * 100) if spot_price > 0 else 0

gex_ratio = abs(call_gex_sum/put_gex_sum) if put_gex_sum != 0 else 0.0

k1, k2, k3, k4, k5, k6, k7, k8 = st.columns(8)
k1.markdown(f'<div class="metric-card"><div class="metric-label">Spot Price</div><div class="metric-value">${spot_price:.2f}</div><div class="metric-sub">{ticker_symbol}</div></div>', unsafe_allow_html=True)
k2.markdown(f'<div class="metric-card"><div class="metric-label">Net GEX</div><div class="metric-value" style="color:{"#10B981" if net_gex_total >= 0 else "#EF4444"};">{fmt_val(net_gex_total)}</div><div class="metric-sub">Ratio: {gex_ratio:.2f}</div></div>', unsafe_allow_html=True)
k3.markdown(f'<div class="metric-card"><div class="metric-label">Call GEX</div><div class="metric-value" style="color:#10B981">{fmt_val(call_gex_sum)}</div><div class="metric-sub">{call_oi_sum:,} OI</div></div>', unsafe_allow_html=True)
k4.markdown(f'<div class="metric-card"><div class="metric-label">Put GEX</div><div class="metric-value" style="color:#EF4444">{fmt_val(put_gex_sum)}</div><div class="metric-sub">{put_oi_sum:,} OI</div></div>', unsafe_allow_html=True)
k5.markdown(f'<div class="metric-card"><div class="metric-label">Total GEX</div><div class="metric-value" style="color:#3B82F6">{fmt_val(total_gex, show_sign=False)}</div><div class="metric-sub">{total_oi_sum:,} OI</div></div>', unsafe_allow_html=True)
k6.markdown(f'<div class="metric-card"><div class="metric-label">Call Wall</div><div class="metric-value" style="color:#10B981">${cw1:.0f}</div><div class="metric-sub">{cw_diff:+.2f}%</div></div>', unsafe_allow_html=True)
k7.markdown(f'<div class="metric-card"><div class="metric-label">Put Wall</div><div class="metric-value" style="color:#EF4444">${pw1:.0f}</div><div class="metric-sub">{pw_diff:+.2f}%</div></div>', unsafe_allow_html=True)
k8.markdown(f'<div class="metric-card"><div class="metric-label">Zero Gamma</div><div class="metric-value" style="color:#F59E0B">${zero_gamma:.2f}</div><div class="metric-sub">{zg_diff:+.2f}%</div></div>', unsafe_allow_html=True)

st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)

# --- ALMACENAMIENTO DE SNAPSHOTS 1-MINUTO EN SUPABASE Y JSONBIN ---
def push_to_supabase_bg(snapshot_payload):
    if supabase:
        try:
            supabase.table("gex_snapshots").insert(snapshot_payload).execute()
        except Exception as e:
            log_to_console("Supabase Async Snapshot Error", str(e))

def push_to_jsonbin_bg(bin_id, api_key, date_key, snapshot_entry):
    try:
        url = f"https://api.jsonbin.io/v3/b/{bin_id}"
        headers = {
            "Content-Type": "application/json",
            "X-Master-Key": api_key
        }
        resp = requests.get(f"{url}/latest", headers=headers, timeout=3)
        current_data = {}
        if resp.status_code == 200:
            record = resp.json().get("record", {})
            if isinstance(record, dict):
                current_data = record
        
        if date_key not in current_data or not isinstance(current_data[date_key], list):
            current_data[date_key] = []
        
        existing_times = [s.get("time") for s in current_data[date_key] if isinstance(s, dict)]
        if snapshot_entry["time"] not in existing_times:
            current_data[date_key].append(snapshot_entry)
            
            if len(current_data) > 15:
                sorted_dates = sorted(list(current_data.keys()))
                for old_d in sorted_dates[:-15]:
                    del current_data[old_d]
            
            requests.put(url, json=current_data, headers=headers, timeout=4)
    except Exception as e:
        log_to_console("JSONBin Async Background Push", str(e))

def export_snapshot_throttled():
    if not is_online:
        return

    last_export = st.session_state.get("last_export_time", 0)
    current_time = time.time()
    
    if current_time - last_export >= 60 and spot_price > 0 and not df_curr.empty:
        st.session_state.last_export_time = current_time
        
        time_str = now_tz.strftime("%H:%M")
        date_str = now_tz.strftime("%Y-%m-%d")
        
        strikes_payload = []
        for _, r in df_curr.iterrows():
            strikes_payload.append({
                "strike": float(r['strike']),
                "net_gex": float(r.get('net_gex', 0.0)),
                "call_gex": float(r.get('call_gex', 0.0)),
                "put_gex": float(r.get('put_gex', 0.0))
            })
        
        snapshot_entry = {
            "symbol": ticker_symbol,
            "time": time_str,
            "spot": float(spot_price),
            "net_gex": float(net_gex_total),
            "strikes": strikes_payload
        }
        
        t_sp = threading.Thread(target=push_to_supabase_bg, args=(snapshot_entry,), daemon=True)
        t_sp.start()

        if JSONBIN_BIN_ID and JSONBIN_API_KEY:
            t_jb = threading.Thread(
                target=push_to_jsonbin_bg,
                args=(JSONBIN_BIN_ID, JSONBIN_API_KEY, date_str, snapshot_entry),
                daemon=True
            )
            t_jb.start()

export_snapshot_throttled()

# --- PESTAÑAS PRINCIPALES ---
tab_gex, tab_live, tab_drift, tab_back, tab_data = st.tabs([
    "GEX INFO",
    "LIVE GAMMA",
    "NET DRIFT",
    "BACKGAMMA",
    "DATA"
])

# --- 1. GEX INFO ---
with tab_gex:
    sub_gex1, sub_gex2 = st.tabs(["NET GEX PROFILE", "CALLS vs PUTS"])
    
    with sub_gex1:
        if not df_curr.empty and 'strike' in df_curr.columns:
            df_sub = df_curr[(df_curr['strike'] >= min_strike) & (df_curr['strike'] <= max_strike)].copy()
            if not df_sub.empty:
                colors = ['#10B981' if v >= 0 else '#EF4444' for v in df_sub['net_gex']]
                xaxis_kwargs = safe_strike_range(df_sub)

                y_max_val = df_sub['net_gex'].max()
                y_min_val = df_sub['net_gex'].min()
                y_max_adj = (max(y_max_val, 0) * 1.5) if y_max_val > 0 else 1000
                y_min_adj = (min(y_min_val, 0) * 1.5) if y_min_val < 0 else -1000

                fig1 = go.Figure()
                fig1.add_trace(go.Bar(
                    x=df_sub['strike'],
                    y=df_sub['net_gex'],
                    orientation='v',
                    marker_color=colors,
                    hovertemplate="<b>Strike:</b> $%{x:.2f}<br><b>Net GEX:</b> %{customdata}<extra></extra>",
                    customdata=[fmt_val(v) for v in df_sub['net_gex']]
                ))
                
                if spot_price > 0:
                    fig1.add_vline(
                        x=spot_price,
                        line_color="#3B82F6",
                        line_width=1.5,
                        line_dash="dash",
                        annotation_text=f"Spot (${spot_price:.2f})",
                        annotation_position="top",
                        annotation_font=dict(color="#60A5FA", size=11, family="JetBrains Mono")
                    )
                
                fig1.update_layout(
                    template="plotly_dark",
                    plot_bgcolor='#06080D',
                    paper_bgcolor='#06080D',
                    title=dict(
                        text="<b>Strike Profile (Net Gamma Exposure)</b>",
                        font=dict(family="Plus Jakarta Sans", size=15, color="#F0F6FC")
                    ),
                    xaxis=dict(
                        title="Strike ($)",
                        gridcolor="rgba(255,255,255,0.05)",
                        tickfont=dict(family="JetBrains Mono", color="#8B949E"),
                        zeroline=False,
                        **xaxis_kwargs
                    ),
                    yaxis=dict(
                        title="Net GEX ($)",
                        gridcolor="rgba(255,255,255,0.05)",
                        tickfont=dict(family="JetBrains Mono", color="#8B949E"),
                        zeroline=True,
                        zerolinecolor="rgba(255,255,255,0.15)",
                        zerolinewidth=1,
                        range=[y_min_adj, y_max_adj]
                    ),
                    height=560,
                    margin=dict(l=50, r=40, t=50, b=40)
                )
                st.plotly_chart(fig1, use_container_width=True)

    with sub_gex2:
        if not df_curr.empty and 'strike' in df_curr.columns:
            df_sub = df_curr[(df_curr['strike'] >= min_strike) & (df_curr['strike'] <= max_strike)].copy()
            if not df_sub.empty:
                xaxis_kwargs = safe_strike_range(df_sub)

                y_max_val = max(df_sub['call_gex'].max(), 0)
                y_min_val = min(df_sub['put_gex'].min(), 0)
                y_max_adj = (y_max_val * 1.5) if y_max_val > 0 else 1000
                y_min_adj = (y_min_val * 1.5) if y_min_val < 0 else -1000

                fig2 = go.Figure()
                fig2.add_trace(go.Bar(
                    x=df_sub['strike'], y=df_sub['call_gex'],
                    name="Call GEX (+)", marker_color='#10B981',
                    hovertemplate="<b>Strike:</b> $%{x:.2f}<br><b>Call GEX:</b> %{customdata}<extra></extra>",
                    customdata=[fmt_val(v) for v in df_sub['call_gex']]
                ))
                fig2.add_trace(go.Bar(
                    x=df_sub['strike'], y=df_sub['put_gex'],
                    name="Put GEX (-)", marker_color='#EF4444',
                    hovertemplate="<b>Strike:</b> $%{x:.2f}<br><b>Put GEX:</b> %{customdata}<extra></extra>",
                    customdata=[fmt_val(v) for v in df_sub['put_gex']]
                ))
                
                if spot_price > 0:
                    fig2.add_vline(
                        x=spot_price,
                        line_color="#3B82F6",
                        line_width=1.5,
                        line_dash="dash",
                        annotation_text=f"Spot (${spot_price:.2f})",
                        annotation_position="top",
                        annotation_font=dict(color="#60A5FA", size=11, family="JetBrains Mono")
                    )
                
                fig2.update_layout(
                    template="plotly_dark", plot_bgcolor='#06080D', paper_bgcolor='#06080D',
                    title=dict(text="<b>Call Gamma vs Put Gamma por Strike</b>", font=dict(family="Plus Jakarta Sans", size=15, color="#F0F6FC")),
                    barmode='relative',
                    xaxis=dict(
                        title="Strike ($)",
                        gridcolor="rgba(255,255,255,0.05)",
                        tickfont=dict(family="JetBrains Mono"),
                        **xaxis_kwargs
                    ),
                    yaxis=dict(
                        title="Gamma Exposure ($)",
                        gridcolor="rgba(255,255,255,0.05)",
                        tickfont=dict(family="JetBrains Mono"),
                        range=[y_min_adj, y_max_adj]
                    ),
                    height=560, margin=dict(l=50, r=40, t=50, b=40)
                )
                st.plotly_chart(fig2, use_container_width=True)

# --- 2. LIVE GAMMA ---
with tab_live:
    st.markdown('<div class="depth-frame">', unsafe_allow_html=True)
    st.markdown(f"<h3 style='margin-top:0; font-weight:700; color:#F0F6FC; font-size:1.1rem;'>🌊 Real-Time Gamma Flow ({tz_choice})</h3>", unsafe_allow_html=True)

    if len(full_timestamps) > 0 and Z_matrix_real.shape[0] > 0 and Z_matrix_real.shape[1] > 0:
        custom_hover_matrix = [[fmt_val(val) for val in row] for row in Z_matrix_real]
        max_real_abs = float(np.max(np.abs(Z_matrix_real))) if Z_matrix_real.size > 0 and np.max(np.abs(Z_matrix_real)) > 0 else 1.0
        Z_matrix_scaled = Z_matrix_real / max_real_abs

        fig_live = go.Figure()

        fig_live.add_trace(go.Heatmap(
            x=full_timestamps,
            y=fine_strikes,
            z=Z_matrix_scaled,
            customdata=custom_hover_matrix,
            hovertemplate="<b>Hora:</b> %{x}<br><b>Strike:</b> $%{y:.2f}<br><b>Net Gamma Real:</b> %{customdata}<extra></extra>",
            zsmooth='best', zmin=-1.0, zmax=1.0, zmid=0,
            colorscale=[
                [0.0, 'rgba(239, 68, 68, 0.9)'],
                [0.4, 'rgba(239, 68, 68, 0.15)'],
                [0.48, 'rgba(6, 8, 13, 0.0)'],
                [0.52, 'rgba(6, 8, 13, 0.0)'],
                [0.6, 'rgba(16, 185, 129, 0.15)'],
                [1.0, 'rgba(16, 185, 129, 0.9)']
            ],
            colorbar=dict(title=dict(text="Net GEX ($)", side="top"), x=-0.05),
            hoverlabel=dict(namelength=0)
        ))

        raw_levels = []
        if min_strike <= cw1 <= max_strike: raw_levels.append(('Call Wall 1', cw1, '#10B981', 'solid'))
        if min_strike <= cw2 <= max_strike: raw_levels.append(('Call Wall 2', cw2, '#10B981', 'dash'))
        if min_strike <= cw3 <= max_strike: raw_levels.append(('Call Wall 3', cw3, '#10B981', 'dot'))
        if min_strike <= pw1 <= max_strike: raw_levels.append(('Put Wall 1', pw1, '#EF4444', 'solid'))
        if min_strike <= pw2 <= max_strike: raw_levels.append(('Put Wall 2', pw2, '#EF4444', 'dash'))
        if min_strike <= pw3 <= max_strike: raw_levels.append(('Put Wall 3', pw3, '#EF4444', 'dot'))
        if min_strike <= zero_gamma <= max_strike: raw_levels.append(('Flip Level', zero_gamma, '#3B82F6', 'dot'))

        grouped_levels = {}
        for label, val, color, dash in raw_levels:
            key = round(val, 1)
            if key not in grouped_levels: grouped_levels[key] = []
            grouped_levels[key].append((label, color, dash))

        for k_val, items in grouped_levels.items():
            for label, color, dash in items:
                fig_live.add_hline(y=k_val, line_color=color, line_width=0.8, line_dash=dash, layer="above")
            labels_str = " / ".join([item[0] for item in items])
            badge_text = f"<b>{labels_str}</b> (${k_val:.0f})"
            main_color = items[0][1]
            fig_live.add_annotation(
                x=0.988, xref="paper", y=k_val, yref="y", text=badge_text, showarrow=False,
                xanchor="right", yanchor="middle", font=dict(family="JetBrains Mono", size=10, color=main_color),
                bgcolor="#090D16", bordercolor=main_color, borderwidth=1, borderpad=3, opacity=0.95
            )

        if not h_1m_reindexed.empty:
            fig_live.add_trace(go.Candlestick(
                x=full_timestamps,
                open=h_1m_reindexed['Open'],
                high=h_1m_reindexed['High'],
                low=h_1m_reindexed['Low'],
                close=h_1m_reindexed['Close'],
                name="Spot Price",
                increasing_line_color='#10B981',
                decreasing_line_color='#EF4444',
                increasing_fillcolor='#10B981',
                decreasing_fillcolor='#EF4444',
                hovertemplate="<b>Hora:</b> %{x}<br><b>Apertura:</b> $%{open:.2f}<br><b>Máximo:</b> $%{high:.2f}<br><b>Mínimo:</b> $%{low:.2f}<br><b>Cierre:</b> $%{close:.2f}<extra></extra>"
            ))

        fig_live.update_layout(
            template="plotly_dark", plot_bgcolor='#06080D', paper_bgcolor='#06080D',
            uirevision="static_user_state",
            xaxis_title=f"Hora Intradía ({tz_choice.split(' ')[0]})", yaxis_title="Precio / Strike ($)",
            height=680, dragmode='pan', hovermode="closest", xaxis_rangeslider_visible=False,
            margin=dict(l=80, r=60, t=40, b=40), yaxis=dict(side='right'),
            hoverlabel=dict(bgcolor="#161B22", bordercolor="#30363D", font_size=12, font_family="JetBrains Mono", namelength=0)
        )

        st.plotly_chart(fig_live, use_container_width=True, config={'scrollZoom': True}, key="heatmap_live")

    st.markdown('</div>', unsafe_allow_html=True)

# --- 3. NET DRIFT ---
with tab_drift:
    st.markdown('<div class="depth-frame">', unsafe_allow_html=True)

    if len(full_timestamps) > 0 and len(call_drift_raw) > 0:
        last_call_val = call_drift_raw[-1] if len(call_drift_raw) > 0 else 0.0
        last_put_val = put_drift_raw[-1] if len(put_drift_raw) > 0 else 0.0
        last_net_val = net_drift_raw[-1] if len(net_drift_raw) > 0 else 0.0
        last_spot_val = closes_drift[-1] if len(closes_drift) > 0 else spot_price

        st.markdown(f"""
            <div style='text-align: center; margin-bottom: 12px;'>
                <h3 style='margin: 0; font-family: "Plus Jakarta Sans"; font-weight: 800; color: #F0F6FC; font-size: 1.15rem;'>
                    Net Drift (Premium) - {ticker_symbol}
                </h3>
                <div style='font-family: "JetBrains Mono"; font-size: 0.82rem; margin-top: 6px; display: flex; justify-content: center; gap: 18px; flex-wrap: wrap;'>
                    <span style='color: #10B981;'>● Calls ({fmt_val(last_call_val)})</span>
                    <span style='color: #EF4444;'>● Puts ({fmt_val(last_put_val)})</span>
                    <span style='color: #F59E0B;'>● Net ({fmt_val(last_net_val)})</span>
                    <span style='color: #3B82F6;'>● {ticker_symbol} (${last_spot_val:.2f})</span>
                </div>
            </div>
        """, unsafe_allow_html=True)

        fig_drift = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.06,
            row_heights=[0.75, 0.25],
            specs=[[{"secondary_y": True}], [{"secondary_y": False}]]
        )

        fig_drift.add_trace(go.Scatter(
            x=full_timestamps, y=call_drift_raw,
            mode='lines', name='Calls',
            line=dict(color='#10B981', width=2),
            hovertemplate="<b>Calls Drift:</b> %{customdata}<extra></extra>",
            customdata=[fmt_val(v) for v in call_drift_raw]
        ), row=1, col=1, secondary_y=False)

        fig_drift.add_trace(go.Scatter(
            x=full_timestamps, y=put_drift_raw,
            mode='lines', name='Puts',
            line=dict(color='#EF4444', width=2),
            hovertemplate="<b>Puts Drift:</b> %{customdata}<extra></extra>",
            customdata=[fmt_val(v) for v in put_drift_raw]
        ), row=1, col=1, secondary_y=False)

        fig_drift.add_trace(go.Scatter(
            x=full_timestamps, y=net_drift_raw,
            mode='lines', name='Net',
            line=dict(color='#F59E0B', width=2),
            hovertemplate="<b>Net Drift:</b> %{customdata}<extra></extra>",
            customdata=[fmt_val(v) for v in net_drift_raw]
        ), row=1, col=1, secondary_y=False)

        fig_drift.add_trace(go.Scatter(
            x=full_timestamps, y=closes_drift,
            mode='lines', name=ticker_symbol,
            line=dict(color='#3B82F6', width=2),
            hovertemplate=f"<b>{ticker_symbol}:</b> " + "$%{y:.2f}<extra></extra>"
        ), row=1, col=1, secondary_y=True)

        fig_drift.add_trace(go.Scatter(
            x=full_timestamps, y=vols_drift,
            mode='lines', name='Volume',
            line=dict(color='#10B981', width=1.5),
            fill='tozeroy',
            fillcolor='rgba(16, 185, 129, 0.25)',
            hovertemplate="<b>Volume:</b> %{y:,.0f}<extra></extra>"
        ), row=2, col=1)

        fig_drift.add_hline(y=0, line_color="rgba(255, 255, 255, 0.2)", line_width=1, row=1, col=1)
        fig_drift.add_hline(y=0, line_color="rgba(255, 255, 255, 0.2)", line_width=1, row=2, col=1)

        fig_drift.update_layout(
            template="plotly_dark",
            plot_bgcolor='#06080D',
            paper_bgcolor='#06080D',
            showlegend=False,
            height=650,
            margin=dict(l=60, r=60, t=30, b=30),
            hovermode="x unified",
            hoverlabel=dict(bgcolor="#161B22", bordercolor="#30363D", font_size=12, font_family="JetBrains Mono")
        )

        fig_drift.update_xaxes(
            gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(family="JetBrains Mono", color="#8B949E"),
            row=2, col=1,
            title_text=f"Hora Intradía ({tz_choice.split(' ')[0]})"
        )

        fig_drift.update_yaxes(
            title_text="Premium ($)",
            gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(family="JetBrains Mono", color="#8B949E"),
            row=1, col=1, secondary_y=False
        )

        fig_drift.update_yaxes(
            title_text="Underlying ($)",
            gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(family="JetBrains Mono", color="#8B949E"),
            row=1, col=1, secondary_y=True
        )

        fig_drift.update_yaxes(
            title_text="Volume",
            gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(family="JetBrains Mono", color="#8B949E"),
            row=2, col=1
        )

        st.plotly_chart(fig_drift, use_container_width=True)
        
    st.markdown('</div>', unsafe_allow_html=True)

# --- 4. BACKGAMMA (REPRODUCCIÓN HISTÓRICA) ---
with tab_back:
    st.markdown('<div class="backtest-controls">', unsafe_allow_html=True)
    st.markdown("<p style='font-family:\"JetBrains Mono\"; font-size:0.80rem; font-weight:800; color:#F0F6FC; letter-spacing:1px; margin-bottom:10px;'>⏮️ REPRODUCCIÓN & BACKTESTING DE SNAPSHOTS</p>", unsafe_allow_html=True)
    
    available_dates = sorted(list(jsonbin_history_data.keys()), reverse=True) if jsonbin_history_data else []

    col_bd1, col_bd2 = st.columns([2, 2])
    with col_bd1:
        selected_date = st.selectbox("FECHA A BACKTESTEAR", available_dates, key="bt_date_select")
    with col_bd2:
        bt_tf = st.selectbox("INTERVALO DE SALTO (TIMEFRAME)", [1, 5, 15, 30, 60], index=0, key="bt_tf_select")

    day_snaps = jsonbin_history_data.get(selected_date, [])
    
    if day_snaps:
        if "bt_snap_index" not in st.session_state:
            st.session_state.bt_snap_index = len(day_snaps) - 1

        col_bt1, col_bt2, col_bt3 = st.columns([2, 1, 1])
        with col_bt1:
            max_snap = max(0, len(day_snaps) - 1)
            st.session_state.bt_snap_index = min(max(0, st.session_state.bt_snap_index), max_snap)
            
            st.session_state.bt_snap_index = st.slider(
                "SNAPSHOTS DISPONIBLES",
                min_value=0,
                max_value=max_snap,
                value=st.session_state.bt_snap_index,
                format="Snap %d"
            )
        with col_bt2:
            st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)
            if st.button("◀ Back", use_container_width=True, key="bt_back_btn"):
                st.session_state.bt_snap_index = max(0, st.session_state.bt_snap_index - bt_tf)
                st.rerun()
        with col_bt3:
            st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)
            if st.button("Next ▶", use_container_width=True, key="bt_next_btn"):
                st.session_state.bt_snap_index = min(len(day_snaps) - 1, st.session_state.bt_snap_index + bt_tf)
                st.rerun()

        st.markdown('</div>', unsafe_allow_html=True)

        sliced_snaps = day_snaps[:st.session_state.bt_snap_index + 1]
        current_snap = sliced_snaps[-1]

        st.markdown('<div class="depth-frame">', unsafe_allow_html=True)
        st.markdown(f"<p style='font-family:\"JetBrains Mono\"; color:#60A5FA; font-size:0.85rem; font-weight:700;'>⏱️ ESTADO DE SIMULACIÓN | Hora: <span style='color:#F0F6FC;'>{current_snap.get('time', 'N/A')}</span> | Spot: <span style='color:#10B981;'>${current_snap.get('spot', 0.0):.2f}</span> | Net GEX: <span style='color:#F59E0B;'>{fmt_val(current_snap.get('net_gex', 0.0))}</span></p>", unsafe_allow_html=True)

        bt_times = [s.get("time") for s in sliced_snaps]
        bt_spots = [s.get("spot", 0.0) for s in sliced_snaps]

        all_strikes_set = set()
        for s in sliced_snaps:
            for st_item in s.get("strikes", []):
                all_strikes_set.add(st_item["strike"])
        
        sorted_bt_strikes = sorted(list(all_strikes_set)) if all_strikes_set else np.linspace(min_strike, max_strike, 20).tolist()
        
        bt_Z_matrix = np.zeros((len(sorted_bt_strikes), len(sliced_snaps)))
        strike_to_idx = {k: i for i, k in enumerate(sorted_bt_strikes)}

        for t_i, s in enumerate(sliced_snaps):
            for st_item in s.get("strikes", []):
                st_val = st_item["strike"]
                if st_val in strike_to_idx:
                    bt_Z_matrix[strike_to_idx[st_val], t_i] = st_item.get("net_gex", 0.0)

        if bt_Z_matrix.size > 0 and bt_Z_matrix.shape[1] > 1:
            bt_Z_matrix = gaussian_filter(bt_Z_matrix, sigma=(0.5, 0.8))

        max_abs_bt = float(np.max(np.abs(bt_Z_matrix))) if bt_Z_matrix.size > 0 and np.max(np.abs(bt_Z_matrix)) > 0 else 1.0
        bt_Z_scaled = bt_Z_matrix / max_abs_bt

        custom_hover_bt = [[fmt_val(val) for val in row] for row in bt_Z_matrix]

        fig_bt_heat = go.Figure()
        fig_bt_heat.add_trace(go.Heatmap(
            x=bt_times,
            y=sorted_bt_strikes,
            z=bt_Z_scaled,
            customdata=custom_hover_bt,
            hovertemplate="<b>Hora Simulada:</b> %{x}<br><b>Strike:</b> $%{y:.2f}<br><b>Net Gamma Registrado:</b> %{customdata}<extra></extra>",
            zsmooth='best', zmin=-1.0, zmax=1.0, zmid=0,
            colorscale=[
                [0.0, 'rgba(239, 68, 68, 0.9)'],
                [0.4, 'rgba(239, 68, 68, 0.15)'],
                [0.48, 'rgba(6, 8, 13, 0.0)'],
                [0.52, 'rgba(6, 8, 13, 0.0)'],
                [0.6, 'rgba(16, 185, 129, 0.15)'],
                [1.0, 'rgba(16, 185, 129, 0.9)']
            ],
            colorbar=dict(title=dict(text="Net GEX ($)", side="top"), x=-0.05)
        ))

        fig_bt_heat.add_trace(go.Scatter(
            x=bt_times,
            y=bt_spots,
            mode='lines+markers',
            name='Spot Real',
            line=dict(color='#3B82F6', width=2),
            marker=dict(size=4, color='#60A5FA')
        ))

        fig_bt_heat.update_layout(
            template="plotly_dark", plot_bgcolor='#06080D', paper_bgcolor='#06080D',
            title=f"<b>LIVE GAMMA REPLAY ({selected_date})</b>",
            xaxis_title="Hora Intradía Registrada", yaxis_title="Strike / Precio ($)",
            height=560, margin=dict(l=80, r=60, t=40, b=40), yaxis=dict(side='right')
        )

        st.plotly_chart(fig_bt_heat, use_container_width=True, key="heatmap_backtest_jsonbin")

        curr_strikes = current_snap.get("strikes", [])
        if curr_strikes:
            df_bt_prof = pd.DataFrame(curr_strikes).sort_values("strike")
            colors_bt_prof = ['#10B981' if v >= 0 else '#EF4444' for v in df_bt_prof['net_gex']]

            fig_bt_prof = go.Figure()
            fig_bt_prof.add_trace(go.Bar(
                x=df_bt_prof['strike'],
                y=df_bt_prof['net_gex'],
                marker_color=colors_bt_prof,
                hovertemplate="<b>Strike:</b> $%{x:.2f}<br><b>Net GEX Registrado:</b> %{customdata}<extra></extra>",
                customdata=[fmt_val(v) for v in df_bt_prof['net_gex']]
            ))

            fig_bt_prof.add_vline(
                x=current_snap.get("spot", 0.0),
                line_color="#3B82F6",
                line_width=1.5,
                line_dash="dash",
                annotation_text=f"Spot (${current_snap.get('spot', 0.0):.2f})",
                annotation_position="top"
            )

            fig_bt_prof.update_layout(
                template="plotly_dark", plot_bgcolor='#06080D', paper_bgcolor='#06080D',
                title=f"<b>NET GEX PROFILE EN EL MINUTO {current_snap.get('time')}</b>",
                xaxis_title="Strike ($)", yaxis_title="Net GEX ($)",
                height=450, margin=dict(l=50, r=40, t=50, b=40)
            )

            st.plotly_chart(fig_bt_prof, use_container_width=True, key="profile_backtest_jsonbin")

        st.markdown('</div>', unsafe_allow_html=True)

# --- 5. DATA ---
with tab_data:
    if not df_curr.empty:
        st.dataframe(df_curr, use_container_width=True)
    else:
        st.info("No hay datos de opciones disponibles actualmente.")
