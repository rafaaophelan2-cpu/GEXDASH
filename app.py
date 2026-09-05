import os
import json
import time
import requests
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

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="GEX Terminal Pro - Schwab", layout="wide", initial_sidebar_state="expanded")

# --- INICIALIZACIÓN DE ESTADOS (CONSOLA Y CHAT) ---
if "console_logs" not in st.session_state:
    st.session_state.console_logs = []

if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []

def log_to_console(source: str, error_detail: str):
    """Registra internamente los detalles de los errores para la Consola."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    st.session_state.console_logs.append({
        "time": timestamp,
        "source": source,
        "error": str(error_detail)
    })

# --- MANEJO SEGURO DE SECRETOS Y TOKEN ---
CLIENT_ID = st.secrets.get("CLIENT_ID", "QJJS3fGYgzh425rtmmGHbPNL5r3ShCHr0FYxerrPzDbAnGxw")
CLIENT_SECRET = st.secrets.get("CLIENT_SECRET", "GQwRhMGJpbHOMB3ANarKzGIgWfxwYUpwN8mvUpyQGpRwd6Jds7gFMnlwiu9THPkj")
JSONBIN_BIN_ID = st.secrets.get("JSONBIN_BIN_ID", "6a9b6fb2da38895dfe3ab4fc")
JSONBIN_API_KEY = st.secrets.get("JSONBIN_API_KEY", "$2a$10$SJzpaPLR88mtOlFsqg3g5OHxzYrwkkS9QJRYTUIGXnhxyW6bi0nyO")
GROQ_API_KEY = st.secrets.get("GROQ_API_KEY", st.secrets.get("GROQ_KEY", st.secrets.get("groq_api_key", os.environ.get("GROQ_API_KEY", None))))
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", None)

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
    return schwab.auth.client_from_token_file(
        token_path=TOKEN_PATH,
        api_key=CLIENT_ID,
        app_secret=CLIENT_SECRET,
        enforce_enums=False
    )

try:
    client = get_schwab_client()
except Exception as e:
    log_to_console("Conexión Schwab API", e)
    st.error("Ocurrió un error, revisa la consola")
    st.stop()

# --- CLIENTES DE IA (GROQ Y GEMINI) ---
@st.cache_resource
def get_gemini_client():
    if GEMINI_API_KEY:
        try:
            return genai.Client(api_key=GEMINI_API_KEY)
        except Exception as e:
            log_to_console("Inicialización Gemini Client", e)
            return None
    return None

ai_client = get_gemini_client()

def query_groq(system_prompt, user_prompt, api_key):
    """Ejecuta consulta a Groq vía SDK si está disponible o mediante API REST directa."""
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
        return completion.choices[0].message.content
    except ImportError:
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
            return resp.json()["choices"][0]["message"]["content"]
        else:
            raise Exception(f"Groq API Error {resp.status_code}: {resp.text}")

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
    </style>
""", unsafe_allow_html=True)

# --- ENCABEZADO Y BOTÓN CONSOLA TOP-RIGHT ---
col_head_title, col_head_console = st.columns([7.5, 2.5])

with col_head_title:
    st.markdown("<h2 style='margin:0; font-weight:800; letter-spacing:-0.5px; background: linear-gradient(90deg, #F0F6FC 0%, #8B949E 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent;'>GEX QUANT TERMINAL</h2>", unsafe_allow_html=True)
    st.markdown("<p style='color:#6E7681; margin:0 0 15px 0; font-size:0.78rem; font-family:\"JetBrains Mono\"; letter-spacing:0.5px;'>SCHWAB REAL-TIME GAMMA EXPOSURE & INTRADAY FLOW</p>", unsafe_allow_html=True)

with col_head_console:
    with st.popover("💻 CONSOLA", use_container_width=True):
        st.markdown("<p style='font-family:\"JetBrains Mono\"; font-weight:800; font-size:0.9rem; color:#F59E0B; margin-bottom:8px;'>💻 CONSOLA DE REGISTROS Y ERRORES</p>", unsafe_allow_html=True)
        if st.button("🗑️ Limpiar Consola", key="btn_clear_console", use_container_width=True):
            st.session_state.console_logs = []
            st.rerun()
        
        st.markdown("---")
        if st.session_state.console_logs:
            for item in reversed(st.session_state.console_logs):
                st.markdown(f"**[{item['time']}] {item['source']}**")
                st.code(item['error'], language="python")
        else:
            st.info("No hay errores registrados en la consola.")

# --- SIDEBAR CONFIG ---
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
    options=[10, 15, 30, 60],
    value=15,
    disabled=not auto_refresh
)

