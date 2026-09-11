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
```

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

`run_command`로 실행한 프로젝트 코드는 Windows 사용자 권한으로 동작합니다. 신뢰하는 workspace만 지정하세요.
