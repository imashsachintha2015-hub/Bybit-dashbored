"""
CME-X4 Mathematical Market-State Research Programs (01 - 10)
Version: 2026-09-25

Strict No-Leakage Implementation:
No future candle, future label, future volume, or post-entry information
may be used in any entry-time calculation.
"""

import numpy as np
import pandas as pd
from scipy import stats

TOTAL_FRICTION_RATE = 0.0015  # 15 bps (11 bps taker fee + 4 bps slippage)

# ══════════════════════════════════════════════════════════════════════════
# PROGRAM 01: MARKET GEOMETRY / TRAJECTORY ENGINE
# ══════════════════════════════════════════════════════════════════════════
def calc_trajectory_geometry(prices, window=20):
    """
    Computes OLS slope beta, R2, curvature kappa, residuals Ze, and channel width W.
    prices: array-like of length >= window
    """
    n = len(prices)
    if n < window:
        return {"slope": 0.0, "r2": 0.0, "curvature": 0.0, "residual_z": 0.0, "channel_width": 0.0}
    
    y = np.array(prices[-window:], dtype=float)
    x = np.arange(window, dtype=float)
    x_mean = np.mean(x)
    y_mean = np.mean(y)
    
    num = np.sum((x - x_mean) * (y - y_mean))
    den = np.sum((x - x_mean) ** 2)
    slope = num / den if den != 0 else 0.0
    intercept = y_mean - slope * x_mean
    
    y_pred = intercept + slope * x
    sse = np.sum((y - y_pred) ** 2)
    sst = np.sum((y - y_mean) ** 2)
    r2 = max(0.0, 1.0 - (sse / sst)) if sst != 0 else 0.0
    
    # Normalized residual at the latest bar
    residuals = y - y_pred
    res_std = np.std(residuals) if len(residuals) > 1 else 1e-6
    z_res = residuals[-1] / (res_std if res_std > 0 else 1e-6)
    
    # Curvature: discrete 2nd derivative d2P/dt2 at latest bar
    curvature = (y[-1] - 2 * y[-2] + y[-3]) if window >= 3 else 0.0
    
    # Channel width: range between upper and lower residual envelop
    channel_width = (np.max(residuals) - np.min(residuals)) / y[-1] if y[-1] > 0 else 0.0
    
    return {
        "slope": float(slope),
        "r2": float(r2),
        "curvature": float(curvature),
        "residual_z": float(z_res),
        "channel_width": float(channel_width)
    }

# ══════════════════════════════════════════════════════════════════════════
# PROGRAM 02: MARKET EFFICIENCY / PATH GEOMETRY
# ══════════════════════════════════════════════════════════════════════════
def calc_market_efficiency(prices, window=14):
    """
    ME_n = |P_t - P_(t-n)| / sum_{i=t-n+1..t} |P_i - P_(i-1)|
    Bounded strictly in [0.0, 1.0].
    """
    if len(prices) < window + 1:
        return 0.5
    sub = np.array(prices[-(window + 1):], dtype=float)
    net_displacement = abs(sub[-1] - sub[0])
    gross_path = np.sum(np.abs(np.diff(sub)))
    return float(net_displacement / gross_path) if gross_path > 0 else 0.0

def calc_multiscale_efficiency(tf_efficiencies, weights=None):
    """ME* = sum(w_i * ME_i) / sum(w_i)"""
    if not tf_efficiencies:
        return 0.5
    keys = list(tf_efficiencies.keys())
    if weights is None:
        weights = {k: 1.0 for k in keys}
    num = sum(weights.get(k, 1.0) * tf_efficiencies[k] for k in keys)
    den = sum(weights.get(k, 1.0) for k in keys)
    return float(num / den) if den > 0 else 0.5