st.sidebar.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
if st.sidebar.button("🔄 ACTUALIZAR DATOS AHORA", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

# --- FUNCIONES DE MERCADO SCHWAB CACHEADAS ---
@st.cache_data(ttl=15)
def fetch_history_schwab(symbol):
    try:
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        resp = client.get_price_history(
            symbol,
            start_datetime=today_start,
            frequency_type=client.PriceHistory.FrequencyType.MINUTE,
            frequency=client.PriceHistory.Frequency.EVERY_MINUTE,
            need_extended_hours_data=False
        )
        if resp.status_code == 200:
            data = resp.json()
            candles = data.get("candles", [])
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
        log_to_console("fetch_history_schwab", e)
    return pd.DataFrame()

@st.cache_data(ttl=15)
def fetch_option_chain_schwab(symbol, strikes_count):
    try:
        today = datetime.now()
        resp = client.get_option_chain(
            symbol=symbol,
            contract_type=client.Options.ContractType.ALL,
            strike_count=strikes_count,
            from_date=today,
            to_date=today + timedelta(days=7)
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        log_to_console("fetch_option_chain_schwab", e)
    return {}

@st.cache_data(ttl=15)
def fetch_nq_price_schwab():
    try:
        resp = client.get_quote("/NQ")
        if resp.status_code == 200:
            data = resp.json()
            quote_data = data.get("/NQ", {}).get("quote", {})
            price = float(quote_data.get("lastPrice", quote_data.get("closePrice", 0.0)))
            if price > 0:
                return price
    except Exception as e:
        log_to_console("fetch_nq_price_schwab", e)
    return 0.0

# --- PROCESAMIENTO DE DATOS ---
now_tz = pd.Timestamp.now(tz=tz_target)
ref_today = now_tz.floor('D').tz_localize(None)

hist_raw = fetch_history_schwab(ticker_symbol)
chain_raw = fetch_option_chain_schwab(ticker_symbol, strike_range)

spot_price = float(chain_raw.get("underlyingPrice", 0.0))
if (spot_price <= 0 or np.isnan(spot_price)) and not hist_raw.empty and 'Close' in hist_raw:
    spot_price = float(hist_raw['Close'].dropna().iloc[-1])

if spot_price <= 0 or np.isnan(spot_price):
    log_to_console("Spot Price Error", f"No se pudo determinar el precio spot para {ticker_symbol}")
    st.error("Ocurrió un error, revisa la consola")
    st.stop()

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

def parse_schwab_chain(chain_data):
    call_map = chain_data.get('callExpDateMap', {})
    put_map = chain_data.get('putExpDateMap', {})
    
    all_exp_keys = sorted(list(set(list(call_map.keys()) + list(put_map.keys()))))
    if not all_exp_keys:
        return pd.DataFrame(), None
    
    selected_exp = all_exp_keys[0]
    calls_for_exp = call_map.get(selected_exp, {})
    puts_for_exp = put_map.get(selected_exp, {})
    
    records = {}
    
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
                'rho_c': 0.0, 'rho_p': 0.0,
                'iv_c': 0.0, 'iv_p': 0.0
            }
        records[strike]['openInterest_c'] = opt.get('openInterest', 0)
        records[strike]['gamma_c'] = opt.get('gamma', 0.0)
        records[strike]['delta_c'] = opt.get('delta', 0.0)
        records[strike]['theta_c'] = opt.get('theta', 0.0)
        records[strike]['vega_c'] = opt.get('vega', 0.0)
        records[strike]['rho_c'] = opt.get('rho', 0.0)
        vol = opt.get('volatility', opt.get('impliedVolatility', 0.0))
        records[strike]['iv_c'] = vol / 100.0 if vol > 2 else vol

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
                'rho_c': 0.0, 'rho_p': 0.0,
                'iv_c': 0.0, 'iv_p': 0.0
            }
        records[strike]['openInterest_p'] = opt.get('openInterest', 0)
        records[strike]['gamma_p'] = opt.get('gamma', 0.0)
        records[strike]['delta_p'] = opt.get('delta', 0.0)
        records[strike]['theta_p'] = opt.get('theta', 0.0)
        records[strike]['vega_p'] = opt.get('vega', 0.0)
        records[strike]['rho_p'] = opt.get('rho', 0.0)
        vol = opt.get('volatility', opt.get('impliedVolatility', 0.0))
        records[strike]['iv_p'] = vol / 100.0 if vol > 2 else vol

    df = pd.DataFrame(list(records.values())).sort_values('strike').reset_index(drop=True)
    return df, selected_exp

df_curr, exp_0dte = parse_schwab_chain(chain_raw)

def fmt_val(val, show_sign=True):
    sign = ("+" if val > 0 else "") if show_sign else ""
    if abs(val) >= 1e9:
        return f"{sign}${val/1e9:.2f}B"
    elif abs(val) >= 1e6:
        return f"{sign}${val/1e6:.1f}M"
    elif abs(val) >= 1e3:
        return f"{sign}${val/1e3:.1f}K"
    else:
        return f"{sign}${val:.1f}"

# --- FUNCIÓN DE RECALCULO DINÁMICO DE GAMMA POR PRECIO SPOT ---
def recalculate_gex_for_spot(df_input, spot_t, T_exp, iv):
    if df_input.empty or spot_t <= 0:
        return df_input
    
    df_out = df_input.copy()
    
    def calc_gamma_dyn(r):
        K = r['strike']
        d1 = (np.log(spot_t / K) + (0.045 + 0.5 * iv**2) * T_exp) / (iv * np.sqrt(T_exp))
        return norm.pdf(d1) / (spot_t * iv * np.sqrt(T_exp))

    df_out['gamma'] = df_out.apply(calc_gamma_dyn, axis=1)
    df_out['call_gex'] = df_out['gamma'] * df_out['openInterest_c'] * (spot_t ** 2) * 0.01
    df_out['put_gex'] = df_out['gamma'] * df_out['openInterest_p'] * (spot_t ** 2) * (-0.01)
    df_out['net_gex'] = df_out['call_gex'] + df_out['put_gex']
    return df_out

# --- CÁLCULOS CUÁNTICOS DE OPCIONES ---
if not df_curr.empty:
    exp_date_part = exp_0dte.split(':')[0] if ':' in exp_0dte else exp_0dte
    exp_dt = pd.to_datetime(exp_date_part).tz_localize(None)
    days_to_exp = max((exp_dt - ref_today).days, 0)
    T_exp = max(days_to_exp / 365.0, 0.5 / 365.0)

    near_atm = df_curr[abs(df_curr['strike'] - spot_price) <= (spot_price * 0.015)]
    valid_ivs = []
    if not near_atm.empty:
        for _, r in near_atm.iterrows():
            if 0.05 < r['iv_c'] < 2.0: valid_ivs.append(r['iv_c'])
            if 0.05 < r['iv_p'] < 2.0: valid_ivs.append(r['iv_p'])
    atm_iv = float(np.median(valid_ivs)) if len(valid_ivs) > 0 else 0.20
    atm_iv = max(atm_iv, 0.12)

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

    df_curr['call_rex'] = df_curr['rho_c'] * df_curr['openInterest_c'] * 100
    df_curr['put_rex'] = df_curr['rho_p'] * df_curr['openInterest_p'] * 100
    df_curr['net_rex'] = df_curr['call_rex'] + df_curr['put_rex']

    calls_dominant = df_curr[df_curr['net_gex'] > 0].sort_values('net_gex', ascending=False)
    top_calls = calls_dominant['strike'].tolist()
    cw1 = top_calls[0] if len(top_calls) > 0 else spot_price
    cw2 = top_calls[1] if len(top_calls) > 1 else cw1
    cw3 = top_calls[2] if len(top_calls) > 2 else cw2

    puts_dominant = df_curr[df_curr['net_gex'] < 0].sort_values('net_gex', ascending=True)
    top_puts = puts_dominant['strike'].tolist()
    pw1 = top_puts[0] if len(top_puts) > 0 else spot_price
    pw2 = top_puts[1] if len(top_puts) > 1 else pw1
    pw3 = top_puts[2] if len(top_puts) > 2 else pw2

    df_curr['cum_gex'] = df_curr['net_gex'].cumsum()
    zero_gamma_idx = (df_curr['cum_gex'].abs()).idxmin()
    zero_gamma = df_curr.loc[zero_gamma_idx]['strike'] if zero_gamma_idx in df_curr.index else spot_price

    net_gex_total = float(df_curr['net_gex'].sum())
    call_gex_sum = float(df_curr['call_gex'].sum())
    put_gex_sum = float(df_curr['put_gex'].sum())
    total_gex = float((df_curr['call_gex'].abs() + df_curr['put_gex'].abs()).sum())
    
    call_oi_sum = int(df_curr['openInterest_c'].sum())
    put_oi_sum = int(df_curr['openInterest_p'].sum())
    total_oi_sum = call_oi_sum + put_oi_sum

    regime_str = "positive regime" if net_gex_total >= 0 else "negative regime"
    condition_str = "Positive – dealers long gamma, hedging dampens volatility (mean-reverting)" if net_gex_total >= 0 else "Negative – dealers short gamma, hedging amplifies trending behavior"
    iv_str = f"{atm_iv * 100:.2f}%"
    iv_rank_str = f"{int(min(max((atm_iv / 0.35) * 100, 15), 85))}th percentile (moderate volatility environment)"
