# My Agent

Windows PC에서 실행하고 같은 PC 또는 Tailscale로 연결된 스마트폰 브라우저에서 사용하는 개인용 멀티모델 AI Agent MVP입니다.

## Agent 팀

기본 설정은 비용을 낮추면서 Coding 결과를 독립 검증하도록 구성되어 있습니다.

| 역할 | 기본 모델 | 사고 수준 | 하는 일 |
|---|---|---:|---|
| Chat | Gemini 3.8 Flash | low | 도구 없는 일반 대화 |
| Architect / Manager | Gemini 3.8 Flash | high | 요청 분석, 설계, Worker 선택, 최종 답변 |
| Main Coding Agent | Gemini 3.8 Flash | medium | 파일 수정, 테스트·빌드, Git 확인 |
| Web Reader | Gemini 3.8 Flash | low | 사용자가 준 공개 HTTP(S) URL 읽기 |
| Test / Review Agent | GPT-5.4 Mini | medium | Coding 결과를 읽기 전용으로 독립 검증 |
| Senior Adjudicator | GPT-5.6 Sol | high | 중요한 설계 불확실성이나 Agent 간 충돌 판정 |
| Korea Real Estate Analyst | 역할별 기존 설정 재사용 | 가변 | 한국 부동산 공식 자료 수집·계산·근거 기반 분석 |

Coding Agent가 작업하면 Review Agent가 자동으로 검사합니다. Review 결과가 `conflict`이거나 명시적으로 escalation이 필요하다고 판정할 때만 Senior Adjudicator가 자동 호출됩니다. Architect도 중요한 설계에 자신이 없을 때만 직접 Senior Adjudicator를 호출하도록 지시되어 있습니다.

`General Agent`였던 구성요소는 역할을 명확히 하기 위해 Web Reader로 정리했습니다. 검색엔진이 아니며 정확한 공개 URL이 포함된 요청에만 사용합니다.

## 주요 기능

- OpenAI Responses API와 Gemini의 OpenAI-compatible API를 하나의 Agent loop에서 지원
- Agent 선택과 tool 실행 과정을 SSE로 브라우저에 실시간 전달
- SQLite(`data/agent.db`)에 workspace별 Chat/Agent 세션과 메시지 보관
- `sessionStorage`는 빠른 브라우저 cache로 병행 사용
- 여러 workspace 등록·선택과 세션별 workspace 고정
- Git status/diff/stat/파일별 diff와 사용자 승인형 commit UI
- ripgrep 기반 workspace 내부 code search
- 구조화 Reviewer 결과와 Plan/Progress SSE
- 역할별 API 호출 토큰과 추정 비용을 `logs/llm_usage.jsonl`에 기록
- `/api/usage`와 화면 우측 상단에서 누적 추정 비용 확인
- API 키는 서버의 환경변수에서만 읽고 브라우저에는 전달하지 않음
- Korea Real Estate Analyst, 공식 source adapter, watchlist, 수집 job, Evidence/Claim 검증

## 구조

```text
app/
  main.py                    FastAPI 라우트, health/usage API, SSE 진행 스트림
  config.py                  Provider·모델·사고 수준·환경변수 설정
  agents/
    manager.py               Gemini Architect와 전체 파이프라인
    coding_agent.py          Gemini Coding Worker
    review_agent.py          읽기 전용 GPT Reviewer
    escalation_agent.py      조건부 GPT Senior Adjudicator
    general_agent.py         공개 URL Web Reader
  tools/
    coding_tools.py          workspace 제한 파일/명령/git/ripgrep 도구
    general_tools.py         SSRF 방어가 적용된 공개 HTTP GET
  llm/
    openai_client.py         OpenAI/Gemini tool-calling adapter
    usage.py                 token·추정 비용 JSONL 기록과 집계
  static/
    index.html
    app.js
    style.css
  real_estate/
    analyst.py                Source Plan, Evidence Pack, 분석·검증 workflow
    calculators.py            중앙값·변화율·거래량 등 결정적 계산
    jobs.py                   단일 background worker와 시작 시 backfill
    models.py                 source/evidence/claim 공통 schema
    policy.py                 정책 진행 상태 분류
    repository.py             부동산 SQLite migration과 저장소
    service.py                Agent/API 통합 서비스
    sources.py                국토부·R-ONE·공식 정책 adapter
    video.py                  Gemini native YouTube 요청 계획 interface
tests/
data/
  agent.db                   자동 생성되는 SQLite DB (Git 제외)
```

