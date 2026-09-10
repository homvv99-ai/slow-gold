import json, hashlib, subprocess, sys
from pathlib import Path
import pandas as pd
import requests

ROOT = Path(__file__).parent
SITE = ROOT / "site" / "data"
SITE.mkdir(parents=True, exist_ok=True)

subprocess.run([sys.executable, str(ROOT / "proof_engine.py")], check=True)

stats = json.loads((ROOT / "out" / "stats.json").read_text(encoding="utf-8"))
trades = pd.read_csv(ROOT / "out" / "ledger_trades.csv")
waits = pd.read_csv(ROOT / "out" / "ledger_waits.csv")
eq = pd.read_csv(ROOT / "out" / "daily_equity.csv", index_col=0)

k = requests.get("https://api.binance.com/api/v3/klines",
                 params={"symbol": "PAXGUSDT", "interval": "1d", "limit": 2},
                 timeout=20).json()
last_date = str(pd.to_datetime(k[-2][0], unit="ms", utc=True))[:10]
last_close = float(k[-2][4])

last = trades.iloc[-1]
in_mkt = str(last["exit_date"]) == "OPEN"
if in_mkt:
    days_state = (pd.Timestamp(last_date) - pd.Timestamp(last["entry_date"])).days
else:
    w = waits.iloc[-1]
    days_state = int(w["days"]) if str(w["end"]) == "NOW" else 0

signal = {"date": last_date, "state": "LONG" if in_mkt else "FLAT",
          "price": last_close,
          "entry_date": str(last["entry_date"]) if in_mkt else None,
          "entry_price": float(last["entry_price"]) if in_mkt else None,
          "days_in_state": int(days_state),
          "generated_at": stats["generated_at"]}

chain_f = SITE / "hashes.json"
chain = json.loads(chain_f.read_text(encoding="utf-8")) if chain_f.exists() else []
prev = chain[-1]["hash"] if chain else "GENESIS"
payload = (ROOT / "out" / "ledger_trades.csv").read_bytes() + prev.encode()
h = hashlib.sha256(payload).hexdigest()
today = stats["generated_at"][:10]
if not chain or chain[-1]["date"] != today:
    chain.append({"date": today, "hash": h, "rows": int(len(trades))})
chain_f.write_text(json.dumps(chain, indent=1), encoding="utf-8")

(SITE / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
(SITE / "signal.json").write_text(json.dumps(signal, ensure_ascii=False, indent=1), encoding="utf-8")
(SITE / "ledger_trades.json").write_text(json.dumps(trades.to_dict(orient="records"), ensure_ascii=False), encoding="utf-8")
(SITE / "ledger_waits.json").write_text(json.dumps(waits.to_dict(orient="records"), ensure_ascii=False), encoding="utf-8")
(SITE / "equity.json").write_text(json.dumps([[str(i)[:10], round(v, 2)] for i, v in eq["equity"].items()]), encoding="utf-8")

print(f"PUBLISHED {today} | state={signal['state']} | chain={len(chain)} | rows={len(trades)}")
