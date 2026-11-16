#!/usr/bin/env python3
"""update.py 계산을 다른 방식(순수 파이썬 반복문)으로 다시 계산해 대조한다.

  python tests/test_offline.py
"""
import datetime as dt
import json
import statistics
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
FX = ROOT / "tests" / "fixtures"
BASE, FINAL = "2026-11-13", "2026-12-09"
STOCKS = ["005930.KS", "000660.KS", "035420.KS", "247540.KQ", "AAPL", "NVDA", "MSFT", "KO"]
TOL = 2e-6


def run(until, now, out):
    subprocess.run([sys.executable, "tests/make_fixtures.py", "--until", until], cwd=ROOT, check=True, capture_output=True)
    subprocess.run([sys.executable, "scripts/update.py", "--fixtures", "tests/fixtures", "--now", now,
                    "--dry-run", "--out", out], cwd=ROOT, check=True, capture_output=True)
    return json.loads(Path(out).read_text())


def load(sym):
    r = json.loads((FX / f"{sym}.json").read_text())["chart"]["result"][0]
    tz = ZoneInfo(r["meta"]["exchangeTimezoneName"])
    close = r["indicators"]["quote"][0]["close"]
    adj = (r["indicators"].get("adjclose") or [{"adjclose": close}])[0]["adjclose"]
    out = {}
    for t, c, a in zip(r["timestamp"], close, adj):
        out[dt.datetime.fromtimestamp(t, tz).date().isoformat()] = (c, a)
    return out, r["meta"]["currency"]


def asof(series, day):
    keys = [d for d in series if d <= day]
    return series[max(keys)]


def independent(upto=None):
    data = {s: load(s) for s in STOCKS + ["SPY"]}
    fx, _ = load("KRW=X")
    days = sorted({BASE} | {d for s in data for d in data[s][0] if d > BASE})
    if upto:
        days = [d for d in days if d <= upto]

    def krw(sym, day):  # 원화 환산 수정주가
        ser, cur = data[sym]
        return asof(ser, day)[1] * (asof(fx, day)[0] if cur == "USD" else 1.0)

    idx = {s: [krw(s, d) / krw(s, BASE) for d in days] for s in data}

    def vol(sym, day):  # 종목 자체 거래일 기준 최근 63개 일간 원화 수익률의 표본표준편차
        ser, cur = data[sym]
        own = [d for d in sorted(ser) if d <= day]
        px = [ser[d][1] * (asof(fx, d)[0] if cur == "USD" else 1.0) for d in own]
        rets = [px[i] / px[i - 1] - 1 for i in range(1, len(px))][-63:]
        return statistics.stdev(rets)

    n = len(STOCKS)
    bh = sum(idx[s][-1] for s in STOCKS) / n
    ew = rp = 1.0
    for t in range(1, len(days)):
        r = {s: idx[s][t] / idx[s][t - 1] - 1 for s in STOCKS}
        ew *= 1 + sum(r.values()) / n
        inv = {s: 1 / vol(s, days[t - 1]) for s in STOCKS}
        tot = sum(inv.values())
        rp *= 1 + sum(inv[s] / tot * r[s] for s in STOCKS)
    return days, {s: idx[s][-1] - 1 for s in idx}, {"p_bh": bh - 1, "p_ew": ew - 1, "p_rp": rp - 1}


def compare(label, got_rows, got_bms, ref_stock, ref_port):
    worst = 0.0
    for p in got_rows:
        worst = max(worst, abs(p["krwRet"] - ref_stock[p["symbol"]]))
    for b in got_bms:
        ref = ref_port[b["id"]] if b["id"] in ref_port else ref_stock["SPY"]
        worst = max(worst, abs(b["krwRet"] - ref))
    assert worst < TOL, f"{label}: 최대 오차 {worst}"
    print(f"OK  {label}: 종목·포트폴리오 수익률 일치 (최대 오차 {worst:.1e})")


def main():
    # 1) 기간 중간(한국 장중, 미국 추수감사절 휴장 다음 날)
    d = run("2026-11-27T05:00:00Z", "2026-11-27T05:00:00Z", "/tmp/sp_mid.json")
    days, st, pf = independent()
    assert d["current"]["dates"] == days
    compare("기간 중", d["current"]["pitches"], d["current"]["benchmarks"], st, pf)
    assert d["final"]["status"] == "pending"
    ranks = [(p["rank"], p["krwRet"]) for p in d["current"]["pitches"]]
    assert ranks == sorted(ranks, key=lambda x: -x[1]), "순위가 수익률 내림차순이 아님"
    nv = [p for p in d["current"]["pitches"] if p["symbol"] == "NVDA"]
    assert len(nv) == 2 and nv[0]["rank"] == nv[1]["rank"], "중복 추천은 공동 순위"
    for b in d["current"]["benchmarks"]:
        if b["kind"] == "portfolio":
            assert abs(sum(w["w"] for w in b["weights"]) - 1) < 1e-5 and len(b["weights"]) == 8
    ko = next(p for p in d["current"]["pitches"] if p["symbol"] == "KO")
    assert abs((1 + ko["localRet"]) * (1 + ko["fxRet"]) - 1 - ko["krwRet"]) < TOL, "원화수익률 = 현지수익률 × 환율"
    print("OK  순위·공동순위·비중 합계·환율 분해")

    # 2) 평가일 당일 한국 장 마감 후, 미국 장 마감 전 → 잠정
    d = run("2026-12-09T08:00:00Z", "2026-12-09T08:00:00Z", "/tmp/sp_prov.json")
    assert d["final"]["status"] == "provisional" and not d["final"]["locked"]
    us = next(p for p in d["final"]["pitches"] if p["symbol"] == "AAPL")
    assert us["priceDate"] == "2026-12-08"
    print("OK  12/9 미국 마감 전: 잠정 (미국 종목은 12/8 종가)")

    # 3) 평가일 이후 이틀이 더 지나도 최종표는 12/9 종가로 고정
    d = run("2026-12-11T08:00:00Z", "2026-12-11T08:00:00Z", "/tmp/sp_fin.json")
    f = d["final"]
    assert f["status"] == "final" and f["locked"] and f["asOfDate"] == FINAL
    days, st, pf = independent(upto=FINAL)
    compare("최종(12/9 종가)", f["pitches"], f["benchmarks"], st, pf)
    assert all(p["priceDate"] == FINAL for p in f["pitches"])
    ko = next(p for p in f["pitches"] if p["symbol"] == "KO")
    assert [x["date"] for x in ko["dividends"]] == ["2026-12-01"] and ko["divRet"] > 0.005, "배당 반영"
    assert d["current"]["meta"]["lastDate"] == "2026-12-11"
    cur_ko = next(p for p in d["current"]["pitches"] if p["symbol"] == "KO")
    assert cur_ko["krwRet"] != ko["krwRet"]
    print("OK  최종표는 12/9 종가 고정, 배당 포함 수익률, 대시보드는 계속 갱신")
    print("모든 검사 통과")


if __name__ == "__main__":
    main()
