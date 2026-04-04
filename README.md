# 🏢 AI Company Hub

여러 AI 에이전트로 구성된 가상 회사를 만들고, 실시간으로 협업하게 만드는 대시보드입니다.

OpenClaw 기반으로 동작하며, CEO/CTO/CMO 등의 역할을 가진 AI 에이전트들이 자연어로 소통하고 업무를 수행합니다.

## ✨ 기능

- **회사 생성** — 프로젝트명과 주제만 입력하면 자동으로 초기 팀(CEO/CTO/CMO) 구성
- **에이전트 관리** — 에이전트 추가/삭제, 상태 확인
- **실시간 채팅** — `@멘션`으로 특정 에이전트에게 지시
- **자연어 에이전트 생성** — 채팅에서 "CTO 만들어줘" 같은 자연어로 에이전트 추가
- **한국어/영어/일본어** 인터페이스 지원
- **토스트 알림** — 생성/삭제 등 작업 상태 실시간 표시

## 📋 요구사항

- **Python 3.8+**
- **OpenClaw** — [설치 가이드](https://docs.openclaw.ai)
- **웹 브라우저** (Chrome, Firefox, Safari 등)

## 🚀 설치 및 실행

```bash
# 1. 리포지토리 클론
git clone https://github.com/rPthrqns/ai-company-hub.git
cd ai-company-hub

# 2. 의존성 없음 (순수 Python 표준 라이브러리)
# OpenClaw만 설치되어 있으면 됩니다

# 3. 서버 실행
cd ai-company/dashboard
python3 server.py

# 4. 브라우저에서 접속
# http://localhost:3000
```

## 📖 사용법

### 회사 만들기
1. 대시보드 우측 상단 `＋ 새 회사` 버튼 클릭
2. 회사명과 주제/목표 입력
3. `생성` 클릭 → 자동으로 CEO, CTO, CMO가 합류

### 에이전트에게 지시하기
- 채팅창에서 메시지 입력 (기본적으로 CEO가 응답)
- `@CTO 웹사이트 기획안 짜줘` — 특정 에이전트에게 지시
- `디자이너 만들어줘` — 자연어로 에이전트 추가

### 지원 에이전트 역할
CEO, CTO, CFO, COO, CMO, CPO, CHRO, CSO, 디자이너, 개발자, 영업, 고객지원, 마케팅, 인사, 법무, 데이터, 홍보, 기획

## 🏗️ 프로젝트 구조

```
ai-company-hub/
├── ai-company/
│   ├── dashboard/
│   │   ├── server.py      # 백엔드 서버 (Python)
│   │   └── index.html     # 프론트엔드 (단일 HTML)
│   └── data/              # 회사 데이터 (자동 생성)
│       ├── companies.json
│       └── company-{id}/  # 각 회사별 상태
└── README.md
```

## 🛠️ 기술 스택

- **Backend**: Python `http.server` (표준 라이브러리)
- **Frontend**: Vanilla HTML/CSS/JS (프레임워크 없음)
- **AI**: OpenClaw Agent System
- **데이터**: JSON 파일 기반

## 📄 라이선스

MIT
