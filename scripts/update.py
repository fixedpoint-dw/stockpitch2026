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
  python scripts/update.py --loop                # 장중 내내 주기적으로 갱신 (GitHub Actions 가 쓰는 방식)
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
        if df.empty:
            raise SymbolNotFound(f"{symbol}: 시세 없음")
        self.df = df

        # Yahoo는 장이 끝난 뒤 한동안 마지막 거래일의 일봉을 비워서(null) 보내는 일이 있다(한국 종목에서 확인).
        # 그대로 두면 그날 수익률이 통째로 사라지므로, 시세 요약(meta)의 최근 체결가·체결 시각으로 그날 값을 채운다.
        self.notes = []
        rmp, rmt = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
        if rmp and rmt and float(rmp) > 0:
            qd = pd.Timestamp(pd.Timestamp(int(rmt), unit="s", tz="UTC").tz_convert(self.tz).strftime("%Y-%m-%d"))
            if qd > self.df.index[-1]:
                px = float(rmp) * self.scale
                self.df.loc[qd, ["close", "adj", "ts"]] = [px, px, int(rmt)]
                self.notes.append(f"{qd:%Y-%m-%d} 일봉 없음 → 시세 요약의 최근 체결가 사용")

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
            try:
                k = float(s.get("numerator")) / float(s.get("denominator"))
            except (TypeError, ValueError, ZeroDivisionError):
                k = 1.0
            self.splits.append({"date": when.strftime("%Y-%m-%d"), "k": k,
                                "ratio": s.get("splitRatio") or f'{s.get("numerator")}:{s.get("denominator")}'})
        self.splits.sort(key=lambda x: x["date"])

        # 수정주가가 비정상(0 이하이거나 종가와 동떨어진 값)이면 그 날은 종가로 대신한다
        r = self.df["adj"] / self.df["close"]
        bad = ~np.isfinite(r) | (r < 0.2) | (r > 1.05)
        if bad.any():
            self.df.loc[bad, "adj"] = self.df.loc[bad, "close"]
            self.notes.append(f"수정주가 이상 {int(bad.sum())}일 → 종가로 대체")

    def _split_scale(self, after: pd.Timestamp, upto: pd.Timestamp) -> float:
        """after 날짜의 가격을 upto 시점의 (분할 반영) 단위로 바꾸는 배율."""
        f = 1.0
        for sp in self.splits:
            if after < pd.Timestamp(sp["date"]) <= upto and sp["k"] > 0:
                f /= sp["k"]
        return f

    def _insert(self, day: pd.Timestamp, px: float, why: str) -> None:
        later = self.df[self.df.index > day]
        ratio = float(later["adj"].iloc[0] / later["close"].iloc[0]) if len(later) else 1.0
        nxt = later.index[0] if len(later) else None
        for dv in self.dividends:  # 그 사이에 배당락이 있으면 수정주가 비율을 한 단계 더 적용
            ex = pd.Timestamp(dv["date"])
            if ex > day and (nxt is None or ex <= nxt):
                ratio *= 1 - dv["amount"] / px
        self.df.loc[day, ["close", "adj", "ts"]] = [px, px * ratio, 0]
        self.df = self.df.sort_index()
        self.notes.append(f"{day:%Y-%m-%d} {why}")

    def repair(self, cached: dict | None, manual: dict | None, base_date: pd.Timestamp) -> None:
        """Yahoo 응답의 빈 곳을 메운다. 새 응답에 있는 날짜의 값은 그대로 두고, 없는 날짜만 채운다.

        1) 이전 실행에서 저장해 둔 종가로 빠진 날짜를 채움 (액면분할로 단위가 바뀌었으면 맞춰서)
        2) 액면분할 직후 과거 가격이 아직 분할 반영 전이면 직접 맞춤
        3) 그래도 기준일 가격이 없으면 설정 파일(base_prices)의 기준가를 사용
        """
        if getattr(self, "stale", False):
            return
        fresh_last = self.df.index[-1]
        entries, adjs = {}, {}
        for d, v in (cached or {}).items():
            if d.startswith("_"):
                continue
            try:
                c, a = (float(v[0]), float(v[1])) if isinstance(v, (list, tuple)) else (float(v), None)
                if c > 0:
                    entries[pd.Timestamp(d)] = c
                    if a and a > 0:
                        adjs[pd.Timestamp(d)] = a
            except (TypeError, ValueError, IndexError):
                continue
        missing = sorted((d for d in entries if d not in self.df.index), reverse=True)
        if missing:
            both = [d for d in entries if d in self.df.index and d != fresh_last]
            if both:  # 양쪽에 다 있는 날짜로 단위(분할 반영 여부)를 맞춘다
                scale = float(np.median([self.df.at[d, "close"] / entries[d] for d in both]))
                if abs(math.log(scale)) < 0.02:
                    scale = 1.0
            else:
                scale = self._split_scale(max(entries), fresh_last)
            # 수정주가도 겹치는 날짜로 단위를 맞춰 그대로 쓴다 (배당으로 과거 수정주가가 일괄 조정된 경우 포함)
            both_adj = [d for d in both if d in adjs]
            adj_scale = float(np.median([self.df.at[d, "adj"] / adjs[d] for d in both_adj])) if both_adj else None
            filled = []
            for d in missing:
                if adj_scale is not None and d in adjs:
                    self.df.loc[d, ["close", "adj", "ts"]] = [entries[d] * scale, adjs[d] * adj_scale, 0]
                    filled.append(d)
                else:
                    self._insert(d, entries[d] * scale, "일봉 없음 → 이전 실행에서 저장한 종가 사용")
            if filled:
                self.df = self.df.sort_index()
                self.notes.append(f"{min(filled):%Y-%m-%d}~{max(filled):%Y-%m-%d} 중 {len(filled)}일 일봉 없음 → 이전 실행에서 저장한 가격 사용")

        for sp in self.splits:  # 분할 전후 가격이 서로 다른 단위로 섞여 있으면 최근 가격의 단위로 통일
            k, D = sp["k"], pd.Timestamp(sp["date"])
            if not (k >= 1.8 or 0 < k <= 0.55):
                continue
            near = [i for i, d in enumerate(self.df.index) if i > 0 and abs((d - D).days) <= 5]
            for i in near:
                jump = float(self.df["close"].iloc[i] / self.df["close"].iloc[i - 1])
                if abs(math.log(jump)) <= 0.5:
                    continue
                for f in (1 / k, k):
                    if abs(math.log(jump / f)) < 0.4:
                        cols = [self.df.columns.get_loc("close"), self.df.columns.get_loc("adj")]
                        self.df.iloc[:i, cols] = self.df.iloc[:i, cols] * f
                        self.notes.append(f"액면분할({sp['ratio']}, {sp['date']}) 전후 가격 단위가 달라 이전 가격을 맞춤")
                        break
                else:
                    continue
                break

        if manual and not (self.df.index <= base_date).any():
            try:
                day, px = pd.Timestamp(manual["date"]), float(manual["close"])
            except (KeyError, TypeError, ValueError):
                return
            if px > 0 and day <= base_date:
                self._insert(day, px * self._split_scale(day, self.df.index[-1]), "기준일 가격 없음 → 설정 파일의 기준가 사용")

    def cache_bars(self, base_date: pd.Timestamp, keep: int = 400) -> dict:
        """다음 실행을 위해 저장할 내용: 기준일까지 70일과 그 뒤의 모든 일별 [종가, 수정주가] (최대 keep일) + 종목 정보.
        Yahoo가 일부 날짜나 종목 전체를 보내지 않아도 여기 저장된 값으로 이어 갈 수 있다."""
        out = {}
        before = self.df[self.df.index <= base_date]
        rows = pd.concat([before.tail(70), self.df[self.df.index > base_date].tail(keep)])  # 기준일 이전 70일은 변동성 계산용
        for day, r in rows.iterrows():
            out[day.strftime("%Y-%m-%d")] = [round(float(r["close"]), 6), round(float(r["adj"]), 6)]
        out["_m"] = {"cur": self.currency, "tz": self.tz, "name": self.name, "exch": self.exchange,
                     "div": [d for d in self.dividends if d["date"] > base_date.strftime("%Y-%m-%d")],
                     "spl": [x for x in self.splits if x["date"] > base_date.strftime("%Y-%m-%d")]}
        return out

    @classmethod
    def from_cache(cls, symbol: str, cached: dict) -> "Asset":
        """Yahoo가 종목 자체를 보내지 않을 때, 저장해 둔 값만으로 만든다 (마지막 저장 가격에서 멈춘 상태)."""
        m = cached.get("_m") or {}
        rows = {}
        for d, v in cached.items():
            if d.startswith("_"):
                continue
            c, a = (v[0], v[1]) if isinstance(v, (list, tuple)) else (v, v)
            rows[pd.Timestamp(d)] = {"close": float(c), "adj": float(a), "ts": 0}
        if not rows or not m.get("cur"):
            raise SymbolNotFound(symbol)
        self = object.__new__(cls)
        self.symbol, self.meta, self.scale = symbol, {}, 1.0
        self.currency, self.tz = m["cur"], m.get("tz") or "UTC"
        self.name, self.exchange = m.get("name") or symbol, m.get("exch") or ""
        self.df = pd.DataFrame.from_dict(rows, orient="index").sort_index()
        self.dividends, self.splits = list(m.get("div") or []), list(m.get("spl") or [])
        self.stale = True
        self.notes = [f"Yahoo가 종목 정보를 보내지 않아 저장해 둔 가격({self.df.index[-1]:%Y-%m-%d}까지)을 사용"]
        return self

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


