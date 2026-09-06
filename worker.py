import os
import json
import time
import requests
import numpy as np
import pandas as pd
from datetime import datetime
from scipy.stats import norm
import schwab

# Configuración de variables desde GitHub Secrets
CLIENT_ID = os.environ.get("CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")
JSONBIN_BIN_ID = os.environ.get("JSONBIN_BIN_ID", "")
JSONBIN_API_KEY = os.environ.get("JSONBIN_API_KEY", "")
SCHWAB_TOKEN_RAW = os.environ.get("SCHWAB_TOKEN", "")

TOKEN_PATH = "schwab_token.json"

def init_schwab():
    if SCHWAB_TOKEN_RAW and not os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH, "w") as f:
            f.write(SCHWAB_TOKEN_RAW)
    try:
        return schwab.auth.client_from_token_file(
            token_path=TOKEN_PATH,
            api_key=CLIENT_ID,
            app_secret=CLIENT_SECRET,
            enforce_enums=False
        )
    except Exception as e:
        print(f"[ERROR] Fallo inicializando Schwab: {e}")
        return None

def fetch_and_process(client, symbol="QQQ"):
    resp = client.get_option_chain(symbol=symbol, contract_type="ALL", strike_count=20)
    if resp.status_code != 200:
        print(f"[ERROR] Status Schwab: {resp.status_code}")
        return None
    
    chain_data = resp.json()
    spot_price = float(chain_data.get("underlyingPrice", 0.0))
    if spot_price <= 0:
        return None

    call_map = chain_data.get('callExpDateMap', {})
    put_map = chain_data.get('putExpDateMap', {})
    all_exps = sorted(list(set(list(call_map.keys()) + list(put_map.keys()))))
    if not all_exps:
        return None

    sel_exp = all_exps[0]
    records = {}

    for strike_str, opt_list in call_map.get(sel_exp, {}).items():
        if not opt_list: continue
        opt = opt_list[0]
        k = float(strike_str)
        records[k] = records.get(k, {'strike': k, 'oi_c': 0, 'oi_p': 0, 'iv_c': 0.2, 'iv_p': 0.2})
        records[k]['oi_c'] = opt.get('openInterest', 0)
        records[k]['iv_c'] = opt.get('volatility', 20.0) / 100.0 if opt.get('volatility', 0) > 2 else 0.2

    for strike_str, opt_list in put_map.get(sel_exp, {}).items():
        if not opt_list: continue
        opt = opt_list[0]
        k = float(strike_str)
        records[k] = records.get(k, {'strike': k, 'oi_c': 0, 'oi_p': 0, 'iv_c': 0.2, 'iv_p': 0.2})
        records[k]['oi_p'] = opt.get('openInterest', 0)
        records[k]['iv_p'] = opt.get('volatility', 20.0) / 100.0 if opt.get('volatility', 0) > 2 else 0.2

    df = pd.DataFrame(list(records.values()))
    T_exp = 0.5 / 365.0
    strikes_payload = []
    net_gex_total = 0.0

    for _, r in df.iterrows():
        K = r['strike']
        iv = max((r['iv_c'] + r['iv_p']) / 2.0, 0.01)
        d1 = (np.log(spot_price / K) + (0.045 + 0.5 * iv**2) * T_exp) / (iv * np.sqrt(T_exp))
        gamma = norm.pdf(d1) / (spot_price * iv * np.sqrt(T_exp))
        
        call_gex = gamma * r['oi_c'] * (spot_price ** 2) * 0.01
        put_gex = gamma * r['oi_p'] * (spot_price ** 2) * (-0.01)
        net_gex = call_gex + put_gex
        net_gex_total += net_gex

        strikes_payload.append({
            "strike": float(K),
            "net_gex": float(net_gex),
            "call_gex": float(call_gex),
            "put_gex": float(put_gex)
        })

    now_str = datetime.now()
    return {
        "time": now_str.strftime("%H:%M"),
        "date": now_str.strftime("%Y-%m-%d"),
        "spot": float(spot_price),
        "net_gex": float(net_gex_total),
        "strikes": strikes_payload
    }

def push_to_jsonbin(snapshot):
    if not JSONBIN_BIN_ID or not JSONBIN_API_KEY:
        print("[WARN] Faltan credenciales de JSONBin")
        return

    url = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"
    headers = {"Content-Type": "application/json", "X-Master-Key": JSONBIN_API_KEY}
    
    resp = requests.get(f"{url}/latest", headers=headers, timeout=5)
    current_data = resp.json().get("record", {}) if resp.status_code == 200 else {}
    
    date_key = snapshot["date"]
    if date_key not in current_data or not isinstance(current_data[date_key], list):
        current_data[date_key] = []

    snap_entry = {
        "time": snapshot["time"],
        "spot": snapshot["spot"],
        "net_gex": snapshot["net_gex"],
        "strikes": snapshot["strikes"]
    }

    existing_times = [s.get("time") for s in current_data[date_key] if isinstance(s, dict)]
    if snapshot["time"] not in existing_times:
        current_data[date_key].append(snap_entry)
        requests.put(url, json=current_data, headers=headers, timeout=5)
        print(f"[OK] Snapshot {snapshot['time']} subida exitosamente.")

if __name__ == "__main__":
    client = init_schwab()
    if client:
        data = fetch_and_process(client)
        if data:
            push_to_jsonbin(data)