# ══════════════════════════════════════════════════════════════════════════
# PROGRAM 03: VOLUME / PRICE DISPLACEMENT EFFICIENCY
# ══════════════════════════════════════════════════════════════════════════
def calc_displacement_efficiency(open_p, high_p, low_p, close_p, volume, vol_std=None):
    """
    DE = |Delta P| / Volume
    DDE = signed(Delta P) / Volume
    DDE_z = |Delta P| / (Volume * sigma)
    """
    delta_p = close_p - open_p
    vol = max(1e-6, volume)
    de = abs(delta_p) / vol
    dde = delta_p / vol
    sigma = vol_std if (vol_std and vol_std > 0) else 1.0
    dde_z = abs(delta_p) / (vol * sigma)
    
    candle_range = max(1e-6, high_p - low_p)
    candle_efficiency = abs(delta_p) / candle_range
    
    return {
        "de": float(de),
        "dde": float(dde),
        "dde_z": float(dde_z),
        "candle_efficiency": float(candle_efficiency)
    }

def classify_volume_displacement_state(vol_ratio, candle_disp_eff):
    """
    4 States:
    1. High Vol + High Disp -> Clean Momentum / Breakout
    2. High Vol + Low Disp  -> Absorption / Exhaustion / Trap
    3. Low Vol + High Disp  -> Fragile Vacuum / Slippage
    4. Low Vol + Low Disp   -> Inertia / Flat Chop
    """
    high_vol = vol_ratio >= 1.25
    high_disp = candle_disp_eff >= 0.40
    
    if high_vol and high_disp:
        return "HIGH_VOL_HIGH_DISP_MOMENTUM"
    elif high_vol and not high_disp:
        return "HIGH_VOL_LOW_DISP_ABSORPTION_TRAP"
    elif not high_vol and high_disp:
        return "LOW_VOL_HIGH_DISP_VACUUM"
    else:
        return "LOW_VOL_LOW_DISP_CHOP"

# ══════════════════════════════════════════════════════════════════════════
# PROGRAM 04: MULTI-TIMEFRAME STATE COHERENCE
# ══════════════════════════════════════════════════════════════════════════
def calc_multitimeframe_coherence(slopes_dict, weights=None):
    """
    Features: beta_1m, beta_5m, beta_15m, beta_60m
    s_i = sign(beta_i)
    Directional Coherence: C = sum(w_i * s_i) / sum(w_i) in [-1, 1]
    Agreement Ratio: A = agreeing_timeframes / total_timeframes
    Magnitude Coherence: MC = |mean(norm_slopes)| / mean(|norm_slopes|)
    """
    if not slopes_dict:
        return {"coherence_C": 0.0, "agreement_ratio_A": 0.5, "magnitude_coherence_MC": 0.0, "regime": "NEUTRAL"}
    
    tfs = list(slopes_dict.keys())
    w = weights or {k: 1.0 for k in tfs}
    
    signs = [np.sign(slopes_dict[k]) for k in tfs]
    w_vals = [w.get(k, 1.0) for k in tfs]
    
    c = float(sum(wi * si for wi, si in zip(w_vals, signs)) / sum(w_vals)) if sum(w_vals) > 0 else 0.0
    
    # Agreement ratio: fraction matching majority sign
    pos_count = sum(1 for s in signs if s > 0)
    neg_count = sum(1 for s in signs if s < 0)
    majority = max(pos_count, neg_count)
    agreement_ratio = float(majority / len(signs)) if len(signs) > 0 else 0.5
    
    # Magnitude coherence
    vals = np.array([slopes_dict[k] for k in tfs], dtype=float)
    std_vals = np.std(vals) if len(vals) > 1 and np.std(vals) > 0 else 1.0
    norm_vals = vals / std_vals
    
    mean_norm = np.mean(norm_vals)
    mean_abs_norm = np.mean(np.abs(norm_vals))
    mc = float(abs(mean_norm) / mean_abs_norm) if mean_abs_norm > 0 else 0.0
    
    if agreement_ratio == 1.0:
        regime = "ALL_BULLISH" if c > 0 else "ALL_BEARISH"
    elif slopes_dict.get('60', 0) > 0 and slopes_dict.get('5', 0) < 0:
        regime = "BULL_PULLBACK_VALUE"
    elif slopes_dict.get('60', 0) < 0 and slopes_dict.get('5', 0) > 0:
        regime = "BEAR_RETRAIN_OVERHANG"
    else:
        regime = "MIXED_TRANSITION"
        
    return {
        "coherence_C": round(c, 3),
        "agreement_ratio_A": round(agreement_ratio, 3),
        "magnitude_coherence_MC": round(mc, 3),
        "regime": regime
    }