def load_asset(yh: Yahoo, ticker: str, cache: dict | None, resolve: bool = True) -> tuple[str, "Asset"]:
    """Yahoo에서 받아 오고, Yahoo가 종목을 보내지 않으면 저장해 둔 값으로 대신한다."""
    try:
        if resolve:
            sym, res = resolve_symbol(yh, ticker)
        else:
            sym, res = ticker, yh.chart(ticker)
        return sym, Asset(sym, res)
    except SymbolNotFound:
        t = ticker.strip().upper()
        if resolve and re.fullmatch(r"[0-9][0-9A-Z]{5}", t):
            cands = [t + ".KS", t + ".KQ"]
        else:
            cands = [t.replace(".", "-") if re.fullmatch(r"[A-Z]+\.[A-Z]", t) else t]
        for sym in cands:
            c = (cache or {}).get(sid_of("c_", sym))
            if c:
                return sym, Asset.from_cache(sym, c)
        raise


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


def build(settings: dict, pitches: list[dict], yh: Yahoo, now: dt.datetime, cache: dict | None = None) -> tuple[dict, dict, dict, list]:
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
                sym, asset = load_asset(yh, p["ticker"], cache)
                assets.setdefault(sym, asset)
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
        sym, asset = load_asset(yh, b["ticker"], cache)
        bench.append({"symbol": sym, "name": b.get("name") or sym, "desc": b.get("desc"), "asset": asset})

    fx: dict[str, Asset] = {}
    for a in list(assets.values()) + [b["asset"] for b in bench]:
        if a.currency != "KRW" and a.currency not in fx:
            s = fx_symbol(a.currency)
            fx[a.currency] = load_asset(yh, s, cache, resolve=False)[1]

    # 2) 공통 달력: 기준일 + 그 이후 어느 시장이든 거래가 있었던 날 ------------ #
    all_assets = list(assets.values()) + [b["asset"] for b in bench]

    # 1-b) 빠진 일봉 보정: 이전 실행에서 저장한 종가로 채우고, 다음 실행을 위해 최근 종가를 다시 저장
    everything = all_assets + list(fx.values())
    new_cache, notes = {}, []
    for a in everything:
        key = sid_of("c_", a.symbol)
        a.repair((cache or {}).get(key), (settings.get("base_prices") or {}).get(a.symbol), base_date)
        new_cache[key] = a.cache_bars(base_date)
        notes += [f"{a.symbol}: {n}" for n in a.notes]
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
            r["error"] = f"Yahoo Finance가 기준일({settings['base_date']}) 가격을 보내지 않았습니다."

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
            "stale": True if getattr(a, "stale", False) else None,
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
    # 평가일 종가가 아직 들어오지 않은 종목이 있으면 확정을 미룬다 (최대 24시간, 그 뒤에는 표시를 남기고 확정)
    missing = [a.symbol for a in everything if final_date not in a.df.index] if cal[-1] >= final_date else []
    if status == "final" and missing and now < lock_at + dt.timedelta(hours=24):
        status = "provisional"
    fin_rows, fin_bms = (cur_rows, cur_bms) if k_fin == k_now else snapshot(k_fin)
    fin_fx = {}
    for cur, a in fx.items():
        f = to_cal(a.df["close"], cal).values
        fin_fx[cur] = {"symbol": a.symbol, "base": float(f[0]), "now": float(f[k_fin]), "ret": float(f[k_fin] / f[0] - 1)}
    final = clean({
        "status": status, "locked": status == "final",
        "computedAt": meta["updatedAt"], "baseDate": settings["base_date"], "finalDate": settings["final_date"],
        "asOfDate": cal[k_fin].strftime("%Y-%m-%d"), "title": meta["title"],
        "rebalance": meta["rebalance"], "volWindow": W, "fx": fin_fx, "missing": missing,
        "pitches": [{k: v for k, v in o.items() if k not in ("prevRank",)} for o in fin_rows],
        "benchmarks": [{k: v for k, v in o.items() if k != "weights"} for o in fin_bms],
    })
    return current, final, new_cache, notes


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


