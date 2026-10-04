"""Módulos experimentais ativos da V3.85.

As funções abaixo são determinísticas: recebem candles/configuração e devolvem uma
decisão. Elas não alteram parâmetros, arquivos ou código em tempo de execução.
"""

import math
from statistics import median


def _ema(values, period):
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    output = [float(values[0])]
    for value in values[1:]:
        output.append(alpha * float(value) + (1.0 - alpha) * output[-1])
    return output


def _rsi(values, period=14):
    if len(values) <= period:
        return None
    changes = [float(values[i]) - float(values[i - 1]) for i in range(1, len(values))]
    gains = [max(change, 0.0) for change in changes[-period:]]
    losses = [max(-change, 0.0) for change in changes[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss <= 1e-12:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))


def _true_ranges(rows):
    output = []
    for index, row in enumerate(rows):
        _, _open, high, low, close = row
        previous_close = rows[index - 1][4] if index else close
        output.append(max(float(high) - float(low), abs(float(high) - float(previous_close)), abs(float(low) - float(previous_close))))
    return output


def _shape(row):
    _, open_, high, low, close = row
    range_ = max(float(high) - float(low), 1e-12)
    body_signed = (float(close) - float(open_)) / range_
    upper = (float(high) - max(float(open_), float(close))) / range_
    lower = (min(float(open_), float(close)) - float(low)) / range_
    return body_signed, upper, lower


def candle_flow(rows, config=None, payout=None):
    """Fluxo direcional por EMA, RSI, força, pavios, PA, contexto e volatilidade."""
    config = config or {}
    if len(rows) < 35:
        return {"signal": 0, "score": 0.0, "reason": "amostra<35", "components": {}}
    closes = [float(row[4]) for row in rows]
    ema9, ema21 = _ema(closes, 9), _ema(closes, 21)
    rsi = _rsi(closes, 14)
    current = rows[-1]
    body_signed, upper_wick, lower_wick = _shape(current)
    ranges = [max(float(row[2]) - float(row[3]), 1e-12) for row in rows]
    median_range = median(ranges[-20:])
    candle_force = abs(float(current[4]) - float(current[1])) / max(median_range, 1e-12)
    atr14 = sum(_true_ranges(rows)[-14:]) / 14.0
    historical_atr = []
    tr = _true_ranges(rows)
    for index in range(14, len(tr) + 1):
        historical_atr.append(sum(tr[index - 14:index]) / 14.0)
    baseline_atr = median(historical_atr[-30:]) if historical_atr else atr14
    volatility_ratio = atr14 / max(baseline_atr, 1e-12)
    last_dirs = [1 if row[4] > row[1] else -1 if row[4] < row[1] else 0 for row in rows[-5:]]
    recent_bias = sum(last_dirs)
    higher = current[2] >= rows[-2][2] and current[3] >= rows[-2][3]
    lower = current[2] <= rows[-2][2] and current[3] <= rows[-2][3]
    configured_payout = float(payout if payout is not None else config.get("payout_fallback", 0.88))
    min_payout = float(config.get("min_payout", 0.70))
    if configured_payout < min_payout:
        return {"signal": 0, "score": 0.0, "reason": f"payout={configured_payout:.2f}<min={min_payout:.2f}", "components": {"payout": configured_payout}}

    votes = {1: [], -1: []}
    if ema9[-1] > ema21[-1]:
        votes[1].append("EMA9>EMA21")
    elif ema9[-1] < ema21[-1]:
        votes[-1].append("EMA9<EMA21")
    if rsi is not None and rsi >= float(config.get("rsi_call", 52.0)):
        votes[1].append(f"RSI={rsi:.1f}")
    elif rsi is not None and rsi <= float(config.get("rsi_put", 48.0)):
        votes[-1].append(f"RSI={rsi:.1f}")
    min_force = float(config.get("min_candle_force", 0.55))
    if candle_force >= min_force and body_signed > 0:
        votes[1].append(f"FORCA={candle_force:.2f}")
    elif candle_force >= min_force and body_signed < 0:
        votes[-1].append(f"FORCA={candle_force:.2f}")
    if lower_wick > upper_wick * 1.25 and body_signed > 0:
        votes[1].append("PAVIO_INFERIOR")
    elif upper_wick > lower_wick * 1.25 and body_signed < 0:
        votes[-1].append("PAVIO_SUPERIOR")
    if higher:
        votes[1].append("PA_HH_HL")
    elif lower:
        votes[-1].append("PA_LH_LL")
    if recent_bias >= 2:
        votes[1].append(f"ULTIMAS5={recent_bias:+d}")
    elif recent_bias <= -2:
        votes[-1].append(f"ULTIMAS5={recent_bias:+d}")
    vol_min = float(config.get("volatility_min_ratio", 0.65))
    vol_max = float(config.get("volatility_max_ratio", 1.80))
    volatility_ok = vol_min <= volatility_ratio <= vol_max
    if not volatility_ok:
        return {"signal": 0, "score": 0.0, "reason": f"volatilidade={volatility_ratio:.2f} fora [{vol_min:.2f},{vol_max:.2f}]", "components": {"volatility": volatility_ratio, "payout": configured_payout}}

    call_votes, put_votes = len(votes[1]), len(votes[-1])
    required = int(config.get("min_confirmations", 4))
    signal = 1 if call_votes >= required and call_votes >= put_votes + 2 else -1 if put_votes >= required and put_votes >= call_votes + 2 else 0
    selected = votes.get(signal, []) if signal else []
    score = min(9.0, 4.0 + len(selected) * 0.75 + min(candle_force, 2.0) * 0.5) if signal else 0.0
    components = {"ema9": ema9[-1], "ema21": ema21[-1], "rsi": rsi, "candle_force": candle_force,
                  "upper_wick": upper_wick, "lower_wick": lower_wick, "recent_bias": recent_bias,
                  "volatility": volatility_ratio, "payout": configured_payout, "votes": selected}
    return {"signal": signal, "score": score, "reason": ",".join(selected) if selected else "sem consenso", "components": components}


