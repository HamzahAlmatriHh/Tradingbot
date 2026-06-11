"""
SQLite Database Manager — بديل CSV لتسجيل الصفقات
=======================================================
- يُنشئ قاعدة البيانات وجداولها تلقائياً عند أول تشغيل
- يُوفر دالة migrate_from_csv() لترحيل البيانات القديمة
- thread-safe عبر check_same_thread=False + قفل threading.Lock
- الـ PerformanceTracker يقرأ منه مباشرة بدل CSV
"""
import os
import sqlite3
import threading
import pandas as pd
from datetime import datetime
from core.logger import logger


DB_PATH = os.getenv("TRADES_DB", "/app/data/trades.db")

_lock = threading.Lock()


# ------------------------------------------------------------------
# Schema
# ------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id              TEXT UNIQUE NOT NULL,
    symbol                TEXT NOT NULL,
    side                  TEXT NOT NULL,
    entry_time            TEXT,
    entry_price           REAL DEFAULT 0,
    exit_time             TEXT,
    exit_price            REAL DEFAULT 0,
    pnl                   REAL DEFAULT 0,
    pnl_pct               REAL DEFAULT 0,
    exit_reason           TEXT DEFAULT '',
    entry_reason          TEXT DEFAULT '',
    sentiment_score       REAL DEFAULT 0,
    adx                   REAL DEFAULT 0,
    ema_200               REAL DEFAULT 0,
    atr                   REAL DEFAULT 0,
    spread                REAL DEFAULT 0,
    volume_24h            REAL DEFAULT 0,
    risk_pct              REAL DEFAULT 0,
    leverage              REAL DEFAULT 1,
    slippage              REAL DEFAULT 0,
    amount                REAL DEFAULT 0,
    filter_profile        TEXT DEFAULT '',
    journal_dir           TEXT DEFAULT '',
    wallet_equity_at_entry REAL DEFAULT 0,
    wallet_pnl_pct        REAL DEFAULT 0,
    reference_balance     REAL DEFAULT 50,
    pnl_ref_50            REAL DEFAULT 0,
    created_at            TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_trades_exit_time  ON trades(exit_time);
