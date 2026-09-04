import os
import json
import streamlit as st
import pandas as pd
import numpy as np
from scipy.stats import norm
import plotly.graph_objects as go
from datetime import datetime, timedelta
import schwab

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="GEX Terminal Pro - Schwab", layout="wide", initial_sidebar_state="expanded")

# --- MANEJO SEGURO DE SECRETOS Y TOKEN ---
# Lee las credenciales desde st.secrets si existen (Streamlit Cloud) o usa valores por defecto
CLIENT_ID = st.secrets.get("CLIENT_ID", "QJJS3fGYgzh425rtmmGHbPNL5r3ShCHr0FYxerrPzDbAnGxw")
CLIENT_SECRET = st.secrets.get("CLIENT_SECRET", "GQwRhMGJpbHOMB3ANarKzGIgWfxwYUpwN8mvUpyQGpRwd6Jds7gFMnlwiu9THPkj")
TOKEN_PATH = "schwab_token.json"

# Crear el archivo de token dinámicamente si se despliega en Streamlit Cloud
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
    st.error(f"Error al conectar con la API de Schwab: {e}")
    st.stop()

# --- ESTILOS CSS ---
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;800&family=Inter:wght@300;400;600;700&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        background-color: #0B0E14;
        color: #C9D1D9;
    }
    
    [data-testid="stSidebar"] {
        background-color: #10141D !important;
        border-right: 1px solid #1F2430;
    }
    
    .metric-card {
        background: linear-gradient(135deg, #141824 0%, #0F121A 100%);
        border: 1px solid #1E2638;
        border-radius: 8px;
        padding: 10px 14px;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.35);
    }
    .metric-label {
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.68rem;
        text-transform: uppercase;
        letter-spacing: 0.8px;
        color: #8B949E;
        margin-bottom: 2px;
    }
    .metric-value {
        font-family: 'JetBrains Mono', monospace;
        font-size: 1.15rem;
        font-weight: 700;
        color: #F0F6FC;
    }
    
    .depth-frame {
        border: 1px solid #1E2433;
        border-radius: 10px;
        padding: 16px;
        background-color: #0E121B;
        margin-top: 10px;
    }

    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        background-color: #10141D;
        padding: 6px;
        border-radius: 8px;
        border: 1px solid #1E2433;
    }
    .stTabs [data-baseweb="tab"] {
        height: 38px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.80rem;
        color: #8B949E;
        border-radius: 6px;
        padding: 0 14px;
    }
    .stTabs [aria-selected="true"] {
        background-color: #1C2333 !important;
        color: #58A6FF !important;
        font-weight: 600;
    }
    </style>