# ══════════════════════════════════════════════════════════════════════════
# PROGRAM 05: MARKET STATE / PHASE TRANSITION MODEL
# ══════════════════════════════════════════════════════════════════════════
STATES = [
    "COMPRESSION", "EXPANSION", "TREND", "EXHAUSTION",
    "REVERSAL", "RANGE", "CHOP", "HIGH_VOLATILITY", "LOW_VOLATILITY"
]

def classify_market_state(df_15m, idx):
    """
    Classifies the bar at index idx into one of 9 discrete states:
    COMPRESSION, EXPANSION, TREND, EXHAUSTION, REVERSAL, RANGE, CHOP, HIGH_VOL, LOW_VOL.
    """
    if idx < 20:
        return "RANGE"
    
    c = df_15m['close'].values[:idx+1]
    h = df_15m['high'].values[:idx+1]
    l = df_15m['low'].values[:idx+1]
    v = df_15m['volume'].values[:idx+1]
    
    # 20-bar ATR and Volatility
    tr = np.maximum(h[-20:] - l[-20:], np.abs(h[-20:] - np.roll(c[-20:], 1)))
    atr_20 = np.mean(tr[1:])
    atr_long = np.mean(h[-60:] - l[-60:]) if idx >= 60 else atr_20
    
    # Slope and efficiency
    traj = calc_trajectory_geometry(c, 20)
    me = calc_market_efficiency(c, 14)
    vol_ratio = v[-1] / (np.mean(v[-20:]) if np.mean(v[-20:]) > 0 else 1.0)
    
    atr_ratio = atr_20 / (atr_long if atr_long > 0 else 1.0)
    
    if atr_ratio < 0.65 and vol_ratio < 0.80:
        return "COMPRESSION"
    elif atr_ratio > 1.50 and vol_ratio > 1.50:
        return "EXPANSION"
    elif traj['r2'] > 0.65 and me > 0.50:
        return "TREND"
    elif traj['r2'] > 0.50 and vol_ratio > 1.80 and me < 0.25:
        return "EXHAUSTION"
    elif abs(traj['residual_z']) > 2.2 and abs(traj['curvature']) > 0:
        return "REVERSAL"
    elif atr_ratio > 1.30:
        return "HIGH_VOLATILITY"
    elif atr_ratio < 0.75:
        return "LOW_VOLATILITY"
    elif me < 0.20:
        return "CHOP"
    else:
        return "RANGE"

def build_transition_matrix(states_sequence):
    """Builds empirical transition probability matrix T_ij = P(S_(t+1) = j | S_t = i)."""
    counts = {s1: {s2: 0 for s2 in STATES} for s1 in STATES}
    totals = {s1: 0 for s1 in STATES}
    
    for i in range(len(states_sequence) - 1):
        s1 = states_sequence[i]
        s2 = states_sequence[i + 1]
        if s1 in counts and s2 in counts[s1]:
            counts[s1][s2] += 1
            totals[s1] += 1
            
    matrix = {}
    for s1 in STATES:
        matrix[s1] = {}
        for s2 in STATES:
            matrix[s1][s2] = round(counts[s1][s2] / totals[s1], 4) if totals[s1] > 0 else 0.0
    return matrix