def revz(rows, config=None):
    """Reversão estatística com Z-score de 120 velas e confirmação obrigatória."""
    config = config or {}
    lookback = int(config.get("lookback", 120))
    if len(rows) < lookback + 2:
        return {"signal": 0, "score": 0.0, "reason": f"amostra<{lookback + 2}", "components": {}}
    closes = [float(row[4]) for row in rows]

    def zscore(values):
        sample = values[-lookback:]
        mean = sum(sample) / lookback
        std = math.sqrt(sum((value - mean) ** 2 for value in sample) / lookback)
        return ((sample[-1] - mean) / std if std > 1e-12 else 0.0), mean, std

    current_z, mean, std = zscore(closes)
    previous_z, _, _ = zscore(closes[:-1])
    threshold = float(config.get("z_threshold", 2.0))
    body_signed, upper_wick, lower_wick = _shape(rows[-1])
    toward_mean = (current_z < 0 and body_signed > 0) or (current_z > 0 and body_signed < 0)
    z_receding = abs(current_z) < abs(previous_z)
    rejection = (current_z < 0 and lower_wick >= upper_wick) or (current_z > 0 and upper_wick >= lower_wick)
    confirmation = toward_mean and z_receding and rejection
    signal = 1 if previous_z <= -threshold and confirmation else -1 if previous_z >= threshold and confirmation else 0
    score = min(9.0, 5.0 + max(0.0, abs(previous_z) - threshold) + (0.75 if rejection else 0.0)) if signal else 0.0
    components = {"z": current_z, "previous_z": previous_z, "mean": mean, "std": std,
                  "toward_mean": toward_mean, "z_receding": z_receding, "rejection": rejection}
    reason = f"z={previous_z:.2f}->{current_z:.2f};reversao_confirmada" if signal else f"z={previous_z:.2f}->{current_z:.2f};sem_confirmacao"
    return {"signal": signal, "score": score, "reason": reason, "components": components}


def _cluster_level(points, price, tolerance, side):
    candidates = []
    for point in points:
        if side == "support" and point > price + tolerance:
            continue
        if side == "resistance" and point < price - tolerance:
            continue
        touches = sum(1 for other in points if abs(other - point) <= tolerance)
        candidates.append((touches, -abs(price - point), point))
    return max(candidates)[2] if candidates else None


def sr_nivel(rows, config=None):
    """Suporte/resistência objetivo por pivôs, toque/proximidade e rejeição."""
    config = config or {}
    lookback = int(config.get("lookback", 60))
    if len(rows) < lookback + 5:
        return {"signal": 0, "score": 0.0, "reason": f"amostra<{lookback + 5}", "components": {}}
    history = rows[-(lookback + 1):-1]
    current = rows[-1]
    atr14 = sum(_true_ranges(rows)[-14:]) / 14.0
    tolerance = max(atr14 * float(config.get("atr_tolerance", 0.25)), 1e-12)
    pivot_highs, pivot_lows = [], []
    for index in range(2, len(history) - 2):
        row = history[index]
        around = history[index - 2:index] + history[index + 1:index + 3]
        if all(float(row[2]) >= float(other[2]) for other in around):
            pivot_highs.append(float(row[2]))
        if all(float(row[3]) <= float(other[3]) for other in around):
            pivot_lows.append(float(row[3]))
    price = float(current[4])
    support = _cluster_level(pivot_lows, price, tolerance, "support")
    resistance = _cluster_level(pivot_highs, price, tolerance, "resistance")
    body_signed, upper_wick, lower_wick = _shape(current)
    support_touch = support is not None and float(current[3]) <= support + tolerance and price >= support - tolerance
    resistance_touch = resistance is not None and float(current[2]) >= resistance - tolerance and price <= resistance + tolerance
    support_rejection = support_touch and body_signed > 0 and lower_wick >= upper_wick
    resistance_rejection = resistance_touch and body_signed < 0 and upper_wick >= lower_wick
    signal = 1 if support_rejection and not resistance_rejection else -1 if resistance_rejection and not support_rejection else 0
    points = pivot_lows if signal == 1 else pivot_highs if signal == -1 else []
    level = support if signal == 1 else resistance if signal == -1 else None
    touches = sum(1 for point in points if level is not None and abs(point - level) <= tolerance)
    minimum_touches = int(config.get("min_touches", 2))
    if signal and touches < minimum_touches:
        signal = 0
    score = min(9.0, 4.5 + min(touches, 4) * 0.65 + (0.75 if signal else 0.0)) if signal else 0.0
    components = {"support": support, "resistance": resistance, "tolerance": tolerance,
                  "support_touch": support_touch, "resistance_touch": resistance_touch,
                  "support_rejection": support_rejection, "resistance_rejection": resistance_rejection,
                  "touches": touches}
    reason = f"nivel={level:.6f};toques={touches};rejeicao" if signal and level is not None else "sem toque/rejeicao confirmados"
    return {"signal": signal, "score": score, "reason": reason, "components": components}