else:
    df_curr = pd.DataFrame()
    cw1, cw2, cw3 = spot_price, spot_price, spot_price
    pw1, pw2, pw3 = spot_price, spot_price, spot_price
    zero_gamma = spot_price
    atm_iv = 0.20
    net_gex_total = 0.0
    call_gex_sum, put_gex_sum, total_gex = 0.0, 0.0, 0.0
    call_oi_sum, put_oi_sum, total_oi_sum = 0, 0, 0
    regime_str = "neutral regime"
    condition_str = "Neutral"
    iv_str = "20.00%"
    iv_rank_str = "N/A"

# --- ASISTENTE IA CON CAPTURA SILENCIOSA DE ERRORES ---
@st.cache_data(ttl=1800, show_spinner=False)
def consultar_ia_cache(tipo_analisis, mensaje_usuario, ticker_symbol, spot_price, net_gex_total, regime_str, call_gex_sum, put_gex_sum, total_gex, cw1, cw2, cw3, pw1, pw2, pw3, zero_gamma, iv_str, condition_str):
    system_prompt = f"""
    Eres un analista cuantitativo experto en estructura de opciones, Gamma Exposure (GEX) y microestructura de mercado.
    Responde en español de forma analítica, directa y profesional usando viñetas.

    Métricas actuales del mercado ({ticker_symbol}):
    - Spot Price: ${spot_price:.2f}
    - Net GEX Total: {fmt_val(net_gex_total)} ({regime_str})
    - Call GEX: {fmt_val(call_gex_sum)} | Put GEX: {fmt_val(put_gex_sum)} | Total GEX: {fmt_val(total_gex, show_sign=False)}
    - Call Walls: CW1=${cw1:.0f}, CW2=${cw2:.0f}, CW3=${cw3:.0f}
    - Put Walls: PW1=${pw1:.0f}, PW2=${pw2:.0f}, PW3=${pw3:.0f}
    - Zero Gamma (Flip Level): ${zero_gamma:.2f}
    - Volatilidad Implícita (ATM): {iv_str}
    - Condición de Mercado: {condition_str}
    """

    prompt_final = mensaje_usuario or f"Proporciona un diagnóstico estratégico del mercado enfocándote en {tipo_analisis}."

    # 1. Intentar con Groq
    if GROQ_API_KEY:
        try:
            return query_groq(system_prompt, prompt_final, GROQ_API_KEY)
        except Exception as e_groq:
            log_to_console("Groq AI Engine", e_groq)

    # 2. Respaldo con Gemini
    if ai_client:
        try:
            response = ai_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=f"{system_prompt}\n\nPregunta del usuario: {prompt_final}"
            )
            return response.text
        except Exception as e_gemini:
            log_to_console("Gemini AI Engine", e_gemini)

    return "Ocurrió un error, revisa la consola"