# ══════════════════════════════════════════════════════════════════════════
# PROGRAM 06: VOLATILITY-NORMALIZED BARRIER PROBABILITY
# ══════════════════════════════════════════════════════════════════════════
def evaluate_barrier_outcome(future_bars, entry_price, direction, alpha=1.0, beta=0.5, sigma=None, friction=TOTAL_FRICTION_RATE):
    """
    Evaluates whether TP is touched before SL.
    For LONG: TP = Entry + alpha * sigma, SL = Entry - beta * sigma
    For SHORT: TP = Entry - alpha * sigma, SL = Entry + beta * sigma
    Includes realistic roundtrip friction (15 bps).
    Returns dict with outcome (WIN/LOSS/EXPIRED), mfe, mae, realized_r.
    """
    if not future_bars or len(future_bars) == 0:
        return {"outcome": "NO_DATA", "won": 0, "mfe_pct": 0.0, "mae_pct": 0.0, "realized_r": 0.0}
    
    sig = sigma if (sigma and sigma > 0) else (entry_price * 0.015)
    
    if direction == "BUY":
        tp_target = entry_price + (alpha * sig)
        sl_target = entry_price - (beta * sig)
        entry_net = entry_price * (1.0 + friction)  # Buying higher due to friction
    else:
        tp_target = entry_price - (alpha * sig)
        sl_target = entry_price + (beta * sig)
        entry_net = entry_price * (1.0 - friction)  # Selling lower due to friction
        
    mfe_pct = 0.0
    mae_pct = 0.0
    won = 0
    outcome = "EXPIRED"
    realized_r = 0.0
    
    for bar in future_bars:
        high = bar['high']
        low = bar['low']
        
        if direction == "BUY":
            gain = ((high - entry_net) / entry_net) * 100.0
            drawdown = ((low - entry_net) / entry_net) * 100.0
            mfe_pct = max(mfe_pct, gain)
            mae_pct = min(mae_pct, drawdown)
            
            # Did it hit SL first or TP first?
            # Pessimistic check: If candle touched both, assume SL hit first
            if low <= sl_target:
                outcome = "LOSS"
                won = 0
                realized_r = -1.0
                break
            elif high >= tp_target:
                outcome = "WIN"
                won = 1
                realized_r = alpha / beta
                break
        else: # SHORT
            gain = ((entry_net - low) / entry_net) * 100.0
            drawdown = ((entry_net - high) / entry_net) * 100.0
            mfe_pct = max(mfe_pct, gain)
            mae_pct = min(mae_pct, drawdown)
            
            if high >= sl_target:
                outcome = "LOSS"
                won = 0
                realized_r = -1.0
                break
            elif low <= tp_target:
                outcome = "WIN"
                won = 1
                realized_r = alpha / beta
                break
                
    if outcome == "EXPIRED":
        last_close = future_bars[-1]['close']
        if direction == "BUY":
            ret = (last_close - entry_net) / entry_net
        else:
            ret = (entry_net - last_close) / entry_net
        risk_dist = (beta * sig) / entry_price
        realized_r = ret / risk_dist if risk_dist > 0 else 0.0
        won = 1 if realized_r > 0 else 0
        
    return {
        "outcome": outcome,
        "won": won,
        "mfe_pct": round(mfe_pct, 3),
        "mae_pct": round(mae_pct, 3),
        "realized_r": round(realized_r, 3)
    }

# ══════════════════════════════════════════════════════════════════════════
# PROGRAM 07: MICROSTRUCTURE / FLOW PRESSURE MODEL
# ══════════════════════════════════════════════════════════════════════════
def calc_microstructure_flow(taker_buy_vol, taker_sell_vol, bid_depth, ask_depth, spread_bps=1.0, displacement=0.0):
    """
    TI = (V_buy - V_sell) / (V_buy + V_sell)
    OBI = (BidDepth - AskDepth) / (BidDepth + AskDepth)
    Flow Agreement F = average(sign(TI), sign(OBI), sign(displacement))
    """
    tot_vol = max(1e-6, taker_buy_vol + taker_sell_vol)
    ti = (taker_buy_vol - taker_sell_vol) / tot_vol
    
    tot_depth = max(1e-6, bid_depth + ask_depth)
    obi = (bid_depth - ask_depth) / tot_depth
    
    f_agreement = (np.sign(ti) + np.sign(obi) + np.sign(displacement)) / 3.0
    
    return {
        "taker_imbalance_TI": round(float(ti), 3),
        "orderbook_imbalance_OBI": round(float(obi), 3),
        "flow_agreement_F": round(float(f_agreement), 3),
        "spread_bps": float(spread_bps)
    }