""", unsafe_allow_html=True)

# --- ENCABEZADO ---
st.markdown("<h2 style='margin:0; font-weight:800; letter-spacing:-0.5px;'>GEX QUANT TERMINAL (SCHWAB API)</h2>", unsafe_allow_html=True)
st.markdown("<p style='color:#6E7681; margin:0 0 15px 0; font-size:0.82rem;'>GAMMA EXPOSURE ENGINE & INTRADAY FLOW ANALYSIS</p>", unsafe_allow_html=True)

# --- SIDEBAR CONFIG ---
ticker_symbol = st.sidebar.text_input("SYMBOL", value="QQQ").upper()
strike_range = st.sidebar.slider("STRIKE RANGE (± ATM)", min_value=10, max_value=50, value=20)
st.sidebar.markdown("---")
tz_choice = st.sidebar.selectbox("TIMEZONE", ["UTC-5 (Lima)", "UTC-4 (New York)"])
tz_target = "America/Lima" if "UTC-5" in tz_choice else "America/New_York"

st.sidebar.markdown("---")
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
        st.error(f"Error obteniendo histórico de Schwab: {e}")
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
        st.error(f"Error obteniendo cadena de opciones de Schwab: {e}")
    return {}

# --- PROCESAMIENTO DE DATOS ---
now_tz = pd.Timestamp.now(tz=tz_target)
ref_today = now_tz.floor('D').tz_localize(None)

hist_raw = fetch_history_schwab(ticker_symbol)
chain_raw = fetch_option_chain_schwab(ticker_symbol, strike_range)

spot_price = float(chain_raw.get("underlyingPrice", 0.0))
if (spot_price <= 0 or np.isnan(spot_price)) and not hist_raw.empty and 'Close' in hist_raw:
    spot_price = float(hist_raw['Close'].dropna().iloc[-1])

if spot_price <= 0 or np.isnan(spot_price):
    st.error(f"Error al obtener el precio de mercado para {ticker_symbol}. Revisa el símbolo o tu conexión con Schwab.")
    st.stop()

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
            records[strike] = {'strike': strike, 'openInterest_c': 0, 'openInterest_p': 0, 'gamma_c': 0.0, 'gamma_p': 0.0, 'iv_c': 0.0, 'iv_p': 0.0}
        records[strike]['openInterest_c'] = opt.get('openInterest', 0)
        records[strike]['gamma_c'] = opt.get('gamma', 0.0)
        vol = opt.get('volatility', opt.get('impliedVolatility', 0.0))
        records[strike]['iv_c'] = vol / 100.0 if vol > 2 else vol

    for strike_str, opt_list in puts_for_exp.items():
        if not opt_list: continue
        opt = opt_list[0]
        strike = float(strike_str)
        if strike not in records:
            records[strike] = {'strike': strike, 'openInterest_c': 0, 'openInterest_p': 0, 'gamma_c': 0.0, 'gamma_p': 0.0, 'iv_c': 0.0, 'iv_p': 0.0}
        records[strike]['openInterest_p'] = opt.get('openInterest', 0)
        records[strike]['gamma_p'] = opt.get('gamma', 0.0)
        vol = opt.get('volatility', opt.get('impliedVolatility', 0.0))
        records[strike]['iv_p'] = vol / 100.0 if vol > 2 else vol

    df = pd.DataFrame(list(records.values())).sort_values('strike').reset_index(drop=True)
    return df, selected_exp

df_curr, exp_0dte = parse_schwab_chain(chain_raw)

def fmt_val(val):
    sign = "+" if val > 0 else ""
    if abs(val) >= 1e9:
        return f"{sign}${val/1e9:.2f}B"
    elif abs(val) >= 1e6:
        return f"{sign}${val/1e6:.1f}M"
    elif abs(val) >= 1e3:
        return f"{sign}${val/1e3:.1f}K"
    else:
        return f"{sign}${val:.1f}"

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

    df_curr['gamma'] = df_curr.apply(
        lambda r: r['gamma_c'] if r['gamma_c'] > 0 else norm.pdf(
            (np.log(spot_price / r['strike']) + (0.045 + 0.5 * atm_iv**2) * T_exp) / (atm_iv * np.sqrt(T_exp))
        ) / (spot_price * atm_iv * np.sqrt(T_exp)), axis=1
    )
    
    df_curr['call_gex'] = df_curr['gamma'] * df_curr['openInterest_c'] * (spot_price ** 2) * 0.01
    df_curr['put_gex'] = df_curr['gamma'] * df_curr['openInterest_p'] * (spot_price ** 2) * (-0.01)
    df_curr['net_gex'] = df_curr['call_gex'] + df_curr['put_gex']

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
else:
    df_curr = pd.DataFrame()
    cw1, cw2, cw3 = spot_price, spot_price, spot_price
    pw1, pw2, pw3 = spot_price, spot_price, spot_price
    zero_gamma = spot_price
    atm_iv = 0.20

min_strike = int(np.floor(spot_price - (strike_range * 0.8)))
max_strike = int(np.ceil(spot_price + (strike_range * 0.8)))

# --- PESTAÑAS PRINCIPALES ---
tab1, tab2, tab3, tab4 = st.tabs(["NET GEX PROFILE", "CALLS vs PUTS", "SURFACE 3D", "GAMMA DEPTH HEATMAP"])

# --- TAB 1: NET GEX PROFILE ---
with tab1:
    if not df_curr.empty:
        df_sub = df_curr[(df_curr['strike'] >= min_strike) & (df_curr['strike'] <= max_strike)].copy()
        colors = ['#00E676' if v >= 0 else '#FF5252' for v in df_sub['net_gex']]
        
        fig1 = go.Figure()
        fig1.add_trace(go.Bar(
            y=df_sub['strike'],
            x=df_sub['net_gex'],
            orientation='h',
            marker_color=colors,
            hovertemplate="<b>Strike:</b> $%{y:.2f}<br><b>Net GEX:</b> $%{x:,.0f}<extra></extra>"
        ))
        
        fig1.add_hline(y=spot_price, line_color="#FFFFFF", line_width=1, line_dash="dash",
                       annotation_text=f"Spot (${spot_price:.2f})", annotation_position="top right")
        
        fig1.update_layout(
            template="plotly_dark", plot_bgcolor='#0E1117', paper_bgcolor='#0E1117',
            title="Perfil de Net Gamma Exposición por Strike (Schwab)",
            xaxis_title="Net GEX ($)", yaxis_title="Strike ($)",
            height=600, margin=dict(l=60, r=40, t=50, b=40)
        )
        st.plotly_chart(fig1, use_container_width=True)

# --- TAB 2: CALLS vs PUTS ---
with tab2:
    if not df_curr.empty:
        df_sub = df_curr[(df_curr['strike'] >= min_strike) & (df_curr['strike'] <= max_strike)].copy()
        
        fig2 = go.Figure()
        fig2.add_trace(go.Bar(
            x=df_sub['strike'], y=df_sub['call_gex'],
            name="Call GEX (+)", marker_color='#00E676',
            hovertemplate="<b>Strike:</b> $%{x:.2f}<br><b>Call GEX:</b> $%{y:,.0f}<extra></extra>"
        ))
        fig2.add_trace(go.Bar(
            x=df_sub['strike'], y=df_sub['put_gex'],
            name="Put GEX (-)", marker_color='#FF5252',
            hovertemplate="<b>Strike:</b> $%{x:.2f}<br><b>Put GEX:</b> $%{y:,.0f}<extra></extra>"
        ))
        
        fig2.update_layout(
            template="plotly_dark", plot_bgcolor='#0E1117', paper_bgcolor='#0E1117',
            title="Comparativa Call GEX vs Put GEX por Strike",
            barmode='relative', xaxis_title="Strike ($)", yaxis_title="Gamma Exposure ($)",
            height=600, margin=dict(l=60, r=40, t=50, b=40)
        )
        st.plotly_chart(fig2, use_container_width=True)

# --- PREPARACIÓN Y ALINEACIÓN DE DATOS TEMPORALES DEL DÍA DE HOY ---
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

    for t_idx, S_t in enumerate(full_spots):
        if S_t <= 0 or np.isnan(S_t): continue
        for _, r in df_curr.iterrows():
            K = r['strike']
            if K < min_strike - 3 or K > max_strike + 3: continue
            net_oi = r['openInterest_c'] - r['openInterest_p']
            if net_oi == 0: continue

            d1_t = (np.log(S_t / K) + (0.045 + 0.5 * atm_iv**2) * T_exp) / (atm_iv * np.sqrt(T_exp))
            gamma_t = norm.pdf(d1_t) / (S_t * atm_iv * np.sqrt(T_exp))
            net_gex_t = net_oi * gamma_t * (S_t ** 2) * 0.01

            gauss_weight = np.exp(-0.5 * ((fine_strikes - K) / 0.05) ** 2)
            Z_matrix_real[:, t_idx] += gauss_weight * net_gex_t

# --- TAB 3: SURFACE 3D ---
with tab3:
    if Z_matrix_real.shape[1] > 1:
        fig3 = go.Figure(data=[go.Surface(
            x=full_timestamps,
            y=fine_strikes,
            z=Z_matrix_real,
            colorscale=[
                [0.0, 'rgb(255, 23, 68)'], [0.5, 'rgb(14, 18, 23)'], [1.0, 'rgb(0, 230, 118)']
            ]
        )])
        fig3.update_layout(
            template="plotly_dark", paper_bgcolor='#0E1117',
            title="Superficie Intradía de Gamma Exposición (3D)",
            scene=dict(
                xaxis_title='Hora',
                yaxis_title='Strike ($)',
                zaxis_title='Net GEX ($)',
                aspectratio=dict(x=1.5, y=1, z=0.5)
            ),
            height=650, margin=dict(l=20, r=20, t=50, b=20)
        )
        st.plotly_chart(fig3, use_container_width=True)

# --- TAB 4: GAMMA DEPTH HEATMAP ---
with tab4:
    st.markdown('<div class="depth-frame">', unsafe_allow_html=True)
    st.markdown(f"### 🌊 Profundidad de Gamma Dinámica ({tz_choice})")

    mc1, mc2, mc3, mc4, mc5, mc6, mc7 = st.columns(7)
    mc1.markdown(f'<div class="metric-card"><div class="metric-label">SPOT</div><div class="metric-value">${spot_price:.2f}</div></div>', unsafe_allow_html=True)
    mc2.markdown(f'<div class="metric-card"><div class="metric-label">CALL WALL 1</div><div class="metric-value" style="color:#00E676">${cw1:.0f}</div></div>', unsafe_allow_html=True)
    mc3.markdown(f'<div class="metric-card"><div class="metric-label">CALL WALL 2</div><div class="metric-value" style="color:#00E676">${cw2:.0f}</div></div>', unsafe_allow_html=True)
    mc4.markdown(f'<div class="metric-card"><div class="metric-label">CALL WALL 3</div><div class="metric-value" style="color:#00E676">${cw3:.0f}</div></div>', unsafe_allow_html=True)
    mc5.markdown(f'<div class="metric-card"><div class="metric-label">PUT WALL 1</div><div class="metric-value" style="color:#FF5252">${pw1:.0f}</div></div>', unsafe_allow_html=True)
    mc6.markdown(f'<div class="metric-card"><div class="metric-label">PUT WALL 2</div><div class="metric-value" style="color:#FF5252">${pw2:.0f}</div></div>', unsafe_allow_html=True)
    mc7.markdown(f'<div class="metric-card"><div class="metric-label">PUT WALL 3</div><div class="metric-value" style="color:#FF5252">${pw3:.0f}</div></div>', unsafe_allow_html=True)

    if len(full_timestamps) > 0 and Z_matrix_real.shape[1] > 0:
        custom_hover_matrix = [[fmt_val(val) for val in row] for row in Z_matrix_real]
        
        max_real_abs = float(np.max(np.abs(Z_matrix_real))) if np.max(np.abs(Z_matrix_real)) > 0 else 1.0
        Z_matrix_scaled = np.sign(Z_matrix_real) * (np.abs(Z_matrix_real / max_real_abs) ** 1.7)

        fig4 = go.Figure()

        fig4.add_trace(go.Heatmap(
            x=full_timestamps,
            y=fine_strikes,
            z=Z_matrix_scaled,
            customdata=custom_hover_matrix,
            hovertemplate="<b>Hora:</b> %{x}<br><b>Strike:</b> $%{y:.2f}<br><b>Net Gamma Real:</b> %{customdata}<extra></extra>",
            zsmooth='best', zmin=-1.0, zmax=1.0, zmid=0,
            colorscale=[
                [0.0, 'rgba(255, 23, 68, 0.95)'], [0.35, 'rgba(255, 23, 68, 0.35)'], [0.48, 'rgba(255, 23, 68, 0.05)'],
                [0.50, 'rgba(14, 18, 23, 0.0)'], [0.52, 'rgba(0, 230, 118, 0.05)'], [0.65, 'rgba(0, 230, 118, 0.35)'],
                [1.0, 'rgba(0, 230, 118, 0.95)']
            ],
            colorbar=dict(title=dict(text="Net GEX ($)", side="top"), x=-0.05),
            hoverlabel=dict(namelength=0)
        ))

        raw_levels = []
        if min_strike <= cw1 <= max_strike: raw_levels.append(('Call Wall 1', cw1, '#00E676', 'solid'))
        if min_strike <= cw2 <= max_strike: raw_levels.append(('Call Wall 2', cw2, '#00E676', 'dash'))
        if min_strike <= cw3 <= max_strike: raw_levels.append(('Call Wall 3', cw3, '#00E676', 'dot'))
        if min_strike <= pw1 <= max_strike: raw_levels.append(('Put Wall 1', pw1, '#FF5252', 'solid'))
        if min_strike <= pw2 <= max_strike: raw_levels.append(('Put Wall 2', pw2, '#FF5252', 'dash'))
        if min_strike <= pw3 <= max_strike: raw_levels.append(('Put Wall 3', pw3, '#FF5252', 'dot'))
        if min_strike <= zero_gamma <= max_strike: raw_levels.append(('Flip Level', zero_gamma, '#00E5FF', 'dot'))

        grouped_levels = {}
        for label, val, color, dash in raw_levels:
            key = round(val, 1)
            if key not in grouped_levels: grouped_levels[key] = []
            grouped_levels[key].append((label, color, dash))

        for k_val, items in grouped_levels.items():
            for label, color, dash in items:
                fig4.add_hline(y=k_val, line_color=color, line_width=0.8, line_dash=dash, layer="above")
            labels_str = " / ".join([item[0] for item in items])
            badge_text = f"<b>{labels_str}</b> (${k_val:.0f})"
            main_color = items[0][1]
            fig4.add_annotation(
                x=0.988, xref="paper", y=k_val, yref="y", text=badge_text, showarrow=False,
                xanchor="right", yanchor="middle", font=dict(family="JetBrains Mono", size=10, color=main_color),
                bgcolor="#10141D", bordercolor=main_color, borderwidth=1, borderpad=3, opacity=0.95
            )

        if not h_1m_reindexed.empty:
            fig4.add_trace(go.Candlestick(
                x=full_timestamps,
                open=h_1m_reindexed['Open'],
                high=h_1m_reindexed['High'],
                low=h_1m_reindexed['Low'],
                close=h_1m_reindexed['Close'],
                name="Spot Price",
                increasing_line_color='#26A69A',
                decreasing_line_color='#EF5350',
                increasing_fillcolor='#26A69A',
                decreasing_fillcolor='#EF5350',
                hovertemplate="<b>Hora:</b> %{x}<br><b>Apertura:</b> $%{open:.2f}<br><b>Máximo:</b> $%{high:.2f}<br><b>Mínimo:</b> $%{low:.2f}<br><b>Cierre:</b> $%{close:.2f}<extra></extra>"
            ))

        fig4.update_layout(
            template="plotly_dark", plot_bgcolor='#0E1117', paper_bgcolor='#0E1117',
            uirevision="static_user_state",
            xaxis_title=f"Hora Intradía ({tz_choice.split(' ')[0]})", yaxis_title="Precio / Strike ($)",
            height=720, dragmode='pan', hovermode="closest", xaxis_rangeslider_visible=False,
            margin=dict(l=80, r=60, t=40, b=40), yaxis=dict(dtick=1, side='right'),
            hoverlabel=dict(bgcolor="#161B22", bordercolor="#30363D", font_size=12, font_family="JetBrains Mono", namelength=0)
        )

        st.plotly_chart(fig4, use_container_width=True, config={'scrollZoom': True}, key="heatmap_depth_v4")
    else:
        st.warning("Sin datos intradía disponibles en Schwab para generar el mapa de profundidad.")

    st.markdown('</div>', unsafe_allow_html=True)