## API 키 발급

두 서비스의 키가 필요합니다.

1. Gemini: <https://aistudio.google.com/app/apikey>
2. OpenAI: <https://platform.openai.com/api-keys>

키는 채팅, Git, 화면, 문서에 붙여 넣지 말고 로컬 `.env`에만 저장하세요.

## Windows에서 실행

프로젝트 폴더에서 다음을 실행합니다.

```powershell
Copy-Item .env.example .env
notepad .env
```

`.env`에서 두 API 키와 workspace를 설정합니다.

```dotenv
GEMINI_API_KEY=발급받은_키
OPENAI_API_KEY=발급받은_키
AGENT_WORKSPACE_ROOT=C:\Users\사용자명\workspace
AGENT_WORKSPACE_ALLOWED_ROOTS=C:\Users\사용자명\workspace
AGENT_DB_PATH=data/agent.db
MOLIT_API_KEY=공공데이터포털_국토부_서비스키
RONE_API_KEY=한국부동산원_RONE_인증키
REAL_ESTATE_POLICY_FEED_URLS=https://www.molit.go.kr/공식문서URL;https://www.fsc.go.kr/공식문서URL
```

부동산 source 키는 선택 사항입니다. `MOLIT_API_KEY` 또는 `RONE_API_KEY`가 없어도 Chat, Coding Agent와 정책 문서 source는 정상 동작하고, 해당 source만 `configuration_required`로 표시됩니다. 키 값은 `/api/health`, source API, 브라우저 또는 로그에 반환하지 않습니다.

기본값에서는 기존 `AGENT_WORKSPACE_ROOT` 하나만 등록할 수 있어 보안 범위가 넓어지지 않습니다. 서로 같은 부모 폴더에 있는 여러 프로젝트를 등록하려면 `AGENT_WORKSPACE_ALLOWED_ROOTS`에 그 공통 부모를 명시하세요. 여러 경계는 세미콜론(`;`)으로 구분합니다.

가상환경과 패키지가 없다면 설치합니다.

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

테스트를 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Code Search에는 `rg.exe`가 필요합니다. 없다면 Windows에서 설치합니다.

```powershell
winget install BurntSushi.ripgrep.MSVC
```

키와 두 모델의 실제 연결을 최소 요청으로 확인합니다. 소량의 API 비용이 발생합니다.

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_api_keys
```

PC에서만 접속할 서버를 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

브라우저에서 <http://localhost:8000>을 엽니다. 화면만 확인할 때는 API 키가 없어도 되지만 Chat과 Agent 요청은 키가 있어야 동작합니다. 종료는 PowerShell에서 `Ctrl+C`입니다.

일반 Chat은 workspace 없이 사용할 수 있습니다. Workspace는 Agent가 파일을 읽고 수정할 프로젝트 폴더이며 Agent 모드에서만 필요합니다. 서버/API 오류는 prompt나 API 키를 제외하고 `logs/server.log`에 기록됩니다. `.env`를 변경했다면 실행 중인 서버를 종료한 뒤 다시 시작해야 새 설정이 반영됩니다.

실행한 PowerShell 창에는 시작·접속 로그와 LLM 오류가 바로 표시됩니다. 파일 로그를 별도 창에서 계속 보려면 다음 명령을 사용합니다.

```powershell
Get-Content .\logs\server.log -Wait
```

## Tailscale로 스마트폰에서 접속

PC와 스마트폰을 같은 tailnet에 연결한 뒤 서버를 다음처럼 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
tailscale ip -4
```

스마트폰에서 `http://<PC의-Tailscale-IP>:8000`을 엽니다. 이 앱에는 사용자 인증이 없으므로 공유기 포트 포워딩이나 공인 인터넷 노출은 하지 마세요.

## 비용 기록

각 API 응답의 input, cached input, output token을 역할별로 기록합니다. 기본 파일은 `logs/llm_usage.jsonl`이며 prompt나 파일 내용은 저장하지 않습니다.

현재 내장 단가는 2026-09-10 확인 기준이며 Gemini 3.8 Flash는 2026-12-31까지의 프로모션 단가입니다. 가격이 바뀌거나 다른 모델을 지정하면 `app/llm/usage.py`의 단가표도 갱신해야 합니다. 알 수 없는 모델은 token은 기록하지만 추정 비용은 계산하지 않습니다.