# ══════════════════════════════════════════════════════════════════════════
# PROGRAM 08: SIGNAL PERSISTENCE + ACTIVATION DYNAMICS
# ══════════════════════════════════════════════════════════════════════════
def evaluate_activation_dynamics(k1_future, entry_price, direction):
    """
    Measures MFE & MAE at T+1m, T+3m, T+5m, T+10m.
    Calculates Activation Ratio: AR_t = MFE_t / max(|MAE_t|, 1e-4).
    """
    res = {}
    horizons = [1, 3, 5, 10]
    
    for h in horizons:
        if len(k1_future) >= h:
            sub = k1_future[:h]
            if direction == "BUY":
                mfe = max(0.0, max(b['high'] - entry_price for b in sub) / entry_price * 100.0)
                mae = max(0.0, min(b['low'] - entry_price for b in sub) / entry_price * 100.0)
            else:
                mfe = max(0.0, max(entry_price - b['low'] for b in sub) / entry_price * 100.0)
                mae = max(0.0, min(entry_price - b['high'] for b in sub) / entry_price * 100.0)
            ar = mfe / max(abs(mae), 0.01)
            res[f"mfe_{h}m"] = round(mfe, 3)
            res[f"mae_{h}m"] = round(mae, 3)
            res[f"activation_ratio_{h}m"] = round(ar, 2)
        else:
            res[f"mfe_{h}m"] = 0.0
            res[f"mae_{h}m"] = 0.0
            res[f"activation_ratio_{h}m"] = 1.0
            
    return res

# ══════════════════════════════════════════════════════════════════════════
# PROGRAM 09: NONLINEAR / SYMBOLIC MARKET EQUATIONS
# ══════════════════════════════════════════════════════════════════════════
def calc_symbolic_equations(r2, me, vol_ratio, de, slope, flow_agreement, vol_expansion, vol_persistence, disp_eff, u_wick, l_wick):
    """
    Evaluates compact symbolic candidate equations:
    1. TrendEfficiency = R2 * ME
    2. PressureEfficiency = VolumeRatio * DisplacementEfficiency
    3. StructuralPressure = TrendSlope * ME * FlowAgreement
    4. ExpansionScore = VolatilityExpansion * VolumePersistence * DisplacementEfficiency
    5. ReversalPressure = WickRejection * Absorption * TrendExtension
    """
    trend_eff = float(r2 * me)
    pressure_eff = float(vol_ratio * de)
    struct_pressure = float(slope * me * (flow_agreement + 1.0) / 2.0)
    exp_score = float(vol_expansion * vol_persistence * disp_eff)
    rev_pressure = float(max(u_wick, l_wick) * (vol_ratio / max(1e-4, disp_eff)))
    
    # Nonlinear transforms
    tanh_trend = float(np.tanh(trend_eff * 2.0))
    sqrt_pressure = float(np.sqrt(max(0.0, pressure_eff)))
    
    return {
        "trend_efficiency": round(trend_eff, 4),
        "pressure_efficiency": round(pressure_eff, 4),
        "structural_pressure": round(struct_pressure, 4),
        "expansion_score": round(exp_score, 4),
        "reversal_pressure": round(rev_pressure, 4),
        "tanh_trend": round(tanh_trend, 4),
        "sqrt_pressure": round(sqrt_pressure, 4)
    }

# ══════════════════════════════════════════════════════════════════════════
# PROGRAM 10: UNIFIED CME-X4 CONDITIONAL MARKET-STATE ENGINE
# ══════════════════════════════════════════════════════════════════════════
def brier_score(y_true, y_prob):
    """Calculates calibration Brier score = 1/N sum (p_i - y_i)^2."""
    if len(y_true) == 0:
        return 0.0
    return float(np.mean((np.array(y_prob) - np.array(y_true)) ** 2))

def calc_auc(y_true, y_prob):
    """Calculates ROC AUC via Wilcoxon rank-sum statistic."""
    y_true = np.array(y_true)
    y_prob = np.array(y_prob)
    n_pos = np.sum(y_true == 1)
    n_neg = np.sum(y_true == 0)
    if n_pos == 0 or n_neg == 0:
        return 0.5
    ranks = stats.rankdata(y_prob)
    r_pos = np.sum(ranks[y_true == 1])
    u = r_pos - (n_pos * (n_pos + 1)) / 2.0
    return float(u / (n_pos * n_neg))