CREATE INDEX IF NOT EXISTS idx_trades_symbol      ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_trade_id    ON trades(trade_id);
"""

# ------------------------------------------------------------------
# Connection
# ------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """يُنشئ قاعدة البيانات والجداول إذا لم تكن موجودة."""
    with _lock:
        try:
            conn = _get_conn()
            conn.executescript(SCHEMA)
            conn.commit()
            conn.close()
            logger.info(f"[DB] قاعدة بيانات SQLite جاهزة: {DB_PATH}")
        except Exception as e:
            logger.error(f"[DB] فشل تهيئة قاعدة البيانات: {e}")


# ------------------------------------------------------------------
# Write
# ------------------------------------------------------------------

def insert_trade(row: dict) -> bool:
    """
    يُدرج صفقة واحدة في قاعدة البيانات.
    يتجاهل التكرار بصمت (ON CONFLICT IGNORE).
    """
    with _lock:
        try:
            conn = _get_conn()
            cols = ", ".join(row.keys())
            placeholders = ", ".join(["?" for _ in row])
            sql = f"INSERT OR IGNORE INTO trades ({cols}) VALUES ({placeholders})"
            conn.execute(sql, list(row.values()))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"[DB] فشل إدراج الصفقة: {e}")
            return False


# ------------------------------------------------------------------
# Read
# ------------------------------------------------------------------

def load_trades_df() -> pd.DataFrame:
    """
    يُعيد جميع الصفقات المغلقة كـ DataFrame مرتبة حسب exit_time.
    يُستخدم من PerformanceTracker بدلاً من pd.read_csv().
    """
    try:
        if not os.path.exists(DB_PATH):
            return _empty_df()
        conn = _get_conn()
        df = pd.read_sql_query(
            "SELECT * FROM trades WHERE exit_time IS NOT NULL AND exit_time != '' ORDER BY exit_time ASC",
            conn
        )
        conn.close()

        if df.empty:
            return _empty_df()

        # تحويل أنواع البيانات
        df["entry_time"] = pd.to_datetime(df["entry_time"], errors="coerce")
        df["exit_time"]  = pd.to_datetime(df["exit_time"],  errors="coerce")

        numeric_cols = [
            "entry_price", "exit_price", "pnl", "pnl_pct", "amount",
            "risk_pct", "leverage", "slippage", "wallet_equity_at_entry",
            "wallet_pnl_pct", "reference_balance", "pnl_ref_50"
        ]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

        df["symbol"] = df["symbol"].fillna("").astype(str)
        df["side"]   = df["side"].fillna("").astype(str).str.upper()

        # حساب حقول مشتقة (مثل PerformanceTracker)
        df["entry_notional"] = (df["entry_price"] * df["amount"]).abs()
        df["leverage"] = df["leverage"].replace(0, 1).clip(lower=1)
        df["margin_used"] = df["entry_notional"] / df["leverage"]
        df["roe_pct"] = df.apply(
            lambda r: (r["pnl"] / r["margin_used"] * 100) if r["margin_used"] > 0 else 0.0,
            axis=1
        )

        return df

    except Exception as e:
        logger.error(f"[DB] فشل قراءة الصفقات: {e}")
        return _empty_df()


def trade_exists(trade_id: str) -> bool:
    """يتحقق إذا كانت الصفقة مسجلة مسبقاً (لمنع التكرار)."""
    try:
        if not os.path.exists(DB_PATH):
            return False
        with _lock:
            conn = _get_conn()
            row = conn.execute(
                "SELECT 1 FROM trades WHERE trade_id = ? LIMIT 1", (trade_id,)
            ).fetchone()
            conn.close()
            return row is not None
    except Exception:
        return False


def _empty_df() -> pd.DataFrame:
    cols = [
        "trade_id", "symbol", "side", "entry_time", "entry_price",
        "exit_time", "exit_price", "pnl", "pnl_pct", "exit_reason",
        "entry_reason", "amount", "risk_pct", "leverage", "slippage",
        "wallet_equity_at_entry", "wallet_pnl_pct", "reference_balance",
        "pnl_ref_50", "filter_profile", "journal_dir", "roe_pct"
    ]
    return pd.DataFrame(columns=cols)


# ------------------------------------------------------------------
# Migration: CSV → SQLite
# ------------------------------------------------------------------

def migrate_from_csv(csv_path: str) -> int:
    """
    يُرحّل الصفقات من ملف CSV القديم إلى SQLite.
    يتجاهل الصفقات المكررة (trade_id موجود مسبقاً).
    يُعيد عدد الصفقات التي تم ترحيلها.
    """
    if not os.path.exists(csv_path):
        logger.info(f"[DB] لا يوجد ملف CSV للترحيل: {csv_path}")
        return 0

    try:
        df = pd.read_csv(csv_path)
        df = df[df["exit_time"].notna()].copy()

        migrated = 0
        for _, row in df.iterrows():
            trade_row = {
                "trade_id":              str(row.get("trade_id", "")),
                "symbol":               str(row.get("symbol", "")),
                "side":                 str(row.get("side", "")),
                "entry_time":           str(row.get("entry_time", "")),
                "entry_price":          float(row.get("entry_price", 0) or 0),
                "exit_time":            str(row.get("exit_time", "")),
                "exit_price":           float(row.get("exit_price", 0) or 0),
                "pnl":                  float(row.get("pnl", 0) or 0),
                "pnl_pct":              float(row.get("pnl_pct", 0) or 0),
                "exit_reason":          str(row.get("exit_reason", "")),
                "entry_reason":         str(row.get("entry_reason", "")),
                "sentiment_score":      float(row.get("sentiment_score", 0) or 0),
                "adx":                  float(row.get("adx", 0) or 0),
                "ema_200":              float(row.get("ema_200", 0) or 0),
                "atr":                  float(row.get("atr", 0) or 0),
                "spread":               float(row.get("spread", 0) or 0),
                "volume_24h":           float(row.get("volume_24h", 0) or 0),
                "risk_pct":             float(row.get("risk_pct", 0) or 0),
                "leverage":             float(row.get("leverage", 1) or 1),
                "slippage":             float(row.get("slippage", 0) or 0),
                "amount":               float(row.get("amount", 0) or 0),
                "filter_profile":       str(row.get("filter_profile", "")),
                "journal_dir":          str(row.get("journal_dir", "")),
                "wallet_equity_at_entry": float(row.get("wallet_equity_at_entry", 0) or 0),
                "wallet_pnl_pct":       float(row.get("wallet_pnl_pct", 0) or 0),
                "reference_balance":    float(row.get("reference_balance", 50) or 50),
                "pnl_ref_50":           float(row.get("pnl_ref_50", 0) or 0),
            }
            if insert_trade(trade_row):
                migrated += 1

        logger.info(f"[DB] تم ترحيل {migrated} صفقة من CSV إلى SQLite.")
        return migrated

    except Exception as e:
        logger.error(f"[DB] فشل ترحيل CSV: {e}")
        return 0