## v2 API

- `GET|POST /api/workspaces`, `GET|PATCH|DELETE /api/workspaces/{id}`
- `GET|POST /api/sessions`, `GET|PATCH|DELETE /api/sessions/{id}`
- `GET /api/workspaces/{id}/git/status`
- `GET /api/workspaces/{id}/git/diff?file=app/main.py`
- `GET /api/workspaces/{id}/git/stat`
- `GET /api/workspaces/{id}/search?q=JWTMiddleware&path=.`
- `POST /api/workspaces/{id}/git/commit`

`/api/chat`과 `/api/agent`는 선택적인 `session_id`, `workspace_id`를 받습니다. `session_id`가 있으면 서버 SQLite history를 사용하고, 없으면 기존 `history` 요청 형식을 그대로 사용합니다.

Commit API는 `{ "message": "...", "confirmed": true }`가 필요합니다. Agent tool에는 commit 기능이 없으며 UI 확인을 승인한 사용자의 별도 요청으로만 호출됩니다. 안전한 변경 파일만 대상으로 하고 hook과 GPG signing을 실행하지 않으며 push 기능은 제공하지 않습니다.

## Korea Real Estate Analyst

이 Agent는 부동산을 검색·요약하거나 가격을 단정하는 챗봇이 아닙니다. 질문을 지역·기간·목적으로 구조화하고, 저장된 공식 자료를 application code로 계산한 뒤 Evidence Pack을 분석합니다. 결과는 기존 Reviewer가 출처와 논리 구조를 검사하며, 세금·법률·대출·고액 판단은 기존 escalation model 설정으로만 승격합니다. 신규 모델 ID는 추가하지 않았습니다.

```text
사용자 질문
→ Manager가 Korea Real Estate Analyst 선택
→ Source Planner
→ 공식 source adapter / SQLite
→ deterministic calculator
→ Evidence Pack
→ 기존 저비용 Manager 모델로 분석
→ 기존 Reviewer
→ 필요할 때만 Senior 검증
→ 모바일 카드 보고서
```

분석 결과는 `fact`, `inference`, `opinion`, `scenario`를 구분합니다. 사실과 추론은 Evidence ID가 없으면 제거하고, D/E 등급만으로 사실을 만들지 않습니다. 수치 사실은 기간·지역·단위가 있는 근거를 요구하며, 시나리오는 발생 조건이 없으면 제거합니다. 실거래 지표는 취소 거래를 제외하고 표본 수와 신고 지연·취소·정정 caveat를 함께 표시합니다. 상관관계를 인과관계로 확정하지 않습니다.

### 분석 모드

현재 UI와 API에서 실제 사용할 수 있는 MVP 모드는 다음 네 가지입니다.

- `market_trend`
- `apartment_comparison`
- `buy_or_sell_scenario`
- `policy_analysis`

`supply_analysis`, `redevelopment_due_diligence`, `tax_and_financing`, `expert_claim_check`는 schema와 routing 확장점만 정의되어 있으며 UI에서 선택할 수 없습니다. 전문가 영상은 공개 HTTPS YouTube URL만 허용하고 기존 Gemini 모델 설정을 재사용하는 Interactions API 요청 빌더가 있습니다. 짧은 일반 영상은 static, 5분 이상이거나 특정 주장·장면 질문은 agentic processing으로 계획하지만, 실제 호출·저장·공식 자료 재검증 workflow는 이번 MVP에서 활성화하지 않았습니다.

### 지원 source

| Source | 신뢰도 | 현재 지원 상태 | 설정 |
|---|---:|---|---|
| 국토교통부 아파트 매매 실거래가 OpenAPI | A | live adapter + XML fixture, 취소·정정 upsert | `MOLIT_API_KEY`, 5자리 법정동 코드 |
| 한국부동산원 R-ONE | B | live adapter + JSON fixture, 수동 수집 | `RONE_API_KEY`, 통계표 ID |
| 정부·국회·지자체 공식 정책 문서 | A | allowlist URL live adapter + HTML fixture | 키 불필요, 공식 URL 필요 |
| Watchlist | 로컬 | CRUD, 지역·단지·비교 지역·법정동 코드 | UI에서 수정 |
| KOSIS / ECOS / 정비사업 / 공급 | - | 공통 adapter 후속 대상, 미구현 | - |
| 민간 매물·호가 | C 후보 | 자동 수집 미구현 | 비공개 API·로그인 우회 금지 |

