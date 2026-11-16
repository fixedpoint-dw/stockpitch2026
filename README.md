# 스톡 피치 리그

학생들이 추천한 종목의 성과를 원화 기준 누적 총수익률(배당 포함)로 추적하는 웹사이트입니다.

- **대시보드** (`index.html`): 종목별 순위, 누적수익률 그래프, 현재가, 환율 효과, 배당, 기준 포트폴리오 대비 성과
- **최종 평가표** (`final.html`): 12월 9일 종가 기준 종목별 누적수익률과 순위. 인쇄(PDF)와 CSV 내려받기 지원
- **기준 포트폴리오**: S&P 500 ETF(SPY), 동일가중 매수 후 보유, 동일가중 리밸런싱, 리스크 패리티

## 동작 방식

```
Yahoo Finance ──(30분마다, GitHub Actions)──▶ scripts/update.py ──▶ Firebase Realtime Database
                                                                        │
                                       학생 브라우저(GitHub Pages) ◀────┘  저장된 내용을 읽어 표시,
                                                                           새로 저장되면 화면이 자동으로 바뀜
```

브라우저는 Yahoo Finance를 직접 부를 수 없어서(접근 제한), GitHub Actions가 대신 가격을 받아 계산한 뒤
Firebase에 저장합니다. 접속한 학생은 Firebase에 저장된 최신 결과를 봅니다.

## 설치 (한 번만)

### 1. GitHub 저장소
이 폴더 전체를 새 **공개(public)** 저장소의 `main` 브랜치에 올립니다. (`.github` 폴더 포함)

