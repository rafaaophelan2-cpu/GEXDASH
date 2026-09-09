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
# Firebase Realtime Database (reemplaza a JSONBin: sin límite de requests,
# 1GB gratis). Los "Database secrets" legacy quedaron obsoletos en Firebase,
# así que en vez de un secreto usamos reglas de la Realtime Database que
# permiten leer/escribir SOLO en los nodos /live_levels y /history (ver
# instrucciones de las reglas). FIREBASE_DB_URL es la URL de tu base de
# datos (Firebase Console > Realtime Database).
FIREBASE_DB_URL = str(st.secrets.get("FIREBASE_DB_URL", "")).strip().rstrip("/")
GROQ_API_KEY = st.secrets.get("GROQ_API_KEY", st.secrets.get("GROQ_KEY", os.environ.get("GROQ_API_KEY", "")))
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))

# Extracción y sanitización robusta de llaves de Supabase
SUPABASE_URL = str(st.secrets.get("SUPABASE_URL", os.environ.get("SUPABASE_URL", ""))).strip().replace('"', '').replace("'", "")
SUPABASE_KEY = str(
    st.secrets.get("SUPABASE_KEY", 
    st.secrets.get("SUPABASE_ANON_KEY", 
    st.secrets.get("SUPABASE_SERVICE_KEY", 
    os.environ.get("SUPABASE_KEY", os.environ.get("SUPABASE_ANON_KEY", "")))))
).strip().replace('"', '').replace("'", "")

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
    if SUPABASE_URL and SUPABASE_KEY and SUPABASE_URL.startswith("http") and len(SUPABASE_KEY) > 20:
        try:
            return create_client(SUPABASE_URL, SUPABASE_KEY)
        except Exception as e:
            return None
    return None

supabase: Client = get_supabase_client()

# --- LECTURA DE SNAPSHOTS DESDE SUPABASE ---
@st.cache_data(ttl=1)
def fetch_supabase_latest_snapshot(symbol="QQQ"):
    if not supabase:
        return None
    try:
        res = supabase.table("gex_intraday") \
            .select("*") \
            .eq("symbol", symbol) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        if res.data and len(res.data) > 0:
            return res.data[0]
    except Exception as e:
        try:
            res = supabase.table("gex_intraday") \
                .select("*") \
                .eq("symbol", symbol) \
                .limit(1) \
                .execute()
            if res.data and len(res.data) > 0:
                return res.data[0]
        except Exception as e2:
            log_to_console("Supabase Snapshot Fetch Error", str(e2))
    return None