수집 결과는 공통 source metadata, 원문 hash, normalized values, caveat를 보관합니다. 같은 원문·기간·지역은 unique idempotency key로 중복 저장하지 않으며 동일 거래는 transaction key로 갱신합니다. 외부 문서 내용은 항상 `UNTRUSTED_EXTERNAL_DATA`로 표시되어 모델 명령으로 취급하지 않습니다.

신뢰도는 A(법령·고시·정부 원문·공식 통계), B(공공·금융기관 연구), C(허용된 민간 가격 자료), D(언론), E(유튜브·블로그·커뮤니티·의견) 순입니다. D/E는 심리와 주장 탐색에만 쓰며 단독으로 factual conclusion을 만들 수 없습니다.

정책 상태는 `statement → pledge → under_review → official_announcement → bill_proposed → bill_passed → promulgated → effective → repealed`로 분리합니다. 단어 순서가 정책 진행 순서를 뜻하는 것은 아니며, 실제 문서의 명시적인 상태만 저장합니다. 보도자료에 “검토 중”이 명시되면 시행 정책으로 승격하지 않습니다. 분석은 정책 변화가 법률·시행령·행정조치와 세금·대출·공급·거래 조건을 거쳐 수요·거래량·가격 가능성에 전달되는 경로와 반대 효과를 함께 요구합니다.

### 지표 계산

현재 code calculator는 유효 실거래의 전용면적당 가격, 월 중앙값, 최근 3개월 이동 중앙값, 6개월·12개월 변화, 거래량 전월비·전년 동월비, 최근 최고가 대비 변화, 비교 지역 가격 비율, 전세가율 함수를 제공합니다. 표본이 기본 임계치보다 적으면 `insufficient_sample`로 표시합니다. 평균보다 중앙값을 우선 사용합니다. 인구·가구·전입·전출·금리·대출·입주 예정 물량은 source가 아직 연결되지 않아 계산 결과에 있는 것처럼 표시하지 않습니다.

### Watchlist와 모바일 사용

하단 `Estate` 메뉴에서 `Analysis`, `Watchlist`, `Sources / Jobs`를 전환합니다. Watchlist에는 관심 지역, 단지, 비교 지역, 국토부 수집용 법정동 코드를 저장할 수 있습니다. 분석 화면에서 Watchlist를 고르면 지역과 비교 지역이 채워집니다. Sources / Jobs에서는 API 키 값이 아닌 설정 필요 여부, 최근 성공 시각, 오류, retry 상태를 확인하고 수동 수집을 등록할 수 있습니다.

보고서는 스마트폰에서 가로 표 대신 세로 카드로 한 줄 결론, 데이터 기준일, 지표, 사실, 추론, 상승·하락 근거, 강세·기준·약세 조건, 부족한 정보, 공식 출처를 표시합니다. 화면은 HTML 문자열을 주입하지 않고 `textContent` 기반 DOM 생성만 사용합니다.

### 수집 주기와 backfill

별도 서버 없이 FastAPI process 안의 daemon worker 하나가 SQLite job을 원자적으로 claim합니다. 앱 시작 시와 날짜가 바뀔 때 Watchlist 지역의 국토부 실거래 및 설정된 공식 정책 URL에 대해 누락 여부를 확인합니다. 최초 실행은 당일, 이후 실행은 마지막 source 성공일 다음 날부터 최대 7일을 backfill합니다. 동일 source·작업 유형·기간·지역·parameter는 하나의 job만 생성되고, 일시 장애는 30초부터 지수 backoff로 최대 3회 재시도합니다. 실패한 source만 재시도하며 상태와 오류는 UI에서 확인할 수 있습니다.

Windows PC가 꺼져 있는 동안에는 실행되지 않으며 다음 시작 때 제한된 backfill로 보완합니다. 현재 자동 주간 보고서 생성과 R-ONE/KOSIS/ECOS 월간 자동 수집은 미구현입니다. SQLite schema와 일반화된 period/job interface는 준비되어 있어 후속 cadence를 추가할 수 있습니다.

### 부동산 API