### 2. Firebase
1. [Firebase 콘솔](https://console.firebase.google.com)에서 프로젝트를 고릅니다(기존 프로젝트를 써도 됩니다).
2. **Realtime Database** 를 만들고, 화면 위쪽의 주소를 복사합니다.
   예: `https://my-project-default-rtdb.firebaseio.com`
3. **규칙(Rules)** 에 `database.rules.json` 내용을 넣습니다.
   기존 프로젝트라면 통째로 바꾸지 말고 `"stockpitch": { ".read": true, ".write": false }` 부분만 기존 규칙 안에 추가하세요.
4. **프로젝트 설정 → 서비스 계정 → 새 비공개 키 생성** 으로 JSON 파일을 받습니다.
   이 키는 프로젝트 전체에 대한 관리자 권한이므로 저장소에 올리지 말고 아래 Secret에만 넣습니다.

### 3. GitHub Secrets
저장소 **Settings → Secrets and variables → Actions → New repository secret** 에 두 개를 등록합니다.

| 이름 | 값 |
|---|---|
| `FIREBASE_SERVICE_ACCOUNT` | 2-4에서 받은 JSON 파일의 내용 전체 |
| `FIREBASE_DATABASE_URL` | 2-2에서 복사한 주소 |

### 4. 사이트 설정
- `assets/config.js` 의 `databaseURL` 에 같은 주소를 넣습니다. (비워 두면 가상 데이터 데모 화면이 열립니다)
- `config/pitches.csv` 에 학생 추천 목록을 넣습니다(아래 형식).
- 필요하면 `config/settings.json` 을 고칩니다.

### 5. 켜기
1. **Settings → Pages → Deploy from a branch → main / (root)** 로 GitHub Pages를 켭니다.
2. **Actions** 탭에서 워크플로를 허용한 뒤 **Update stock pitch data → Run workflow** 로 한 번 실행합니다.
3. 실행 기록에 순위가 찍히면 성공입니다. `https://<계정>.github.io/<저장소>/` 로 접속해 확인합니다.

## 추천 목록 형식 (`config/pitches.csv`)

```csv
student,ticker,name
홍길동,005930,삼성전자
김영희,AAPL,Apple
김영희,035720,카카오
```

- `ticker`: 한국 종목은 6자리 코드만 적으면 코스피(.KS) → 코스닥(.KQ) 순으로 자동으로 찾습니다.
  미국 종목은 티커 그대로(`BRK.B` 는 `BRK-B` 로 자동 변환). Yahoo Finance 심볼을 직접 적어도 됩니다(`7203.T` 등, 통화는 자동으로 원화 환산).
- `name`: 화면에 보일 종목명. 비우면 Yahoo Finance의 이름을 씁니다.
- 한 학생이 여러 종목을 추천하면 줄을 여러 개 씁니다. 여러 학생이 같은 종목을 추천하면 순위는 공동 순위, 포트폴리오에는 한 번만 들어갑니다.
- 파일을 고쳐 올리면(push) 자동으로 한 번 다시 계산합니다. 티커가 잘못된 줄은 화면에 "확인"으로 표시됩니다.
- 사이트는 누구나 볼 수 있습니다. 실명 공개가 부담되면 `student` 에 학번 뒷자리나 별명을 쓰세요.

## 설정 (`config/settings.json`)

| 항목 | 기본값 | 뜻 |
|---|---|---|
| `base_date` | `2026-11-13` | 출발 기준일. 이 날(또는 직전 거래일)의 각 시장 종가가 기준가 |
| `final_date` | `2026-12-09` | 평가 기준일. 한국·미국 각각 이 날 장 마감 종가로 평가 |
| `final_lock_utc` | `2026-12-10T03:00:00Z` | 최종 평가를 확정하는 시각(한국 12/10 낮 12시). 미국 12/9 장 마감(한국 12/10 06시) 뒤 여유를 둔 값 |
| `benchmarks` | SPY | 비교용 ETF 목록. 예: `{"ticker": "069500", "name": "KODEX 200"}` 추가 가능 |
| `rebalance` | `daily` | 동일가중 리밸런싱·리스크 패리티의 리밸런싱 주기. `daily` / `weekly` / `none` |
| `vol_window_days` | `63` | 리스크 패리티 변동성 계산 창(거래일). 63일 ≈ 3개월 |

## 계산 방법

- **가격**: Yahoo Finance 일별 시세. 장중에는 그날 봉이 현재가로 채워지므로 30분마다 갱신됩니다(시세 자체가 15~20분 지연될 수 있음).
- **누적수익률**: 수정주가(adjusted close) 비율. 배당·분할이 반영된 총수익률이며 배당은 배당락일 재투자 가정입니다.
  화면의 "배당 기여"는 총수익률에서 주가 수익률을 뺀 값입니다.
- **원화 환산**: (1 + 원화 수익률) = (1 + 현지통화 수익률) × (1 + 환율 변동률). 환율은 Yahoo Finance `KRW=X` 의 같은 날짜 일별 값입니다.
  미국 종가(한국 시각 다음 날 06시)와 가까운 시점의 환율이 쓰입니다.
- **날짜 맞추기**: 날짜 D의 값은 한국 종목은 D일 한국 종가, 미국 종목은 D일 미국 종가입니다. 한쪽 시장이 쉬는 날은 직전 종가를 씁니다.
- **동일가중 매수 후 보유**: 기준일에 1/N씩 사서 그대로 둠.
- **동일가중 리밸런싱**: 리밸런싱 시점마다 비중을 1/N로 되돌림.
- **리스크 패리티**: 종목별 원화 일간 수익률의 최근 63거래일 표준편차 σᵢ를 구해 비중 wᵢ ∝ 1/σᵢ. 리밸런싱 시점마다 전날까지의 자료로 다시 계산(상관관계는 쓰지 않는 역변동성 방식).
- **최종 평가**: `final_date` 의 값으로 계산해 `final_lock_utc` 이후 첫 실행에서 확정·잠금합니다. 그 뒤로는 대시보드만 계속 갱신되고 최종 평가표는 바뀌지 않습니다.
  꼭 다시 계산해야 하면 Actions에서 `force_final` 을 체크하고 실행합니다.

## Firebase에 저장되는 내용

| 경로 | 내용 |
|---|---|
| `stockpitch/current` | 현재 순위, 종목별 정보, 기준 포트폴리오, 기준일 이후 일별 누적수익률 시계열 |
| `stockpitch/final` | 평가 기준일 종가 기준 평가표 (`status`: `pending` → `provisional` → `final`) |
| `stockpitch/status` | 마지막 실행 결과(성공/실패, 시각) |

## 내 컴퓨터에서 확인

```bash
pip install -r scripts/requirements.txt
python scripts/update.py --dry-run --out data/preview.json   # 실제 Yahoo 시세로 계산만 (Firebase에 쓰지 않음)
python tests/test_offline.py                                 # 가상 시세로 계산 검산
python -m http.server 8000                                   # http://localhost:8000/?demo 로 데모 화면
```

## 알아둘 점

- Yahoo Finance는 공식 API가 아니어서 가끔 응답이 막히거나 늦을 수 있습니다. 실패하면 이전 데이터를 그대로 두고 화면에 안내가 뜹니다. Actions 실패 알림 메일도 옵니다.
- GitHub Actions의 예약 실행은 정시에 돌지 않을 수 있습니다(보통 수 분~수십 분 지연).
- 한국 종목의 배당 정보는 Yahoo Finance에 늦게 반영되는 경우가 있습니다. 평가 전 최종 평가표의 "배당 기여"를 한 번 확인하세요.
- 평가가 끝나면 Actions 탭에서 워크플로를 끄면(Disable workflow) 됩니다.
- `data/demo.json` 은 화면 확인용 가상 데이터입니다(실제 시세 아님).
