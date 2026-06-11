"""
Telegram Mini App — Flask Dashboard Server
يعمل في خيط منفصل على port 8080 بجانب البوت الرئيسي.
"""
import os
import json
import math
import threading
import pandas as pd
from flask import Flask, jsonify, render_template
from datetime import datetime
from core.config import Config
from core.logger import logger

app = Flask(__name__, template_folder="templates", static_folder="static")

_state_manager = None

def init_webapp(state_manager=None):
    global _state_manager
    _state_manager = state_manager

# ------------------------------------------------------------------
# Data helpers
# ------------------------------------------------------------------

def _load_trades() -> list:
    csv_path = getattr(Config, "TESTNET_TRADES_LOG", "/app/data/testnet_trades_log.csv")
    if not os.path.exists(csv_path):
        return []
    try:
        df = pd.read_csv(csv_path)
        df["entry_time"] = pd.to_datetime(df["entry_time"], errors="coerce")
        df["exit_time"]  = pd.to_datetime(df["exit_time"],  errors="coerce")
        df = df[df["exit_time"].notna()].copy()
        for col in ["pnl", "pnl_pct", "entry_price", "exit_price", "roe_pct",
                    "wallet_equity_at_entry", "wallet_pnl_pct"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        if "roe_pct" not in df.columns:
            df["roe_pct"] = 0.0
        df = df.sort_values("exit_time", ascending=False)
        records = []
        for _, row in df.head(60).iterrows():
            records.append({
                "symbol":      str(row.get("symbol", "")),
                "side":        str(row.get("side", "")).upper(),
                "entry_price": float(row.get("entry_price", 0)),
                "exit_price":  float(row.get("exit_price", 0)),
                "pnl":         float(row.get("pnl", 0)),
                "pnl_pct":     float(row.get("pnl_pct", 0)),
                "roe_pct":     float(row.get("roe_pct", 0)),
                "exit_reason": str(row.get("exit_reason", "")),
                "entry_time":  row["entry_time"].strftime("%m-%d %H:%M") if pd.notna(row["entry_time"]) else "",
                "exit_time":   row["exit_time"].strftime("%m-%d %H:%M")  if pd.notna(row["exit_time"])  else "",
            })
        return records
    except Exception as e:
        logger.error(f"[WebApp] Error loading trades: {e}")
        return []

def _load_state() -> dict:
    state_file = os.getenv("STATE_FILE", "/app/data/bot_state.json")
    if not os.path.exists(state_file):
        return {}
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _compute_stats(trades: list) -> dict:
    if not trades:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                "total_pnl": 0.0, "profit_factor": 0.0, "best_trade": 0.0, "worst_trade": 0.0}
    pnls    = [t["pnl"] for t in trades]
    winners = [p for p in pnls if p > 0]
    losers  = [p for p in pnls if p <= 0]
    gross_profit = sum(winners)
    gross_loss   = abs(sum(losers))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
    return {
        "total":         len(trades),
        "wins":          len(winners),
        "losses":        len(losers),
        "win_rate":      round(len(winners) / len(trades) * 100, 1) if trades else 0.0,
        "total_pnl":     round(sum(pnls), 4),
        "profit_factor": round(pf, 2),
        "best_trade":    round(max(pnls), 4),
        "worst_trade":   round(min(pnls), 4),
    }

def _build_chart_data(trades: list) -> dict:
    """آخر 30 صفقة بالترتيب الزمني لرسم منحنى تراكمي"""
    recent = list(reversed(trades[:30]))
    labels  = [t["exit_time"][-5:] for t in recent]
    running = 0.0
    data    = []
    for t in recent:
        running += t["pnl"]
        data.append(round(running, 4))
    bar_colors = ["rgba(52,211,153,.85)" if t["pnl"] >= 0 else "rgba(248,113,113,.85)" for t in recent]
    return {"labels": labels, "cumulative": data, "bar_colors": bar_colors}

# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("dashboard.html")

@app.route("/api/dashboard")
def api_dashboard():
    trades = _load_trades()
    state  = _load_state()
    stats  = _compute_stats(trades)
    chart  = _build_chart_data(trades)

    # Positions from state
    open_positions = []
    for symbol, meta in state.get("entry_metadata", {}).items():
        open_positions.append({
            "symbol":      symbol,
            "side":        str(meta.get("side", "")).upper(),
            "entry_price": float(meta.get("entry_price", 0) or 0),
            "sl":          float(meta.get("current_sl") or meta.get("sl") or 0),
            "tp":          float(meta.get("tp1") or meta.get("tp") or 0),
            "entry_time":  str(meta.get("entry_time", "")),
            "partial_done": bool(meta.get("partial_tp_done", False)),
        })

    wallet = state.get("simulated_wallet", {})
    ref_balance = float(getattr(Config, "REFERENCE_BALANCE", 50.0))

    return jsonify({
        "stats":          stats,
        "trades":         trades[:25],
        "open_positions": open_positions,
        "chart":          chart,
        "wallet":         wallet,
        "ref_balance":    ref_balance,
        "updated_at":     datetime.now().strftime("%H:%M:%S"),
    })

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

# ------------------------------------------------------------------
# Launcher
# ------------------------------------------------------------------

def start_webapp(state_manager=None, port: int = 8080):
    """يشغّل Flask في خيط daemon منفصل"""
    init_webapp(state_manager)

    def _run():
        try:
            logger.info(f"[WebApp] Starting dashboard on port {port}...")
            import logging
            log = logging.getLogger("werkzeug")
            log.setLevel(logging.ERROR)   # تكتيم سجلات Flask الصاخبة
            app.run(host="0.0.0.0", port=port, debug=False,
                    use_reloader=False, threaded=True)
        except Exception as e:
            logger.error(f"[WebApp] Server error: {e}")

    t = threading.Thread(target=_run, daemon=True, name="WebAppServer")
    t.start()
    logger.info("[WebApp] Dashboard running at https://tradingbot-production-0b71.up.railway.app/")
    return t