def consultar_ia(tipo_analisis=None, mensaje_usuario=None):
    return consultar_ia_cache(
        tipo_analisis, mensaje_usuario, ticker_symbol, spot_price,
        net_gex_total, regime_str, call_gex_sum, put_gex_sum, total_gex,
        cw1, cw2, cw3, pw1, pw2, pw3, zero_gamma, iv_str, condition_str
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
            st.rerun()

    st.caption("Diagnóstico en vivo del mercado según perfiles GEX")

    col_btn1, col_btn2 = st.columns(2)
    if col_btn1.button("📊 Pre-Market", key="btn_ai_premarket", use_container_width=True):
        with st.spinner("Analizando pre-market..."):
            res = consultar_ia(tipo_analisis="Pre-Market")
            st.session_state.chat_messages.append({"role": "assistant", "content": res})

    if col_btn2.button("📈 Intradía", key="btn_ai_intraday", use_container_width=True):
        with st.spinner("Analizando flujo intradía..."):
            res = consultar_ia(tipo_analisis="Mercado Intradía")
            st.session_state.chat_messages.append({"role": "assistant", "content": res})

    st.markdown("---")

    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    if chat_input := st.chat_input("Escribe tu pregunta sobre GEX..."):
        st.session_state.chat_messages.append({"role": "user", "content": chat_input})
        with st.chat_message("user"):
            st.write(chat_input)

        with st.spinner("Pensando..."):
            respuesta_bot = consultar_ia(mensaje_usuario=chat_input)
            st.session_state.chat_messages.append({"role": "assistant", "content": respuesta_bot})
            with st.chat_message("assistant"):
                st.write(respuesta_bot)

# --- PANEL DE MÉTRICAS TOP ---
cw_diff = ((cw1 - spot_price) / spot_price * 100) if spot_price > 0 else 0
pw_diff = ((pw1 - spot_price) / spot_price * 100) if spot_price > 0 else 0
zg_diff = ((zero_gamma - spot_price) / spot_price * 100) if spot_price > 0 else 0

k1, k2, k3, k4, k5, k6, k7, k8 = st.columns(8)
k1.markdown(f'<div class="metric-card"><div class="metric-label">Spot Price</div><div class="metric-value">${spot_price:.2f}</div><div class="metric-sub">{ticker_symbol}</div></div>', unsafe_allow_html=True)
k2.markdown(f'<div class="metric-card"><div class="metric-label">Net GEX</div><div class="metric-value" style="color:{"#10B981" if net_gex_total >= 0 else "#EF4444"};">{fmt_val(net_gex_total)}</div><div class="metric-sub">Ratio: {abs(call_gex_sum/put_gex_sum):.2f}</div></div>', unsafe_allow_html=True)
k3.markdown(f'<div class="metric-card"><div class="metric-label">Call GEX</div><div class="metric-value" style="color:#10B981">{fmt_val(call_gex_sum)}</div><div class="metric-sub">{call_oi_sum:,} OI</div></div>', unsafe_allow_html=True)
k4.markdown(f'<div class="metric-card"><div class="metric-label">Put GEX</div><div class="metric-value" style="color:#EF4444">{fmt_val(put_gex_sum)}</div><div class="metric-sub">{put_oi_sum:,} OI</div></div>', unsafe_allow_html=True)
k5.markdown(f'<div class="metric-card"><div class="metric-label">Total GEX</div><div class="metric-value" style="color:#3B82F6">{fmt_val(total_gex, show_sign=False)}</div><div class="metric-sub">{total_oi_sum:,} OI</div></div>', unsafe_allow_html=True)
k6.markdown(f'<div class="metric-card"><div class="metric-label">Call Wall</div><div class="metric-value" style="color:#10B981">${cw1:.0f}</div><div class="metric-sub">{cw_diff:+.2f}%</div></div>', unsafe_allow_html=True)
k7.markdown(f'<div class="metric-card"><div class="metric-label">Put Wall</div><div class="metric-value" style="color:#EF4444">${pw1:.0f}</div><div class="metric-sub">{pw_diff:+.2f}%</div></div>', unsafe_allow_html=True)
k8.markdown(f'<div class="metric-card"><div class="metric-label">Zero Gamma</div><div class="metric-value" style="color:#F59E0B">${zero_gamma:.2f}</div><div class="metric-sub">{zg_diff:+.2f}%</div></div>', unsafe_allow_html=True)

st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)

# --- EXPORTACIÓN A NUBE (JSONBIN) ---
if JSONBIN_BIN_ID and JSONBIN_API_KEY:
    try:
        url = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"
        headers = {
            'Content-Type': 'application/json',
            'X-Master-Key': JSONBIN_API_KEY
        }
        export_payload = {
            "qqq_spot": float(spot_price),
            "conversion_ratio": float(conversion_ratio),
            "metrics": {
                "net_gex": net_gex_total,
                "net_gex_regime": regime_str,
                "total_gex": total_gex,
                "gamma_condition": condition_str,
                "iv_atm": iv_str,
                "iv_rank": iv_rank_str
            },
            "cw1": float(cw1), "cw2": float(cw2), "cw3": float(cw3),
            "pw1": float(pw1), "pw2": float(pw2), "pw3": float(pw3),
            "levels": df_curr[['strike', 'net_gex']].to_dict(orient='records') if not df_curr.empty else []
        }
        requests.put(url, json=export_payload, headers=headers, timeout=5)
    except Exception as e:
        log_to_console("JSONBin Export Error", e)

min_strike = int(np.floor(spot_price - strike_range))
max_strike = int(np.ceil(spot_price + strike_range))

# --- PESTAÑAS PRINCIPALES ---
tab_gex, tab_live, tab_back, tab_data, tab_greeks, tab_3d = st.tabs([
    "GEX INFO",
    "LIVE GAMMA",
    "BACKGAMMA",
    "DATA",
    "GREEKS",
    "SURFACE 3D"
])

