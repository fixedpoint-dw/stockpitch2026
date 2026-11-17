#!/usr/bin/env python3
"""갱신 실행 조율 (표준 라이브러리만 사용 — 패키지 설치 전에 돌 수 있어야 한다).

GitHub의 예약 실행은 몇 시간씩 건너뛰는 일이 있어서, 한 번 시작된 실행이 장중 내내
주기적으로 갱신하다가 끝날 때 다음 실행을 직접 불러 이어 가게 한다(update.py --loop).
예약 실행은 그 고리가 끊겼을 때 다시 시작시키는 역할만 한다.

  python3 scripts/runner.py check   # 이미 다른 실행이 갱신 중이면 skip=true 를 출력
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
MARKETS = {  # 정규장 (현지 시각)
    "KR": ("Asia/Seoul", (9, 0), (15, 30)),
    "US": ("America/New_York", (9, 30), (16, 0)),
}
COOLDOWN_MIN = 20     # 마감 뒤 이만큼은 장중과 같은 간격으로 더 갱신 (종가 확정 반영)
NEAR_HOURS = 9        # 다음 개장이 이 시간 안이면 실행을 끝내지 않고 이어 간다 (평일 낮~밤 공백 포함)
ALIVE_SEC = 300       # 다른 실행의 생존 신호가 이보다 최근이면 살아 있는 것으로 본다
IDLE_REFRESH_H = 6    # 주말 등 장이 멀 때의 갱신 간격


def phase(now: dt.datetime) -> tuple[str, float]:
    """('open' | 'near' | 'far', 다음 개장까지 남은 시간[시간])"""
    if os.environ.get("SP_PHASE"):  # 테스트용: 시각과 무관하게 상태를 고정
        return os.environ["SP_PHASE"], 0.0
    wait = float("inf")
    for tzname, o, c in MARKETS.values():
        tz = ZoneInfo(tzname)
        today = now.astimezone(tz).date()
        for add in range(0, 6):
            d = today + dt.timedelta(days=add)
            if d.weekday() >= 5:
                continue
            op = dt.datetime(d.year, d.month, d.day, o[0], o[1], tzinfo=tz)
            cl = dt.datetime(d.year, d.month, d.day, c[0], c[1], tzinfo=tz) + dt.timedelta(minutes=COOLDOWN_MIN)
            if op <= now < cl:
                return "open", 0.0
            if now < op:
                wait = min(wait, (op - now).total_seconds() / 3600)
                break
    return ("near" if wait <= NEAR_HOURS else "far"), wait


def age_sec(iso: str | None, now: dt.datetime) -> float:
    try:
        return (now - dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds()
    except Exception:
        return float("inf")


def _get(url: str):
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read().decode() or "null")


def check() -> int:
    """예약 실행이 실제로 일을 해야 하는지 판단한다. 직접 실행·파일 변경으로 시작된 경우는 항상 진행."""
    now = dt.datetime.now(dt.timezone.utc)
    skip, why = False, "진행"
    try:
        if os.environ.get("GITHUB_EVENT_NAME") == "schedule":
            base = os.environ["FIREBASE_DATABASE_URL"].rstrip("/")
            path = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8")).get("firebase_path", "stockpitch")
            runner = _get(f"{base}/{path}/runner.json") or {}
            status = _get(f"{base}/{path}/status.json") or {}
            if age_sec(runner.get("at"), now) < ALIVE_SEC:
                skip, why = True, "다른 실행이 갱신 중"
            elif phase(now)[0] == "far" and status.get("ok") and age_sec(status.get("at"), now) < IDLE_REFRESH_H * 3600:
                skip, why = True, "장이 열리지 않는 시간대이고 최근에 갱신함"
    except Exception as e:  # 확인에 실패하면 그냥 진행한다
        why = f"확인 실패({e}) → 진행"
    print(f"[확인] {why}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"skip={'true' if skip else 'false'}\n")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["check"]:
        sys.exit(check())
    ph, wait = phase(dt.datetime.now(dt.timezone.utc))
    print(ph, f"다음 개장까지 {wait:.1f}시간")
