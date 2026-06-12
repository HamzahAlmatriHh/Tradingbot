"""
Telegram Mini App — Flask Dashboard Server
يعمل في خيط منفصل على port 8080 بجانب البوت الرئيسي.
"""
import os
import json
import threading
import pandas as pd
from flask import Flask, jsonify, render_template
from datetime import datetime
from core.config import Config
from core.logger import logger

app = Flask(__name__, template_folder="templates", static_folder="static")

_state_manager = None
_client = None

def init_webapp(state_manager=None, client=None):
    global _state_manager, _client
    _state_manager = state_manager
    _client = client

# ------------------------------------------------------------------
# Live Binance API helpers (بيانات حقيقية من باينانس مباشرة)
# ------------------------------------------------------------------

def _get_live_wallet() -> dict:
    """يجلب الرصيد الحقيقي من باينانس عبر الكلاينت الرئيسي"""
    global _client
    try:
        if not _client:
            raise Exception("Client not initialized in webapp")
        
        balance = _client.get_balance()
        if not balance:
            raise Exception("Empty balance returned")
            
        usdt    = balance.get("USDT", {})
        info    = balance.get("info", {})
        
        total   = float(usdt.get("total",  0) or 0)
        free    = float(usdt.get("free",   0) or 0)
        used    = float(usdt.get("used",   0) or 0)
        unrealized_pnl = float(info.get("totalUnrealizedProfit", 0) or 0)
        total_margin = float(info.get("totalMarginBalance", total))

        return {
            "wallet_balance":   round(total_margin, 2),
            "available":        round(free,  2),
            "used_margin":      round(used,  2),
            "unrealized_pnl":   round(unrealized_pnl, 4),
            "source":           "binance_live",
        }
    except Exception as e:
        logger.warning(f"[WebApp] تعذر جلب الرصيد من باينانس: {e}")
        return {
            "wallet_balance": 0,
            "available":      0,
            "used_margin":    0,
            "unrealized_pnl": 0,
            "source":         "unavailable",
        }


def _get_live_positions() -> list:
    """يجلب الصفقات المفتوحة الحقيقية عبر الكلاينت الرئيسي"""
    global _client
    try:
        if not _client:
            raise Exception("Client not initialized")
            
        state_meta = {}
        if _state_manager:
            state_meta = _state_manager.get_all_metadata()
            
        all_positions = _client.exchange.fetch_positions()
        active = []
        for pos in all_positions:
            contracts = float(pos.get("contracts", 0) or 0)
            if contracts == 0:
                continue

            entry  = float(pos.get("entryPrice",      0) or 0)
            mark   = float(pos.get("markPrice",        0) or 0)
            upnl   = float(pos.get("unrealizedPnl",    0) or 0)
            lev    = float(pos.get("leverage",         1) or 1)
            side   = "LONG" if contracts > 0 else "SHORT"
            symbol = str(pos.get("symbol", "")).split(":")[0]

            notional = entry * abs(contracts)
            margin   = notional / lev if lev > 0 else notional
            roe_pct  = (upnl / margin * 100) if margin > 0 else 0.0
            
            # جلب SL/TP من حالة البوت إذا كانت الصفقة مفتوحة عبر البوت
            bot_meta = state_meta.get(symbol, {})
            sl = float(bot_meta.get("sl", 0) or 0)
            tp = float(bot_meta.get("tp", 0) or 0)

            active.append({
                "symbol":          symbol,
                "side":            side,
                "contracts":       abs(contracts),
                "margin_used":     round(margin, 2),
                "entry_price":     round(entry, 4),
                "mark_price":      round(mark,  4),
                "unrealized_pnl":  round(upnl,  4),
                "roe_pct":         round(roe_pct, 2),
                "leverage":        int(lev),
                "sl":              sl if sl > 0 else None,
                "tp":              tp if tp > 0 else None,
                "source":          "binance_live",
            })
        return active

    except Exception as e:
        logger.warning(f"[WebApp] تعذر جلب الصفقات المفتوحة من باينانس: {e}")
        return _positions_from_state()