def run_once(args, settings: dict, pitches: list[dict], root, yh: "Yahoo", now: dt.datetime, force_final: bool) -> int:
    """한 번 조회·계산·저장. 성공 0, 실패 1 (실패해도 이전 데이터는 그대로 둔다)."""
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    yh.rng = history_range(settings, now)
    try:
        if args.cache_file:
            cache = json.loads(Path(args.cache_file).read_text()) if Path(args.cache_file).exists() else {}
        else:
            cache = (root.child("bars").get() or {}) if root is not None else {}
        current, final, new_cache, notes = build(settings, pitches, yh, now, cache)
    except Exception as e:  # 어떤 오류든 상태만 기록 (화면에 안내가 뜬다)
        import traceback
        traceback.print_exc()
        kind = "시세 조회 실패" if isinstance(e, (FetchError, SymbolNotFound)) else "계산 오류"
        print(f"[오류] {kind}: {e}", file=sys.stderr, flush=True)
        if root is not None:
            root.child("status").set({"ok": False, "at": stamp, "message": f"{kind}: {e}"[:300]})
        return 1

    if args.demo:
        current["meta"]["demo"] = True
        final["demo"] = True

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({"current": current, "final": final}, ensure_ascii=False), encoding="utf-8")

    if args.cache_file:
        Path(args.cache_file).write_text(json.dumps(new_cache))
    bad = [p for p in current["pitches"] if "error" in p]
    if root is not None:
        root.child("bars").set(new_cache)
        root.child("current").set(current)
        prev = root.child("final").get() or {}
        if prev.get("locked") and not force_final:
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
    for n in notes:
        print(f"  [보정] {n}")
    if final.get("missing"):
        print(f"  [평가일 종가 미도착] {', '.join(final['missing'])}")
    for p in bad:
        print(f"  [확인 필요] {p['student']} {p['ticker']}: {p['error']}", file=sys.stderr)
    sys.stdout.flush()
    return 0


