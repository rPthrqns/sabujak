# Sabujak (사부작)

> **AI agents quietly getting work done, one small task at a time.**
>
> "사부작사부작" — 한국어 의태어로 **조용하지만 꾸준히 작은 일을 처리하는 모습**. AI 에이전트들이 자율적으로 협업하는 가상 회사 시뮬레이션 플랫폼. CEO, CTO, CMO 등 에이전트를 배치하면 @멘션으로 소통하고, 칸반으로 작업을 관리하며, 결재를 올리고, 결과물을 만들어냅니다.

![Python](https://img.shields.io/badge/Python-3.12+-3776ab?style=flat&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?style=flat&logo=sqlite&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=flat)

## Quick Start

```bash
git clone https://github.com/rPthrqns/sabujak.git
cd sabujak
pip install -r requirements.txt

python3 -u dashboard/server.py
# → http://localhost:3000
```

## 작동 방식

회사를 만들고, 주제를 정하면 AI 에이전트들이 자율적으로 일합니다.

```
나 → @CEO "회사 홈페이지를 만들어줘"
  CEO → @CTO "프론트엔드 개발" + @CMO "디자인 시안"
      → [TASK_ADD:홈페이지 제작:high]
      → [APPROVAL:purchase:도메인 구매:호스팅 비용 승인 필요]
  CTO → (개발 후 결과물 저장, CEO에게 보고)
  CMO → (디자인 완료, CEO에게 보고)
```

에이전트 응답은 가드레일로 검증됩니다 — `@멘션`이나 `[COMMAND:]` 없이 "확인하겠습니다" 같은 준비 발언만 하면 거부되고 재시도합니다.

## UI

### 레이아웃

```
┌─────────────────────────────────────────┐
│  Header (회사 탭 / 검색 / 🤖＋ / 🔗)    │
├────┬────────────────────────────────────┤
│ 👔 │  👤 나                    14:23    │
│ 📈 │  @CEO 시장 조사해줘                │
│ 💻 │                                    │
│ 🎨 │  👔 CEO                  14:24    │
│ 👥 │  ## 시장 조사 결과                  │
│    │  **경쟁사 분석** 완료               │
│    │  | 항목 | 결과 |                    │
│    │  @CMO 마케팅 데이터 수집해줘        │
│    │                                    │
├────┴────────────────────────────────────┤
│  [📎] @에이전트 지시사항 입력...    [⏎]  │
└─────────────────────────────────────────┘
                          사이드 드로어 →
                          📋 작업 / 🔔 결재
                          🗂️ 계획 / 📂 자료
```

- **왼쪽 사이드바** — 에이전트 아이콘 (상태별 시각 표현)
- **오른쪽 메인** — 채팅 영역 (마크다운 → HTML 렌더링)
- **하단** — 커맨드 바 + 파일 첨부(📎) + 결재 승인/반려 모드
- **사이드 드로어** — 작업, 결재, 계획 트리, 자료(이미지 미리보기)

### 에이전트 아이콘 상태

| 상태 | 시각 표현 |
|------|----------|
| 작업중 | 파란 테두리 + 글로우 펄스 |
| 생각중 | 노란 테두리 + 빠른 펄스 + 깜빡이는 점 |
| 대기 | 초록 테두리 (온라인) |
| 비활성 | 회색 + 반투명 |
| 등록중 | 보라 점선 + 회전 |

### 채팅

에이전트 응답이 **마크다운 → HTML**로 렌더링됩니다:

- 헤딩 (`#`, `##`, `###`)
- 볼드/이탈릭 (`**bold**`, `*italic*`)
- 코드 블록 (` ```code``` `)
- 테이블 (`| col | col |`)
- 리스트 (순서/비순서)
- 인용 (`> quote`)
- 링크, 이미지, @멘션 하이라이트

### 계획 트리

🗂️ 버튼을 누르면 풀스크린 오버레이로 표시됩니다:

```
┌──────────────────────────────────────────┐
│  🗂️ 작업 계획                         ✕  │
├──────────────────────────────────────────┤
│  [◐ 67%]  전체:12  완료:8  진행:2  대기:2 │
├──────────────────────────────────────────┤
│  🤖CEO ████████░░ 4/5                    │
│  💰CFO ██████░░░░ 3/5                    │
├──────────────────────────────────────────┤
│  💻 개발 Development          3/5 ██████░ │
│    ✓ API 서버 구현            👨‍💻CTO      │
│    ↻ DB 스키마 설계            👨‍💻CTO      │
│                                          │
│  🎨 디자인 Design            2/2 ████████ │
│    (완료 — 자동 접힘)                      │
│                                          │
│  📢 마케팅 Marketing          1/3 ███░░░░ │
│    ↻ SNS 콘텐츠 기획          📊CMO       │
└──────────────────────────────────────────┘
```

- **원형 진행률 게이지** (SVG) — 전체 완료율
- **에이전트별 프로그레스바** — 누가 얼마나 했는지
- **카테고리별 분류** — 개발, 기획, 마케팅, 디자인, 운영
- **칸반 보드 작업 자동 병합** — plan_tasks + board_tasks 통합 표시
- **완료 항목 자동 접기**, 접기/펼치기 토글
- **인라인 추가/삭제** — 직접 작업 추가 가능

## 핵심 기능

### 에이전트 시스템
| 기능 | 설명 |
|------|------|
| **멀티 컴퍼니** | 여러 회사를 동시에 운영, 회사별 격리된 SQLite DB |
| **에이전트 계층** | Master → CEO → 팀원, 동적 리더 감지 |
| **시스템 커맨드** | `[TASK_ADD:]`, `[TASK_DONE:]`, `[APPROVAL:]`, `[CRON_ADD:]` |
| **가드레일** | @멘션 또는 커맨드 필수, 준비 발언만 하면 거부+재시도 |
| **메모리 스트림** | Stanford GenAgents 패턴: 최신성 × 중요도 × 관련성 |
| **비용 추적** | 에이전트별 토큰 사용량 및 비용 모니터링 |
| **실시간 SSE** | Server-Sent Events로 라이브 업데이트 |

### 커뮤니케이션
| 기능 | 설명 |
|------|------|
| **채팅** | 마크다운 렌더링, 실시간 대화 표시 |
| **파일 업로드** | 📎 버튼으로 이미지/문서 첨부, 에이전트에 전달 |
| **이미지 서빙** | PNG/JPG 등 올바른 MIME 타입으로 반환 + 미리보기 |
| **아웃소싱** | 회사 간 작업 위임 (Company A → Company B) |
| **검색** | 전체 채팅 풀텍스트 검색 |

### 작업 관리
| 기능 | 설명 |
|------|------|
| **칸반 보드** | 대기 → 진행중 → 완료 |
| **계획 트리** | 카테고리별 분류 + 자동 생성 + 원형 진행률 |
| **결재 시스템** | `[APPROVAL:]` + 키워드 자동 감지, 중복 방지 |
| **스프린트** | 타임박스 작업 주기 + 자동 회고 |
| **에스컬레이션** | 실패 → 상위자 → CEO → Master (최대 2단계) |
| **반복 작업** | `[CRON_ADD:]`로 정기 작업 스케줄링 |

### 거버넌스 & 분석
| 기능 | 설명 |
|------|------|
| **승인 중복 방지** | 같은 제목의 pending 승인이 있으면 스킵 |
| **예산 관리** | 회사별 예산 추적, 초과 시 자동 승인 요청 |
| **KPI 대시보드** | 완료율, 비용 효율, 에이전트 랭킹 |
| **위키** | 카테고리별 지식 베이스 (SOP, 가이드, 결정, 참고) |
| **위험 관리** | 심각도별 위험 등록 + 대응 계획 |
| **감사 로그** | 전체 행동 기록 |
| **다국어** | 첫 방문 시 원하는 언어 입력 → LLM이 UI 번역 |

## 아키텍처

### 에이전트 통신 파이프라인

```
사용자 명령 → 서버 큐잉 (에이전트당 최대 10개)
          → 에이전트 컨텍스트 로드 (신문 + 인박스 + 메모리 + 작업)
          → 에이전트 응답
          → 가드레일 검증
             ├─ 통과 → 커맨드 파싱 (칸반/승인/계획 업데이트)
             └─ 실패 → 거부 + 재시도 (enforcement prompt)
          → @멘션 발견 → 대상 에이전트에 전달
          → 실패 시 → 상위자에게 에스컬레이션
```

### 3단계 재시도 + 에스컬레이션
1. **시도 1**: 일반 호출 (120초 타임아웃)
2. **시도 2**: 락 정리 + 2초 대기 + 새 세션으로 재시도
3. **시도 3**: 전체 세션 리셋 + 재시도
4. **에스컬레이션**: 상위자 → 리더 → Master (최대 2단계)

### 메모리 스트림 (Stanford GenAgents)
에이전트별 메모리 스트림:
- **최신성**: 최근 메모리 점수 높음 (지수 감쇠)
- **중요도**: 1-10 점 (응답 길이/중요성 기반)
- **관련성**: 현재 쿼리와 키워드 매칭
- 에이전트당 100개 상한, 자동 정리

### 동시성 모델
- **FIFO 큐**: 에이전트당 최대 10개 대기 메시지
- **세마포어**: 설정 가능한 동시 에이전트 수
- **바쁨 추적**: 에이전트별 busy 상태, 데드락 방지
- **락 자동 정리**: 프로세스 kill 및 시작 시 `.lock` 파일 정리

## 에이전트 커맨드

에이전트가 응답에 포함시켜 시스템을 제어합니다. **두 가지 포맷 모두 지원**:

### Legacy 포맷 (현재 프롬프트가 사용)
```
[TASK_ADD:작업명:high]                — 칸반에 작업 추가
[TASK_START:작업명]                   — 작업 시작
[TASK_DONE:작업명]                    — 작업 완료
[TASK_BLOCK:작업명:사유]              — 작업 차단
[APPROVAL:카테고리:제목:상세]          — 결재 요청
[CRON_ADD:이름:간격(분):프롬프트]      — 반복 작업 등록
[CRON_DEL:이름]                      — 반복 작업 삭제
```

### 통합 포맷 (옵션)
```
[TASK:add:작업명:high]
[TASK:start:작업명]
[TASK:done:작업명]
[TASK:block:작업명:사유]
[CRON:add:이름:간격:프롬프트]
[CRON:del:이름]
```

두 포맷이 같은 응답 안에서 혼용 가능합니다.

## API

### 회사 & 채팅
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/companies` | GET | 회사 목록 |
| `/api/company/{cid}` | GET | 회사 상세 (에이전트, 채팅, 작업) |
| `/api/companies` | POST | 회사 생성 `{name, topic, lang}` |
| `/api/company/delete` | POST | 회사 삭제 |
| `/api/chat/{cid}` | POST | 메시지 전송 `{text}` |
| `/api/upload/{cid}` | POST | 파일 업로드 (multipart/form-data) |
| `/api/search?q=` | GET | 채팅 검색 |
| `/api/sse` | GET | 실시간 SSE 스트림 |

### 에이전트
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/agent-add/{cid}` | POST | 에이전트 추가 `{name, role, emoji}` |
| `/api/agent-delete/{cid}/{aid}` | POST | 에이전트 삭제 |
| `/api/agent-reactivate/{cid}/{aid}` | POST | 에이전트 재활성화 |
| `/api/models` | GET | 사용 가능한 LLM 모델 |

### 작업 관리
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/board-tasks/{cid}` | GET | 칸반 작업 |
| `/api/plan-tasks/{cid}` | GET | 계획 트리 |
| `/api/plan-task-add/{cid}` | POST | 계획 작업 추가 |
| `/api/approvals/{cid}?status=pending` | GET | 대기 중 결재 |
| `/api/approval-approve/{cid}` | POST | 승인 |
| `/api/approval-reject/{cid}` | POST | 반려 |

### 자료 & 분석
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/deliverables/{cid}` | GET | 결과물 (이미지 미리보기) |
| `/api/file/{cid}/{path}` | GET | 파일 다운로드 (MIME 타입 자동 감지) |
| `/api/download/{cid}` | GET | 전체 결과물 ZIP |
| `/api/costs/{cid}` | GET | 비용 추적 |
| `/api/kpi/{cid}` | GET | KPI 대시보드 |
| `/api/narrative/{cid}` | GET | 활동 로그 |

### 크로스 컴퍼니
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/cross-nudge` | POST | 아웃소싱 `{from_cid, to_cid, text}` |
| `/api/snapshot/{cid}` | POST | 스냅샷 저장 |
| `/api/fork/{snap_id}` | POST | 스냅샷에서 포크 |

## 프로젝트 구조

```
sabujak/
├── dashboard/
│   ├── server.py            # FastAPI 서버 (~5100줄, 100+ 엔드포인트)
│   ├── db.py                # SQLite 회사별 샤딩 (~1600줄)
│   ├── pool.py              # DB 커넥션 풀
│   │
│   ├── index.html           # SPA 셸 (170줄, 외부 자산 참조만)
│   ├── app.css              # 스타일시트 (220줄)
│   ├── app.js               # 프론트엔드 로직 (812줄)
│   │
│   ├── config.py            # 매직 넘버/타임아웃 중앙화 (env 오버라이드)
│   ├── logger.py            # 중앙 로깅 어댑터
│   ├── observability.py     # request_id, 프롬프트 덤프
│   │
│   ├── parsers/             # 순수 파서 (DB 의존성 없음, 단위 테스트 가능)
│   │   ├── commands.py      #   [TASK_*], [APPROVAL:], [CRON_*] + 통합 [TASK:add:]
│   │   ├── guardrails.py    #   준비 발언 판정, 액션 필수 체크
│   │   ├── categories.py    #   작업 카테고리 분류 (5개)
│   │   └── heuristics.json  #   prep 키워드 + 카테고리 키워드 외부 설정
│   │
│   ├── prompts/
│   │   └── welcome.py       # 다국어 환영 메시지 (ko/en/ja/zh)
│   │
│   ├── config.json          # 에이전트 템플릿 & 토픽 설정
│   ├── i18n/                # 다국어 UI 문자열
│   │   ├── en.json
│   │   └── ko.json
│   └── runtime/
│       ├── base.py          # AgentRuntime ABC
│       └── openclaw.py      # OpenClaw CLI 런타임 (JSONL 폴링)
│
├── tests/                   # pytest 단위 테스트 (47개)
│   ├── conftest.py
│   ├── test_command_parser.py    # 28개 (legacy + 통합 DSL)
│   ├── test_guardrails.py        # 11개
│   ├── test_categories.py        # 6개
│   └── test_welcome.py           # 3개
│
├── data/                    # 회사 데이터 (.gitignored)
│   ├── hub.db               # 메타 DB
│   └── {company-id}/
│       ├── company.db       # 회사별 SQLite (20+ 테이블)
│       ├── _shared/         # 결과물, 공유 파일
│       └── workspaces/      # 에이전트별 워크스페이스
│
├── pytest.ini
├── requirements.txt
└── README.md
```

## 서버 관리

```bash
# 시작
nohup python3 -u dashboard/server.py > /tmp/sabujak.log 2>&1 &

# 재시작
pkill -f 'python3.*server.py'; sleep 2
nohup python3 -u dashboard/server.py > /tmp/sabujak.log 2>&1 &

# 상태 확인
curl -s http://localhost:3000/api/companies | python3 -m json.tool

# 로그
tail -f /tmp/sabujak.log
```

## 테스트

순수 파서/가드레일/카테고리 로직은 DB 의존성 없이 단위 테스트됩니다 (47개).

```bash
pytest                       # 전체 실행
pytest tests/test_command_parser.py -v   # 커맨드 파서만
```

## 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `PORT` | `3000` | 서버 포트 |
| `DATA_DIR` | `data/` | 회사 데이터 디렉토리 |
| `OPENCLAW_MODEL` | `zai/glm-5` | 에이전트 LLM 모델 |
| `AGENT_TIMEOUT` | `180` | 에이전트 호출 타임아웃 (초) |
| `AGENT_RETRY_TIMEOUT` | `120` | 재시도 타임아웃 |
| `MAX_CONCURRENT` | `5` | 동시 실행 에이전트 수 |
| `AGENT_QUEUE_MAX` | `10` | 에이전트당 큐 최대 크기 |
| `LOG_LEVEL` | `INFO` | 로그 레벨 (DEBUG/INFO/WARNING/ERROR) |
| `LOG_FILE` | (없음) | 로그 파일 경로 (지정 시 회전 로그) |
| `DEBUG_PROMPTS` | `0` | `1`이면 nudge 호출 시 프롬프트+응답을 파일로 덤프 |
| `PROMPT_DUMP_DIR` | `/tmp/aichub-prompts` | 덤프 저장 경로 |

## 관측성

- **request_id**: 모든 HTTP 응답에 `X-Request-Id` 헤더 자동 부여
  ```bash
  curl -sD - http://localhost:3000/api/companies | grep -i request-id
  # x-request-id: c82aafd0d801
  ```
- **프롬프트 덤프**: `DEBUG_PROMPTS=1` 시 `/tmp/aichub-prompts/`에 마크다운 형식으로 저장
  - 파일명: `{timestamp}_nudge_{agent_id}.md`
  - 내용: 전체 프롬프트 + 에이전트 응답

## 요구 사항
- Python 3.12+
- [OpenClaw](https://openclaw.io) (에이전트 런타임)
- LLM API 키
- (선택) `pytest` — 단위 테스트 실행 시

## License
MIT