- `GET /api/real-estate/capabilities`
- `POST /api/real-estate/analyze` (SSE)
- `GET|POST /api/real-estate/watchlists`
- `PATCH|DELETE /api/real-estate/watchlists/{id}`
- `GET /api/real-estate/sources`
- `GET|POST /api/real-estate/jobs`, `GET /api/real-estate/jobs/{id}`
- `GET /api/real-estate/reports/latest`, `GET /api/real-estate/reports/{id}`
- `GET /api/real-estate/policies?region=...`
- `GET /api/real-estate/evidence/{id}`

수동 job 요청의 `parameters`에서 key/token/secret/password 계열 이름은 거부합니다. API 키를 job payload로 보내지 말고 서버 환경변수에만 설정하세요.

### SQLite migration

기존 `data/agent.db`에 `schema_migrations`, `source_registry`, `raw_source_items`, `normalized_indicators`, `real_estate_transactions`, `real_estate_watchlists`, `policy_events`, `expert_claims`, `ingestion_jobs`, `real_estate_reports`를 `CREATE TABLE IF NOT EXISTS` 방식으로 추가합니다. migration ID는 `2026091201_real_estate_mvp`입니다. 전국 전체 데이터를 선적재하지 않고 Watchlist 중심으로만 수집합니다.

## 설정

역할별로 `*_PROVIDER`, `*_MODEL`, `*_REASONING_EFFORT`를 설정할 수 있습니다. 지원 provider는 `gemini`, `openai`입니다.

| 환경변수 | 기본값 |
|---|---|
| `MANAGER_MODEL` | `gemini-3.8-flash` |
| `MANAGER_REASONING_EFFORT` | `high` |
| `CODING_MODEL` | `gemini-3.8-flash` |
| `CODING_REASONING_EFFORT` | `medium` |
| `REVIEW_MODEL` | `gpt-5.4-mini` |
| `ESCALATION_MODEL` | `gpt-5.6-sol` |
| `AUTO_ESCALATION_ENABLED` | `true` |
| `MAX_AGENT_STEPS` | `15` |
| `COMMAND_TIMEOUT_SECONDS` | `120` |
| `MAX_TOOL_OUTPUT_CHARS` | `20000` |
| `AGENT_DB_PATH` | `data/agent.db` |
| `AGENT_WORKSPACE_ALLOWED_ROOTS` | `AGENT_WORKSPACE_ROOT` 값 |
| `MOLIT_API_KEY` | 빈 값 (`configuration_required`) |
| `RONE_API_KEY` | 빈 값 (`configuration_required`) |
| `REAL_ESTATE_POLICY_FEED_URLS` | 빈 값, 세미콜론 구분 공식 URL |
| `REAL_ESTATE_SOURCE_TIMEOUT_SECONDS` | `20` |
| `REAL_ESTATE_SOURCE_MAX_BYTES` | `2000000` |

## 보안 경계

- 모든 파일 경로를 resolve한 뒤 `AGENT_WORKSPACE_ROOT` 하위인지 검사합니다.
- `.env`, SSH/AWS 폴더, private key와 Git 내부 파일 접근을 차단합니다.
- shell 없이 허용된 개발 실행 파일만 단일 프로세스로 실행합니다.
- pipe, redirect, multiline, inline script, git commit/push를 차단합니다.
- Agent command/tool의 commit/push는 계속 차단되며 commit은 확인 필수 사용자 API에서만 가능합니다.
- 추가 workspace는 `AGENT_WORKSPACE_ALLOWED_ROOTS` 내부 실제 디렉터리만 등록할 수 있습니다.
- Git diff/status와 ripgrep 결과에서도 credential·`.git`·SSH/AWS 경로를 제외합니다.
- Web Reader는 DNS 확인 후 사설·loopback·link-local 주소를 차단하고 redirect를 따라가지 않습니다.
- 로그에는 provider, model, 역할, token, 추정 비용만 기록하고 prompt와 tool 결과는 기록하지 않습니다.
- 부동산 fetch는 HTTPS, SSRF 검사, 공식 domain allowlist, redirect 차단, timeout, 응답 크기 제한을 적용합니다.
- 외부 정책·영상 content는 prompt가 아니라 신뢰할 수 없는 evidence data로 격리합니다.
- Real Estate UI/API는 API Key 값을 받거나 반환하지 않으며 실제 매매·송금·계약 기능이 없습니다.

`run_command`로 실행한 프로젝트 코드는 Windows 사용자 권한으로 동작합니다. 신뢰하는 workspace만 지정하세요.