def loop(args, settings: dict, pitches: list[dict], root, yh: "Yahoo") -> int:
    """장중 내내 주기적으로 갱신한다. GitHub 예약 실행이 몇 시간씩 건너뛰어도 끊기지 않게 하려는 것.

    - 시작하면 runner 노드에 자기 표시를 남겨 넘겨받는다(이전 실행은 이를 보고 물러난다).
    - 장중에는 update_minutes_open, 그 외에는 update_minutes_closed 간격으로 갱신.
    - 다음 개장이 멀면(주말) 끝낸다. 실행 시간 한도가 다가오면 다음 실행을 부르도록 chain=true 를 남긴다.
    """
    import runner
    run_id = os.environ.get("GITHUB_RUN_ID") or f"local-{os.getpid()}"
    event = os.environ.get("GITHUB_EVENT_NAME", "manual")
    hb = root.child("runner")
    utc = lambda: dt.datetime.now(dt.timezone.utc)
    iso = lambda: utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    mine = lambda: (hb.get() or {}).get("id") == run_id

    def finish(chain: bool) -> None:
        if mine():
            hb.delete()
        out = os.environ.get("GITHUB_OUTPUT")
        if chain and out:
            with open(out, "a") as f:
                f.write("chain=true\n")

    hb.set({"id": run_id, "at": iso(), "event": event})
    t_open = float(settings.get("update_minutes_open", 5)) * 60
    t_closed = float(settings.get("update_minutes_closed", 30)) * 60
    started, limit = time.monotonic(), args.loop_minutes * 60
    fails, first = 0, True
    while True:
        if not mine():
            print("다른 실행이 넘겨받아 이 실행은 끝냅니다.")
            return 0
        ok = run_once(args, settings, pitches, root, yh, utc(), args.force_final and first) == 0
        first = False
        fails = 0 if ok else fails + 1
        if fails >= 6:
            print("[오류] 여섯 번 연속 실패해 중단합니다.", file=sys.stderr)
            finish(False)
            return 1
        ph, wait = runner.phase(utc())
        if ph == "far":
            print(f"다음 개장까지 {wait:.0f}시간 남아 실행을 끝냅니다. 그동안은 예약 실행이 몇 시간마다 갱신합니다.")
            finish(False)
            return 0 if ok else 1
        interval = t_open if ph == "open" else t_closed
        if not ok:
            interval = max(interval, 15 * 60)  # 실패 뒤에는 조금 쉬었다가
        if time.monotonic() - started + interval > limit:
            print("실행 시간 한도가 가까워 다음 실행에 넘깁니다.")
            finish(True)
            return 0
        print(f"  다음 갱신까지 {interval / 60:.0f}분 대기 ({'장중' if ph == 'open' else '장외'})", flush=True)
        end = time.monotonic() + interval
        while time.monotonic() < end:
            time.sleep(max(0.0, min(60.0, end - time.monotonic())))
            if not mine():
                print("다른 실행이 넘겨받아 이 실행은 끝냅니다.")
                return 0
            hb.update({"at": iso()})  # 생존 신호 (id 는 건드리지 않는다)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="Firebase에 쓰지 않음")
    ap.add_argument("--out", help="현재 상태 JSON을 저장할 경로 ({current, final} 묶음)")
    ap.add_argument("--fixtures", help="Yahoo 대신 읽을 JSON 폴더 (오프라인 테스트용)")
    ap.add_argument("--now", help="현재 시각을 고정 (ISO8601, 테스트용)")
    ap.add_argument("--force-final", action="store_true", help="잠긴 최종 평가를 다시 계산해 덮어씀")
    ap.add_argument("--config-dir", default=str(ROOT / "config"), help="pitches.csv, settings.json 이 있는 폴더")
    ap.add_argument("--demo", action="store_true", help="결과에 데모 표시를 넣음")
    ap.add_argument("--cache-file", help="일별 종가 캐시를 Firebase 대신 이 파일에 읽고 쓴다 (테스트용)")
    ap.add_argument("--loop", action="store_true", help="끝내지 않고 주기적으로 계속 갱신 (GitHub Actions 용)")
    ap.add_argument("--loop-minutes", type=float, default=300, help="--loop 한 번의 최대 실행 시간(분)")
    args = ap.parse_args()

    cfg_dir = Path(args.config_dir)
    settings = json.loads((cfg_dir / "settings.json").read_text(encoding="utf-8"))
    pitches = load_pitches(cfg_dir / "pitches.csv")
    root = None if args.dry_run else firebase_root(settings.get("firebase_path", "stockpitch"))
    yh = Yahoo(args.fixtures, "1y")
    if args.loop and root is not None:
        return loop(args, settings, pitches, root, yh)
    now = (dt.datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now
           else dt.datetime.now(dt.timezone.utc))
    return run_once(args, settings, pitches, root, yh, now, args.force_final)


if __name__ == "__main__":
    sys.exit(main())