# --- 1. GEX INFO ---
with tab_gex:
    sub_gex1, sub_gex2 = st.tabs(["NET GEX PROFILE", "CALLS vs PUTS"])
    
    with sub_gex1:
        if not df_curr.empty:
            df_sub = df_curr[(df_curr['strike'] >= min_strike) & (df_curr['strike'] <= max_strike)].copy()
            colors = ['#10B981' if v >= 0 else '#EF4444' for v in df_sub['net_gex']]
            
            x_min_raw, x_max_raw = df_sub['strike'].min(), df_sub['strike'].max()
            x_mid = (x_min_raw + x_max_raw) / 2.0
            x_half_span = ((x_max_raw - x_min_raw) / 2.0) + 1.0
            x_min_val = x_mid - (x_half_span * 2.0)
            x_max_val = x_mid + (x_half_span * 2.0)

            y_max_val = df_sub['net_gex'].max()
            y_min_val = df_sub['net_gex'].min()
            y_max_adj = (max(y_max_val, 0) * 1.8) if y_max_val > 0 else 1000
            y_min_adj = (min(y_min_val, 0) * 1.8) if y_min_val < 0 else -1000

            fig1 = go.Figure()
            fig1.add_trace(go.Bar(
                x=df_sub['strike'],
                y=df_sub['net_gex'],
                orientation='v',
                marker_color=colors,
                hovertemplate="<b>Strike:</b> $%{x:.2f}<br><b>Net GEX:</b> %{customdata}<extra></extra>",
                customdata=[fmt_val(v) for v in df_sub['net_gex']]
            ))
            
            fig1.add_vline(
                x=spot_price,
                line_color="#3B82F6",
                line_width=1.5,
                line_dash="dash",
                annotation_text="Spot",
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
                    range=[x_min_val, x_max_val]
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
        if not df_curr.empty:
            df_sub = df_curr[(df_curr['strike'] >= min_strike) & (df_curr['strike'] <= max_strike)].copy()
            
            x_min_raw, x_max_raw = df_sub['strike'].min(), df_sub['strike'].max()
            x_mid = (x_min_raw + x_max_raw) / 2.0
            x_half_span = ((x_max_raw - x_min_raw) / 2.0) + 1.0
            x_min_val = x_mid - (x_half_span * 2.0)
            x_max_val = x_mid + (x_half_span * 2.0)

            y_max_val = max(df_sub['call_gex'].max(), 0)
            y_min_val = min(df_sub['put_gex'].min(), 0)
            y_max_adj = (y_max_val * 1.8) if y_max_val > 0 else 1000
            y_min_adj = (y_min_val * 1.8) if y_min_val < 0 else -1000

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
            
            fig2.add_vline(
                x=spot_price,
                line_color="#3B82F6",
                line_width=1.5,
                line_dash="dash",
                annotation_text="Spot",
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
                    range=[x_min_val, x_max_val]
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

# --- PREPARACIÓN TEMPORAL (INTRADAY HISTORIA) ---
h_1m = fetch_history_schwab(ticker_symbol)

if not h_1m.empty:
    h_1m = h_1m.tz_convert(tz_target)
    today_date_str = now_tz.strftime('%Y-%m-%d')
    h_1m_today = h_1m[h_1m.index.strftime('%Y-%m-%d') == today_date_str].copy()
    
    if h_1m_today.empty:
        today_date_str = h_1m.index.max().strftime('%Y-%m-%d')
        h_1m_today = h_1m[h_1m.index.strftime('%Y-%m-%d') == today_date_str].copy()

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
    
    h_1m_today = h_1m_today[~h_1m_today.index.duplicated(keep='last')]
    h_1m_reindexed = h_1m_today.reindex(full_time_grid)
    
    spot_series = h_1m_reindexed['Close'].ffill().bfill()
    full_spots = spot_series.fillna(spot_price).tolist()
else:
    full_timestamps = []
    full_spots = []
    h_1m_reindexed = pd.DataFrame()

fine_strikes = np.linspace(min_strike, max_strike, int((max_strike - min_strike) * 10 + 1))
Z_matrix_real = np.zeros((len(fine_strikes), len(full_timestamps))) if len(full_timestamps) > 0 else np.zeros((0,0))

if not df_curr.empty and len(full_timestamps) > 0:
    exp_date_part = exp_0dte.split(':')[0] if ':' in exp_0dte else exp_0dte
    exp_dt = pd.to_datetime(exp_date_part).tz_localize(None)
    days_to_exp = max((exp_dt - ref_today).days, 0)
    T_exp = max(days_to_exp / 365.0, 0.5 / 365.0)

    sigma_k = 0.32

    for t_idx, S_t in enumerate(full_spots):
        if S_t <= 0 or np.isnan(S_t): continue
        for _, r in df_curr.iterrows():
            K = r['strike']
            if K < min_strike - 1 or K > max_strike + 1: continue
            net_oi = r['openInterest_c'] - r['openInterest_p']
            if net_oi == 0: continue

            d1_t = (np.log(S_t / K) + (0.045 + 0.5 * atm_iv**2) * T_exp) / (atm_iv * np.sqrt(T_exp))
            gamma_t = norm.pdf(d1_t) / (S_t * atm_iv * np.sqrt(T_exp))
            net_gex_t = net_oi * gamma_t * (S_t ** 2) * 0.01

            gauss_weight = np.exp(-0.5 * ((fine_strikes - K) / sigma_k) ** 2)
            Z_matrix_real[:, t_idx] += gauss_weight * net_gex_t

    if Z_matrix_real.size > 0 and Z_matrix_real.shape[1] > 1:
        Z_matrix_real = gaussian_filter(Z_matrix_real, sigma=(0.8, 1.4))

# --- 2. LIVE GAMMA (TIEMPO REAL PURO) ---
with tab_live:
    st.markdown('<div class="depth-frame">', unsafe_allow_html=True)
    st.markdown(f"<h3 style='margin-top:0; font-weight:700; color:#F0F6FC; font-size:1.1rem;'>🌊 Real-Time Gamma Flow ({tz_choice})</h3>", unsafe_allow_html=True)

    if len(full_timestamps) > 0 and Z_matrix_real.shape[1] > 0:
        custom_hover_matrix = [[fmt_val(val) for val in row] for row in Z_matrix_real]
        max_real_abs = float(np.max(np.abs(Z_matrix_real))) if np.max(np.abs(Z_matrix_real)) > 0 else 1.0
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
            margin=dict(l=80, r=60, t=40, b=40), yaxis=dict(dtick=1, side='right'),
            hoverlabel=dict(bgcolor="#161B22", bordercolor="#30363D", font_size=12, font_family="JetBrains Mono", namelength=0)
        )

        st.plotly_chart(fig_live, use_container_width=True, config={'scrollZoom': True}, key="heatmap_live")
    else:
        st.info("Esperando actualización de datos intradía...")

    st.markdown('</div>', unsafe_allow_html=True)

# --- 3. BACKGAMMA (EXCLUSIVA PARA BACKTEST) ---
with tab_back:
    if "backtest_shift" not in st.session_state:
        st.session_state.backtest_shift = 0

    st.markdown('<div class="backtest-controls">', unsafe_allow_html=True)
    st.markdown("<p style='font-family:\"JetBrains Mono\"; font-size:0.80rem; font-weight:800; color:#F0F6FC; letter-spacing:1px; margin-bottom:10px;'>⏮️ REPRODUCCIÓN & BACKTESTING DE GAMMA</p>", unsafe_allow_html=True)
    
    col_bt1, col_bt2, col_bt3 = st.columns([2, 1, 1])
    with col_bt1:
        bt_tf = st.selectbox("INTERVALO DE SALTO (MINUTOS)", [1, 5, 15, 30, 60], index=0, key="bt_tf_select")
    with col_bt2:
        st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)
        if st.button("◀ Back", use_container_width=True, key="bt_back_btn"):
            st.session_state.backtest_shift -= bt_tf
    with col_bt3:
        st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)
        if st.button("Next ▶", use_container_width=True, key="bt_next_btn"):
            st.session_state.backtest_shift += bt_tf
            if st.session_state.backtest_shift > 0:
                st.session_state.backtest_shift = 0

    st.markdown('</div>', unsafe_allow_html=True)

    total_len = len(full_timestamps)
    if total_len > 0:
        shift_idx = st.session_state.backtest_shift
        target_len = max(1, min(total_len, total_len + shift_idx))
        
        bt_timestamps = full_timestamps[:target_len]
        bt_h_1m = h_1m_reindexed.iloc[:target_len] if not h_1m_reindexed.empty else pd.DataFrame()
        bt_Z_matrix = Z_matrix_real[:, :target_len] if Z_matrix_real.shape[1] > 0 else Z_matrix_real
    else:
        bt_timestamps = full_timestamps
        bt_h_1m = h_1m_reindexed
        bt_Z_matrix = Z_matrix_real

    st.markdown('<div class="depth-frame">', unsafe_allow_html=True)
    
    if len(bt_timestamps) > 0:
        current_bt_time = bt_timestamps[-1]
        st.markdown(f"<p style='font-family:\"JetBrains Mono\"; color:#60A5FA; font-size:0.85rem; font-weight:700;'>⏱️ ESTADO DE BACKTEST | Hora Simulada: <span style='color:#F0F6FC;'>{current_bt_time}</span> (Desplazamiento: {st.session_state.backtest_shift} min)</p>", unsafe_allow_html=True)

    if len(bt_timestamps) > 0 and bt_Z_matrix.shape[1] > 0:
        custom_hover_matrix = [[fmt_val(val) for val in row] for row in bt_Z_matrix]
        max_real_abs = float(np.max(np.abs(bt_Z_matrix))) if np.max(np.abs(bt_Z_matrix)) > 0 else 1.0
        Z_matrix_scaled = bt_Z_matrix / max_real_abs

        fig_back = go.Figure()

        fig_back.add_trace(go.Heatmap(
            x=bt_timestamps,
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
                fig_back.add_hline(y=k_val, line_color=color, line_width=0.8, line_dash=dash, layer="above")
            labels_str = " / ".join([item[0] for item in items])
            badge_text = f"<b>{labels_str}</b> (${k_val:.0f})"
            main_color = items[0][1]
            fig_back.add_annotation(
                x=0.988, xref="paper", y=k_val, yref="y", text=badge_text, showarrow=False,
                xanchor="right", yanchor="middle", font=dict(family="JetBrains Mono", size=10, color=main_color),
                bgcolor="#090D16", bordercolor=main_color, borderwidth=1, borderpad=3, opacity=0.95
            )

        if not bt_h_1m.empty:
            fig_back.add_trace(go.Candlestick(
                x=bt_timestamps,
                open=bt_h_1m['Open'],
                high=bt_h_1m['High'],
                low=bt_h_1m['Low'],
                close=bt_h_1m['Close'],
                name="Spot Price",
                increasing_line_color='#10B981',
                decreasing_line_color='#EF4444',
                increasing_fillcolor='#10B981',
                decreasing_fillcolor='#EF4444',
                hovertemplate="<b>Hora:</b> %{x}<br><b>Apertura:</b> $%{open:.2f}<br><b>Máximo:</b> $%{high:.2f}<br><b>Mínimo:</b> $%{low:.2f}<br><b>Cierre:</b> $%{close:.2f}<extra></extra>"
            ))

        fig_back.update_layout(
            template="plotly_dark", plot_bgcolor='#06080D', paper_bgcolor='#06080D',
            uirevision="static_user_state",
            xaxis_title=f"Hora Intradía Rebobinada ({tz_choice.split(' ')[0]})", yaxis_title="Precio / Strike ($)",
            height=680, dragmode='pan', hovermode="closest", xaxis_rangeslider_visible=False,
            margin=dict(l=80, r=60, t=40, b=40), yaxis=dict(dtick=1, side='right'),
            hoverlabel=dict(bgcolor="#161B22", bordercolor="#30363D", font_size=12, font_family="JetBrains Mono", namelength=0)
        )

        st.plotly_chart(fig_back, use_container_width=True, config={'scrollZoom': True}, key="heatmap_backtest")

        if not df_curr.empty:
            st.markdown("<hr style='border-color:rgba(255,255,255,0.08); margin: 25px 0;'>", unsafe_allow_html=True)
            
            bt_spot_val = bt_h_1m['Close'].dropna().iloc[-1] if (not bt_h_1m.empty and 'Close' in bt_h_1m and len(bt_h_1m['Close'].dropna()) > 0) else spot_price

            df_curr_bt = recalculate_gex_for_spot(df_curr, bt_spot_val, T_exp, atm_iv)

            df_sub_bt = df_curr_bt[(df_curr_bt['strike'] >= min_strike) & (df_curr_bt['strike'] <= max_strike)].copy()
            colors_bt = ['#10B981' if v >= 0 else '#EF4444' for v in df_sub_bt['net_gex']]

            x_min_raw, x_max_raw = df_sub_bt['strike'].min(), df_sub_bt['strike'].max()
            x_mid = (x_min_raw + x_max_raw) / 2.0
            x_half_span = ((x_max_raw - x_min_raw) / 2.0) + 1.0
            x_min_val = x_mid - (x_half_span * 2.0)
            x_max_val = x_mid + (x_half_span * 2.0)

            y_max_val = df_sub_bt['net_gex'].max()
            y_min_val = df_sub_bt['net_gex'].min()
            y_max_adj = (max(y_max_val, 0) * 1.8) if y_max_val > 0 else 1000
            y_min_adj = (min(y_min_val, 0) * 1.8) if y_min_val < 0 else -1000

            fig_bt_profile = go.Figure()
            fig_bt_profile.add_trace(go.Bar(
                x=df_sub_bt['strike'],
                y=df_sub_bt['net_gex'],
                orientation='v',
                marker_color=colors_bt,
                hovertemplate="<b>Strike:</b> $%{x:.2f}<br><b>Net GEX Recomputado:</b> %{customdata}<extra></extra>",
                customdata=[fmt_val(v) for v in df_sub_bt['net_gex']]
            ))

            fig_bt_profile.add_vline(
                x=bt_spot_val,
                line_color="#3B82F6",
                line_width=1.5,
                line_dash="dash",
                annotation_text=f"Spot: ${bt_spot_val:.2f}",
                annotation_position="top",
                annotation_font=dict(color="#60A5FA", size=11, family="JetBrains Mono")
            )

            fig_bt_profile.update_layout(
                template="plotly_dark",
                plot_bgcolor='#06080D',
                paper_bgcolor='#06080D',
                title=dict(
                    text=f"<b>Strike Profile Dynamic (Net Gamma Exposure @ ${bt_spot_val:.2f})</b>",
                    font=dict(family="Plus Jakarta Sans", size=15, color="#F0F6FC")
                ),
                xaxis=dict(
                    title="Strike ($)",
                    gridcolor="rgba(255,255,255,0.05)",
                    tickfont=dict(family="JetBrains Mono", color="#8B949E"),
                    zeroline=False,
                    range=[x_min_val, x_max_val]
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
                height=500,
                margin=dict(l=50, r=40, t=50, b=40)
            )
            st.plotly_chart(fig_bt_profile, use_container_width=True, key="profile_backtest")
    else:
        st.info("Sin datos para la simulación.")

    st.markdown('</div>', unsafe_allow_html=True)

# --- 4. DATA ---
with tab_data:
    sub_dt1, sub_dt2 = st.tabs(["TODAY'S DATA", "DATA GRID"])
    
    with sub_dt1:
        st.markdown(f"""
        <div class="depth-frame">
            <h3 style="margin-top:0; margin-bottom: 16px; color: #F0F6FC; font-size: 1.1rem; font-weight: 700;">Gamma Regime & Volatility Summary</h3>
            <ul style="list-style-type: disc; margin: 0; padding-left: 20px; color: #C9D1D9; font-size: 0.90rem; line-height: 2.1; font-family: 'JetBrains Mono', monospace;">
                <li><b>Net GEX:</b> <span style="color: {'#10B981' if net_gex_total >= 0 else '#EF4444'}; font-weight: 700;">{fmt_val(net_gex_total)}</span> ({regime_str})</li>
                <li><b>Total GEX:</b> <span style="color: #F0F6FC; font-weight: 700;">{fmt_val(total_gex, show_sign=False)}</span></li>
                <li><b>Gamma Condition:</b> <span style="color: #8B949E;">{condition_str}</span></li>
                <li><b>IV (ATM / 0DTE):</b> <span style="color: #60A5FA; font-weight: 700;">{iv_str}</span></li>
                <li><b>IV Rank:</b> <span style="color: #8B949E;">{iv_rank_str}</span></li>
            </ul>
        </div>
        """, unsafe_allow_html=True)

    with sub_dt2:
        if not df_curr.empty:
            st.dataframe(
                df_curr[['strike', 'openInterest_c', 'openInterest_p', 'iv_c', 'iv_p', 'net_gex', 'net_dex', 'net_tex', 'net_vex', 'net_rex']],
                use_container_width=True,
                height=600
            )

# --- 5. GREEKS ---
with tab_greeks:
    sub_dex, sub_tex, sub_vex, sub_rex = st.tabs([
        "DELTA EXPOSURE (DEX)",
        "THETA EXPOSURE (TEX)",
        "VEGA EXPOSURE (VEX)",
        "RHO EXPOSURE (REX)"
    ])
    
    with sub_dex:
        if not df_curr.empty:
            df_sub = df_curr[(df_curr['strike'] >= min_strike) & (df_curr['strike'] <= max_strike)].copy()
            x_min_raw, x_max_raw = df_sub['strike'].min(), df_sub['strike'].max()
            x_mid = (x_min_raw + x_max_raw) / 2.0
            x_half_span = ((x_max_raw - x_min_raw) / 2.0) + 1.0
            x_min_val, x_max_val = x_mid - (x_half_span * 2.0), x_mid + (x_half_span * 2.0)

            fig_dex = go.Figure()
            fig_dex.add_trace(go.Bar(
                x=df_sub['strike'], y=df_sub['net_dex'],
                name="Net DEX ($M)", marker_color='#3B82F6',
                hovertemplate="<b>Strike:</b> $%{x:.2f}<br><b>Net DEX:</b> $%{y:.2f}M<extra></extra>"
            ))
            fig_dex.add_vline(x=spot_price, line_color="#3B82F6", line_width=1.5, line_dash="dash", annotation_text="Spot", annotation_position="top")

            fig_dex.update_layout(
                template="plotly_dark", plot_bgcolor='#06080D', paper_bgcolor='#06080D',
                title="Delta Exposure Total (DEX) por Strike ($ Millones)",
                xaxis=dict(title="Strike ($)", gridcolor="rgba(255,255,255,0.05)", range=[x_min_val, x_max_val]),
                yaxis=dict(title="DEX ($ Millones)", gridcolor="rgba(255,255,255,0.05)"),
                height=560, margin=dict(l=50, r=40, t=50, b=40)
            )
            st.plotly_chart(fig_dex, use_container_width=True)

    with sub_tex:
        if not df_curr.empty:
            df_sub = df_curr[(df_curr['strike'] >= min_strike) & (df_curr['strike'] <= max_strike)].copy()
            x_min_raw, x_max_raw = df_sub['strike'].min(), df_sub['strike'].max()
            x_mid = (x_min_raw + x_max_raw) / 2.0
            x_half_span = ((x_max_raw - x_min_raw) / 2.0) + 1.0
            x_min_val, x_max_val = x_mid - (x_half_span * 2.0), x_mid + (x_half_span * 2.0)

            fig_tex = go.Figure()
            fig_tex.add_trace(go.Bar(
                x=df_sub['strike'], y=df_sub['net_tex'],
                name="Net TEX ($)", marker_color='#F59E0B',
                hovertemplate="<b>Strike:</b> $%{x:.2f}<br><b>Net TEX:</b> $%{y:,.0f}<extra></extra>"
            ))
            fig_tex.add_vline(x=spot_price, line_color="#3B82F6", line_width=1.5, line_dash="dash", annotation_text="Spot", annotation_position="top")

            fig_tex.update_layout(
                template="plotly_dark", plot_bgcolor='#06080D', paper_bgcolor='#06080D',
                title="Theta Exposure Total (TEX - Pérdida por Decaimiento Temporal $/día)",
                xaxis=dict(title="Strike ($)", gridcolor="rgba(255,255,255,0.05)", range=[x_min_val, x_max_val]),
                yaxis=dict(title="TEX ($/día)", gridcolor="rgba(255,255,255,0.05)"),
                height=560, margin=dict(l=50, r=40, t=50, b=40)
            )
            st.plotly_chart(fig_tex, use_container_width=True)

    with sub_vex:
        if not df_curr.empty:
            df_sub = df_curr[(df_curr['strike'] >= min_strike) & (df_curr['strike'] <= max_strike)].copy()
            x_min_raw, x_max_raw = df_sub['strike'].min(), df_sub['strike'].max()
            x_mid = (x_min_raw + x_max_raw) / 2.0
            x_half_span = ((x_max_raw - x_min_raw) / 2.0) + 1.0
            x_min_val, x_max_val = x_mid - (x_half_span * 2.0), x_mid + (x_half_span * 2.0)

            fig_vex = go.Figure()
            fig_vex.add_trace(go.Bar(
                x=df_sub['strike'], y=df_sub['net_vex'],
                name="Net VEX ($)", marker_color='#8B5CF6',
                hovertemplate="<b>Strike:</b> $%{x:.2f}<br><b>Net VEX:</b> $%{y:,.0f}<extra></extra>"
            ))
            fig_vex.add_vline(x=spot_price, line_color="#3B82F6", line_width=1.5, line_dash="dash", annotation_text="Spot", annotation_position="top")

            fig_vex.update_layout(
                template="plotly_dark", plot_bgcolor='#06080D', paper_bgcolor='#06080D',
                title="Vega Exposure Total (VEX - Sensibilidad $/1% Cambio en IV)",
                xaxis=dict(title="Strike ($)", gridcolor="rgba(255,255,255,0.05)", range=[x_min_val, x_max_val]),
                yaxis=dict(title="VEX ($/1% IV)", gridcolor="rgba(255,255,255,0.05)"),
                height=560, margin=dict(l=50, r=40, t=50, b=40)
            )
            st.plotly_chart(fig_vex, use_container_width=True)

    with sub_rex:
        if not df_curr.empty:
            df_sub = df_curr[(df_curr['strike'] >= min_strike) & (df_curr['strike'] <= max_strike)].copy()
            x_min_raw, x_max_raw = df_sub['strike'].min(), df_sub['strike'].max()
            x_mid = (x_min_raw + x_max_raw) / 2.0
            x_half_span = ((x_max_raw - x_min_raw) / 2.0) + 1.0
            x_min_val, x_max_val = x_mid - (x_half_span * 2.0), x_mid + (x_half_span * 2.0)

            fig_rex = go.Figure()
            fig_rex.add_trace(go.Bar(
                x=df_sub['strike'], y=df_sub['net_rex'],
                name="Net REX ($)", marker_color='#10B981',
                hovertemplate="<b>Strike:</b> $%{x:.2f}<br><b>Net REX:</b> $%{y:,.0f}<extra></extra>"
            ))
            fig_rex.add_vline(x=spot_price, line_color="#3B82F6", line_width=1.5, line_dash="dash", annotation_text="Spot", annotation_position="top")

            fig_rex.update_layout(
                template="plotly_dark", plot_bgcolor='#06080D', paper_bgcolor='#06080D',
                title="Rho Exposure Total (REX - Sensibilidad $/1% Cambio en Tasa)",
                xaxis=dict(title="Strike ($)", gridcolor="rgba(255,255,255,0.05)", range=[x_min_val, x_max_val]),
                yaxis=dict(title="REX ($/1% Tasa)", gridcolor="rgba(255,255,255,0.05)"),
                height=560, margin=dict(l=50, r=40, t=50, b=40)
            )
            st.plotly_chart(fig_rex, use_container_width=True)

# --- 6. SURFACE 3D ---
with tab_3d:
    if Z_matrix_real.shape[1] > 1:
        max_abs_gex = float(np.max(np.abs(Z_matrix_real))) if np.max(np.abs(Z_matrix_real)) > 0 else 1.0

        Z_surface_display = np.sign(Z_matrix_real) * (np.abs(Z_matrix_real / max_abs_gex) ** 0.45) * max_abs_gex

        fig3 = go.Figure(data=[go.Surface(
            x=full_timestamps,
            y=fine_strikes,
            z=Z_surface_display,
            cmin=-max_abs_gex,
            cmax=max_abs_gex,
            colorscale=[
                [0.0, '#EF4444'],
                [0.35, '#2A1215'],
                [0.5, '#06080D'],
                [0.65, '#122A1E'],
                [1.0, '#10B981']
            ],
            lighting=dict(ambient=0.6, diffuse=0.8, fresnel=0.2, specular=0.4, roughness=0.3),
            contours=dict(z=dict(show=True, usecolormap=True, highlightcolor="#FFFFFF", project_z=True))
        )])

        fig3.update_layout(
            template="plotly_dark",
            paper_bgcolor='#06080D',
            title="Superficie Intradía Continuada de Gamma Exposición (3D Continuous Surface)",
            scene=dict(
                xaxis_title='Hora',
                yaxis_title='Strike ($)',
                zaxis_title='Net GEX Amplificado ($)',
                aspectratio=dict(x=1.6, y=1.2, z=0.6),
                camera=dict(eye=dict(x=1.6, y=-1.6, z=1.0)),
                xaxis=dict(gridcolor="#1E2433", backgroundcolor="#06080D"),
                yaxis=dict(gridcolor="#1E2433", backgroundcolor="#06080D"),
                zaxis=dict(gridcolor="#1E2433", backgroundcolor="#06080D")
            ),
            height=680,
            margin=dict(l=20, r=20, t=50, b=20)
        )
        st.plotly_chart(fig3, use_container_width=True)

# --- BUCLE DE AUTO-REFRESCO AL FINAL ---
if auto_refresh:
    time.sleep(refresh_interval)
    st.rerun()
from groq import Groq
import streamlit as st

# Reemplaza con la variable donde guardas tu clave de Groq
client = Groq(api_key=st.secrets["GROQ_API_KEY"])

try:
    models = client.models.list()
    model_ids = [m.id for m in models.data]
    st.write("Modelos disponibles en mi cuenta:", model_ids)
except Exception as e:
    st.error(f"Error: {e}")