@st.cache_data(ttl=2)
def fetch_supabase_gex_history(symbol="QQQ", limit=100):
    if not supabase:
        return []
    try:
        res = supabase.table("gex_intraday") \
            .select("*") \
            .eq("symbol", symbol) \
            .order("created_at", desc=False) \
            .limit(limit) \
            .execute()
        if res.data:
            return res.data
    except Exception as e:
        try:
            res = supabase.table("gex_intraday") \
                .select("*") \
                .eq("symbol", symbol) \
                .limit(limit) \
                .execute()
            if res.data:
                return res.data
        except Exception as e2:
            log_to_console("Supabase History Fetch Error", str(e2))
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
        font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif !important;
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

    /* Alinea verticalmente el badge ONLINE/OFFLINE con el botón DTE de la
       misma fila, y lo acerca al borde del botón (con un pequeño espacio,
       sin tocarlo) para que al abrir el popover de DTE no quede tapando
       ni sobreponiéndose al badge. */
    [data-testid="stHorizontalBlock"]:has(.badge-online),
    [data-testid="stHorizontalBlock"]:has(.badge-offline),
    [data-testid="stHorizontalBlock"]:has(.badge-warning) {
        align-items: center !important;
    }
    [data-testid="stHorizontalBlock"]:has(.badge-online) > div:has(.badge-online),
    [data-testid="stHorizontalBlock"]:has(.badge-offline) > div:has(.badge-offline),
    [data-testid="stHorizontalBlock"]:has(.badge-warning) > div:has(.badge-warning) {
        display: flex !important;
        align-items: center !important;
        justify-content: flex-end !important;
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
    
    .data-summary-box {
        background: #0E131F;
        border: 1px solid rgba(59, 130, 246, 0.3);
        border-radius: 10px;
        padding: 20px;
        margin-bottom: 20px;
    }

    .data-table-container {
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 8px;
        overflow: hidden;
        margin-bottom: 20px;
    }
    </style>
""", unsafe_allow_html=True)

# --- MÓDULO DE AUTENTICACIÓN ---
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "user_email" not in st.session_state:
    st.session_state.user_email = ""

def login_user(username_in, password_in):
    user_clean = username_in.strip().lower()
    pass_clean = password_in.strip()
    
    if not user_clean or not pass_clean:
        return False, "Por favor ingresa un usuario y contraseña válidos."

    if supabase:
        try:
            pass_hash = hashlib.sha256(pass_clean.encode('utf-8')).hexdigest()
            res = supabase.table("app_users").select("*").eq("username", user_clean).execute()
            if res.data and len(res.data) > 0:
                user_record = res.data[0]
                db_hash = str(user_record.get('password_hash', '')).strip()
                if db_hash == pass_hash or db_hash == pass_clean:
                    st.session_state.authenticated = True
                    st.session_state.user_email = user_record.get('username', user_clean)
                    return True, f"Bienvenido {user_record.get('name', user_clean)}"
        except Exception as e:
            log_to_console("Supabase Login Error", str(e))

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

    dev_users = {"admin": "admin123", "trader": "gex2026"}
    if user_clean in dev_users and dev_users[user_clean] == pass_clean:
        st.session_state.authenticated = True
        st.session_state.user_email = user_clean
        return True, "Inicio de sesión en modo desarrollo."

    return False, "Usuario o contraseña incorrectos."

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
                supabase.table("chat_messages").insert({"role": role, "content": cleaned_content}).execute()
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

# --- ACCESO SEGURO AL CLIENTE SCHWAB COMPARTIDO (FIX BUG MULTIUSUARIO) ---
# 'client' es UN SOLO objeto (st.cache_resource) reutilizado por las 3
# sesiones de usuario en paralelo. schwab-py envuelve una requests.Session +
# manejo de refresh de OAuth token, que no está garantizado thread-safe: si
# dos sesiones llaman client.get_option_chain()/get_price_history()/get_quote()
# casi al mismo tiempo (p. ej. porque cambiar el DTE dispara un rerun extra
# de una de las sesiones justo cuando el TTL del cache ya expiró), puede
# haber una condición de carrera que hace fallar una de las dos llamadas.
#
# El problema NO era solo esa excepción puntual: como fetch_option_chain_schwab
# (y las demás fetch_* de Schwab) están cacheadas con st.cache_data usando
# solo (symbol, strikes) como clave -es decir, UNA sola entrada de caché
# compartida por TODOS los usuarios del mismo ticker-, un resultado vacío
# causado por esa excepción quedaba GUARDADO en esa única entrada compartida
# y se servía a las otras 2 sesiones durante el TTL, aunque ellas no hubieran
# tocado nada. Eso es lo que se veía como "otro usuario pierde su vista al
# cambiar el DTE".
#
# Solución: (1) un Lock que serializa todas las llamadas de red al cliente
# compartido, para eliminar la condición de carrera de raíz; (2) un cache de
# "último dato bueno" en memoria de proceso (separado del st.cache_data de
# Streamlit) para que, si una llamada falla igual, se sirva el último dato
# válido conocido en vez de un resultado vacío -y así un fallo transitorio en
# una sesión nunca "vacía" el gráfico de las otras-.
_schwab_client_lock = threading.Lock()
_schwab_last_good = {}

def _schwab_call_with_fallback(cache_key, empty_value, fetch_fn):
    """Ejecuta fetch_fn() serializado por _schwab_client_lock. Si devuelve un
    resultado no vacío, lo guarda como 'último bueno' para cache_key y lo
    retorna. Si falla o devuelve vacío, retorna el último bueno conocido (si
    existe) en vez de propagar el vacío al caché compartido de Streamlit."""
    try:
        with _schwab_client_lock:
            result = fetch_fn()
        is_empty = (
            result is None
            or (isinstance(result, (dict, list)) and len(result) == 0)
            or (isinstance(result, pd.DataFrame) and result.empty)
            or (isinstance(result, (int, float)) and result == empty_value)
        )
        if not is_empty:
            _schwab_last_good[cache_key] = result
            return result
    except Exception as e:
        log_to_console(f"Schwab call error ({cache_key})", str(e))
    return _schwab_last_good.get(cache_key, empty_value)

@st.cache_data(ttl=20)
def fetch_firebase_history(db_url):
    if not db_url:
        return {}
    try:
        url = f"{db_url}/history.json"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            return resp.json() or {}
    except Exception as e:
        log_to_console("Firebase Read Error", str(e))
    return {}

# --- CLIENTES DE IA ---
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
            model="openai/gpt-oss-120b",
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
            "model": "openai/gpt-oss-120b",
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

# --- SIDEBAR ---
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
now_tz = pd.Timestamp.now(tz=tz_target)

# --- Zona horaria FIJA para guardado de snapshots (Firebase/Supabase) ---
# tz_target/now_tz de arriba son SOLO para lo que cada usuario ve en pantalla
# (selector por sesión). Si 3 personas usan la web con selecciones distintas
# (Lima vs NY), guardar snapshots con la hora de "now_tz" hacía que el mismo
# instante real quedara etiquetado con horas distintas según quién lo guardó
# (desfase de 1h), rompiendo el chequeo anti-duplicados de Firebase y
# desalineando NET DRIFT/BACKGAMMA al leer datos guardados por otra sesión.
# Ahora TODO lo que se guarda (time/date de snapshots) usa siempre NY,
# sin importar qué tenga elegido cada usuario en su sidebar.
STORAGE_TZ = "America/New_York"
now_tz_store = pd.Timestamp.now(tz=STORAGE_TZ)

st.sidebar.markdown("<hr style='border-color:rgba(255,255,255,0.06);'>", unsafe_allow_html=True)

auto_refresh = st.sidebar.toggle("AUTO-REFRESCO EN VIVO", value=True)
refresh_interval = st.sidebar.select_slider(
    "INTERVALO (SEGUNDOS)",
    options=[1, 2, 5, 10, 15, 30, 60],
    value=15,
    disabled=not auto_refresh
)

if auto_refresh:
    st.sidebar.caption(f"⏱️ ÚLTIMA ACTUALIZACIÓN: {now_tz.strftime('%H:%M:%S')}")
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=refresh_interval * 1000, key="gex_auto_refresh")
    except ImportError:
        st.components.v1.html(
            f"<script>setTimeout(function(){{ window.parent.postMessage({{type: 'streamlit:render'}}, '*'); location.reload(); }}, {refresh_interval * 1000});</script>",
            height=0, width=0
        )

st.sidebar.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
if st.sidebar.button("🔄 ACTUALIZAR DATOS AHORA", use_container_width=True):
    # IMPORTANTE: st.cache_data.clear() borra el caché de TODA la app para
    # TODOS los usuarios conectados (no solo el de esta sesión). Por eso,
    # cuando alguien pulsaba este botón, a cualquier otra persona viendo la
    # web en simultáneo se le vaciaban de golpe todos los datos/barras hasta
    # el siguiente fetch. En vez de borrar el caché global, forzamos un
    # rerun: como los caches ya tienen TTLs cortos (5-30s), los datos se
    # refrescan solos sin afectar a otras sesiones activas.
    st.rerun()

# --- FUNCIONES DE MERCADO SCHWAB ---
@st.cache_data(ttl=5)
def fetch_history_schwab(symbol):
    if not client:
        return pd.DataFrame()

    def _do_fetch():
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
        return pd.DataFrame()

    return _schwab_call_with_fallback(f"history:{symbol}", pd.DataFrame(), _do_fetch)

@st.cache_data(ttl=5)
def fetch_option_chain_schwab(symbol, strikes_count):
    if not client:
        return {}

    def _do_fetch():
        today = datetime.now()
        contract_type = getattr(client.Options.ContractType, 'ALL', 'ALL') if hasattr(client, 'Options') else 'ALL'
        resp = client.get_option_chain(
            symbol=symbol,
            contract_type=contract_type,
            strike_count=strikes_count,
            from_date=today,
            to_date=today + timedelta(days=90) # Extendido a 90 días DTE
        )
        if resp.status_code == 200:
            return resp.json()
        return {}

    return _schwab_call_with_fallback(f"chain:{symbol}:{strikes_count}", {}, _do_fetch)

@st.cache_data(ttl=5)
def fetch_nq_price_schwab():
    if not client:
        return 0.0

    def _do_fetch():
        resp = client.get_quote("/NQ")
        if resp.status_code == 200:
            data = resp.json()
            nq_data = data.get("/NQ", {}) if isinstance(data, dict) else {}
            quote_data = nq_data.get("quote", {}) if isinstance(nq_data, dict) else {}
            price = float(quote_data.get("lastPrice", quote_data.get("closePrice", 0.0)))
            if price > 0:
                return price
        return 0.0

    return _schwab_call_with_fallback("nq_price", 0.0, _do_fetch)

@st.cache_data(ttl=15)
def fetch_vix_schwab():
    if not client:
        return 0.0

    def _do_fetch():
        for sym in ["$VIX", "VIX", "$VIX.X"]:
            resp = client.get_quote(sym)
            if resp.status_code == 200:
                data = resp.json()
                vix_data = data.get(sym, {}) if isinstance(data, dict) else {}
                quote_data = vix_data.get("quote", {}) if isinstance(vix_data, dict) else {}
                price = float(quote_data.get("lastPrice", quote_data.get("closePrice", 0.0)))
                if price > 0:
                    return price
        return 0.0

    return _schwab_call_with_fallback("vix_price", 0.0, _do_fetch)

now_tz = pd.Timestamp.now(tz=tz_target)
ref_today = now_tz.floor('D').tz_localize(None)

latest_supabase_snap = fetch_supabase_latest_snapshot(ticker_symbol)
hist_raw = fetch_history_schwab(ticker_symbol)
chain_raw = fetch_option_chain_schwab(ticker_symbol, strike_range)

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

    def extract_iv(opt_dict):
        vol = float(opt_dict.get('volatility', opt_dict.get('impliedVolatility', 0.0)))
        if vol > 2.0:
            vol = vol / 100.0
        return max(vol, 0.001)

    def clean_greek(val, default=0.0):
        try:
            v = float(val)
            if np.isnan(v) or np.isinf(v) or v <= -500.0 or v >= 500.0:
                return default
            return v
        except (ValueError, TypeError):
            return default

    all_records = []

    for exp_key in all_exp_keys:
        parts = exp_key.split(':')
        exp_date_str = parts[0] if len(parts) > 0 else exp_key
        try:
            dte_val = int(parts[1]) if len(parts) > 1 else 0
        except ValueError:
            dte_val = 0

        calls_for_exp = call_map.get(exp_key) or {}
        puts_for_exp = put_map.get(exp_key) or {}
        records = {}

        for strike_str, opt_list in calls_for_exp.items():
            if not opt_list: continue
            opt = opt_list[0]
            strike = float(strike_str)
            if strike not in records:
                records[strike] = {
                    'strike': strike, 'exp_key': exp_key, 'exp_date': exp_date_str, 'dte': dte_val,
                    'openInterest_c': 0, 'openInterest_p': 0,
                    'gamma_c': 0.0, 'gamma_p': 0.0, 'delta_c': 0.0, 'delta_p': 0.0,
                    'theta_c': 0.0, 'theta_p': 0.0, 'vega_c': 0.0, 'vega_p': 0.0,
                    'vanna_c': 0.0, 'vanna_p': 0.0, 'iv_c': 0.0, 'iv_p': 0.0
                }
            records[strike]['openInterest_c'] = int(opt.get('openInterest', 0))
            records[strike]['gamma_c'] = clean_greek(opt.get('gamma'))
            records[strike]['delta_c'] = abs(clean_greek(opt.get('delta')))
            records[strike]['theta_c'] = clean_greek(opt.get('theta'))
            records[strike]['vega_c'] = clean_greek(opt.get('vega'))
            records[strike]['iv_c'] = extract_iv(opt)

        for strike_str, opt_list in puts_for_exp.items():
            if not opt_list: continue
            opt = opt_list[0]
            strike = float(strike_str)
            if strike not in records:
                records[strike] = {
                    'strike': strike, 'exp_key': exp_key, 'exp_date': exp_date_str, 'dte': dte_val,
                    'openInterest_c': 0, 'openInterest_p': 0,
                    'gamma_c': 0.0, 'gamma_p': 0.0, 'delta_c': 0.0, 'delta_p': 0.0,
                    'theta_c': 0.0, 'theta_p': 0.0, 'vega_c': 0.0, 'vega_p': 0.0,
                    'vanna_c': 0.0, 'vanna_p': 0.0, 'iv_c': 0.0, 'iv_p': 0.0
                }
            records[strike]['openInterest_p'] = int(opt.get('openInterest', 0))
            records[strike]['gamma_p'] = clean_greek(opt.get('gamma'))
            records[strike]['delta_p'] = -abs(clean_greek(opt.get('delta')))
            records[strike]['theta_p'] = clean_greek(opt.get('theta'))
            records[strike]['vega_p'] = clean_greek(opt.get('vega'))
            records[strike]['iv_p'] = extract_iv(opt)

        all_records.extend(list(records.values()))

    df = pd.DataFrame(all_records).sort_values(['dte', 'strike']).reset_index(drop=True) if all_records else pd.DataFrame()
    return df, selected_exp

df_curr, exp_0dte = parse_schwab_chain(chain_raw)

is_online = False
is_cloud_backup = False

if client is not None and isinstance(chain_raw, dict) and len(chain_raw) > 0 and spot_price > 0 and not df_curr.empty:
    is_online = True

# Flag exclusivo para el badge de estado: True si SCHWAB devolvio CUALQUIER
# informacion (aunque no haya podido parsearse del todo), False si esta
# completamente ausente. No se usa para decidir fuente de datos/fallback:
# eso lo sigue controlando 'is_online' de arriba.
schwab_status_online = client is not None and isinstance(chain_raw, dict) and len(chain_raw) > 0

jsonbin_history_data = fetch_firebase_history(FIREBASE_DB_URL)

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
            for col in ['delta_c', 'delta_p', 'theta_c', 'theta_p', 'vega_c', 'vega_p', 'vanna_c', 'vanna_p']:
                if col not in df_curr.columns: df_curr[col] = 0.0
            exp_0dte = now_tz.strftime('%Y-%m-%d') + ":0"
            if 'exp_date' not in df_curr.columns: df_curr['exp_date'] = exp_0dte.split(':')[0]
            if 'dte' not in df_curr.columns: df_curr['dte'] = 0
            if 'exp_key' not in df_curr.columns: df_curr['exp_key'] = exp_0dte
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
                    for col in ['delta_c', 'delta_p', 'theta_c', 'theta_p', 'vega_c', 'vega_p', 'vanna_c', 'vanna_p']:
                        if col not in df_curr.columns: df_curr[col] = 0.0
                    exp_0dte = latest_date_key + ":0"
                    if 'exp_date' not in df_curr.columns: df_curr['exp_date'] = latest_date_key
                    if 'dte' not in df_curr.columns: df_curr['dte'] = 0
                    if 'exp_key' not in df_curr.columns: df_curr['exp_key'] = exp_0dte

if spot_price <= 0:
    spot_price = TICKER_DEFAULTS.get(ticker_symbol, 480.00)

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
# Mismo instante real, etiquetado en NY fija (STORAGE_TZ) — se usa solo para
# cruzar contra snapshots guardados en Firebase/Supabase (que desde el fix
# de zona horaria siempre guardan su "time" en NY), sin importar qué tenga
# elegido el usuario en su sidebar. El eje X del gráfico sigue usando
# full_timestamps (hora de visualización de cada sesión).
full_timestamps_ny = full_time_grid.tz_convert(STORAGE_TZ).strftime('%H:%M').tolist()

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
        full_timestamps = [pd.to_datetime(s.get("time", s.get("created_at", datetime.now()))).strftime('%H:%M') for s in supabase_history]
        full_timestamps_ny = full_timestamps  # ya vienen guardados en NY fija
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
        full_timestamps_ny = full_timestamps  # ya vienen guardados en NY fija
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
            'vanna_c': 0.02,
            'vanna_p': 0.02,
            'iv_c': iv,
            'iv_p': iv
        })
    df_curr = pd.DataFrame(syn_records)
    exp_0dte = now_tz.strftime('%Y-%m-%d') + ":0"

# --- ENCABEZADO ---
# Ratio [8.5, 1.5] igual al usado por el panel de metricas (col_metrics_title,
# col_metrics_dte), para que el boton de CONSOLA quede exactamente del mismo
# ancho (y en el mismo borde derecho) que el boton de DTE.
col_head_title, col_head_console = st.columns([8.5, 1.5])

with col_head_title:
    st.markdown("<h2 style='margin:0; font-weight:800; letter-spacing:-0.5px; background: linear-gradient(90deg, #F0F6FC 0%, #8B949E 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent;'>GEX QUANT TERMINAL</h2>", unsafe_allow_html=True)
    st.markdown("<p style='color:#6E7681; margin:0 0 15px 0; font-size:0.78rem; font-family:\"JetBrains Mono\"; letter-spacing:0.5px;'>SCHWAB REAL-TIME GAMMA EXPOSURE & INTRADAY FLOW</p>", unsafe_allow_html=True)

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
vix_val = fetch_vix_schwab()

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

def compute_call_put_walls(df_grouped, spot_ref, gap=2.0):
    """
    Determina Call Walls y Put Walls por DOMINANCIA DE SIGNO del net_gex:
    - Un strike es Call Wall SI Y SOLO SI su net_gex es positivo (predominan
      calls) y es Put Wall si y solo si es negativo (predominan puts). Nunca
      puede ser ambos a la vez, a diferencia del esquema anterior que
      rankeaba call_gex y put_gex por separado (eso permitía que un mismo
      strike apareciera como Call Wall y también como Put Wall).
    - El ranking dentro de cada lado es por la MAGNITUD del net_gex de ese
      strike (no por el volumen bruto call_gex/put_gex).

    df_grouped debe venir ya agrupado por 'strike' (una sola fila por strike,
    sumando todas las expiraciones) y tener las columnas 'strike' y 'net_gex'.
    Devuelve (cw1, cw2, cw3, pw1, pw2, pw3).
    """
    if df_grouped is None or df_grouped.empty or 'net_gex' not in df_grouped.columns:
        return (spot_ref + gap * 2.5, spot_ref + gap * 5, spot_ref + gap * 7.5,
                spot_ref - gap * 2.5, spot_ref - gap * 5, spot_ref - gap * 7.5)

    calls_side = df_grouped[df_grouped['net_gex'] > 0].sort_values('net_gex', ascending=False)
    top_calls = calls_side['strike'].tolist()
    cw1 = top_calls[0] if len(top_calls) > 0 else spot_ref + gap * 2.5
    cw2 = top_calls[1] if len(top_calls) > 1 else cw1 + gap
    cw3 = top_calls[2] if len(top_calls) > 2 else cw2 + gap

    puts_side = df_grouped[df_grouped['net_gex'] < 0].sort_values('net_gex', ascending=True)
    top_puts = puts_side['strike'].tolist()
    pw1 = top_puts[0] if len(top_puts) > 0 else spot_ref - gap * 2.5
    pw2 = top_puts[1] if len(top_puts) > 1 else pw1 - gap
    pw3 = top_puts[2] if len(top_puts) > 2 else pw2 - gap

    return cw1, cw2, cw3, pw1, pw2, pw3

def get_nearest_dte_subset(df_source):
    """
    Devuelve el subconjunto de df_source correspondiente a la expiración
    con menor DTE (0DTE cuando existe), de forma determinística e
    independiente de cualquier selección de DTE que cualquier sesión/usuario
    tenga en pantalla en ese momento (session_state es por sesión, así que
    no se puede usar como fuente de verdad para datos que se comparten
    entre sesiones o se guardan a Firebase/Supabase). Si no hay columnas
    'exp_key'/'dte' disponibles, devuelve df_source completo sin filtrar.
    """
    if df_source is None or df_source.empty:
        return df_source
    if 'exp_key' in df_source.columns and 'dte' in df_source.columns:
        nearest_key = df_source.sort_values('dte')['exp_key'].iloc[0]
        df_sel = df_source[df_source['exp_key'] == nearest_key]
        if not df_sel.empty:
            return df_sel
    return df_source

if not df_curr.empty and spot_price > 0:
    # NOTA: antes esta condicion tambien exigia "exp_0dte is not None", lo que
    # acoplaba el calculo de TODAS las Griegas (DEX/TEX/VEX/CHEX/VANNA) e incluso
    # el propio GEX a la disponibilidad de una fecha de expiracion valida. Si
    # exp_0dte llegaba como None por cualquier motivo (fallback de datos, timing
    # de carga, etc.), ninguna de estas columnas se generaba en df_curr y todos
    # los perfiles de Griegas quedaban vacios en TODOS los DTE. Se desacopla:
    # ahora solo se necesita tener datos y un spot valido; exp_0dte solo afecta
    # el calculo de T_exp (dias a vencimiento) para la recalibracion dinamica.
    if exp_0dte is not None:
        exp_date_part = exp_0dte.split(':')[0] if isinstance(exp_0dte, str) and ':' in exp_0dte else str(exp_0dte)
        try:
            exp_dt = pd.to_datetime(exp_date_part).tz_localize(None)
            days_to_exp = max((exp_dt - ref_today).days, 0)
        except Exception:
            days_to_exp = 0
    else:
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
    df_curr['net_dex'] = (df_curr['call_dex'] + df_curr['put_dex']).fillna(0.0)

    df_curr['call_tex'] = df_curr['theta_c'] * df_curr['openInterest_c'] * 100
    df_curr['put_tex'] = df_curr['theta_p'] * df_curr['openInterest_p'] * 100
    df_curr['net_tex'] = (df_curr['call_tex'] + df_curr['put_tex']).fillna(0.0)

    df_curr['call_vex'] = df_curr['vega_c'] * df_curr['openInterest_c'] * 100
    df_curr['put_vex'] = df_curr['vega_p'] * df_curr['openInterest_p'] * 100
    df_curr['net_vex'] = (df_curr['call_vex'] + df_curr['put_vex']).fillna(0.0)

    r_rate = 0.045
    valid_strikes = df_curr['strike'] > 0
    d1_calc = (np.log(spot_price / df_curr.loc[valid_strikes, 'strike']) + (r_rate + 0.5 * atm_iv**2) * T_exp) / (atm_iv * np.sqrt(T_exp))
    d2_calc = d1_calc - atm_iv * np.sqrt(T_exp)
    
    call_charm_annual = - norm.pdf(d1_calc) * (r_rate / (atm_iv * np.sqrt(T_exp)) - d2_calc / (2.0 * T_exp))
    put_charm_annual = call_charm_annual + (r_rate * np.exp(-r_rate * T_exp) * norm.cdf(-d1_calc))

    df_curr.loc[valid_strikes, 'charm_c'] = call_charm_annual / 365.0
    df_curr.loc[valid_strikes, 'charm_p'] = put_charm_annual / 365.0
    df_curr['charm_c'] = df_curr['charm_c'].fillna(0.0)
    df_curr['charm_p'] = df_curr['charm_p'].fillna(0.0)

    df_curr['call_chex'] = df_curr['charm_c'] * df_curr['openInterest_c'] * 100 * spot_price / 1e6
    df_curr['put_chex'] = df_curr['charm_p'] * df_curr['openInterest_p'] * 100 * spot_price / 1e6
    df_curr['net_chex'] = (df_curr['call_chex'] + df_curr['put_chex']).fillna(0.0)

    vanna_val = - norm.pdf(d1_calc) * d2_calc / max(atm_iv, 0.001)
    df_curr.loc[valid_strikes, 'vanna_c'] = vanna_val
    df_curr.loc[valid_strikes, 'vanna_p'] = vanna_val
    df_curr['vanna_c'] = df_curr['vanna_c'].fillna(0.0)
    df_curr['vanna_p'] = df_curr['vanna_p'].fillna(0.0)

    df_curr['call_vanna'] = df_curr['vanna_c'] * df_curr['openInterest_c'] * 100 * spot_price / 1e6
    df_curr['put_vanna'] = df_curr['vanna_p'] * df_curr['openInterest_p'] * 100 * spot_price / 1e6
    df_curr['net_vanna'] = (df_curr['call_vanna'] + df_curr['put_vanna']).fillna(0.0)

    # Call Wall / Put Wall se definen por DOMINANCIA DE SIGNO del net_gex:
    # un strike es Call Wall si y solo si su net_gex es positivo, y Put Wall
    # si y solo si es negativo. Nunca puede ser ambos a la vez, y el ranking
    # dentro de cada lado es por la magnitud de ese net_gex (ver
    # compute_call_put_walls arriba).
    #
    # df_curr puede tener varias filas por el mismo strike (una por cada
    # expiración). Si no se agrupa primero, un strike con 2+ expiraciones
    # puede colarse dos veces en el top 3 (ej. cw1 y cw2 con el mismo
    # número), pisando el lugar de otro strike realmente distinto. Se agrupa
    # por strike sumando call_gex/put_gex/net_gex de todas las expiraciones
    # antes de elegir los 3 más dominantes de cada lado.
    df_gex_by_strike = df_curr.groupby('strike', as_index=False)[['call_gex', 'put_gex', 'net_gex']].sum()

    cw1, cw2, cw3, pw1, pw2, pw3 = compute_call_put_walls(df_gex_by_strike, spot_price)

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
    net_vanna_total = float(df_curr['net_vanna'].sum())

    regime_str = "positive regime" if net_gex_total >= 0 else "negative regime"
    condition_str = "Positive – dealers long gamma, hedging dampens volatility (mean-reverting)" if net_gex_total >= 0 else "Negative – dealers short gamma, hedging amplifies trending behavior"
    iv_str = f"{atm_iv * 100:.2f}%"
    iv_rank_str = f"{int(min(max((atm_iv / 0.35) * 100, 15), 85))}th percentile"
else:
    cw1, cw2, cw3 = spot_price + 5, spot_price + 10, spot_price + 15
    pw1, pw2, pw3 = spot_price - 5, spot_price - 10, spot_price - 15
    zero_gamma = spot_price
    atm_iv = 0.20
    net_gex_total = 0.0
    call_gex_sum, put_gex_sum, total_gex = 0.0, 0.0, 0.0
    call_oi_sum, put_oi_sum, total_oi_sum = 0, 0, 0
    net_dex_total, net_tex_total, net_vex_total, net_chex_total, net_vanna_total = 0.0, 0.0, 0.0, 0.0, 0.0
    regime_str = "neutral regime"
    condition_str = "Neutral"
    iv_str = "20.00%"
    iv_rank_str = "N/A"

# --- EVALUACIÓN Y CLASIFICACIÓN DEL VIX ---
if vix_val <= 0:
    vix_val = (atm_iv * 100) if (atm_iv > 0) else 16.50

if vix_val < 15.0:
    vix_status = "Baja Volatilidad"
    vix_desc = "Mercado calmado / TPs cortos"
    vix_color = "#60A5FA"
elif 15.0 <= vix_val < 25.0:
    vix_status = "Volatilidad Media"
    vix_desc = "Rango saludable / Aguantar Runners"
    vix_color = "#10B981"
elif 25.0 <= vix_val <= 30.0:
    vix_status = "Volatilidad Alta"
    vix_desc = "Rango saludable / Aguantar Runners"
    vix_color = "#F59E0B"
else:
    vix_status = "Muy Alta Volatilidad"
    vix_desc = "Miedo grande / Movimientos muy expansivos"
    vix_color = "#EF4444"

# --- PUBLICACIÓN EN VIVO PARA EL INDICADOR DE QUANTOWER (GexProfileCloud.cs) ---
# El indicador lee, cada 10s, un nodo de Firebase Realtime Database con el
# esquema: { qqq_spot, conversion_ratio, cw1..cw3, pw1..pw3, levels: [...] }.
# Va SIN thread (a diferencia del push del historial): el payload es chico,
# el timeout es corto, y necesitamos el resultado real (status code o
# excepcion) para poder mostrarlo en la barra lateral.
def push_live_levels_to_firebase_sync(db_url, payload):
    try:
        url = f"{db_url}/live_levels.json"
        resp = requests.put(url, json=payload, headers={"Content-Type": "application/json"}, timeout=6)
        if resp.status_code == 200:
            return {"ok": True, "code": resp.status_code, "detail": "OK"}
        return {"ok": False, "code": resp.status_code, "detail": resp.text[:200]}
    except Exception as e:
        return {"ok": False, "code": None, "detail": str(e)[:200]}

def export_live_levels_to_quantower():
    if not FIREBASE_DB_URL:
        st.session_state["quantower_push_status"] = {
            "ok": False, "code": None,
            "detail": "Falta FIREBASE_DB_URL en secrets.",
            "timestamp": datetime.now().strftime("%H:%M:%S")
        }
        return
    if spot_price <= 0 or df_curr is None or df_curr.empty or 'net_gex' not in df_curr.columns:
        return

    last_push = st.session_state.get("last_live_levels_push", 0.0)
    now_ts = time.time()
    if now_ts - last_push < 8:
        return
    st.session_state["last_live_levels_push"] = now_ts

    df_levels_source = df_curr
    live_cw1, live_cw2, live_cw3 = cw1, cw2, cw3
    live_pw1, live_pw2, live_pw3 = pw1, pw2, pw3

    # El feed de Quantower NO debe depender de lo que cualquier usuario tenga
    # seleccionado en su navegador: st.session_state es POR SESIÓN, así que
    # con varias personas viendo la web a la vez (cada una con su propio DTE
    # elegido), depender de esa selección haría que el feed compartido de
    # Firebase parpadeara entre la selección de quien hizo el último push.
    # En vez de eso, el feed siempre usa una regla fija y determinística:
    # la expiración más cercana (0DTE), igual sin importar cuántas sesiones
    # haya abiertas ni qué esté mirando cada quien en pantalla.
    df_sel = get_nearest_dte_subset(df_curr)
    if not df_sel.empty and df_sel is not df_curr:
        df_levels_source = df_sel
        df_walls_live = df_sel.groupby('strike', as_index=False)['net_gex'].sum()
        live_cw1, live_cw2, live_cw3, live_pw1, live_pw2, live_pw3 = compute_call_put_walls(df_walls_live, spot_price)

    df_levels = df_levels_source.groupby('strike', as_index=False)['net_gex'].sum().sort_values('strike')
    levels_payload = [
        {"strike": float(r['strike']), "net_gex": float(r['net_gex'])}
        for _, r in df_levels.iterrows()
    ]

    live_payload = {
        "qqq_spot": float(spot_price),
        "conversion_ratio": float(conversion_ratio) if 'conversion_ratio' in dir() and conversion_ratio else 41.125,
        "cw1": float(live_cw1), "cw2": float(live_cw2), "cw3": float(live_cw3),
        "pw1": float(live_pw1), "pw2": float(live_pw2), "pw3": float(live_pw3),
        "levels": levels_payload
    }

    result = push_live_levels_to_firebase_sync(FIREBASE_DB_URL, live_payload)
    result["timestamp"] = datetime.now().strftime("%H:%M:%S")
    st.session_state["quantower_push_status"] = result
    if not result["ok"]:
        log_to_console("Quantower Live Levels Push", f"{result['code']} - {result['detail']}")

export_live_levels_to_quantower()

_qs = st.session_state.get("quantower_push_status")
if _qs:
    if _qs["ok"]:
        st.sidebar.caption(f"🟢 Feed Quantower OK · {_qs['timestamp']}")
    else:
        st.sidebar.caption(f"🔴 Feed Quantower FALLÓ ({_qs['code'] or 'sin conexión'}) · {_qs['timestamp']} · {_qs['detail']}")

@st.cache_data(ttl=30)
def compute_z_matrix_cached(fine_strikes_arr, full_spots_arr, df_records, min_stk, max_stk, iv_val, t_exp_val):
    Z_mat = np.zeros((len(fine_strikes_arr), len(full_spots_arr)))
    if len(df_records) == 0 or len(full_spots_arr) == 0:
        return Z_mat
    
    df_temp = pd.DataFrame(df_records)
    df_sub = df_temp[(df_temp['strike'] >= min_stk - 2) & (df_temp['strike'] <= max_stk + 2)]
    if df_sub.empty:
        return Z_mat

    strikes = df_sub['strike'].values
    net_ois = (df_sub['openInterest_c'] - df_sub['openInterest_p']).values
    sigma_k = 0.12
    vol_sqrt_T = max(iv_val * np.sqrt(t_exp_val), 1e-4)

    for t_idx, S_t in enumerate(full_spots_arr):
        if S_t <= 0 or np.isnan(S_t): continue
        d1_t = (np.log(S_t / strikes) + (0.045 + 0.5 * iv_val**2) * t_exp_val) / vol_sqrt_T
        gamma_t = norm.pdf(d1_t) / (S_t * vol_sqrt_T)
        net_gex_t = net_ois * gamma_t * (S_t ** 2) * 0.01

        for k_idx, K in enumerate(strikes):
            if net_gex_t[k_idx] == 0: continue
            gauss_weight = np.exp(-0.5 * ((fine_strikes_arr - K) / sigma_k) ** 2)
            Z_mat[:, t_idx] += gauss_weight * net_gex_t[k_idx]

    if Z_mat.size > 0 and Z_mat.shape[1] > 1:
        # sigma[0] (eje de strikes, en unidades de índice de fine_strikes que
        # están espaciados cada $0.5) se sube apenas de 0.0 a 0.45 (~$0.22 de
        # difuminado real). Es lo justo para que el borde del nivel se vea
        # suave/iluminado en vez de un bloque con bordes duros, sin que el
        # resplandor de un strike entero se mezcle con el del strike de al
        # lado (que está a $1, o sea 2 pasos de índice de distancia).
        # sigma[1] (eje de tiempo) se mantiene igual.
        Z_mat = gaussian_filter(Z_mat, sigma=(0.45, 0.6))
    return Z_mat

if 'Z_matrix_real' not in locals() or Z_matrix_real.shape[0] == 0:
    # Mismo criterio que el feed en vivo a Quantower y el fix #5 de snapshots:
    # el heatmap de LIVE GAMMA debe reflejar SOLO la expiración más cercana
    # (0DTE), no todas las expiraciones combinadas. Antes se usaba df_curr sin
    # filtrar, sumando el open interest de TODAS las expiraciones por strike
    # (cada una con su propia campana gaussiana), lo que inflaba el heatmap
    # muy por encima del net_gex real de 0DTE que muestra GEX INFO.
    df_live_gamma_source = get_nearest_dte_subset(df_curr)
    records_dict = df_live_gamma_source[['strike', 'openInterest_c', 'openInterest_p']].to_dict('records') if not df_live_gamma_source.empty else []
    Z_matrix_real = compute_z_matrix_cached(fine_strikes, np.array(full_spots), records_dict, min_strike, max_strike, atm_iv, T_exp)

# --- Net Drift: histórico REAL de Call/Put GEX (no un proxy de precio) ---
# Antes esto se inventaba a partir del movimiento del precio y el volumen
# (price_changes_drift * vols_drift), lo cual sesgaba a Calls a solo crecer
# en subidas y a Puts a solo crecer casi siempre, sin importar la cantidad
# real de contratos/gamma de cada lado. El resultado: las dos líneas SIEMPRE
# divergían en direcciones opuestas en vez de entrelazarse.
#
# En vez de eso, reconstruimos la serie real del día a partir de los
# snapshots que la app ya guarda cada ~60s (export_snapshot_throttled),
# que incluyen call_gex/put_gex reales por strike. Sumando esos valores por
# timestamp obtenemos cómo evolucionó el GEX real de calls y de puts a lo
# largo del día: sube y baja de forma independiente según cambian OI/gamma/
# spot, exactamente lo que se busca (cuanto más negativo el lado put, más
# debe "bajar" esa línea, y viceversa para calls).
# Se usa now_tz_store (NY fija) y no now_tz (por sesión) porque las llaves
# de fecha/hora en Firebase/Supabase ahora siempre se guardan en NY,
# independientemente de la zona horaria que cada usuario tenga elegida.
today_key_drift = now_tz_store.strftime('%Y-%m-%d')
today_snaps_for_drift = []
if jsonbin_history_data and today_key_drift in jsonbin_history_data:
    today_snaps_for_drift = jsonbin_history_data.get(today_key_drift, [])

# Si Firebase todavía no tiene (o tiene muy pocos) snapshots de HOY -por
# ejemplo, recién se abrió la web, o hubo un hipo momentáneo al leerlo-,
# probamos Supabase como respaldo. Antes esto solo se intentaba dentro de
# 'elif is_cloud_backup and latest_supabase_snap', pero is_cloud_backup
# significa "el feed en vivo de Schwab está caído" (otra cosa totalmente
# distinta) -no "Firebase no tiene datos de hoy"-, así que ese respaldo casi
# nunca se activaba cuando realmente hacía falta, y NET DRIFT terminaba
# cayendo al proxy viejo (precio x volumen) en vez de mostrar datos reales.
if len(today_snaps_for_drift) < 2:
    _supa_fallback = fetch_supabase_gex_history(ticker_symbol, limit=390)
    if _supa_fallback and len(_supa_fallback) >= 2:
        today_snaps_for_drift = _supa_fallback

call_gex_by_time, put_gex_by_time, net_gex_by_time = {}, {}, {}
for _snap in today_snaps_for_drift:
    _t_key = _snap.get("time")
    if not _t_key:
        continue
    _strikes_list = _snap.get("strikes", [])
    if _strikes_list:
        _c_sum = sum(float(_s.get("call_gex", 0.0)) for _s in _strikes_list)
        _p_sum = sum(float(_s.get("put_gex", 0.0)) for _s in _strikes_list)
    else:
        _c_sum, _p_sum = 0.0, 0.0
    call_gex_by_time[_t_key] = _c_sum
    put_gex_by_time[_t_key] = _p_sum
    net_gex_by_time[_t_key] = float(_snap.get("net_gex", _c_sum + _p_sum))

has_real_drift_data = len(call_gex_by_time) >= 2

closes_drift = np.array(full_spots)
vols_drift = h_1m_reindexed['Volume'].fillna(1000).values if not h_1m_reindexed.empty else np.full(len(full_timestamps), 1000)

if has_real_drift_data:
    call_drift_raw = np.zeros(len(full_timestamps))
    put_drift_raw = np.zeros(len(full_timestamps))
    net_drift_raw = np.zeros(len(full_timestamps))
    _last_c, _last_p, _last_n = 0.0, 0.0, 0.0
    for _i, _t in enumerate(full_timestamps_ny):
        if _t in call_gex_by_time:
            _last_c, _last_p, _last_n = call_gex_by_time[_t], put_gex_by_time[_t], net_gex_by_time[_t]
        call_drift_raw[_i] = _last_c
        put_drift_raw[_i] = _last_p
        net_drift_raw[_i] = _last_n
    last_call_drift, last_put_drift, last_net_drift = float(call_drift_raw[-1]), float(put_drift_raw[-1]), float(net_drift_raw[-1])
else:
    # Fallback: aún no hay suficiente histórico real guardado hoy (ej. app
    # recién abierta). Se usa el proxy anterior solo como relleno temporal.
    if len(closes_drift) > 1:
        price_changes_drift = np.diff(closes_drift, prepend=closes_drift[0])
        call_drift_raw = np.cumsum(np.where(price_changes_drift >= 0, price_changes_drift * vols_drift * 0.12, price_changes_drift * vols_drift * 0.08))
        put_drift_raw = np.cumsum(np.where(price_changes_drift < 0, -price_changes_drift * vols_drift * 0.18, price_changes_drift * vols_drift * 0.05))
        net_drift_raw = call_drift_raw - put_drift_raw
        last_call_drift, last_put_drift, last_net_drift = float(call_drift_raw[-1]), float(put_drift_raw[-1]), float(net_drift_raw[-1])
    else:
        call_drift_raw, put_drift_raw, net_drift_raw = np.zeros(len(full_timestamps)), np.zeros(len(full_timestamps)), np.zeros(len(full_timestamps))
        last_call_drift, last_put_drift, last_net_drift = 0.0, 0.0, 0.0

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

# --- MOTOR DE ANÁLISIS DEDICADO E IA CON ESCENARIOS Y VIX ---
def generar_analisis_local(ticker, spot, net_gex, regime, condition,
                          call_gex, put_gex, total_gex,
                          cw1_v, cw2_v, cw3_v, pw1_v, pw2_v, pw3_v, zg_v,
                          iv_txt, iv_rank, dex_v, tex_v, vex_v,
                          chex_v, vanna_v, drift_v, vix_v=16.50):
    is_pos = net_gex >= 0
    dist_zg = ((spot - zg_v) / spot) * 100 if spot > 0 else 0.0
    dist_cw1 = ((cw1_v - spot) / spot) * 100 if spot > 0 else 0.0
    dist_pw1 = ((spot - pw1_v) / spot) * 100 if spot > 0 else 0.0
    
    regime_tipo = "RÉGIMEN POSITIVO DE GAMMA (Long Gamma Dealers)" if is_pos else "RÉGIMEN NEGATIVO DE GAMMA (Short Gamma Dealers)"
    
    behavior = (
        "Los creadores de mercado (dealers) actúan como amortiguadores comprando en caídas y vendiendo en subidas. "
        "Esto favorece un comportamiento de reversión a la media (rango comprimido) y absorbe los impulsos de volatilidad."
        if is_pos else
        "Los creadores de mercado (dealers) aceleran el movimiento vendiendo en caídas y comprando en subidas. "
        "Esto promueve expansiones direccionales, rupturas agresivas de soportes/resistencias y alta volatilidad."
    )
    
    if vix_v < 15.0:
        vix_guidance = f"**VIX en {vix_v:.2f} (Volatilidad Baja / Calmada)**: Mercado calmado y movimientos poco expansivos. No se recomiendan Take Profits (TP) muy largos."
    elif 15.0 <= vix_v < 25.0:
        vix_guidance = f"**VIX en {vix_v:.2f} (Volatilidad Media)**: Volatilidad muy sana para el mercado. Es un excelente entorno para aguantar **runners** (TPs más largos)."
    elif 25.0 <= vix_v <= 30.0:
        vix_guidance = f"**VIX en {vix_v:.2f} (Volatilidad Alta)**: Rango saludable para el mercado. Se pueden aguantar **runners** (TPs más largos)."
    else:
        vix_guidance = f"**VIX en {vix_v:.2f} (Muy Alta Volatilidad / Miedo)**: Miedo del mercado muy grande y movimientos altamente expansivos. Reducir tamaño de posición."

    if drift_v > 1e6:
        drift_bias = "fuertemente alcista (acumulación dominante de primas Call)"
    elif drift_v < -1e6:
        drift_bias = "fuertemente bajista (presión compradora en primas Put)"
    else:
        drift_bias = "neutral / equilibrado entre flujos de compra y venta"

    entry_a_call = cw1_v + 0.50
    target_a_call = cw2_v
    entry_a_put = pw1_v - 0.50
    target_a_put = pw2_v

    entry_b_short = cw1_v - 0.25
    target_b_short = zg_v
    entry_b_long = pw1_v + 0.25
    target_b_long = zg_v

    sweep_high = cw1_v + 1.50
    rev_target_high = spot
    sweep_low = pw1_v - 1.50
    rev_target_low = spot

    return f"""### 📌 DIAGNÓSTICO ESTRATÉGICO Y ESCENARIOS DE MERCADO ({ticker})

#### 1. Estado Actual y Régimen del Mercado
* **Precio Spot Actual**: ${spot:.2f} USD | **Zero Gamma Level (Flip)**: ${zg_v:.2f} USD ({dist_zg:+.2f}% de distancia).
* **Régimen Dominante**: {regime_tipo}.
* **Índice VIX**: {vix_guidance}
* **Dinámica de Volatilidad**: IV ATM en {iv_txt} (Percentil: {iv_rank}). {behavior}

#### 2. Puntos Clave de Inflexión y Niveles Operativos
* **Resistencias Principales (Call Walls)**:
  - **CW1 (Techo Principal)**: ${cw1_v:.0f} USD ({dist_cw1:+.2f}%)
  - **CW2 / CW3 (Resistencias de Extensión)**: ${cw2_v:.0f} USD / ${cw3_v:.0f} USD
* **Soportes Principales (Put Walls)**:
  - **PW1 (Suelo Principal)**: ${pw1_v:.0f} USD (-{dist_pw1:.2f}%)
  - **PW2 / PW3 (Soportes de Extensión)**: ${pw2_v:.0f} USD / ${pw3_v:.0f} USD
* **Flip Level / Pivote Técnico**: **${zg_v:.2f} USD**. Mantenerse por encima sostiene el control alcista en rango; perforar a la baja liberará volatilidad a favor de los vendedores.

#### 3. Análisis de Flujo y Griegas
* **Delta Exposure (DEX)**: {dex_v:.2f}M USD. Muestra un sesgo direccional {"positivo" if dex_v >= 0 else "negativo"}.
* **Charm Exposure (CHEX)**: {chex_v:.2f}M USD/día. Decaimiento de delta por tiempo atrae el precio hacia strikes con mayor acumulación de OI.
* **Vanna Exposure (VANNA)**: {vanna_v:.2f}M USD. Mide el impacto en deltas si la IV comprime o se expande en la sesión.
* **Net Premium Drift**: {fmt_val(drift_v).replace('$', '')} USD. El flujo acumulado muestra un sesgo {drift_bias}.

#### 4. Escenarios Operativos Cuantitativos (A, B y C)

* **Escenario A (Continuación / Retesteo Aceptado)**:
  - **Caso Alcista**: Ruptura sostenida y consolidación por encima de Call Wall 1 (${cw1_v:.0f} USD). **Precio de Entrada de Confirmación**: ${entry_a_call:.2f} USD | **Precio Objetivo**: ${target_a_call:.0f} USD (CW2).
  - **Caso Bajista**: Perforación y aceptación por debajo de Put Wall 1 (${pw1_v:.0f} USD). **Precio de Entrada de Confirmación**: ${entry_a_put:.2f} USD | **Precio Objetivo**: ${target_a_put:.0f} USD (PW2).

* **Escenario B (Rechazo en Nivel Clave)**:
  - **Rechazo en Resistencia**: Ataque a CW1 (${cw1_v:.0f} USD) con absorción de oferta y reitero de rechazo en velas intradía. **Entrada en Venta**: ${entry_b_short:.2f} USD | **Precio Objetivo**: ${target_b_short:.2f} USD (Zero Gamma Level).
  - **Rebote en Soporte**: Testeo de PW1 (${pw1_v:.0f} USD) con fuerte defensa de primas y rebote inmediato. **Entrada en Compra**: ${entry_b_long:.2f} USD | **Precio Objetivo**: ${target_b_long:.2f} USD (Zero Gamma Level).

* **Escenario C (Trampa / Falsa Ruptura - Liquidity Sweep)**:
  - **Barrido Superior**: Dilatación de alta volatilidad sobre CW1 alcanzando **${sweep_high:.2f} USD** para barrer liquidez de compra (stop loss) y reingresar rápidamente bajo ${cw1_v:.0f} USD. **Precio Numérico de Reversión Esperado**: ${rev_target_high:.2f} USD / ${zg_v:.2f} USD.
  - **Barrido Inferior**: Falsa ruptura de PW1 cayendo hasta **${sweep_low:.2f} USD** para activar stops de compradores y revertir velozmente por encima de ${pw1_v:.0f} USD. **Precio Numérico de Reversión Esperado**: ${rev_target_low:.2f} USD.
"""

def get_intraday_context(hist_df, current_price):
    """
    Resume el movimiento de precio de HOY (apertura, máximo, mínimo, y el
    movimiento de los últimos ~30 minutos) para que la IA pueda razonar
    escenarios coherentes con lo que el precio YA hizo, en vez de proponer
    entradas/objetivos desconectados del movimiento reciente.
    """
    if hist_df is None or hist_df.empty or 'Close' not in hist_df.columns:
        return "Sin datos de velas intradía disponibles todavía."
    try:
        day_high = float(hist_df['High'].max())
        day_low = float(hist_df['Low'].min())
        day_open = float(hist_df['Open'].iloc[0])
        rango_total = max(day_high - day_low, 0.01)

        recent_window = hist_df.tail(30)
        move_pts = 0.0
        move_min = 0
        if not recent_window.empty:
            move_start = float(recent_window['Close'].iloc[0])
            move_pts = current_price - move_start
            move_min = len(recent_window)

        if current_price > (day_low + rango_total * 0.66):
            posicion_rango = "en la parte ALTA"
        elif current_price < (day_low + rango_total * 0.33):
            posicion_rango = "en la parte BAJA"
        else:
            posicion_rango = "en la zona MEDIA"

        direccion = "al alza" if move_pts > 0.05 else "a la baja" if move_pts < -0.05 else "prácticamente lateral"

        return (
            f"Apertura de hoy: {day_open:.2f} | Máximo del día: {day_high:.2f} | "
            f"Mínimo del día: {day_low:.2f} | Rango recorrido hoy: {rango_total:.2f} pts. "
            f"El precio actual está {posicion_rango} de ese rango. "
            f"En los últimos {move_min} min se movió {move_pts:+.2f} pts ({direccion})."
        )
    except Exception:
        return "Sin datos de velas intradía disponibles todavía."

def consultar_ia(tipo_analisis="Análisis General", mensaje_usuario=None,
                  metrics_override=None, dte_context_label=None, spot_override=None):
    """
    metrics_override: dict opcional (salida de compute_metrics_for_dte) para que
    el analisis use los niveles/griegas de un DTE especifico en vez de los
    globales (todas las expiraciones combinadas).
    dte_context_label: texto opcional que se le indica a la IA para dejar claro
    con que expiracion(es) se genero el analisis.
    spot_override: precio manual opcional (ej. cuando el precio automatico esta
    desactualizado por un feriado bursatil) que reemplaza a spot_price para
    todo el analisis, incluyendo el texto que ve la IA.
    """
    m = metrics_override or {}
    spot_ia = float(spot_override) if spot_override and spot_override > 0 else spot_price
    conversion_ratio_ia = (nq_price / spot_ia) if nq_price > 0 and spot_ia > 0 else conversion_ratio

    net_dex_val = m.get("net_dex_val", float(df_curr['net_dex'].sum()) if not df_curr.empty and 'net_dex' in df_curr.columns else 0.0)
    net_tex_val = m.get("net_tex_val", float(df_curr['net_tex'].sum()) if not df_curr.empty and 'net_tex' in df_curr.columns else 0.0)
    net_vex_val = m.get("net_vex_val", float(df_curr['net_vex'].sum()) if not df_curr.empty and 'net_vex' in df_curr.columns else 0.0)
    net_chex_val = m.get("net_chex_val", float(df_curr['net_chex'].sum()) if not df_curr.empty and 'net_chex' in df_curr.columns else 0.0)
    net_vanna_val = m.get("net_vanna_val", float(df_curr['net_vanna'].sum()) if not df_curr.empty and 'net_vanna' in df_curr.columns else 0.0)

    cw1_ia = m.get("cw1", cw1)
    cw2_ia = m.get("cw2", cw2)
    cw3_ia = m.get("cw3", cw3)
    pw1_ia = m.get("pw1", pw1)
    pw2_ia = m.get("pw2", pw2)
    pw3_ia = m.get("pw3", pw3)
    zero_gamma_ia = m.get("zero_gamma", zero_gamma)
    net_gex_total_ia = m.get("net_gex_total", net_gex_total)
    call_gex_sum_ia = m.get("call_gex_sum", call_gex_sum)
    put_gex_sum_ia = m.get("put_gex_sum", put_gex_sum)
    regime_str_ia = m.get("regime_str", regime_str)
    condition_str_ia = m.get("condition_str", condition_str)
    iv_str_ia = m.get("iv_str", iv_str)
    iv_rank_str_ia = m.get("iv_rank_str", iv_rank_str)

    dte_note = (
        f"NOTA IMPORTANTE: Este analisis se genera EXCLUSIVAMENTE con los datos de la(s) expiracion(es): {dte_context_label}."
        if dte_context_label else ""
    )

    manual_price_note = (
        f"NOTA: El precio de {ticker_symbol} fue ingresado manualmente por el usuario (${spot_ia:.2f}) "
        f"porque el precio automatico estaba desactualizado (ej. feriado bursatil, divergencia ETH/RTH). "
        f"Usa este precio como el spot real vigente para todo el analisis."
        if spot_override and spot_override > 0 else ""
    )

    intraday_context = get_intraday_context(hist_raw, spot_ia)

    system_prompt = f"""
    Eres un analista de order flow y estratega de day trading/scalping especializado en opciones y futuros de Nasdaq (NQ/MNQ), operando dentro del GEX Quant Terminal. {dte_note}{manual_price_note}

    PERFIL DEL TRADER AL QUE ASESORAS (condiciona TODA tu respuesta):
    - Opera intradía puro: sus trades duran entre 5 y 30 minutos, NUNCA "swing".
    - Usa footprint chart, cumulative delta y volume profile como herramientas de ejecución. Tú no tienes esos datos en vivo, pero debes razonar en esos términos: absorción, agresión compradora/vendedora, mecha de rechazo, POC, nodos de alto/bajo volumen (HVN/LVN).
    - Opera MNQ/NQ (Nasdaq), pero tus niveles de referencia (Call/Put Walls, Zero Gamma) están en {ticker_symbol} — factor de conversión: {conversion_ratio_ia:.4f}.
    - NUNCA propongas objetivos (TP) de tipo swing. Los objetivos deben ser alcanzables en minutos, no en días.

    REGLAS DURAS DE COHERENCIA DE PRECIOS (verifícalas numéricamente antes de responder; si las violas, la respuesta es inútil para este trader):
    1. El precio actual de {ticker_symbol} es {spot_ia:.2f}. Toda entrada que propongas debe estar razonablemente cerca de este precio (un pullback/retest lógico), nunca en un nivel ya lejano que implique que el precio ya recorrió gran parte del movimiento.
    2. En un LONG: el Take Profit SIEMPRE debe ser un precio MAYOR que el de entrada. En un SHORT: el Take Profit SIEMPRE debe ser un precio MENOR que el de entrada.
    3. NO propongas cazar una reversión (short después de una caída fuerte, o long después de una subida fuerte) sin una razón estructural explícita (rechazo confirmado en un nivel de gamma, agotamiento de la mecha, absorción visible). Nunca sugieras "shortear" muy por debajo de donde ya cayó el precio, ni "comprar" muy por encima de donde ya subió, sin ese sustento.
    4. Usa el contexto de movimiento reciente de abajo para calibrar tus escenarios: si ya hubo un movimiento grande y reciente, prioriza continuación con retest o agotamiento en un nivel específico — no ignores que el movimiento ya ocurrió.

    CONTEXTO DE PRECIO INTRADÍA (movimiento ya ocurrido hoy — ÚSALO, no lo ignores):
    {intraday_context}

    DATOS DEL MERCADO EN TIEMPO REAL ({ticker_symbol}):
    - Ticker: {ticker_symbol} | Spot Price: {spot_ia:.2f} USD | Ratio NQ: {conversion_ratio_ia:.4f}
    - Índice VIX: {vix_val:.2f} ({vix_status} - {vix_desc})
    - Volatilidad Implícita (IV ATM): {iv_str_ia} | Percentil Rank: {iv_rank_str_ia}
    - Régimen de Gamma: {regime_str_ia} ({condition_str_ia})
    - Net GEX Total: {fmt_val(net_gex_total_ia).replace('$', '')} USD (Call GEX: {fmt_val(call_gex_sum_ia).replace('$', '')} USD, Put GEX: {fmt_val(put_gex_sum_ia).replace('$', '')} USD)
    - Call Walls (Resistencias): CW1={cw1_ia:.0f} USD, CW2={cw2_ia:.0f} USD, CW3={cw3_ia:.0f} USD
    - Put Walls (Soportes): PW1={pw1_ia:.0f} USD, PW2={pw2_ia:.0f} USD, PW3={pw3_ia:.0f} USD
    - Zero Gamma Level (Flip): {zero_gamma_ia:.2f} USD
    - Delta Exposure (DEX): {net_dex_val:.2f}M USD | Theta Exposure (TEX): {net_tex_val:,.0f} USD/día
    - Vega Exposure (VEX): {net_vex_val:,.0f} USD/1% IV | Charm Exposure (CHEX): {net_chex_val:.2f}M USD/día | Vanna (VANNA): {net_vanna_val:.2f}M USD
    - Net Premium Drift: {fmt_val(last_net_drift).replace('$', '')} USD

    REGLAS DE INTERPRETACIÓN DEL VIX (para scalping, no para swing):
    1. VIX < 15: Volatilidad calmada. Rango intradía comprimido — objetivos de scalp más cortos de lo normal.
    2. VIX 15-30 (15-24 media, 25-30 alta): Volatilidad sana, rango intradía amplio — es donde mejor rinde el scalping.
    3. VIX > 30: Volatilidad muy alta, mechas violentas — exige confirmación de absorción antes de entrar, evita perseguir el primer impulso.

    CÓMO RAZONAR LOS ESCENARIOS (reemplaza cualquier plantilla genérica de niveles sueltos):
    Cada escenario debe explicar el MECANISMO, no solo tirar un número. Ejemplo: al romper y sostenerse por encima de un Call Wall dominante, los market makers que estaban cortos gamma dejan de necesitar comprar futuros para cubrirse en ese nivel — se retira un freno estructural y el camino de menor resistencia gamma queda abierto hacia el siguiente nivel (normalmente el próximo Call Wall o el Zero Gamma). Razona así, con causa y efecto.
    Apóyate en los conceptos de order flow que tu trader sí puede confirmar en su footprint/cumulative delta/volume profile: menciona qué debería ver ahí para validar cada escenario (ej. "confirmar con absorción de vendedores en el footprint antes de sumar tamaño", "buscar una mecha de rechazo con reversión de delta acumulado", "vigilar si el volumen se apila como nodo de alto volumen (POC) en la zona, o si es zona de bajo volumen y por tanto de tránsito rápido").

    REGLA DE DIRECCIONALIDAD (CRÍTICA — verifícala línea por línea antes de responder; un error aquí invierte el trade y puede costar dinero real):
    - Rechazo/rebote en un Put Wall o soporte (mecha de rechazo alcista, absorción de compra, delta que pasa a positivo o se sostiene) es ALCISTA → Dirección = LONG, entrada cerca de ese soporte, TP por ENCIMA de la entrada.
    - Rechazo/rebote en un Call Wall o resistencia (mecha de rechazo bajista, absorción de venta, delta que pasa a negativo) es BAJISTA → Dirección = SHORT, entrada cerca de esa resistencia, TP por DEBAJO de la entrada.
    - Ruptura y sostenimiento por ENCIMA de un Call Wall = continuación ALCISTA → LONG.
    - Ruptura y sostenimiento por DEBAJO de un Put Wall = continuación BAJISTA → SHORT.
    - Antes de escribir la Dirección de cada escenario, relee la condición/mecanismo que tú mismo describiste para ese escenario y verifica que la Dirección sea consistente con ella (una mecha de rechazo alcista en un Put Wall NUNCA puede terminar en "Short" — si eso pasa, corrígelo antes de responder, no lo dejes así).

    REGLAS DE RESPUESTA OBLIGATORIAS:
    1. NO respondas con mensajes vacíos o saludos genéricos.
    2. DEBES incluir obligatoriamente las siguientes secciones:
       **1. Estado Actual y Contexto Intradía** (régimen de gamma, VIX, y qué ha hecho el precio hoy — usa el contexto de arriba)
       **2. Niveles Operativos Relevantes para Scalping** (solo los 1-2 niveles MÁS relevantes dado dónde está el precio ahora, no los seis de memoria)
       **3. Qué Vigilar en Order Flow** (absorción, delta acumulado, volume profile, mechas de rechazo — en términos de qué confirmaría o invalidaría cada escenario)
       **4. Escenarios Operativos de Scalping (5-30 min, ENTRADA/TP COHERENTES CON EL PRECIO ACTUAL Y EL MOVIMIENTO RECIENTE, Y CON LA REGLA DE DIRECCIONALIDAD DE ARRIBA):**
          * **Escenario A (Ruptura y Continuación)**: si rompe y sostiene el nivel dominante más cercano al precio actual, hacia dónde iría y POR QUÉ (mecanismo de hedging), con entrada y TP numéricos coherentes.
          * **Escenario B (Rechazo en Nivel Clave)**: si el precio reacciona en el nivel dominante más cercano, con entrada y TP numéricos coherentes hacia el nivel opuesto o Zero Gamma.
          * **Escenario C (Trampa / Falsa Ruptura)**: barrido de liquidez y reversión, con precio de invalidación y objetivo numérico.
       **5. Resumen Rápido para el Trader**: SIEMPRE termina con una tabla en formato Markdown válido (con fila separadora de guiones), con exactamente estas columnas: Escenario | Dirección | Entrada | TP | Invalidación | Comentario clave de OF. Debe tener una fila por cada escenario (A, B y C) y CADA CELDA debe estar completa, usando los mismos números y la misma dirección que ya escribiste arriba para ese escenario — nunca dejes una celda vacía. Si por alguna razón no puedes completar una fila con datos reales, omite esa fila entera en vez de dejarla vacía.
    3. NUNCA uses notación LaTeX ni símbolos de dólar dobles ($$). Usa fuentes y letras normales en USD.
    """

    prompt_final = mensaje_usuario or f"Entrega un informe cuantitativo completo de opciones para {tipo_analisis} con los datos del mercado actual, incluyendo el diagnóstico del VIX y explícitamente los Escenarios A, B y C con precios numéricos exactos."

    if GROQ_API_KEY:
        try:
            res_groq = query_groq(system_prompt, prompt_final, GROQ_API_KEY)
            if res_groq and len(res_groq.strip()) > 40:
                return res_groq
        except Exception as e_groq:
            log_to_console("Groq AI Engine", str(e_groq))

    if ai_client:
        try:
            response = ai_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=f"{system_prompt}\n\nSolicitud: {prompt_final}"
            )
            if response and response.text and len(response.text.strip()) > 40:
                return clean_ai_response(response.text)
        except Exception as e_gemini:
            log_to_console("Gemini AI Engine", str(e_gemini))

    return generar_analisis_local(
        ticker_symbol, spot_ia, net_gex_total_ia, regime_str_ia, condition_str_ia,
        call_gex_sum_ia, put_gex_sum_ia, total_gex,
        cw1_ia, cw2_ia, cw3_ia, pw1_ia, pw2_ia, pw3_ia, zero_gamma_ia,
        iv_str_ia, iv_rank_str_ia, net_dex_val, net_tex_val, net_vex_val,
        net_chex_val, net_vanna_val, last_net_drift, vix_val
    )

def render_dte_selector(df_source, location_key, state_key="selected_dte_keys",
                         use_popover=False, popover_label="📂 DTE"):
    """
    AUDITADO (bug multiusuario "cambiar DTE expulsa a otro usuario"): esta
    funcion usa exclusivamente st.session_state, que en Streamlit esta
    aislado por sesion de navegador -no es la causa del bug-. La causa real
    era el cache compartido de las funciones fetch_*_schwab (ver comentario
    junto a _schwab_call_with_fallback, arriba en el archivo).

    Renderiza el selector de DTE (boton "DTE" + botones rapidos + multiselect).

    - state_key controla en que variable de session_state se guarda la seleccion.
      Si dos llamadas usan el MISMO state_key quedan sincronizadas entre si
      (asi es como GEX INFO, GREEKS y el panel superior comparten
      'selected_dte_keys' por defecto). Si usas un state_key DISTINTO
      (ej: 'selected_dte_keys_data'), ese selector queda totalmente
      INDEPENDIENTE del resto.
    - 'location_key' debe ser unico por cada lugar donde se invoque la funcion,
      para no chocar los keys internos de los widgets de Streamlit.
    - use_popover=True dibuja el selector como un boton pequeño (st.popover) en
      vez del expander de ancho completo; util para ponerlo al lado de otro boton.

    NOTA IMPORTANTE sobre la sincronizacion entre multiples instancias:
    Cuando el mismo state_key se comparte entre varias llamadas (GEX INFO,
    GREEKS, panel superior), cada una tiene su PROPIO widget de multiselect
    (con su propio widget_key). Para mantenerlos sincronizados sin pisar una
    edicion manual reciente, se usa un callback (on_change) que, apenas el
    usuario cambia CUALQUIERA de los multiselects, propaga ese nuevo valor al
    state_key compartido y a todos los demas widgets registrados bajo ese
    mismo state_key. Esto evita el bug de "la seleccion manual se revierte
    sola": antes, cada llamada re-sincronizaba su propio widget leyendo el
    state_key compartido en CADA rerun (incluso cuando ese widget no fue el
    que el usuario toco), y como esa lectura ocurria con el valor todavia
    viejo, terminaba sobreescribiendo el cambio recien hecho antes de que el
    multiselect llegara a mostrarlo.
    """
    df_filtered = df_source.copy()

    if df_source.empty or 'exp_key' not in df_source.columns:
        return df_filtered

    exp_groups = []
    for exp_k, group in df_source.groupby('exp_key'):
        d_str = group['exp_date'].iloc[0] if 'exp_date' in group.columns else exp_k.split(':')[0]
        dte_v = int(group['dte'].iloc[0]) if 'dte' in group.columns else 0
        net_gex_v = group['net_gex'].sum() if 'net_gex' in group.columns else 0.0

        try:
            dt_obj = datetime.strptime(d_str, "%Y-%m-%d")
            formatted_date = dt_obj.strftime("%d %b %Y").upper()
        except Exception:
            formatted_date = d_str

        color_icon = "🟢" if net_gex_v >= 0 else "🔴"
        formatted_val = fmt_val(net_gex_v)

        exp_groups.append({
            'exp_key': exp_k,
            'date_formatted': formatted_date,
            'dte': dte_v,
            'net_gex': net_gex_v,
            'label': f"{formatted_date} {dte_v}DTE | {color_icon} {formatted_val}"
        })

    exp_df = pd.DataFrame(exp_groups).sort_values('dte').reset_index(drop=True)
    options_list = exp_df['exp_key'].tolist()

    if state_key not in st.session_state or not st.session_state[state_key]:
        st.session_state[state_key] = [exp_df['exp_key'].iloc[0]] if not exp_df.empty else []

    widget_key = f"multiselect_dte_{location_key}"

    # Registro de todos los widget_keys que comparten este mismo state_key, para
    # poder propagar un cambio de uno hacia los demas via callback.
    registry_key = f"_dte_widget_registry__{state_key}"
    if registry_key not in st.session_state:
        st.session_state[registry_key] = set()
    st.session_state[registry_key].add(widget_key)

    def _apply_selection(new_keys):
        """Aplica new_keys al state_key compartido y a TODOS los widgets
        (incluido el propio) que comparten ese state_key, para que todos
        queden sincronizados de inmediato."""
        st.session_state[state_key] = new_keys
        for other_key in st.session_state[registry_key]:
            st.session_state[other_key] = new_keys

    def _on_multiselect_change():
        # Se dispara ANTES de que el script vuelva a correr, con el valor
        # nuevo ya cargado en st.session_state[widget_key]. Lo propagamos
        # hacia el state_key compartido y hacia los demas widgets.
        _apply_selection(st.session_state[widget_key])

    def _render_controls():
        col_b1, col_b2, col_b3, col_b4 = st.columns(4)
        if col_b1.button("0 DTE Only", key=f"btn_0dte_{location_key}"):
            sel = [exp_df[exp_df['dte'] == 0]['exp_key'].iloc[0]] if not exp_df[exp_df['dte'] == 0].empty else [exp_df['exp_key'].iloc[0]]
            _apply_selection(sel)
            st.rerun()
        if col_b2.button("<= 7 DTE", key=f"btn_7dte_{location_key}"):
            _apply_selection(exp_df[exp_df['dte'] <= 7]['exp_key'].tolist())
            st.rerun()
        if col_b3.button("<= 30 DTE", key=f"btn_30dte_{location_key}"):
            _apply_selection(exp_df[exp_df['dte'] <= 30]['exp_key'].tolist())
            st.rerun()
        if col_b4.button("TODAS LAS DTE", key=f"btn_all_dte_{location_key}"):
            _apply_selection(exp_df['exp_key'].tolist())
            st.rerun()

        labels_dict = dict(zip(exp_df['exp_key'], exp_df['label']))

        # Solo se inicializa la PRIMERA vez que existe este widget en la sesion;
        # despues de eso, el propio widget (y el callback) son la fuente de
        # verdad, para no pisar ediciones manuales en reruns posteriores.
        if widget_key not in st.session_state:
            st.session_state[widget_key] = [k for k in st.session_state[state_key] if k in options_list]

        st.multiselect(
            "Selecciona las expiraciones activas para el gráfico:",
            options=options_list,
            format_func=lambda x: labels_dict.get(x, x),
            key=widget_key,
            on_change=_on_multiselect_change
        )

    if use_popover:
        with st.popover(popover_label, use_container_width=True):
            _render_controls()
    else:
        col_dte_box, _ = st.columns([1, 3])
        with col_dte_box:
            with st.expander(popover_label, expanded=False):
                _render_controls()

    if st.session_state[state_key]:
        agg_map = {
            'net_gex': 'sum', 'call_gex': 'sum', 'put_gex': 'sum',
            'openInterest_c': 'sum', 'openInterest_p': 'sum'
        }
        for extra_col in ['net_dex', 'call_dex', 'put_dex', 'net_tex', 'net_vex', 'net_chex', 'net_vanna']:
            if extra_col in df_source.columns:
                agg_map[extra_col] = 'sum'

        df_filtered = df_source[df_source['exp_key'].isin(st.session_state[state_key])]
        df_filtered = df_filtered.groupby('strike', as_index=False).agg(agg_map)

    return df_filtered

# --- SESION DE TRADING (UTC-5 / LIMA) Y METRICAS POR DTE ---
def get_session_state():
    """
    Calcula el estado de la sesion de trading en hora de Lima (UTC-5), de forma
    INDEPENDIENTE al selector de Timezone del sidebar (tz_choice). La sesion de
    trading es de 08:30 a 15:00 hora Lima, de Lunes a Viernes.

    Devuelve:
      - now_lima: Timestamp actual en America/Lima
      - intraday_active: True si la sesion esta activa en este momento
      - today_date_str: fecha (YYYY-MM-DD) de hoy en Lima
      - premarket_date_str: fecha (YYYY-MM-DD) de la sesion "proxima" aun no
        iniciada (hoy mismo si aun no son las 08:30, si no, el siguiente dia habil)
    """
    now_lima = pd.Timestamp.now(tz="America/Lima")
    is_weekday = now_lima.weekday() < 5  # 0=Lunes ... 4=Viernes

    session_start = now_lima.replace(hour=8, minute=30, second=0, microsecond=0)
    session_end = now_lima.replace(hour=15, minute=0, second=0, microsecond=0)

    intraday_active = bool(is_weekday and session_start <= now_lima <= session_end)

    if is_weekday and now_lima <= session_start:
        premarket_date = now_lima.normalize()
    else:
        premarket_date = now_lima.normalize() + pd.Timedelta(days=1)
        while premarket_date.weekday() >= 5:
            premarket_date += pd.Timedelta(days=1)

    return {
        "now_lima": now_lima,
        "intraday_active": intraday_active,
        "today_date_str": now_lima.strftime('%Y-%m-%d'),
        "premarket_date_str": premarket_date.strftime('%Y-%m-%d'),
    }


def find_exp_keys_by_date(df_source, target_date_str):
    """
    Busca los exp_key de df_source cuya fecha de expiracion (exp_date) coincide
    exactamente con target_date_str (YYYY-MM-DD). Si no hay ninguna expiracion
    exactamente en esa fecha, usa la expiracion disponible mas cercana hacia
    adelante (la siguiente fecha de trading listada).
    """
    if df_source is None or df_source.empty or 'exp_date' not in df_source.columns:
        return []

    exact = df_source[df_source['exp_date'] == target_date_str]['exp_key'].unique().tolist()
    if exact:
        return exact

    future = df_source[df_source['exp_date'] > target_date_str]
    if not future.empty:
        nearest_date = future['exp_date'].min()
        return df_source[df_source['exp_date'] == nearest_date]['exp_key'].unique().tolist()

    return []


def compute_metrics_for_dte(df_source, exp_keys, spot_ref):
    """
    Recalcula Call/Put Walls, Zero Gamma, Net GEX y Griegas (DEX/TEX/VEX/CHEX/VANNA)
    usando UNICAMENTE los datos de las expiraciones (exp_key) indicadas en exp_keys.
    No vuelve a calcular Gamma/Delta/etc desde cero (ya vienen calculados en
    df_source con el T_exp del vencimiento mas cercano); solo filtra y vuelve a
    agregar por strike para ese subconjunto de DTE.
    """
    fallback = {
        "cw1": spot_ref + 5, "cw2": spot_ref + 10, "cw3": spot_ref + 15,
        "pw1": spot_ref - 5, "pw2": spot_ref - 10, "pw3": spot_ref - 15,
        "zero_gamma": spot_ref, "net_gex_total": 0.0, "call_gex_sum": 0.0, "put_gex_sum": 0.0,
        "net_dex_val": 0.0, "net_tex_val": 0.0, "net_vex_val": 0.0, "net_chex_val": 0.0, "net_vanna_val": 0.0,
        "iv_str": "20.00%", "iv_rank_str": "N/A", "regime_str": "neutral regime",
        "condition_str": "Neutral",
    }

    if df_source is None or df_source.empty or not exp_keys or 'exp_key' not in df_source.columns:
        return fallback

    df_sel = df_source[df_source['exp_key'].isin(exp_keys)].copy()
    if df_sel.empty:
        return fallback

    agg_map = {}
    for col in ['net_gex', 'call_gex', 'put_gex', 'openInterest_c', 'openInterest_p',
                'net_dex', 'call_dex', 'put_dex', 'net_tex', 'net_vex', 'net_chex', 'net_vanna']:
        if col in df_sel.columns:
            agg_map[col] = 'sum'
    for col in ['iv_c', 'iv_p']:
        if col in df_sel.columns:
            agg_map[col] = 'mean'

    df_agg = df_sel.groupby('strike', as_index=False).agg(agg_map).sort_values('strike').reset_index(drop=True)

    # Igual que en el bloque principal: dominancia de SIGNO del net_gex, un
    # strike solo puede ser Call Wall o Put Wall, nunca ambos (ver
    # compute_call_put_walls).
    cw1_v, cw2_v, cw3_v, pw1_v, pw2_v, pw3_v = compute_call_put_walls(df_agg, spot_ref)

    zero_gamma_v = spot_ref
    if 'net_gex' in df_agg.columns and not df_agg.empty:
        df_agg['cum_gex'] = df_agg['net_gex'].cumsum()
        zg_idx = df_agg['cum_gex'].abs().idxmin()
        if zg_idx in df_agg.index:
            zero_gamma_v = df_agg.loc[zg_idx]['strike']

    net_gex_total_v = float(df_agg['net_gex'].sum()) if 'net_gex' in df_agg.columns else 0.0
    call_gex_sum_v = float(df_agg['call_gex'].sum()) if 'call_gex' in df_agg.columns else 0.0
    put_gex_sum_v = float(df_agg['put_gex'].sum()) if 'put_gex' in df_agg.columns else 0.0

    net_dex_v = float(df_agg['net_dex'].sum()) if 'net_dex' in df_agg.columns else 0.0
    net_tex_v = float(df_agg['net_tex'].sum()) if 'net_tex' in df_agg.columns else 0.0
    net_vex_v = float(df_agg['net_vex'].sum()) if 'net_vex' in df_agg.columns else 0.0
    net_chex_v = float(df_agg['net_chex'].sum()) if 'net_chex' in df_agg.columns else 0.0
    net_vanna_v = float(df_agg['net_vanna'].sum()) if 'net_vanna' in df_agg.columns else 0.0

    valid_ivs = []
    if spot_ref > 0:
        near_atm = df_agg[abs(df_agg['strike'] - spot_ref) <= (spot_ref * 0.025)]
        if not near_atm.empty:
            for _, r in near_atm.iterrows():
                if 'iv_c' in r and 0.02 < r.get('iv_c', 0) < 3.0: valid_ivs.append(r['iv_c'])
                if 'iv_p' in r and 0.02 < r.get('iv_p', 0) < 3.0: valid_ivs.append(r['iv_p'])
    atm_iv_v = float(np.median(valid_ivs)) if len(valid_ivs) > 0 else 0.20

    regime_str_v = "positive regime" if net_gex_total_v >= 0 else "negative regime"
    condition_str_v = ("Positive – dealers long gamma, hedging dampens volatility (mean-reverting)"
                        if net_gex_total_v >= 0 else
                        "Negative – dealers short gamma, hedging amplifies trending behavior")
    iv_str_v = f"{atm_iv_v * 100:.2f}%"
    iv_rank_str_v = f"{int(min(max((atm_iv_v / 0.35) * 100, 15), 85))}th percentile"

    return {
        "cw1": cw1_v, "cw2": cw2_v, "cw3": cw3_v, "pw1": pw1_v, "pw2": pw2_v, "pw3": pw3_v,
        "zero_gamma": zero_gamma_v, "net_gex_total": net_gex_total_v,
        "call_gex_sum": call_gex_sum_v, "put_gex_sum": put_gex_sum_v,
        "net_dex_val": net_dex_v, "net_tex_val": net_tex_v, "net_vex_val": net_vex_v,
        "net_chex_val": net_chex_v, "net_vanna_val": net_vanna_v,
        "iv_str": iv_str_v, "iv_rank_str": iv_rank_str_v,
        "regime_str": regime_str_v, "condition_str": condition_str_v,
    }

# --- WIDGET CHATBOT SIDEBAR ---
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

    st.caption("Diagnóstico en vivo del mercado según VIX, perfiles GEX, Griegas y Escenarios A, B y C")

    session_info = get_session_state()

    col_btn1, col_btn2, col_btn3 = st.columns(3)

    with col_btn1:
        if st.button("📊 Pre-Market", key="btn_ai_premarket", use_container_width=True):
            spot_manual_str_pm = st.session_state.get("precio_manual_premarket", "")
            try:
                spot_manual_pm = float(spot_manual_str_pm) if spot_manual_str_pm.strip() else 0.0
            except ValueError:
                spot_manual_pm = 0.0
            spot_pm = spot_manual_pm if spot_manual_pm > 0 else spot_price

            premarket_keys = find_exp_keys_by_date(df_curr, session_info["premarket_date_str"])
            metrics_pm = compute_metrics_for_dte(df_curr, premarket_keys, spot_pm)
            with st.spinner("Analizando pre-market..."):
                res = consultar_ia(
                    tipo_analisis="Pre-Market",
                    mensaje_usuario="Genera el análisis estratégico Pre-Market evaluando VIX, régimen de Gamma, niveles clave, Griegas y Escenarios A, B y C.",
                    metrics_override=metrics_pm,
                    dte_context_label=f"sesión próxima del {session_info['premarket_date_str']}" + (
                        f" (precio manual ${spot_pm:.2f})" if spot_manual_pm > 0 else ""
                    ),
                    spot_override=spot_pm
                )
                save_chat_message("assistant", res)
        st.caption("🕒 Sesión próxima")
        st.text_input(
            "💲 Precio manual (opcional)",
            value="", placeholder="Ej: 721.40",
            key="precio_manual_premarket",
            help="Si el precio automático está desactualizado (feriados, baja liquidez, divergencia ETH/RTH), escribe aquí el precio real de QQQ para que el análisis Pre-Market lo use. Déjalo vacío para usar el precio automático."
        )

    with col_btn2:
        if st.button("📈 Intradía", key="btn_ai_intraday", use_container_width=True):
            intraday_keys = find_exp_keys_by_date(df_curr, today_date_str)
            metrics_id = compute_metrics_for_dte(df_curr, intraday_keys, spot_price)
            with st.spinner("Analizando intradía..."):
                res = consultar_ia(
                    tipo_analisis="Mercado Intradía",
                    mensaje_usuario="Genera el informe intradía evaluando lectura de VIX, flujo de Gamma, decaimiento por Charm/Vanna, Net Drift y Escenarios A, B y C.",
                    metrics_override=metrics_id,
                    dte_context_label=f"sesión actual del {today_date_str}"
                )
                save_chat_message("assistant", res)
        st.caption("🟢 Sesión actual" if session_info["intraday_active"] else "⚪ No hay sesión actual")

    with col_btn3:
        if st.button("🧠 Análisis", key="btn_ai_analisis", use_container_width=True):
            analisis_keys = st.session_state.get("selected_dte_keys_chat_analisis", [])
            metrics_an = compute_metrics_for_dte(df_curr, analisis_keys, spot_price)
            with st.spinner("Procesando análisis completo..."):
                res = consultar_ia(
                    tipo_analisis="Análisis Estratégico",
                    mensaje_usuario="Proporciona el Diagnóstico Estratégico completo con niveles exactos, VIX y Escenarios A, B y C de trading.",
                    metrics_override=metrics_an,
                    dte_context_label=", ".join(analisis_keys) if analisis_keys else None
                )
                save_chat_message("assistant", res)
        render_dte_selector(
            df_curr, location_key="chat_analisis",
            state_key="selected_dte_keys_chat_analisis",
            use_popover=True, popover_label="📂 DTE"
        )

    st.markdown("---")

    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    if chat_input := st.chat_input("Pregunta sobre VIX, GEX, Griegas o niveles de mercado..."):
        save_chat_message("user", chat_input)
        with st.chat_message("user"):
            st.write(chat_input)

        with st.spinner("Analizando datos..."):
            respuesta_bot = consultar_ia(mensaje_usuario=chat_input)
            save_chat_message("assistant", respuesta_bot)
            with st.chat_message("assistant"):
                st.write(respuesta_bot)

# --- PANEL DE MÉTRICAS TOP (AHORA FILTRABLE POR DTE) ---
# El badge de estado de SCHWAB va justo a la izquierda del boton de DTE.
col_metrics_title, col_metrics_badge, col_metrics_dte = st.columns([7.2, 1.3, 1.5])
with col_metrics_title:
    st.markdown("<p style='margin:0 0 4px 0; font-family:\"JetBrains Mono\"; font-size:0.72rem; color:#6E7681; letter-spacing:0.5px;'>📊 PANEL DE MÉTRICAS (filtrable por DTE — compartido con GEX INFO y GREEKS)</p>", unsafe_allow_html=True)
with col_metrics_badge:
    if schwab_status_online:
        st.markdown('<div class="badge-online">🟢 ONLINE</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="badge-offline">🔴 OFFLINE</div>', unsafe_allow_html=True)
with col_metrics_dte:
    # Mismo state_key por defecto ("selected_dte_keys") que usan GEX INFO y GREEKS:
    # al compartir la variable de session_state, cambiar el DTE aquí, en GEX INFO
    # o en GREEKS actualiza los tres paneles a la vez.
    df_header_filtered = render_dte_selector(
        df_curr, location_key="header_metrics",
        state_key="selected_dte_keys",
        use_popover=True, popover_label="📂 DTE"
    )

if not df_header_filtered.empty and 'net_gex' in df_header_filtered.columns:
    net_gex_total = float(df_header_filtered['net_gex'].sum())
    call_gex_sum = float(df_header_filtered['call_gex'].sum()) if 'call_gex' in df_header_filtered.columns else 0.0
    put_gex_sum = float(df_header_filtered['put_gex'].sum()) if 'put_gex' in df_header_filtered.columns else 0.0
    total_gex = float(abs(call_gex_sum) + abs(put_gex_sum))
    call_oi_sum = int(df_header_filtered['openInterest_c'].sum()) if 'openInterest_c' in df_header_filtered.columns else 0
    put_oi_sum = int(df_header_filtered['openInterest_p'].sum()) if 'openInterest_p' in df_header_filtered.columns else 0
    total_oi_sum = call_oi_sum + put_oi_sum

    df_hdr_sorted = df_header_filtered.sort_values('strike').reset_index(drop=True)
    # Igual que en el cálculo global: agrupar por strike sumando call_gex/
    # put_gex/net_gex de todas las expiraciones antes de rankear, para no
    # subestimar un strike cuyo volumen está repartido entre varias
    # expiraciones. Dominancia por SIGNO del net_gex (ver compute_call_put_walls).
    df_hdr_gex_by_strike = df_hdr_sorted.groupby('strike', as_index=False)[['call_gex', 'put_gex', 'net_gex']].sum() if {'call_gex', 'put_gex', 'net_gex'}.issubset(df_hdr_sorted.columns) else pd.DataFrame()
    cw1, _cw2_hdr, _cw3_hdr, pw1, _pw2_hdr, _pw3_hdr = compute_call_put_walls(df_hdr_gex_by_strike, spot_price)

    df_hdr_sorted['cum_gex'] = df_hdr_sorted['net_gex'].cumsum()
    zero_gamma = spot_price
    if not df_hdr_sorted.empty:
        zg_idx_hdr = df_hdr_sorted['cum_gex'].abs().idxmin()
        if zg_idx_hdr in df_hdr_sorted.index:
            zero_gamma = float(df_hdr_sorted.loc[zg_idx_hdr]['strike'])
# Si la seleccion de DTE queda vacia, se conservan los totales globales (todas
# las expiraciones) ya calculados mas arriba en el script, en vez de mostrar
# el panel en blanco.

cw_diff = ((cw1 - spot_price) / spot_price * 100) if spot_price > 0 else 0
pw_diff = ((pw1 - spot_price) / spot_price * 100) if spot_price > 0 else 0
zg_diff = ((zero_gamma - spot_price) / spot_price * 100) if spot_price > 0 else 0
gex_ratio = abs(call_gex_sum/put_gex_sum) if put_gex_sum != 0 else 0.0

k1, k2, k3, k4, k5, k6, k7, k8, k9 = st.columns(9)
k1.markdown(f'<div class="metric-card"><div class="metric-label">Spot Price</div><div class="metric-value">${spot_price:.2f}</div><div class="metric-sub">{ticker_symbol}</div></div>', unsafe_allow_html=True)
k2.markdown(f'<div class="metric-card"><div class="metric-label">Net GEX</div><div class="metric-value" style="color:{"#10B981" if net_gex_total >= 0 else "#EF4444"};">{fmt_val(net_gex_total)}</div><div class="metric-sub">Ratio: {gex_ratio:.2f}</div></div>', unsafe_allow_html=True)
k3.markdown(f'<div class="metric-card"><div class="metric-label">Call GEX</div><div class="metric-value" style="color:#10B981">{fmt_val(call_gex_sum)}</div><div class="metric-sub">{call_oi_sum:,} OI</div></div>', unsafe_allow_html=True)
k4.markdown(f'<div class="metric-card"><div class="metric-label">Put GEX</div><div class="metric-value" style="color:#EF4444">{fmt_val(put_gex_sum)}</div><div class="metric-sub">{put_oi_sum:,} OI</div></div>', unsafe_allow_html=True)
k5.markdown(f'<div class="metric-card"><div class="metric-label">Total GEX</div><div class="metric-value" style="color:#3B82F6">{fmt_val(total_gex, show_sign=False)}</div><div class="metric-sub">{total_oi_sum:,} OI</div></div>', unsafe_allow_html=True)
k6.markdown(f'<div class="metric-card"><div class="metric-label">Call Wall</div><div class="metric-value" style="color:#10B981">${cw1:.0f}</div><div class="metric-sub">{cw_diff:+.2f}%</div></div>', unsafe_allow_html=True)
k7.markdown(f'<div class="metric-card"><div class="metric-label">Put Wall</div><div class="metric-value" style="color:#EF4444">${pw1:.0f}</div><div class="metric-sub">{pw_diff:+.2f}%</div></div>', unsafe_allow_html=True)
k8.markdown(f'<div class="metric-card"><div class="metric-label">Zero Gamma</div><div class="metric-value" style="color:#F59E0B">${zero_gamma:.2f}</div><div class="metric-sub">{zg_diff:+.2f}%</div></div>', unsafe_allow_html=True)
k9.markdown(f'<div class="metric-card"><div class="metric-label">VIX</div><div class="metric-value" style="color:{vix_color}">{vix_val:.2f}</div><div class="metric-sub">{vix_status}</div></div>', unsafe_allow_html=True)

st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)

def push_to_supabase_bg(snapshot_payload):
    if supabase:
        try:
            # Dedup: con 3 sesiones guardando cada ~60s de forma independiente,
            # 2-3 usuarios pueden caer casi en el mismo minuto y duplicar la
            # fila (a diferencia de Firebase, esta tabla no tenía ningún
            # chequeo). Antes de insertar, revisamos si ya existe una entrada
            # para este symbol+time (ambos ya vienen en hora NY fija).
            existing = supabase.table("gex_intraday") \
                .select("id") \
                .eq("symbol", snapshot_payload["symbol"]) \
                .eq("time", snapshot_payload["time"]) \
                .limit(1) \
                .execute()
            if existing.data:
                return
            supabase.table("gex_intraday").insert(snapshot_payload).execute()
        except Exception as e:
            log_to_console("Supabase Async Snapshot Error", str(e))

def push_history_entry_to_firebase(db_url, date_key, snapshot_entry):
    try:
        day_url = f"{db_url}/history/{date_key}.json"
        resp = requests.get(day_url, timeout=3)
        day_list = resp.json() if resp.status_code == 200 else None
        if not isinstance(day_list, list):
            day_list = []

        existing_times = [s.get("time") for s in day_list if isinstance(s, dict)]
        if snapshot_entry["time"] in existing_times:
            return

        day_list.append(snapshot_entry)
        requests.put(day_url, json=day_list, headers={"Content-Type": "application/json"}, timeout=4)

        # Poda: con snapshots cada ~60s en horario de mercado, un día ronda
        # ~1MB en Firebase. Con el 1GB gratis de espacio, dejamos hasta 180
        # días (~180MB) de colchón para el backtest sin acercarnos al límite.
        shallow_resp = requests.get(f"{db_url}/history.json?shallow=true", timeout=3)
        if shallow_resp.status_code == 200:
            all_dates = shallow_resp.json() or {}
            if isinstance(all_dates, dict) and len(all_dates) > 180:
                for old_d in sorted(all_dates.keys())[:-180]:
                    requests.delete(f"{db_url}/history/{old_d}.json", timeout=3)
    except Exception as e:
        log_to_console("Firebase Async Background Push", str(e))

def export_snapshot_throttled():
    if not is_online:
        return

    last_export = st.session_state.get("last_export_time", 0)
    current_time = time.time()
    
    if current_time - last_export >= 60 and spot_price > 0 and not df_curr.empty:
        st.session_state.last_export_time = current_time
        # NY fija (STORAGE_TZ) para que el mismo instante real se guarde
        # siempre con la misma etiqueta de hora, sin importar la sesión.
        time_str = now_tz_store.strftime("%H:%M")
        date_str = now_tz_store.strftime("%Y-%m-%d")

        # El snapshot que se guarda para BACKGAMMA/backtest SIEMPRE usa la
        # expiración más cercana (0DTE), igual que el feed en vivo — nunca
        # la que cualquier sesión tenga seleccionada en pantalla. Antes esto
        # mezclaba dos fuentes distintas dentro del mismo snapshot: el total
        # ("net_gex") salía filtrado por el DTE de sesión (df_header_filtered),
        # mientras que el detalle por strike ("strikes") salía de TODAS las
        # expiraciones sin filtrar ni agrupar — lo que podía mostrar un total
        # negativo mientras el perfil por strike se veía claramente dominado
        # por barras verdes (positivas), como reportado.
        df_snapshot_source = get_nearest_dte_subset(df_curr)
        df_snapshot_grouped = df_snapshot_source.groupby('strike', as_index=False)[['call_gex', 'put_gex', 'net_gex']].sum()

        strikes_payload = [
            {
                "strike": float(r['strike']),
                "net_gex": float(r.get('net_gex', 0.0)),
                "call_gex": float(r.get('call_gex', 0.0)),
                "put_gex": float(r.get('put_gex', 0.0))
            }
            for _, r in df_snapshot_grouped.iterrows()
        ]

        snapshot_entry = {
            "symbol": ticker_symbol,
            "time": time_str,
            "spot": float(spot_price),
            "net_gex": float(df_snapshot_grouped['net_gex'].sum()),
            "strikes": strikes_payload
        }
        
        threading.Thread(target=push_to_supabase_bg, args=(snapshot_entry,), daemon=True).start()

        if FIREBASE_DB_URL:
            threading.Thread(
                target=push_history_entry_to_firebase,
                args=(FIREBASE_DB_URL, date_str, snapshot_entry),
                daemon=True
            ).start()

export_snapshot_throttled()

# --- PESTAÑAS PRINCIPALES ---
tab_gex, tab_live, tab_drift, tab_greeks, tab_back, tab_data = st.tabs([
    "GEX INFO",
    "LIVE GAMMA",
    "NET DRIFT",
    "GREEKS",
    "BACKGAMMA",
    "DATA"
])

# --- 1. GEX INFO ---
with tab_gex:
    df_gex_filtered = render_dte_selector(df_curr, "gex")

    sub_gex1, sub_gex2 = st.tabs(["NET GEX PROFILE", "CALLS vs PUTS"])
    
    with sub_gex1:
        if not df_gex_filtered.empty and 'strike' in df_gex_filtered.columns:
            df_sub = df_gex_filtered[(df_gex_filtered['strike'] >= min_strike) & (df_gex_filtered['strike'] <= max_strike)].copy()
            if not df_sub.empty:
                df_sub = df_sub.reset_index(drop=True)
                colors = ['#10B981' if v >= 0 else '#EF4444' for v in df_sub['net_gex']]
                xaxis_kwargs = safe_strike_range(df_sub)

                y_max_val = df_sub['net_gex'].max()
                y_min_val = df_sub['net_gex'].min()
                # Poco margen (headroom) por encima/debajo del maximo y minimo real:
                # esto hace que las barras "llenen" casi todo el alto disponible del
                # recuadro (casi el doble de largo visualmente que antes, cuando el
                # margen era 1.5x el valor maximo/minimo).
                y_max_adj = (max(y_max_val, 0) * 1.08) if y_max_val > 0 else 1000
                y_min_adj = (min(y_min_val, 0) * 1.08) if y_min_val < 0 else -1000

                # Resalta con borde blanco la barra verde (positiva) de mayor valor
                # y la barra roja (negativa) de mayor valor negativo.
                max_pos_idx = df_sub['net_gex'].idxmax() if (df_sub['net_gex'] > 0).any() else None
                min_neg_idx = df_sub['net_gex'].idxmin() if (df_sub['net_gex'] < 0).any() else None
                line_colors = []
                line_widths = []
                for idx in df_sub.index:
                    if idx == max_pos_idx or idx == min_neg_idx:
                        line_colors.append('#FFFFFF')
                        line_widths.append(2.5)
                    else:
                        line_colors.append('rgba(0,0,0,0)')
                        line_widths.append(0)

                fig1 = go.Figure()
                fig1.add_trace(go.Bar(
                    x=df_sub['strike'], y=df_sub['net_gex'],
                    orientation='v', marker=dict(color=colors, line=dict(color=line_colors, width=line_widths)),
                    hovertemplate="<b>Strike:</b> $%{x:.2f}<br><b>Net GEX:</b> %{customdata}<extra></extra>",
                    customdata=[fmt_val(v) for v in df_sub['net_gex']]
                ))
                
                if spot_price > 0:
                    fig1.add_vline(
                        x=spot_price, line_color="#3B82F6", line_width=1.5, line_dash="dash",
                        annotation_text=f"Spot (${spot_price:.2f})", annotation_position="top",
                        annotation_font=dict(color="#60A5FA", size=11, family="JetBrains Mono")
                    )
                
                fig1.update_layout(
                    template="plotly_dark", plot_bgcolor='#06080D', paper_bgcolor='#06080D',
                    title=dict(text="<b>Strike Profile (Net Gamma Exposure)</b>", font=dict(family="Plus Jakarta Sans", size=15, color="#F0F6FC")),
                    xaxis=dict(title="Strike ($)", gridcolor="rgba(255,255,255,0.05)", tickfont=dict(family="JetBrains Mono", color="#8B949E"), zeroline=False, **xaxis_kwargs),
                    yaxis=dict(title="Net GEX ($)", gridcolor="rgba(255,255,255,0.05)", tickfont=dict(family="JetBrains Mono", color="#8B949E"), zeroline=True, zerolinecolor="rgba(255,255,255,0.15)", zerolinewidth=1, range=[y_min_adj, y_max_adj]),
                    height=710, margin=dict(l=50, r=40, t=50, b=40)
                )
                st.plotly_chart(fig1, use_container_width=True)

    with sub_gex2:
        if not df_gex_filtered.empty and 'strike' in df_gex_filtered.columns:
            df_sub = df_gex_filtered[(df_gex_filtered['strike'] >= min_strike) & (df_gex_filtered['strike'] <= max_strike)].copy()
            if not df_sub.empty:
                df_sub = df_sub.reset_index(drop=True)
                xaxis_kwargs = safe_strike_range(df_sub)
                y_max_val = max(df_sub['call_gex'].max(), 0)
                y_min_val = min(df_sub['put_gex'].min(), 0)
                y_max_adj = (y_max_val * 1.08) if y_max_val > 0 else 1000
                y_min_adj = (y_min_val * 1.08) if y_min_val < 0 else -1000

                # Mismo criterio de delineado blanco que NET GEX PROFILE: resalta
                # la barra de Call GEX mas grande y la de Put GEX mas negativa.
                max_call_idx = df_sub['call_gex'].idxmax() if (df_sub['call_gex'] > 0).any() else None
                min_put_idx = df_sub['put_gex'].idxmin() if (df_sub['put_gex'] < 0).any() else None

                call_line_colors, call_line_widths = [], []
                put_line_colors, put_line_widths = [], []
                for idx in df_sub.index:
                    if idx == max_call_idx:
                        call_line_colors.append('#FFFFFF'); call_line_widths.append(2.5)
                    else:
                        call_line_colors.append('rgba(0,0,0,0)'); call_line_widths.append(0)
                    if idx == min_put_idx:
                        put_line_colors.append('#FFFFFF'); put_line_widths.append(2.5)
                    else:
                        put_line_colors.append('rgba(0,0,0,0)'); put_line_widths.append(0)

                fig2 = go.Figure()
                fig2.add_trace(go.Bar(
                    x=df_sub['strike'], y=df_sub['call_gex'], name="Call GEX (+)",
                    marker=dict(color='#10B981', line=dict(color=call_line_colors, width=call_line_widths)),
                    hovertemplate="<b>Strike:</b> $%{x:.2f}<br><b>Call GEX:</b> %{customdata}<extra></extra>", customdata=[fmt_val(v) for v in df_sub['call_gex']]
                ))
                fig2.add_trace(go.Bar(
                    x=df_sub['strike'], y=df_sub['put_gex'], name="Put GEX (-)",
                    marker=dict(color='#EF4444', line=dict(color=put_line_colors, width=put_line_widths)),
                    hovertemplate="<b>Strike:</b> $%{x:.2f}<br><b>Put GEX:</b> %{customdata}<extra></extra>", customdata=[fmt_val(v) for v in df_sub['put_gex']]
                ))
                
                if spot_price > 0:
                    fig2.add_vline(x=spot_price, line_color="#3B82F6", line_width=1.5, line_dash="dash", annotation_text=f"Spot (${spot_price:.2f})")
                
                fig2.update_layout(
                    template="plotly_dark", plot_bgcolor='#06080D', paper_bgcolor='#06080D',
                    title=dict(text="<b>Call Gamma vs Put Gamma por Strike</b>", font=dict(family="Plus Jakarta Sans", size=15, color="#F0F6FC")),
                    barmode='relative',
                    xaxis=dict(title="Strike ($)", gridcolor="rgba(255,255,255,0.05)", tickfont=dict(family="JetBrains Mono"), **xaxis_kwargs),
                    yaxis=dict(title="Gamma Exposure ($)", gridcolor="rgba(255,255,255,0.05)", tickfont=dict(family="JetBrains Mono"), range=[y_min_adj, y_max_adj]),
                    height=710, margin=dict(l=50, r=40, t=50, b=40)
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
            x=full_timestamps, y=fine_strikes, z=Z_matrix_scaled, customdata=custom_hover_matrix,
            hovertemplate="<b>Hora:</b> %{x}<br><b>Strike:</b> $%{y:.2f}<br><b>Net Gamma Real:</b> %{customdata}<extra></extra>",
            # zsmooth='best' interpola entre celdas (en vez de pintar
            # rectángulos planos con bordes duros), dando el efecto de
            # iluminación/resplandor difuminado que se pidió.
            zsmooth='best', zmin=-1.0, zmax=1.0, zmid=0,
            # La opacidad máxima baja de 0.95 a 0.55 para que los niveles más
            # fuertes se sigan viendo claramente por encima del resto, pero
            # sin saturar el color al punto de tapar las velas. Se agregan
            # paradas intermedias para que el degradado hacia el centro
            # transparente sea más gradual (efecto "glow") en vez de un
            # salto abrupto.
            colorscale=[
                [0.0, 'rgba(239, 68, 68, 0.55)'],
                [0.12, 'rgba(239, 68, 68, 0.32)'],
                [0.30, 'rgba(239, 68, 68, 0.10)'],
                [0.47, 'rgba(6, 8, 13, 0.0)'],
                [0.53, 'rgba(6, 8, 13, 0.0)'],
                [0.70, 'rgba(16, 185, 129, 0.10)'],
                [0.88, 'rgba(16, 185, 129, 0.32)'],
                [1.0, 'rgba(16, 185, 129, 0.55)']
            ],
            colorbar=dict(title=dict(text="Net GEX ($)", side="top"), x=-0.05)
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
                x=full_timestamps, open=h_1m_reindexed['Open'], high=h_1m_reindexed['High'],
                low=h_1m_reindexed['Low'], close=h_1m_reindexed['Close'], name="Spot Price",
                increasing_line_color='#10B981', decreasing_line_color='#EF4444',
                increasing_fillcolor='#10B981', decreasing_fillcolor='#EF4444'
            ))

        fig_live.update_layout(
            template="plotly_dark", plot_bgcolor='#06080D', paper_bgcolor='#06080D',
            uirevision="static_user_state", xaxis_title=f"Hora Intradía ({tz_choice.split(' ')[0]})", yaxis_title="Precio / Strike ($)",
            height=680, dragmode='pan', hovermode="closest", xaxis_rangeslider_visible=False,
            margin=dict(l=80, r=60, t=40, b=40), yaxis=dict(side='right')
        )
        st.plotly_chart(fig_live, use_container_width=True, config={'scrollZoom': True}, key="heatmap_live")

    st.markdown('</div>', unsafe_allow_html=True)

# --- 3. NET DRIFT ---
with tab_drift:
    st.markdown('<div class="depth-frame">', unsafe_allow_html=True)
    if len(full_timestamps) > 0 and len(call_drift_raw) > 0:
        last_call_val, last_put_val, last_net_val = call_drift_raw[-1], put_drift_raw[-1], net_drift_raw[-1]
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
            rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.06,
            row_heights=[0.75, 0.25], specs=[[{"secondary_y": False}], [{"secondary_y": False}]]
        )

        # Escala el movimiento del Spot Price al rango numérico del Drift para compartir 1 solo eje Y
        drift_max = max(abs(call_drift_raw).max(), abs(put_drift_raw).max(), abs(net_drift_raw).max(), 1.0)
        spot_change = closes_drift - closes_drift[0]
        spot_max_change = max(abs(spot_change).max(), 0.001)
        spot_scaled = (spot_change / spot_max_change) * drift_max

        fig_drift.add_trace(go.Scatter(
            x=full_timestamps, y=call_drift_raw, mode='lines', name='Calls',
            line=dict(color='#10B981', width=2),
            hovertemplate="<b>Hora:</b> %{x}<br><b>Calls:</b> %{customdata}<extra></extra>",
            customdata=[fmt_val(v) for v in call_drift_raw]
        ), row=1, col=1)

        fig_drift.add_trace(go.Scatter(
            x=full_timestamps, y=put_drift_raw, mode='lines', name='Puts',
            line=dict(color='#EF4444', width=2),
            hovertemplate="<b>Hora:</b> %{x}<br><b>Puts:</b> %{customdata}<extra></extra>",
            customdata=[fmt_val(v) for v in put_drift_raw]
        ), row=1, col=1)

        fig_drift.add_trace(go.Scatter(
            x=full_timestamps, y=net_drift_raw, mode='lines', name='Net',
            line=dict(color='#F59E0B', width=2),
            hovertemplate="<b>Hora:</b> %{x}<br><b>Net Drift:</b> %{customdata}<extra></extra>",
            customdata=[fmt_val(v) for v in net_drift_raw]
        ), row=1, col=1)

        fig_drift.add_trace(go.Scatter(
            x=full_timestamps, y=spot_scaled, mode='lines', name=ticker_symbol,
            line=dict(color='#3B82F6', width=2),
            hovertemplate=f"<b>Hora:</b> %{{x}}<br><b>{ticker_symbol}:</b> $%{{customdata:.2f}}<extra></extra>",
            customdata=closes_drift
        ), row=1, col=1)

        fig_drift.add_trace(go.Scatter(
            x=full_timestamps, y=vols_drift, mode='lines', name='Volume',
            line=dict(color='#10B981', width=1.5), fill='tozeroy',
            fillcolor='rgba(16, 185, 129, 0.25)'
        ), row=2, col=1)

        fig_drift.update_xaxes(showgrid=True, gridcolor="rgba(255,255,255,0.05)")
        fig_drift.update_yaxes(fixedrange=False, autorange=True, showgrid=True, gridcolor="rgba(255,255,255,0.05)", row=1, col=1)
        fig_drift.update_yaxes(fixedrange=False, autorange=True, showgrid=True, gridcolor="rgba(255,255,255,0.05)", row=2, col=1)

        fig_drift.update_layout(
            template="plotly_dark", plot_bgcolor='#06080D', paper_bgcolor='#06080D',
            showlegend=False, height=650, margin=dict(l=60, r=60, t=30, b=30),
            hovermode="x unified", dragmode='pan', uirevision="static_user_state"
        )
        st.plotly_chart(fig_drift, use_container_width=True, config={'scrollZoom': True}, key="net_drift_chart")

    st.markdown('</div>', unsafe_allow_html=True)

# --- 4. GREEKS (GRIEGAS) ---
with tab_greeks:
    st.markdown('<div class="depth-frame">', unsafe_allow_html=True)
    st.markdown("<h3 style='margin-top:0; font-weight:800; color:#F0F6FC; font-size:1.1rem; letter-spacing:0.5px;'>📊 PERFILES DE EXPOSICIÓN DE GRIEGAS</h3>", unsafe_allow_html=True)

    df_grk_filtered = render_dte_selector(df_curr, "grk")

    net_dex_total_grk = float(df_grk_filtered['net_dex'].sum()) if not df_grk_filtered.empty and 'net_dex' in df_grk_filtered.columns else 0.0
    net_tex_total_grk = float(df_grk_filtered['net_tex'].sum()) if not df_grk_filtered.empty and 'net_tex' in df_grk_filtered.columns else 0.0
    net_vex_total_grk = float(df_grk_filtered['net_vex'].sum()) if not df_grk_filtered.empty and 'net_vex' in df_grk_filtered.columns else 0.0
    net_chex_total_grk = float(df_grk_filtered['net_chex'].sum()) if not df_grk_filtered.empty and 'net_chex' in df_grk_filtered.columns else 0.0
    net_vanna_total_grk = float(df_grk_filtered['net_vanna'].sum()) if not df_grk_filtered.empty and 'net_vanna' in df_grk_filtered.columns else 0.0

    c_dex = "#10B981" if net_dex_total_grk >= 0 else "#EF4444"
    c_tex = "#10B981" if net_tex_total_grk >= 0 else "#EF4444"
    c_vex = "#10B981" if net_vex_total_grk >= 0 else "#EF4444"
    c_chex = "#10B981" if net_chex_total_grk >= 0 else "#EF4444"
    c_vanna = "#10B981" if net_vanna_total_grk >= 0 else "#EF4444"

    g1, g2, g3, g4, g5 = st.columns(5)
    g1.markdown(f'<div class="metric-card"><div class="metric-label">Net Delta (DEX)</div><div class="metric-value" style="color:{c_dex};">${net_dex_total_grk:.2f}M</div><div class="metric-sub">Delta Exposure</div></div>', unsafe_allow_html=True)
    g2.markdown(f'<div class="metric-card"><div class="metric-label">Net Theta (TEX)</div><div class="metric-value" style="color:{c_tex};">{fmt_val(net_tex_total_grk)}</div><div class="metric-sub">Decaimiento / Día</div></div>', unsafe_allow_html=True)
    g3.markdown(f'<div class="metric-card"><div class="metric-label">Net Vega (VEX)</div><div class="metric-value" style="color:{c_vex};">{fmt_val(net_vex_total_grk)}</div><div class="metric-sub">Por +1% IV</div></div>', unsafe_allow_html=True)
    g4.markdown(f'<div class="metric-card"><div class="metric-label">Net Charm (CHEX)</div><div class="metric-value" style="color:{c_chex};">${net_chex_total_grk:.2f}M</div><div class="metric-sub">Decaimiento Delta / Día</div></div>', unsafe_allow_html=True)
    g5.markdown(f'<div class="metric-card"><div class="metric-label">Net Vanna (VANNA)</div><div class="metric-value" style="color:{c_vanna};">${net_vanna_total_grk:.2f}M</div><div class="metric-sub">Sensibilidad a Vol</div></div>', unsafe_allow_html=True)

    st.markdown("<div style='height: 15px;'></div>", unsafe_allow_html=True)

    sub_grk1, sub_grk2, sub_grk3, sub_grk4, sub_grk5 = st.tabs([
        "DELTA (DEX)", "THETA (TEX)", "VEGA (VEX)", "CHARM (CHEX)", "VANNA (VANNA EX)"
    ])

    # Helpers compartidos con NET GEX PROFILE: mismo margen de eje Y (1.08x
    # sobre el maximo/minimo real, antes 1.5x) y mismo delineado blanco de
    # 2.5px resaltando la barra extrema, para que las Griegas luzcan
    # consistentes con el panel de GEX INFO.
    def _grk_headroom_range(vals):
        y_max_val = vals.max()
        y_min_val = vals.min()
        y_max_adj = (max(y_max_val, 0) * 1.08) if y_max_val > 0 else 1000
        y_min_adj = (min(y_min_val, 0) * 1.08) if y_min_val < 0 else -1000
        return y_min_adj, y_max_adj

    def _grk_extreme_outline(vals):
        max_idx = vals.idxmax() if (vals > 0).any() else None
        min_idx = vals.idxmin() if (vals < 0).any() else None
        line_colors, line_widths = [], []
        for idx in vals.index:
            if idx == max_idx or idx == min_idx:
                line_colors.append('#FFFFFF'); line_widths.append(2.5)
            else:
                line_colors.append('rgba(0,0,0,0)'); line_widths.append(0)
        return line_colors, line_widths

    GRK_HEIGHT = 710
    GRK_MARGIN = dict(l=50, r=40, t=50, b=40)

    if not df_grk_filtered.empty and 'strike' in df_grk_filtered.columns:
        df_grk_sub = df_grk_filtered[(df_grk_filtered['strike'] >= min_strike) & (df_grk_filtered['strike'] <= max_strike)].copy()
        df_grk_sub = df_grk_sub.reset_index(drop=True)
        xaxis_kwargs_grk = safe_strike_range(df_grk_sub)

        # 1. DELTA (DEX)
        with sub_grk1:
            if not df_grk_sub.empty and 'net_dex' in df_grk_sub.columns:
                colors_net_dex = ['#10B981' if v >= 0 else '#EF4444' for v in df_grk_sub['net_dex']]
                line_colors_dex, line_widths_dex = _grk_extreme_outline(df_grk_sub['net_dex'])
                y_min_dex, y_max_dex = _grk_headroom_range(df_grk_sub['net_dex'])
                fig_net_dex = go.Figure()
                fig_net_dex.add_trace(go.Bar(x=df_grk_sub['strike'], y=df_grk_sub['net_dex'], marker=dict(color=colors_net_dex, line=dict(color=line_colors_dex, width=line_widths_dex)), name="Net DEX"))
                if spot_price > 0: fig_net_dex.add_vline(x=spot_price, line_color="#3B82F6", line_dash="dash", annotation_text=f"Spot (${spot_price:.2f})")
                fig_net_dex.update_layout(template="plotly_dark", plot_bgcolor='#06080D', paper_bgcolor='#06080D', title="<b>Net Delta Exposure (DEX) Profile por Strike (M USD)</b>", xaxis=dict(title="Strike ($)", **xaxis_kwargs_grk), yaxis=dict(range=[y_min_dex, y_max_dex]), height=GRK_HEIGHT, margin=GRK_MARGIN)
                st.plotly_chart(fig_net_dex, use_container_width=True)

                st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)

                max_call_dex_idx = df_grk_sub['call_dex'].idxmax() if (df_grk_sub['call_dex'] > 0).any() else None
                min_put_dex_idx = df_grk_sub['put_dex'].idxmin() if (df_grk_sub['put_dex'] < 0).any() else None
                call_dex_line_colors, call_dex_line_widths = [], []
                put_dex_line_colors, put_dex_line_widths = [], []
                for idx in df_grk_sub.index:
                    if idx == max_call_dex_idx:
                        call_dex_line_colors.append('#FFFFFF'); call_dex_line_widths.append(2.5)
                    else:
                        call_dex_line_colors.append('rgba(0,0,0,0)'); call_dex_line_widths.append(0)
                    if idx == min_put_dex_idx:
                        put_dex_line_colors.append('#FFFFFF'); put_dex_line_widths.append(2.5)
                    else:
                        put_dex_line_colors.append('rgba(0,0,0,0)'); put_dex_line_widths.append(0)
                y_max_call_dex = max(df_grk_sub['call_dex'].max(), 0)
                y_min_put_dex = min(df_grk_sub['put_dex'].min(), 0)
                y_max_dex_adj = (y_max_call_dex * 1.08) if y_max_call_dex > 0 else 1000
                y_min_dex_adj = (y_min_put_dex * 1.08) if y_min_put_dex < 0 else -1000

                fig_dex = go.Figure()
                fig_dex.add_trace(go.Bar(x=df_grk_sub['strike'], y=df_grk_sub['call_dex'], name="Call DEX (+)", marker=dict(color='#38BDF8', line=dict(color=call_dex_line_colors, width=call_dex_line_widths))))
                fig_dex.add_trace(go.Bar(x=df_grk_sub['strike'], y=df_grk_sub['put_dex'], name="Put DEX (-)", marker=dict(color='#1E40AF', line=dict(color=put_dex_line_colors, width=put_dex_line_widths))))
                if spot_price > 0: fig_dex.add_vline(x=spot_price, line_color="#3B82F6", line_dash="dash", annotation_text=f"Spot (${spot_price:.2f})")
                fig_dex.update_layout(template="plotly_dark", plot_bgcolor='#06080D', paper_bgcolor='#06080D', title="<b>Call vs Put Delta Exposure (DEX) por Strike (M USD)</b>", barmode='relative', xaxis=dict(title="Strike ($)", **xaxis_kwargs_grk), yaxis=dict(range=[y_min_dex_adj, y_max_dex_adj]), height=GRK_HEIGHT, margin=GRK_MARGIN)
                st.plotly_chart(fig_dex, use_container_width=True)

        # 2. THETA (TEX)
        with sub_grk2:
            if not df_grk_sub.empty and 'net_tex' in df_grk_sub.columns:
                colors_net_tex = ['#10B981' if v >= 0 else '#EF4444' for v in df_grk_sub['net_tex']]
                line_colors_tex, line_widths_tex = _grk_extreme_outline(df_grk_sub['net_tex'])
                y_min_tex, y_max_tex = _grk_headroom_range(df_grk_sub['net_tex'])
                fig_net_tex = go.Figure()
                fig_net_tex.add_trace(go.Bar(x=df_grk_sub['strike'], y=df_grk_sub['net_tex'], marker=dict(color=colors_net_tex, line=dict(color=line_colors_tex, width=line_widths_tex)), name="Net TEX"))
                if spot_price > 0: fig_net_tex.add_vline(x=spot_price, line_color="#3B82F6", line_dash="dash", annotation_text=f"Spot (${spot_price:.2f})")
                fig_net_tex.update_layout(template="plotly_dark", plot_bgcolor='#06080D', paper_bgcolor='#06080D', title="<b>Net Theta Exposure (TEX) Profile por Strike ($/día)</b>", xaxis=dict(title="Strike ($)", **xaxis_kwargs_grk), yaxis=dict(range=[y_min_tex, y_max_tex]), height=GRK_HEIGHT, margin=GRK_MARGIN)
                st.plotly_chart(fig_net_tex, use_container_width=True)

        # 3. VEGA (VEX)
        with sub_grk3:
            if not df_grk_sub.empty and 'net_vex' in df_grk_sub.columns:
                colors_net_vex = ['#10B981' if v >= 0 else '#EF4444' for v in df_grk_sub['net_vex']]
                line_colors_vex, line_widths_vex = _grk_extreme_outline(df_grk_sub['net_vex'])
                y_min_vex, y_max_vex = _grk_headroom_range(df_grk_sub['net_vex'])
                fig_net_vex = go.Figure()
                fig_net_vex.add_trace(go.Bar(x=df_grk_sub['strike'], y=df_grk_sub['net_vex'], marker=dict(color=colors_net_vex, line=dict(color=line_colors_vex, width=line_widths_vex)), name="Net VEX"))
                if spot_price > 0: fig_net_vex.add_vline(x=spot_price, line_color="#3B82F6", line_dash="dash", annotation_text=f"Spot (${spot_price:.2f})")
                fig_net_vex.update_layout(template="plotly_dark", plot_bgcolor='#06080D', paper_bgcolor='#06080D', title="<b>Net Vega Exposure (VEX) Profile por Strike ($/1% IV)</b>", xaxis=dict(title="Strike ($)", **xaxis_kwargs_grk), yaxis=dict(range=[y_min_vex, y_max_vex]), height=GRK_HEIGHT, margin=GRK_MARGIN)
                st.plotly_chart(fig_net_vex, use_container_width=True)

        # 4. CHARM (CHEX)
        with sub_grk4:
            if not df_grk_sub.empty and 'net_chex' in df_grk_sub.columns:
                colors_net_chex = ['#10B981' if v >= 0 else '#EF4444' for v in df_grk_sub['net_chex']]
                line_colors_chex, line_widths_chex = _grk_extreme_outline(df_grk_sub['net_chex'])
                y_min_chex, y_max_chex = _grk_headroom_range(df_grk_sub['net_chex'])
                fig_net_chex = go.Figure()
                fig_net_chex.add_trace(go.Bar(x=df_grk_sub['strike'], y=df_grk_sub['net_chex'], marker=dict(color=colors_net_chex, line=dict(color=line_colors_chex, width=line_widths_chex)), name="Net CHEX"))
                if spot_price > 0: fig_net_chex.add_vline(x=spot_price, line_color="#3B82F6", line_dash="dash", annotation_text=f"Spot (${spot_price:.2f})")
                fig_net_chex.update_layout(template="plotly_dark", plot_bgcolor='#06080D', paper_bgcolor='#06080D', title="<b>Net Charm Exposure (CHEX) Profile por Strike (M USD/día)</b>", xaxis=dict(title="Strike ($)", **xaxis_kwargs_grk), yaxis=dict(range=[y_min_chex, y_max_chex]), height=GRK_HEIGHT, margin=GRK_MARGIN)
                st.plotly_chart(fig_net_chex, use_container_width=True)

        # 5. VANNA (VANNA)
        with sub_grk5:
            if not df_grk_sub.empty and 'net_vanna' in df_grk_sub.columns:
                colors_net_vanna = ['#10B981' if v >= 0 else '#EF4444' for v in df_grk_sub['net_vanna']]
                line_colors_vanna, line_widths_vanna = _grk_extreme_outline(df_grk_sub['net_vanna'])
                y_min_vanna, y_max_vanna = _grk_headroom_range(df_grk_sub['net_vanna'])
                fig_net_vanna = go.Figure()
                fig_net_vanna.add_trace(go.Bar(x=df_grk_sub['strike'], y=df_grk_sub['net_vanna'], marker=dict(color=colors_net_vanna, line=dict(color=line_colors_vanna, width=line_widths_vanna)), name="Net Vanna"))
                if spot_price > 0: fig_net_vanna.add_vline(x=spot_price, line_color="#3B82F6", line_dash="dash", annotation_text=f"Spot (${spot_price:.2f})")
                fig_net_vanna.update_layout(template="plotly_dark", plot_bgcolor='#06080D', paper_bgcolor='#06080D', title="<b>Net Vanna Exposure Profile por Strike (M USD)</b>", xaxis=dict(title="Strike ($)", **xaxis_kwargs_grk), yaxis=dict(range=[y_min_vanna, y_max_vanna]), height=GRK_HEIGHT, margin=GRK_MARGIN)
                st.plotly_chart(fig_net_vanna, use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

# --- 5. BACKGAMMA ---
with tab_back:
    st.markdown('<div class="depth-frame">', unsafe_allow_html=True)
    st.markdown("<h3 style='margin-top:0; font-weight:800; color:#F0F6FC; font-size:1.1rem; letter-spacing:0.5px;'>📜 BACKGAMMA - BACKTEST DE GAMMA</h3>", unsafe_allow_html=True)

    if jsonbin_history_data:
        dates_avail = sorted(list(jsonbin_history_data.keys()), reverse=True)
        sel_date = st.selectbox("Seleccionar Fecha de Historial:", dates_avail, key="backtest_sel_date")

        day_snaps_raw = jsonbin_history_data.get(sel_date, [])
        day_snaps = sorted(
            [s for s in day_snaps_raw if isinstance(s, dict) and s.get("time")],
            key=lambda s: s["time"]
        )

        if day_snaps:
            times_hist = [s.get("time") for s in day_snaps]
            spots_hist = [s.get("spot") for s in day_snaps]
            gex_hist = [s.get("net_gex") for s in day_snaps]
            n_snaps = len(day_snaps)

            # El índice del scrubber vive en session_state por fecha, para que
            # cambiar de día no arrastre una posición fuera de rango.
            idx_state_key = f"backtest_idx_{sel_date}"
            if idx_state_key not in st.session_state:
                st.session_state[idx_state_key] = n_snaps - 1
            st.session_state[idx_state_key] = min(st.session_state[idx_state_key], n_snaps - 1)

            col_play, col_speed, col_sync = st.columns([1.1, 1.6, 2.3])
            with col_play:
                playing = st.toggle("▶ Reproducir", key=f"backtest_playing_{sel_date}")
            with col_speed:
                speed_ms = st.select_slider(
                    "Velocidad", options=[2000, 1000, 500, 250],
                    value=1000, key=f"backtest_speed_{sel_date}"
                )
            with col_sync:
                sync_qt = st.toggle("📡 Sincronizar con Quantower (Backtest)", key="backtest_sync_qt")

            if playing and n_snaps > 1:
                try:
                    from streamlit_autorefresh import st_autorefresh
                    st_autorefresh(interval=speed_ms, key=f"backtest_autoplay_{sel_date}")
                except ImportError:
                    st.components.v1.html(
                        f"<script>setTimeout(function(){{ window.parent.postMessage({{type: 'streamlit:render'}}, '*'); location.reload(); }}, {speed_ms});</script>",
                        height=0, width=0
                    )
                st.session_state[idx_state_key] = (st.session_state[idx_state_key] + 1) % n_snaps

            sel_idx = st.slider(
                "Arrastra para moverte en el tiempo:",
                min_value=0, max_value=n_snaps - 1,
                key=idx_state_key
            )
            sel_snap = day_snaps[sel_idx]
            sel_time = sel_snap.get("time", "--:--")
            st.caption(f"🕒 {sel_date}  {sel_time}  ·  paso {sel_idx + 1}/{n_snaps}")

            # "Rastro": lo ya recorrido queda en color, lo que falta se apaga a gris.
            colors_gex = ['#10B981' if (v or 0) >= 0 else '#EF4444' for v in gex_hist]
            colors_gex = [c if i <= sel_idx else 'rgba(148,163,184,0.2)' for i, c in enumerate(colors_gex)]

            fig_back = make_subplots(
                rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
                subplot_titles=(f"Precio Spot ({sel_date})", "Net GEX Intradía")
            )
            fig_back.add_trace(go.Scatter(
                x=times_hist[:sel_idx + 1], y=spots_hist[:sel_idx + 1],
                mode='lines', line=dict(color='#3B82F6', width=2), name='Recorrido'
            ), row=1, col=1)
            fig_back.add_trace(go.Scatter(
                x=times_hist[sel_idx:], y=spots_hist[sel_idx:],
                mode='lines', line=dict(color='rgba(148,163,184,0.25)', width=2), name='Pendiente'
            ), row=1, col=1)
            fig_back.add_trace(go.Scatter(
                x=[times_hist[sel_idx]], y=[spots_hist[sel_idx]], mode='markers',
                marker=dict(color='#FBBF24', size=13, line=dict(color='white', width=1.5)), name='Ahora'
            ), row=1, col=1)
            fig_back.add_trace(go.Bar(x=times_hist, y=gex_hist, marker_color=colors_gex, name='Net GEX'), row=2, col=1)
            fig_back.update_layout(
                template="plotly_dark", plot_bgcolor='#06080D', paper_bgcolor='#06080D',
                height=430, showlegend=False
            )
            st.plotly_chart(fig_back, use_container_width=True)

            # --- Perfil de Gamma por Strike en el instante exacto seleccionado ---
            strikes_snap = sel_snap.get("strikes", [])
            if strikes_snap:
                df_snap = pd.DataFrame(strikes_snap)
                if 'net_gex' in df_snap.columns and 'strike' in df_snap.columns:
                    df_snap = df_snap.groupby('strike', as_index=False)['net_gex'].sum().sort_values('strike')
                    snap_spot = float(sel_snap.get("spot", 0.0) or 0.0)
                    b_cw1, b_cw2, b_cw3, b_pw1, b_pw2, b_pw3 = compute_call_put_walls(df_snap, snap_spot)

                    fig_strike = go.Figure()
                    fig_strike.add_trace(go.Bar(
                        x=df_snap['strike'], y=df_snap['net_gex'],
                        marker_color=['#10B981' if v >= 0 else '#EF4444' for v in df_snap['net_gex']]
                    ))
                    fig_strike.add_vline(x=snap_spot, line_dash="dash", line_color="#60A5FA",
                                          annotation_text=f"Spot (${snap_spot:.2f})")
                    fig_strike.update_layout(
                        title=f"Strike Profile (Net Gamma Exposure) — {sel_date} {sel_time}",
                        template="plotly_dark", plot_bgcolor='#06080D', paper_bgcolor='#06080D',
                        height=420, showlegend=False, xaxis_title="Strike ($)", yaxis_title="Net GEX ($)"
                    )
                    st.plotly_chart(fig_strike, use_container_width=True)

                    st.markdown(f"""
                        <p style="font-family:'JetBrains Mono'; font-size:0.82rem; color:#D1D5DB;">
                        ● <b>CW1:</b> ${b_cw1:.0f} | <b>CW2:</b> ${b_cw2:.0f} | <b>CW3:</b> ${b_cw3:.0f}<br>
                        ● <b>PW1:</b> ${b_pw1:.0f} | <b>PW2:</b> ${b_pw2:.0f} | <b>PW3:</b> ${b_pw3:.0f}
                        </p>
                    """, unsafe_allow_html=True)

                    if sync_qt:
                        if FIREBASE_DB_URL:
                            last_bt_push = st.session_state.get("last_backtest_push", 0.0)
                            now_ts = time.time()
                            if now_ts - last_bt_push >= 0.6:
                                st.session_state["last_backtest_push"] = now_ts
                                bt_levels_payload = [
                                    {"strike": float(r['strike']), "net_gex": float(r['net_gex'])}
                                    for _, r in df_snap.iterrows()
                                ]
                                bt_payload = {
                                    "qqq_spot": snap_spot,
                                    "conversion_ratio": float(conversion_ratio) if 'conversion_ratio' in dir() and conversion_ratio else 41.125,
                                    "cw1": float(b_cw1), "cw2": float(b_cw2), "cw3": float(b_cw3),
                                    "pw1": float(b_pw1), "pw2": float(b_pw2), "pw3": float(b_pw3),
                                    "levels": bt_levels_payload,
                                    "label": f"{sel_date} {sel_time}"
                                }
                                try:
                                    requests.put(
                                        f"{FIREBASE_DB_URL}/backtest_levels.json", json=bt_payload,
                                        headers={"Content-Type": "application/json"}, timeout=5
                                    )
                                    st.sidebar.caption(f"🟣 Backtest enviado a Quantower · {sel_time}")
                                except Exception as e:
                                    log_to_console("Backtest Push Error", str(e))
                        else:
                            st.warning("Falta FIREBASE_DB_URL en secrets para poder sincronizar con Quantower.")
            else:
                st.info("Este snapshot no guardó el detalle por strike (capturas antiguas). Elige un snapshot más reciente.")
        else:
            st.info("No hay datos para la fecha seleccionada.")
    else:
        st.info("No hay historial de Backgamma disponible todavía. Deja la web abierta durante el mercado para que empiece a acumular capturas — el backtest solo cubre desde que la app comenzó a guardar snapshots.")
    st.markdown('</div>', unsafe_allow_html=True)


# --- 6. DATA ---
with tab_data:
    st.markdown('<div class="depth-frame">', unsafe_allow_html=True)
    st.markdown("<h3 style='margin-top:0; font-weight:800; color:#F0F6FC; font-size:1.15rem; letter-spacing:0.5px;'>📋 DATA SUMMARY & DIAGNÓSTICO INSTITUCIONAL</h3>", unsafe_allow_html=True)
    
    st.markdown(f"""
        <div class="data-summary-box">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                <h4 style="margin: 0; font-family: 'Plus Jakarta Sans'; font-weight: 700; color: #60A5FA;">⚡ Resumen Ejecutivo de Mercado ({ticker_symbol})</h4>
                <span style="font-family: 'JetBrains Mono'; font-size: 0.8rem; color: #8B949E;">Índice VIX: <b style="color: {vix_color};">{vix_val:.2f}</b> ({vix_status})</span>
            </div>
            <p style="font-family: 'JetBrains Mono'; font-size: 0.82rem; color: #D1D5DB; margin-bottom: 8px;">
                ● <b>Spot Price:</b> ${spot_price:.2f} USD | <b>Zero Gamma (Flip):</b> ${zero_gamma:.2f} USD | <b>Régimen:</b> <span style="color: {'#10B981' if net_gex_total >= 0 else '#EF4444'};">{regime_str.upper()}</span><br>
                ● <b>Call Wall Principal (CW1):</b> ${cw1:.0f} USD | <b>Put Wall Principal (PW1):</b> ${pw1:.0f} USD<br>
                ● <b>Net GEX Total:</b> {fmt_val(net_gex_total)} | <b>Net DEX:</b> ${net_dex_total:.2f}M USD | <b>IV ATM:</b> {iv_str}
            </p>
        </div>
    """, unsafe_allow_html=True)

    col_diag_btn, col_diag_dte, col_diag_space = st.columns([3.5, 1.5, 5.0])
    with col_diag_btn:
        btn_gen_diag = st.button("🤖 GENERAR DIAGNÓSTICO DE MERCADO (IA)", key="btn_data_ia_diag", use_container_width=True)
    with col_diag_dte:
        render_dte_selector(
            df_curr, location_key="data_diag",
            state_key="selected_dte_keys_data",
            use_popover=True, popover_label="📂 DTE"
        )

    if btn_gen_diag or st.session_state.get("data_ia_diag_result"):
        if btn_gen_diag:
            diag_keys = st.session_state.get("selected_dte_keys_data", [])
            metrics_diag = compute_metrics_for_dte(df_curr, diag_keys, spot_price)
            with st.spinner("Procesando análisis profundo y generando Escenarios A, B y C..."):
                prompt_diag = (
                    f"Genera un Diagnóstico de Mercado e informe táctico completo para {ticker_symbol} "
                    f"con base en el resumen de datos actual. Incluye un análisis exhaustivo del impacto del VIX en {vix_val:.2f}, "
                    f"el comportamiento esperado según el régimen de Gamma, el desglose de Griegas (DEX, TEX, VEX, CHEX, VANNA) "
                    f"y OBLIGATORIAMENTE los Escenarios A (Continuación / Retesteo Aceptado), B (Rechazo en Nivel Clave) y "
                    f"C (Trampa / Falsa Ruptura - Liquidity Sweep) especificando los precios numéricos exactos de entrada y objetivo."
                )
                diag_output = consultar_ia(
                    tipo_analisis="Diagnóstico Data Summary",
                    mensaje_usuario=prompt_diag,
                    metrics_override=metrics_diag,
                    dte_context_label=", ".join(diag_keys) if diag_keys else None
                )
                st.session_state["data_ia_diag_result"] = diag_output

        if "data_ia_diag_result" in st.session_state:
            st.markdown("<div style='margin-top: 15px; padding: 18px; background: rgba(14, 19, 31, 0.95); border: 1px solid rgba(59, 130, 246, 0.4); border-radius: 10px;'>", unsafe_allow_html=True)
            st.markdown(st.session_state["data_ia_diag_result"])
            st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<hr style='border-color:rgba(255,255,255,0.08); margin: 25px 0;'>", unsafe_allow_html=True)
    st.markdown("<h4 style='font-family:\"Plus Jakarta Sans\"; font-weight:700; color:#F0F6FC;'>📊 Cadena de Opciones y Exposición Cuantitativa por Strike</h4>", unsafe_allow_html=True)
    
    if not df_curr.empty:
        cols_show = ['strike', 'openInterest_c', 'openInterest_p', 'call_gex', 'put_gex', 'net_gex', 'call_dex', 'put_dex', 'net_dex', 'iv_c', 'iv_p']
        available_cols = [c for c in cols_show if c in df_curr.columns]
        
        df_display = df_curr[available_cols].copy()
        st.dataframe(
            df_display.style.format({
                'strike': '${:.2f}',
                'openInterest_c': '{:,.0f}',
                'openInterest_p': '{:,.0f}',
                'call_gex': lambda v: fmt_val(v),
                'put_gex': lambda v: fmt_val(v),
                'net_gex': lambda v: fmt_val(v),
                'call_dex': '${:.2f}M',
                'put_dex': '${:.2f}M',
                'net_dex': '${:.2f}M',
                'iv_c': '{:.2%}',
                'iv_p': '{:.2%}'
            }),
            use_container_width=True,
            height=450
        )
    else:
        st.info("No hay datos de opciones disponibles para mostrar en la tabla.")
        
    st.markdown('</div>', unsafe_allow_html=True)
