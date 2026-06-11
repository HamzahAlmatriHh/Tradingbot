"""
Patch script: يضيف دالة get_htf_trend ويحدث توقيع evaluate_trend
"""
import re

path = "engines/ta_engine.py"

with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()

# --- البحث عن سطر "def evaluate_trend" ---
et_line_idx = None
for i, line in enumerate(lines):
    if "def evaluate_trend(self, df: pd.DataFrame" in line:
        et_line_idx = i
        break

if et_line_idx is None:
    print("ERROR: could not find evaluate_trend")
    exit(1)

print(f"Found evaluate_trend at line {et_line_idx + 1}: {lines[et_line_idx].rstrip()}")

# --- 1. تحديث توقيع evaluate_trend لإضافة htf_trend ---
original_sig = lines[et_line_idx]
if "htf_trend" not in original_sig:
    new_sig = original_sig.rstrip().rstrip(":")
    # أضف المعامل قبل القوس الأخير
    new_sig = new_sig.replace(
        "details: dict = None)",
        'details: dict = None, htf_trend: str = "neutral")'
    )
    new_sig += ":\n"
    lines[et_line_idx] = new_sig
    print(f"Updated signature: {new_sig.rstrip()}")
else:
    print("Signature already has htf_trend — skipping")

# --- 2. تحديث docstring (السطر التالي للتعريف) ---
# نبحث عن السطر الأول للـ docstring بعد evaluate_trend
doc_start = et_line_idx + 1
doc_end = doc_start
for i in range(doc_start, doc_start + 10):
    if i < len(lines) and '"""' in lines[i]:
        doc_end = i
        break

# استبدل محتوى الـ docstring
new_docstring = (
    '        """\n'
    '        \u062a\u0642\u064a\u064a\u0645 \u0645\u0646\u0627\u0637\u0642 \u0627\u0644\u062f\u062e\u0648\u0644 \u0627\u0644\u0627\u0633\u062a\u0631\u0627\u062a\u064a\u062c\u064a\u0629 \u0628\u0646\u0627\u0621\u064b \u0639\u0644\u0649 \u0627\u0644\u0642\u0648\u0627\u0639\u062f \u0627\u0644\u0627\u062d\u062a\u0631\u0627\u0641\u064a\u0629.\n'
    '        htf_trend: \u0627\u0644\u062a\u0631\u0646\u062f \u0645\u0646 \u0627\u0644\u0625\u0637\u0627\u0631 \u0627\u0644\u0632\u0645\u0646\u064a \u0627\u0644\u0623\u0639\u0644\u0649 (1H) \u2014 \u064a\u064f\u0633\u062a\u062e\u062f\u0645 \u0643\u0641\u064a\u062a\u0648.\n'
    '        """\n'
)
lines[doc_start:doc_end + 1] = [new_docstring]
print(f"Updated docstring at lines {doc_start+1}–{doc_end+1}")

# --- 3. إدراج دالة get_htf_trend قبل evaluate_trend ---
# نحسب الـ idx مجدداً بعد تعديل الـ lines
for i, line in enumerate(lines):
    if "def evaluate_trend(self, df: pd.DataFrame" in line:
        et_line_idx = i
        break

htf_method = '''    def get_htf_trend(self, symbol: str, client) -> str:
        """
        \u2705 \u062c\u062f\u064a\u062f: \u0641\u0644\u062a\u0631 \u0627\u0644\u062a\u0631\u0646\u062f \u0627\u0644\u0639\u0644\u0648\u064a (Higher TimeFrame Confirmation)
        \u064a\u062c\u0644\u0628 \u0628\u064a\u0627\u0646\u0627\u062a 1H \u0648\u064a\u0642\u064a\u0651\u0645 \u0627\u0644\u0627\u062a\u062c\u0627\u0647 \u0628\u0646\u0627\u0621\u064b \u0639\u0644\u0649 EMA50 \u0648 EMA200.
        \u064a\u064f\u0633\u062a\u062e\u062f\u0645 \u0643\u0641\u064a\u062a\u0648: \u0644\u0627 \u0646\u062f\u062e\u0644 LONG \u0639\u0644\u0649 15m \u0625\u0630\u0627 \u0643\u0627\u0646 1H \u0647\u0627\u0628\u0637\u0627\u064b \u0648\u0627\u0644\u0639\u0643\u0633.
        \u0627\u0644\u0646\u062a\u064a\u062c\u0629 \u0645\u062e\u0632\u0651\u0646\u0629 \u0641\u064a cache \u0644\u0645\u062f\u0629 15 \u062f\u0642\u064a\u0642\u0629 \u0644\u062a\u062c\u0646\u0628 \u0627\u0633\u062a\u062f\u0639\u0627\u0621\u0627\u062a API \u0645\u062a\u0643\u0631\u0631\u0629.
        """
        import time
        now = time.time()
        cached = _htf_cache.get(symbol)
        if cached and (now - cached["ts"]) < HTF_CACHE_TTL_SECONDS:
            return cached["trend"]
        try:
            bars_1h = client.fetch_ohlcv(symbol, timeframe='1h', limit=60)
            if bars_1h is None or len(bars_1h) < 40:
                return "neutral"
            df_htf = bars_1h.copy()
            df_htf['ema_50']  = ta.trend.EMAIndicator(close=df_htf['close'], window=50).ema_indicator()
            df_htf['ema_200'] = ta.trend.EMAIndicator(close=df_htf['close'], window=200).ema_indicator()
            last_htf = df_htf.iloc[-2]
            ema50  = last_htf.get('ema_50',  0)
            ema200 = last_htf.get('ema_200', 0)
            close  = last_htf.get('close',   0)
            if ema50 > ema200 and close > ema50:
                trend = "bullish"
            elif ema50 < ema200 and close < ema50:
                trend = "bearish"
            else:
                trend = "neutral"
            _htf_cache[symbol] = {"trend": trend, "ts": now}
            logger.debug(f"[HTF 1H] {symbol}: EMA50={ema50:.4f} | EMA200={ema200:.4f} --> Trend={trend.upper()}")
            return trend
        except Exception as e:
            logger.warning(f"[HTF] \u0641\u0634\u0644 \u062c\u0644\u0628 \u0628\u064a\u0627\u0646\u0627\u062a 1H \u0644\u0640 {symbol}: {e}")
            return "neutral"

'''

lines.insert(et_line_idx, htf_method)
print(f"Inserted get_htf_trend before line {et_line_idx + 1}")

with open(path, "w", encoding="utf-8") as f:
    f.writelines(lines)

print("✅ Patch applied successfully!")

# تحقق من الملف
with open(path, "r", encoding="utf-8") as f:
    content = f.read()
assert "get_htf_trend" in content, "get_htf_trend not found!"
assert 'htf_trend: str = "neutral"' in content, "htf_trend parameter not found!"
print("✅ Verification passed.")
