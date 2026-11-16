#!/usr/bin/env python3
"""오프라인 테스트/데모용 가상 시세 생성기.

Yahoo Finance chart API와 같은 모양의 JSON을 tests/fixtures/ 에 만든다.
가격은 난수로 만든 가상의 값이며 실제 시세가 아니다.

  python tests/make_fixtures.py --until 2026-11-27T05:00:00Z
"""
import argparse
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

OUT = Path(__file__).resolve().parent / "fixtures"
US_HOLIDAYS = {"2025-11-27", "2025-12-25", "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
               "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26"}
KR_HOLIDAYS = {"2025-12-25", "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-03-02",
               "2026-05-01", "2026-05-05", "2026-05-25", "2026-06-03", "2026-08-17", "2026-09-24",
               "2026-09-25", "2026-10-05", "2026-10-09"}

KOSDAQ_DEMO = {"357780", "101160", "036540", "196170", "310210", "083650", "144960", "247540"}

# symbol: (통화, 시간대, 장 시작(현지), 장 마감(현지), 시작가, 연변동성, 연수익률, 배당[(날짜, 금액)])
SPEC = {
    "005930.KS": ("KRW", "Asia/Seoul", (9, 0), (15, 30), 98000, 0.30, 0.25, []),
    "000660.KS": ("KRW", "Asia/Seoul", (9, 0), (15, 30), 540000, 0.45, 0.50, []),
    "035420.KS": ("KRW", "Asia/Seoul", (9, 0), (15, 30), 250000, 0.33, -0.10, []),
    "247540.KQ": ("KRW", "Asia/Seoul", (9, 0), (15, 30), 150000, 0.60, -0.20, []),
    "AAPL": ("USD", "America/New_York", (9, 30), (16, 0), 270, 0.25, 0.10, [("2026-02-09", 0.26), ("2026-05-11", 0.27), ("2026-08-10", 0.27), ("2026-11-09", 0.27)]),
    "NVDA": ("USD", "America/New_York", (9, 30), (16, 0), 190, 0.45, 0.40, [("2026-03-11", 0.01), ("2026-06-10", 0.01), ("2026-09-09", 0.01), ("2026-12-02", 0.01)]),
    "MSFT": ("USD", "America/New_York", (9, 30), (16, 0), 510, 0.22, 0.12, [("2026-02-19", 0.91), ("2026-05-21", 0.91), ("2026-08-20", 0.91), ("2026-11-19", 0.96)]),
    "KO": ("USD", "America/New_York", (9, 30), (16, 0), 71, 0.15, 0.05, [("2026-03-13", 0.53), ("2026-06-12", 0.53), ("2026-09-14", 0.53), ("2026-12-01", 0.53)]),
    "SPY": ("USD", "America/New_York", (9, 30), (16, 0), 670, 0.15, 0.10, [("2026-03-20", 1.8), ("2026-06-19", 1.85), ("2026-09-18", 1.9)]),
    "KRW=X": ("KRW", "Europe/London", (0, 0), (23, 59), 1460, 0.08, -0.02, []),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", default="2026-11-27T05:00:00Z")
    ap.add_argument("--start", default="2025-11-17")
    ap.add_argument("--pitches", help="이 CSV의 모든 티커에 가상 시세를 만든다 (데모 화면용)")
    args = ap.parse_args()
    until = dt.datetime.fromisoformat(args.until.replace("Z", "+00:00"))
    OUT.mkdir(parents=True, exist_ok=True)
    for f in OUT.glob("*.json"):
        f.unlink()

    spec = dict(SPEC)
    if args.pitches:
        import csv, re, zlib
        for row in csv.DictReader(open(args.pitches, encoding="utf-8-sig")):
            t = row["ticker"].strip().upper()
            g = np.random.default_rng(zlib.crc32(t.encode()))
            if re.fullmatch(r"[0-9][0-9A-Z]{5}", t):
                sym = t + (".KQ" if t in KOSDAQ_DEMO else ".KS")
                one = ("KRW", "Asia/Seoul", (9, 0), (15, 30), float(g.integers(200, 3000)) * 100, float(g.uniform(0.25, 0.6)), float(g.uniform(-0.3, 0.5)), [])
            else:
                sym = t.replace(".", "-")
                one = ("USD", "America/New_York", (9, 30), (16, 0), float(g.uniform(20, 600)), float(g.uniform(0.2, 0.8)), float(g.uniform(-0.3, 0.6)), [])
            spec.setdefault(sym, one)

    for n, (sym, (cur, tzname, o, c, p0, vol, mu, divs)) in enumerate(spec.items()):
        tz = ZoneInfo(tzname)
        rng = np.random.default_rng(1000 + n)  # 종목마다 고정 난수 → until과 무관하게 같은 경로
        hol = KR_HOLIDAYS if tzname == "Asia/Seoul" else US_HOLIDAYS if tzname == "America/New_York" else set()
        ts, close = [], []
        d = dt.date.fromisoformat(args.start)
        price = float(p0)
        divmap = dict(divs)
        paid = {}
        while True:
            if d.weekday() < 5 and d.isoformat() not in hol:
                opn = dt.datetime(d.year, d.month, d.day, o[0], o[1], tzinfo=tz)
                cls = dt.datetime(d.year, d.month, d.day, c[0], c[1], tzinfo=tz)
                if opn > until:
                    break
                r = rng.normal(mu / 252, vol / np.sqrt(252))
                if d.isoformat() in divmap:  # 배당락
                    price -= divmap[d.isoformat()]
                    paid[d.isoformat()] = int(opn.timestamp())
                full = price * (1 + r)
                if until < cls:  # 장중: 하루 변동의 일부만 반영하고 현재 시각을 타임스탬프로
                    frac = (until - opn) / (cls - opn)
                    close.append(price * (1 + r * frac))
                    ts.append(int(until.timestamp()))
                else:
                    close.append(full)
                    ts.append(int(opn.timestamp()))
                price = full
            d += dt.timedelta(days=1)
            if dt.datetime(d.year, d.month, d.day, tzinfo=tz) > until:
                break
        close = np.array(close)
        if cur == "KRW" and sym != "KRW=X":
            close = np.round(close / 100) * 100
        # 수정주가: 배당락일 이전 가격에 (1 - 배당/전일종가)를 누적 적용
        adj = close.copy()
        dates = [dt.datetime.fromtimestamp(t, tz).date().isoformat() for t in ts]
        for ex, amt in divmap.items():
            if ex in dates and dates.index(ex) > 0:
                i = dates.index(ex)
                adj[:i] *= 1 - amt / close[i - 1]
        res = {
            "meta": {"currency": cur, "symbol": sym, "exchangeTimezoneName": tzname,
                     "fullExchangeName": {"Asia/Seoul": "KOSDAQ" if sym.endswith(".KQ") else "KSE",
                                          "America/New_York": "NasdaqGS", "Europe/London": "CCY"}[tzname],
                     "regularMarketPrice": float(close[-1]), "regularMarketTime": ts[-1]},
            "timestamp": ts,
            "events": {"dividends": {str(t): {"amount": divmap[d_], "date": t} for d_, t in paid.items()}},
            "indicators": {"quote": [{"close": [float(x) for x in close]}],
                           "adjclose": [{"adjclose": [float(x) for x in adj]}]},
        }
        if sym == "KRW=X":
            res["indicators"].pop("adjclose")
            res["events"] = {}
        (OUT / f"{sym}.json").write_text(json.dumps({"chart": {"result": [res], "error": None}}))
    print(f"fixtures → {OUT} (until {until.isoformat()})")


if __name__ == "__main__":
    main()
