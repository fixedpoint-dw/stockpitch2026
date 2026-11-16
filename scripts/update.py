#!/usr/bin/env python3
"""Stock Pitch 성과 추적기 - 데이터 갱신 스크립트.

Yahoo Finance에서 일별 시세(수정주가 포함)와 환율을 받아
  1) 종목별 원화 기준 누적 총수익률(배당 재투자 가정)과 랭킹
  2) 동일가중 매수 후 보유 / 동일가중 리밸런싱 / 리스크 패리티 포트폴리오
  3) S&P 500 ETF 벤치마크
  4) 평가 기준일(final_date) 종가 기준 최종 평가표
를 계산해 Firebase Realtime Database에 저장한다.

사용 예
  python scripts/update.py                       # Yahoo 조회 → Firebase 저장
  python scripts/update.py --dry-run --out x.json  # Firebase 없이 결과만 파일로
  python scripts/update.py --fixtures tests/fixtures --now 2026-11-27T05:00:00Z --dry-run --out data/demo.json
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import re
import sys
import time
from urllib.parse import quote
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
KST = ZoneInfo("Asia/Seoul")

# 통화 단위 보정 (런던 상장 종목은 펜스 단위로 호가)
SUBUNIT = {"GBp": ("GBP", 0.01), "GBX": ("GBP", 0.01), "ZAc": ("ZAR", 0.01), "ILA": ("ILS", 0.01)}

MARKET_HOURS = {  # 정규장 (현지 시각)
    "KR": ("Asia/Seoul", dt.time(9, 0), dt.time(15, 30)),
    "US": ("America/New_York", dt.time(9, 30), dt.time(16, 0)),
}


class SymbolNotFound(Exception):
    pass


class FetchError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Yahoo Finance
# --------------------------------------------------------------------------- #
class Yahoo:
    """Yahoo Finance chart API 클라이언트 (일별 시세 + 배당/분할 이벤트)."""

    def __init__(self, fixtures: str | None = None, rng: str = "1y"):
        self.fixtures = Path(fixtures) if fixtures else None
        self.rng = rng
        self._session = None

    def _get_session(self):
        if self._session is None:
            try:  # 브라우저 TLS 지문을 흉내 내 차단을 줄인다
                from curl_cffi import requests as creq
                self._session = creq.Session(impersonate="chrome")
            except Exception:
                import requests
                self._session = requests.Session()
                self._session.headers["User-Agent"] = UA
        return self._session

    def chart(self, symbol: str) -> dict:
        if self.fixtures is not None:
            p = self.fixtures / f"{symbol}.json"
            if not p.exists():
                raise SymbolNotFound(symbol)
            return json.loads(p.read_text())["chart"]["result"][0]

        params = {"range": self.rng, "interval": "1d", "events": "div,splits",
                  "includePrePost": "false"}
        last = None
        for attempt in range(4):
            for host in HOSTS:
                url = f"https://{host}/v8/finance/chart/{quote(symbol, safe='=-.')}"   # ^KS11 → %5EKS11
                try:
                    r = self._get_session().get(url, params=params, timeout=25)
                    try:
                        j = r.json()
                    except Exception:
                        j = None
                    err = ((j or {}).get("chart") or {}).get("error")
                    if err and "not found" in str(err.get("code", "")).lower():
                        raise SymbolNotFound(symbol)
                    if r.status_code == 200 and j and j["chart"].get("result"):
                        return j["chart"]["result"][0]
                    last = f"HTTP {r.status_code}"
                except SymbolNotFound:
                    raise
                except Exception as e:  # 네트워크 오류 → 재시도
                    last = repr(e)
            time.sleep(2 ** attempt)
        raise FetchError(f"{symbol}: {last}")


class Asset:
    """한 종목(또는 환율)의 일별 시계열. 인덱스는 거래소 현지 날짜."""

    def __init__(self, symbol: str, res: dict):
        meta = res.get("meta", {})
        self.symbol = symbol
        self.meta = meta
        cur = meta.get("currency") or "USD"
        self.scale = 1.0
        if cur in SUBUNIT:
            cur, self.scale = SUBUNIT[cur]
        self.currency = cur
        self.tz = meta.get("exchangeTimezoneName") or "UTC"
        self.name = meta.get("longName") or meta.get("shortName") or symbol
        self.exchange = meta.get("fullExchangeName") or meta.get("exchangeName") or ""

        ts = res.get("timestamp") or []
        ind = res.get("indicators", {})
        close = (ind.get("quote") or [{}])[0].get("close") or []
        adj = (ind.get("adjclose") or [{}])[0].get("adjclose") or close
        if not ts or not close:
            raise SymbolNotFound(f"{symbol}: 시세 없음")
        local = pd.to_datetime(ts, unit="s", utc=True).tz_convert(self.tz)
        df = pd.DataFrame({
            "close": pd.to_numeric(pd.Series(close, dtype="float64")).values,
            "adj": pd.to_numeric(pd.Series(adj, dtype="float64")).values,
            "ts": np.asarray(ts, dtype="int64"),
        })
        df["date"] = local.strftime("%Y-%m-%d")
        df = df.dropna(subset=["close"])
        df = df[df["close"] > 0]
        df["adj"] = df["adj"].fillna(df["close"])
        df = df.groupby("date", sort=True).last()  # 같은 날짜가 두 번 오면(장중 봉) 마지막 값
        df.index = pd.to_datetime(df.index)
        df["close"] *= self.scale
        df["adj"] *= self.scale
        self.df = df

        self.dividends = []
        ev = res.get("events") or {}
        for d in (ev.get("dividends") or {}).values():
            when = pd.Timestamp(d["date"], unit="s", tz="UTC").tz_convert(self.tz)
            self.dividends.append({"date": when.strftime("%Y-%m-%d"),
                                   "amount": float(d["amount"]) * self.scale})
        self.dividends.sort(key=lambda x: x["date"])
        self.splits = []
        for s in (ev.get("splits") or {}).values():
            when = pd.Timestamp(s["date"], unit="s", tz="UTC").tz_convert(self.tz)
            self.splits.append({"date": when.strftime("%Y-%m-%d"),
                                "ratio": s.get("splitRatio") or f'{s.get("numerator")}:{s.get("denominator")}'})

    @property
    def market(self) -> str:
        return {"KRW": "KR", "USD": "US"}.get(self.currency, self.currency)


def resolve_symbol(yh: Yahoo, ticker: str) -> tuple[str, dict]:
    """학생이 적은 티커를 Yahoo 심볼로 바꾼다. 6자리 한국 종목코드는 .KS → .KQ 순으로 시도."""
    t = ticker.strip().upper()
    if re.fullmatch(r"[0-9][0-9A-Z]{5}", t):
        for suffix in (".KS", ".KQ"):
            try:
                return t + suffix, yh.chart(t + suffix)
            except SymbolNotFound:
                continue
        raise SymbolNotFound(ticker)
    t = t.replace(".", "-") if re.fullmatch(r"[A-Z]+\.[A-Z]", t) else t  # BRK.B → BRK-B
    return t, yh.chart(t)  # 지수는 ^KS11 처럼 Yahoo 심볼 그대로


def fx_symbol(cur: str) -> str:
    return "KRW=X" if cur == "USD" else f"{cur}KRW=X"


# --------------------------------------------------------------------------- #
# 계산
# --------------------------------------------------------------------------- #
def to_cal(s: pd.Series, cal: pd.DatetimeIndex) -> pd.Series:
    """시계열을 공통 달력에 맞춘다(직전 값으로 채움)."""
    return s.reindex(s.index.union(cal)).ffill().reindex(cal)


def max_drawdown(v: np.ndarray) -> float:
    peak = np.maximum.accumulate(v)
    return float((v / peak - 1).min())


def rebalance_mask(cal: pd.DatetimeIndex, mode: str) -> np.ndarray:
    n = len(cal)
    if mode == "none":
        return np.zeros(n, bool)
    if mode == "weekly":  # 매주 첫 거래일 종가에 리밸런싱
        wk = cal.isocalendar().week.values
        m = np.zeros(n, bool)
        m[1:] = wk[1:] != wk[:-1]
        return m
    return np.ones(n, bool)  # daily


def simulate(P: np.ndarray, target, mask: np.ndarray):
    """P: (T×N) 원화 총수익 지수. target(t) → t일 종가 기준 목표 비중.
    반환: 포트폴리오 지수 V(T), 마지막 날의 실제(표류) 비중."""
    T, N = P.shape
    V = np.ones(T)
    h = np.asarray(target(0), float)
    w_now = h.copy()
    for t in range(1, T):
        h = h * P[t] / P[t - 1]
        V[t] = h.sum()
        w_now = h / V[t]
        if mask[t]:
            h = np.asarray(target(t), float) * V[t]
    return V, w_now


def rank_desc(values: list[float]) -> list[int]:
    """공동 순위는 같은 등수(1, 2, 2, 4 방식)."""
    return [1 + sum(1 for o in values if o > v + 1e-12) for v in values]


def sid_of(prefix: str, symbol: str) -> str:
    return prefix + re.sub(r"[^0-9A-Za-z\-]", "_", symbol)


def clean(o):
    """NaN/None 제거 + 반올림 (Firebase는 null을 저장하지 않는다)."""
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items() if v is not None and not (isinstance(v, float) and not math.isfinite(v))}
    if isinstance(o, (list, tuple)):
        return [clean(v) if not (isinstance(v, float) and not math.isfinite(v)) else 0 for v in o]
    if isinstance(o, (np.floating, float)):
        return round(float(o), 6)
    if isinstance(o, np.integer):
        return int(o)
    return o


def market_state(market: str, last_bar_date: str, now: dt.datetime) -> str:
    if market not in MARKET_HOURS:
        return "unknown"
    tz, o, c = MARKET_HOURS[market]
    loc = now.astimezone(ZoneInfo(tz))
    if loc.weekday() < 5 and o <= loc.time() < c and last_bar_date == loc.strftime("%Y-%m-%d"):
        return "open"
    return "closed"


def build(settings: dict, pitches: list[dict], yh: Yahoo, now: dt.datetime) -> tuple[dict, dict]:
    base_date = pd.Timestamp(settings["base_date"])
    final_date = pd.Timestamp(settings["final_date"])
    lock_at = settings.get("final_lock_utc")
    lock_at = (dt.datetime.fromisoformat(lock_at.replace("Z", "+00:00")) if lock_at
               else (final_date + pd.Timedelta(days=1, hours=3)).to_pydatetime().replace(tzinfo=dt.timezone.utc))
    W = int(settings.get("vol_window_days", 63))
    min_obs = int(settings.get("vol_min_obs", 20))

    # 1) 시세 수집 ---------------------------------------------------------- #
    assets: dict[str, Asset] = {}
    rows, by_key, tick2sym = [], {}, {}
    for p in pitches:
        t = p["ticker"].strip().upper()
        if t not in tick2sym:
            try:
                sym, res = resolve_symbol(yh, p["ticker"])
                if sym not in assets:
                    assets[sym] = Asset(sym, res)
                tick2sym[t] = sym
            except SymbolNotFound:
                tick2sym[t] = None
        sym = tick2sym[t]
        key = sym or "ERR:" + t
        if key in by_key:  # 같은 종목을 여러 명이 추천 → 한 줄에 추천자를 모두 적는다
            row = by_key[key]
            if p["student"] and p["student"] not in row["students"]:
                row["students"].append(p["student"])
            row["name"] = row["name"] or p.get("name") or ""
            continue
        row = {"students": [p["student"]] if p["student"] else [], "ticker": p["ticker"], "name": p.get("name") or ""}
        if sym:
            row["symbol"] = sym
        else:
            row["error"] = "Yahoo Finance에서 종목을 찾지 못했습니다. 티커를 확인하세요."
        by_key[key] = row
        rows.append(row)
    for i, row in enumerate(rows):
        row["id"] = f"r{i + 1}"
        row["student"] = ", ".join(row["students"])
        row["nRec"] = len(row["students"])

    bench = []
    for b in settings.get("benchmarks", []):
        sym, res = resolve_symbol(yh, b["ticker"])
        bench.append({"symbol": sym, "name": b.get("name") or sym, "desc": b.get("desc"), "asset": Asset(sym, res)})

    fx: dict[str, Asset] = {}
    for a in list(assets.values()) + [b["asset"] for b in bench]:
        if a.currency != "KRW" and a.currency not in fx:
            s = fx_symbol(a.currency)
            fx[a.currency] = Asset(s, yh.chart(s))

    # 2) 공통 달력: 기준일 + 그 이후 어느 시장이든 거래가 있었던 날 ------------ #
    all_assets = list(assets.values()) + [b["asset"] for b in bench]
    days = set()
    for a in all_assets:
        days.update(d for d in a.df.index if d > base_date)
    cal = pd.DatetimeIndex(sorted({base_date} | days))
    T = len(cal)

    def frame(a: Asset):
        adj = to_cal(a.df["adj"], cal)
        close = to_cal(a.df["close"], cal)
        bar = to_cal(pd.Series(a.df.index, index=a.df.index), cal)
        f = to_cal(fx[a.currency].df["close"], cal) if a.currency != "KRW" else pd.Series(1.0, index=cal)
        krw = adj * f
        ok = bool(np.isfinite(krw.iloc[0])) and krw.iloc[0] > 0
        idx = (krw / krw.iloc[0]).values if ok else None
        # 3개월 이동 변동성: 원화 환산 일간 수익률의 표준편차(연율화), 종목 자체 거래일 기준
        f_own = to_cal(fx[a.currency].df["close"], a.df.index) if a.currency != "KRW" else 1.0
        r = (a.df["adj"] * f_own).pct_change()
        vol = to_cal(r.rolling(W, min_periods=min_obs).std() * math.sqrt(252), cal)
        return {"adj": adj.values, "close": close.values, "bar": bar, "fx": f.values, "idx": idx, "vol": vol.values, "ok": ok}

    frames = {s: frame(a) for s, a in assets.items()}
    for b in bench:
        b["frame"] = frame(b["asset"])
        if not b["frame"]["ok"]:
            raise FetchError(f"벤치마크 {b['symbol']}의 기준일 가격이 없습니다")
    for r in rows:
        if "symbol" in r and not frames[r["symbol"]]["ok"]:
            r["error"] = f"기준일({settings['base_date']}) 이전 가격이 없습니다."

    # 3) 포트폴리오 (중복 추천 종목은 한 번만 편입) --------------------------- #
    univ = [s for s in assets if frames[s]["ok"]]
    N = len(univ)
    ports = []
    if N:
        P = np.column_stack([frames[s]["idx"] for s in univ])
        VOL = np.column_stack([frames[s]["vol"] for s in univ])
        mask = rebalance_mask(cal, settings.get("rebalance", "daily"))
        eq = np.full(N, 1.0 / N)

        def rp(t):
            v = VOL[t].copy()
            good = np.isfinite(v) & (v > 0)
            v[~good] = np.median(v[good]) if good.any() else 1.0
            w = 1.0 / v
            return w / w.sum()

        specs = [
            ("p_bh", "동일가중 매수 후 보유", "기준일에 같은 금액씩 사서 그대로 보유", lambda t: eq, np.zeros(T, bool)),
            ("p_ew", "동일가중 리밸런싱", "비중을 계속 1/N로 유지", lambda t: eq, mask),
            ("p_rp", "리스크 패리티", "최근 3개월 변동성의 역수 비중", rp, mask),
        ]
        for pid, name, desc, fn, m in specs:
            ports.append({"id": pid, "name": name, "desc": desc, "kind": "portfolio", "fn": fn, "mask": m,
                          "V": simulate(P, fn, m)[0]})

    # 4) 임의 시점 k의 스냅샷 ------------------------------------------------ #
    def stock_at(sym: str, k: int) -> dict:
        a, f = assets.get(sym) or next(b["asset"] for b in bench if b["symbol"] == sym), None
        f = frames.get(sym) or next(b["frame"] for b in bench if b["symbol"] == sym)
        bar0, bark = f["bar"].iloc[0], f["bar"].iloc[k]
        own = a.df.index.get_loc(bark)
        local_day = float(a.df["close"].iloc[own] / a.df["close"].iloc[own - 1] - 1) if own > 0 else 0.0
        local_ret = f["adj"][k] / f["adj"][0] - 1
        price_ret = f["close"][k] / f["close"][0] - 1
        d0, dk = bar0.strftime("%Y-%m-%d"), bark.strftime("%Y-%m-%d")
        out = {
            "symbol": sym, "sid": sid_of("s_", sym), "market": a.market, "currency": a.currency,
            "exchange": a.exchange, "yahooName": a.name,
            "basePrice": float(f["close"][0]), "baseBarDate": d0,
            "price": float(f["close"][k]), "priceDate": dk,
            "localRet": local_ret, "priceRet": price_ret, "divRet": local_ret - price_ret,
            "fxRet": float(f["fx"][k] / f["fx"][0] - 1), "krwRet": float(f["idx"][k] - 1),
            "dayRet": float(f["idx"][k] / f["idx"][k - 1] - 1) if k > 0 else 0.0,
            "localDayRet": local_day,
            "mdd": max_drawdown(f["idx"][: k + 1]),
            "vol3m": float(f["vol"][k]) if np.isfinite(f["vol"][k]) else None,
            "dividends": [d for d in a.dividends if d0 < d["date"] <= dk],
            "splits": [s for s in a.splits if d0 < s["date"] <= dk],
        }
        if a.currency != "KRW":
            out["fxBase"], out["fxNow"] = float(f["fx"][0]), float(f["fx"][k])
        return out

    def snapshot(k: int) -> tuple[list, list]:
        out_rows = []
        for r in rows:
            o = {kk: r[kk] for kk in ("id", "student", "nRec", "ticker")}
            if "error" in r:
                o.update(name=r["name"] or r["ticker"], error=r["error"])
            else:
                o.update(stock_at(r["symbol"], k))
                o["name"] = r["name"] or o["yahooName"]
            out_rows.append(o)
        valid = [o for o in out_rows if "error" not in o]
        for o, rk in zip(valid, rank_desc([o["krwRet"] for o in valid])):
            o["rank"] = rk
        if k > 0:
            prev = [float(frames[o["symbol"]]["idx"][k - 1] - 1) for o in valid]
            for o, rk in zip(valid, rank_desc(prev)):
                o["prevRank"] = rk
        out_rows.sort(key=lambda o: (o.get("rank", 10 ** 6), o["student"]))

        bms = []
        for b in bench:
            o = stock_at(b["symbol"], k)
            o.update(id=sid_of("b_", b["symbol"]), sid=sid_of("b_", b["symbol"]), name=b["name"], kind="etf", desc=b["desc"])
            bms.append(o)
        for p in ports:
            V = p["V"]
            _, w = simulate(P[: k + 1], p["fn"], p["mask"][: k + 1])
            if k == 0:
                w = np.asarray(p["fn"](0), float)
            bms.append({
                "id": p["id"], "sid": p["id"], "name": p["name"], "desc": p["desc"], "kind": "portfolio",
                "krwRet": float(V[k] - 1), "dayRet": float(V[k] / V[k - 1] - 1) if k > 0 else 0.0,
                "mdd": max_drawdown(V[: k + 1]),
                "weights": [{"sid": sid_of("s_", s), "w": float(x)} for s, x in zip(univ, w)],
            })
        return out_rows, bms

    # 5) 현재 상태 ----------------------------------------------------------- #
    k_now = T - 1
    cur_rows, cur_bms = snapshot(k_now)
    series = {sid_of("s_", s): [round(float(x), 6) for x in frames[s]["idx"]] for s in univ}
    for b in bench:
        series[sid_of("b_", b["symbol"])] = [round(float(x), 6) for x in b["frame"]["idx"]]
    for p in ports:
        series[p["id"]] = [round(float(x), 6) for x in p["V"]]

    fx_info = {}
    for cur, a in fx.items():
        f = to_cal(a.df["close"], cal).values
        fx_info[cur] = {"symbol": a.symbol, "base": float(f[0]), "now": float(f[-1]),
                        "ret": float(f[-1] / f[0] - 1),
                        "dayRet": float(f[-1] / f[-2] - 1) if T > 1 else 0.0}

    markets = {}
    for a in all_assets:
        if a.market not in markets:
            markets[a.market] = {"state": market_state(a.market, a.df.index[-1].strftime("%Y-%m-%d"), now),
                                 "lastBar": a.df.index[-1].strftime("%Y-%m-%d")}

    stock_names = {}
    for o in cur_rows:
        if "sid" in o:
            stock_names.setdefault(o["sid"], o["name"])

    meta = {
        "title": settings.get("title", "Stock Pitch"), "subtitle": settings.get("subtitle", ""),
        "updatedAt": now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "baseDate": settings["base_date"], "finalDate": settings["final_date"],
        "finalLockAt": lock_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lastDate": cal[-1].strftime("%Y-%m-%d"),
        "rebalance": settings.get("rebalance", "daily"), "volWindow": W,
        "nRecommenders": sum(r["nRec"] for r in rows), "nRows": len(rows), "nStocks": N, "fx": fx_info, "markets": markets,
    }
    current = clean({"meta": meta, "dates": [d.strftime("%Y-%m-%d") for d in cal],
                     "pitches": cur_rows, "benchmarks": cur_bms, "series": series,
                     "stockNames": stock_names})

    # 6) 최종 평가 ----------------------------------------------------------- #
    if cal[-1] < final_date:
        status, k_fin = "pending", k_now
    else:
        k_fin = int(np.searchsorted(cal.values, final_date.to_datetime64(), side="right") - 1)
        status = "final" if now >= lock_at else "provisional"
    fin_rows, fin_bms = (cur_rows, cur_bms) if k_fin == k_now else snapshot(k_fin)
    fin_fx = {}
    for cur, a in fx.items():
        f = to_cal(a.df["close"], cal).values
        fin_fx[cur] = {"symbol": a.symbol, "base": float(f[0]), "now": float(f[k_fin]), "ret": float(f[k_fin] / f[0] - 1)}
    final = clean({
        "status": status, "locked": status == "final",
        "computedAt": meta["updatedAt"], "baseDate": settings["base_date"], "finalDate": settings["final_date"],
        "asOfDate": cal[k_fin].strftime("%Y-%m-%d"), "title": meta["title"],
        "rebalance": meta["rebalance"], "volWindow": W, "fx": fin_fx,
        "pitches": [{k: v for k, v in o.items() if k not in ("prevRank",)} for o in fin_rows],
        "benchmarks": [{k: v for k, v in o.items() if k != "weights"} for o in fin_bms],
    })
    return current, final


# --------------------------------------------------------------------------- #
# 입출력
# --------------------------------------------------------------------------- #
def load_pitches(path: Path) -> list[dict]:
    out = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            if not row.get("ticker") or next(iter(row.values()), "").startswith("#"):
                continue
            who = row.get("student") or row.get("recommender") or row.get("추천자") or row.get("학생") or ""
            out.append({"student": who, "ticker": row["ticker"], "name": row.get("name", "")})
    if not out:
        raise SystemExit("config/pitches.csv 에 종목이 없습니다.")
    return out


def history_range(settings: dict, now: dt.datetime) -> str:
    """기준일 이전 변동성 계산 구간(약 5개월)까지 덮는 가장 짧은 조회 기간. 사이트를 오래 운영해도 기준일 가격이 빠지지 않게 한다."""
    r = settings.get("history_range", "auto")
    if r != "auto":
        return r
    base = dt.datetime.fromisoformat(settings["base_date"]).replace(tzinfo=dt.timezone.utc)
    need = (now - base).days + 160
    for days, name in ((365, "1y"), (730, "2y"), (1825, "5y"), (3650, "10y")):
        if need <= days - 5:
            return name
    return "max"


def firebase_root(path: str):
    import firebase_admin
    from firebase_admin import credentials, db
    sa = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    url = os.environ.get("FIREBASE_DATABASE_URL")
    if not sa or not url:
        raise SystemExit("환경변수 FIREBASE_SERVICE_ACCOUNT, FIREBASE_DATABASE_URL 이 필요합니다 (README 참고).")
    cred = credentials.Certificate(json.loads(sa) if sa.lstrip().startswith("{") else sa)
    firebase_admin.initialize_app(cred, {"databaseURL": url.rstrip("/")})
    return db.reference(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="Firebase에 쓰지 않음")
    ap.add_argument("--out", help="현재 상태 JSON을 저장할 경로 ({current, final} 묶음)")
    ap.add_argument("--fixtures", help="Yahoo 대신 읽을 JSON 폴더 (오프라인 테스트용)")
    ap.add_argument("--now", help="현재 시각을 고정 (ISO8601, 테스트용)")
    ap.add_argument("--force-final", action="store_true", help="잠긴 최종 평가를 다시 계산해 덮어씀")
    ap.add_argument("--config-dir", default=str(ROOT / "config"), help="pitches.csv, settings.json 이 있는 폴더")
    ap.add_argument("--demo", action="store_true", help="결과에 데모 표시를 넣음")
    args = ap.parse_args()

    cfg_dir = Path(args.config_dir)
    settings = json.loads((cfg_dir / "settings.json").read_text(encoding="utf-8"))
    pitches = load_pitches(cfg_dir / "pitches.csv")
    now = (dt.datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now
           else dt.datetime.now(dt.timezone.utc))
    root = None if args.dry_run else firebase_root(settings.get("firebase_path", "stockpitch"))
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        current, final = build(settings, pitches, Yahoo(args.fixtures, history_range(settings, now)), now)
    except (FetchError, SymbolNotFound) as e:
        print(f"[오류] 시세 조회 실패: {e}", file=sys.stderr)
        if root is not None:  # 이전 데이터는 그대로 두고 상태만 기록
            root.child("status").set({"ok": False, "at": stamp, "message": str(e)[:300]})
        return 1

    if args.demo:
        current["meta"]["demo"] = True
        final["demo"] = True

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({"current": current, "final": final}, ensure_ascii=False), encoding="utf-8")

    bad = [p for p in current["pitches"] if "error" in p]
    if root is not None:
        root.child("current").set(current)
        prev = root.child("final").get() or {}
        if prev.get("locked") and not args.force_final:
            print("최종 평가는 이미 확정되어 그대로 둡니다 (--force-final 로 재계산).")
        else:
            root.child("final").set(final)
        root.child("status").set({"ok": True, "at": stamp,
                                  "message": f"{len(bad)}개 종목 오류" if bad else "정상"})

    m = current["meta"]
    print(f"[{stamp}] {m['lastDate']} 기준 · 추천자 {m['nRecommenders']}명 · 종목 {m['nStocks']}개 · 최종평가 {final['status']}")
    for p in current["pitches"][:5]:
        if "rank" in p:
            print(f"  {p['rank']:>2}위 {p['student']} {p['name']} {p['krwRet'] * 100:+.2f}%")
    for b in current["benchmarks"]:
        print(f"  [기준] {b['name']} {b['krwRet'] * 100:+.2f}%")
    for p in bad:
        print(f"  [확인 필요] {p['student']} {p['ticker']}: {p['error']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