def _positions_from_state() -> list:
    """احتياطي: الصفقات المسجلة في state.json (بوت فقط)"""
    state = _load_state()
    result = []
    for symbol, meta in state.get("entry_metadata", {}).items():
        result.append({
            "symbol":         symbol,
            "side":           str(meta.get("side", "")).upper(),
            "contracts":      float(meta.get("amount", 0) or 0),
            "entry_price":    float(meta.get("entry_price", 0) or 0),
            "mark_price":     0.0,
            "unrealized_pnl": 0.0,
            "roe_pct":        0.0,
            "leverage":       int(meta.get("leverage", 1) or 1),
            "source":         "state_json",
        })
    return result

# ------------------------------------------------------------------
# Trade / State helpers
# ------------------------------------------------------------------

def _load_trades() -> list:
    # ── SQLite أولاً ─────────────────────────────────────────────
    try:
        from utils.db_manager import load_trades_df, DB_PATH
        if os.path.exists(DB_PATH):
            df = load_trades_df()
            if not df.empty:
                return _df_to_records(df)
    except Exception:
        pass

    # ── CSV احتياطي ───────────────────────────────────────────────
    csv_path = getattr(Config, "TESTNET_TRADES_LOG", "/app/data/testnet_trades_log.csv")
    if not os.path.exists(csv_path):
        return []
    try:
        df = pd.read_csv(csv_path)
        df["entry_time"] = pd.to_datetime(df["entry_time"], errors="coerce")
        df["exit_time"]  = pd.to_datetime(df["exit_time"],  errors="coerce")
        df = df[df["exit_time"].notna()].copy()
        for col in ["pnl", "pnl_pct", "entry_price", "exit_price"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        df["roe_pct"] = 0.0
        return _df_to_records(df)
    except Exception as e:
        logger.error(f"[WebApp] Error loading trades from CSV: {e}")
        return []


def _df_to_records(df: pd.DataFrame) -> list:
    """يحوّل DataFrame إلى قائمة dicts جاهزة لـ JSON."""
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
            "entry_time":  row["entry_time"].strftime("%m-%d %H:%M") if pd.notna(row.get("entry_time")) else "",
            "exit_time":   row["exit_time"].strftime("%m-%d %H:%M")  if pd.notna(row.get("exit_time"))  else "",
        })
    return records

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
    recent  = list(reversed(trades[:30]))
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
    trades         = _load_trades()
    stats          = _compute_stats(trades)
    chart          = _build_chart_data(trades)
    wallet         = _get_live_wallet()        # ← رصيد حقيقي من باينانس
    open_positions = _get_live_positions()     # ← صفقات حقيقية من باينانس

    return jsonify({
        "stats":          stats,
        "trades":         trades[:25],
        "open_positions": open_positions,
        "chart":          chart,
        "wallet":         wallet,
        "updated_at":     datetime.now().strftime("%H:%M:%S"),
    })

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

# ------------------------------------------------------------------
# Launcher
# ------------------------------------------------------------------

def start_webapp(state_manager=None, client=None, port: int = 8080):
    """يشغّل Flask في خيط daemon منفصل"""
    init_webapp(state_manager, client)

    def _run():
        try:
            logger.info(f"[WebApp] Starting dashboard on port {port}...")
            import logging
            log = logging.getLogger("werkzeug")
            log.setLevel(logging.ERROR)
            app.run(host="0.0.0.0", port=port, debug=False,
                    use_reloader=False, threaded=True)
        except Exception as e:
            logger.error(f"[WebApp] Server error: {e}")

    t = threading.Thread(target=_run, daemon=True, name="WebAppServer")
    t.start()
    logger.info("[WebApp] Dashboard running at https://tradingbot-production-0b71.up.railway.app/")
    return t
