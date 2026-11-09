# AI 주식 심리 분석 에이전트

yfinance 시세 → Claude AI 시장 심리 분석 → Oracle 적재 → Neo4j 지식 그래프 → GraphRAG 질의 / MCP 서버

> 개인 학습용 프로젝트입니다. 분석 결과는 투자 조언이 아니며, 자체 검증 결과 다음 날 주가 예측력은 확인되지 않았습니다 (`docs/2026-10-25_mcp_test_and_evaluation.md`).

## 구성

| 파일 | 역할 |
|---|---|
| `sentiment_agent.py` | 배치: 인기 종목 거래량 Top 10 + ETF 10종 + 관심 종목의 빠진 거래일을 분석해 Oracle에 저장, 끝나면 Neo4j 동기화 |
| `universe.py` | 분석 대상 종목 목록. 내 관심 종목은 `my_portfolio.py`에 `MY_PORTFOLIO = [...]`로 두면 그 값을 사용 (git 제외) |
| `graph_sync.py` | Oracle → Neo4j 지식 그래프 동기화 |
| `graph_rag.py` | 자연어 질문 → Cypher → Neo4j 조회 → 답변 |
| `stock_graph_mcp.py` | Claude Code 등에서 쓰는 MCP 서버 (읽기 전용) |
| `claude_cli.py` | Claude 호출 모듈 (Claude Code CLI `claude -p`) |
| `config.py` | DB 접속 설정 (`.env`에서 읽음) |
| `db/init.sql` | Oracle 테이블 정의 |

## 준비물

- Python 3.11+
- Docker (Docker Desktop)
- [Claude Code](https://docs.claude.com/en/docs/claude-code) 설치 후 로그인 (`claude` 실행 → 로그인)
  - AI 분석은 API 키가 아니라 **로그인한 Claude 구독 사용량**으로 호출합니다.

## 설치 및 실행

```bash
git clone <이 저장소 주소>
cd ai-agent-project

# 1. 비밀번호 설정 - .env는 git에 올라가지 않습니다
cp .env.example .env          # PowerShell: Copy-Item .env.example .env
#    .env를 열어 ORACLE_DB_PASSWORD, NEO4J_PASSWORD(8자 이상)를 원하는 값으로 변경

# 2. DB 실행 (Oracle + Neo4j). Oracle은 첫 실행 시 초기화에 1~2분 걸리고 테이블도 자동 생성됩니다
docker compose up -d
docker compose logs -f oracle   # "DATABASE IS READY TO USE!"가 나오면 Ctrl+C

# 3. 파이썬 패키지
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 4. 실행
python sentiment_agent.py AAPL       # 한 종목만 테스트
python sentiment_agent.py            # 전체 배치 (Claude 호출이 많으니 사용량 주의)
python graph_rag.py "엔비디아를 담은 ETF는?"
```

Neo4j 브라우저: http://localhost:7474 (계정 `neo4j` / `.env`의 `NEO4J_PASSWORD`). 예제 쿼리는 `graph_queries.cypher`에 있습니다.

## MCP 서버 등록 (Claude Code)

```bash
claude mcp add stock-graph --scope user -- <python 경로> <프로젝트 경로>/stock_graph_mcp.py
```

venv를 쓴다면 `<python 경로>`는 `.venv/Scripts/python.exe`(Windows) 또는 `.venv/bin/python` 절대 경로로 지정합니다. 등록 후 Claude Code에서 "내 포트폴리오 최신 심리 알려줘"처럼 질문하면 됩니다.

## 설정

모든 접속 정보는 `.env`(또는 OS 환경변수)로 지정합니다. 항목은 `.env.example`을 참고하세요.

| 변수 | 기본값 |
|---|---|
| `ORACLE_DB_PASSWORD` | (필수) |
| `ORACLE_USER` / `ORACLE_DSN` | `system` / `localhost:1521/FREEPDB1` |
| `NEO4J_PASSWORD` | (필수) |
| `NEO4J_USER` / `NEO4J_URI` | `neo4j` / `bolt://localhost:7687` |
| `CLAUDE_BIN` | 자동 탐색 (`claude` CLI 위치를 못 찾을 때만 지정) |

이미 운영 중인 Oracle이 있다면 `db/init.sql`의 `CREATE TABLE`만 실행하고 `ORACLE_DSN`을 그쪽으로 맞춘 뒤, `docker compose up -d neo4j`로 Neo4j만 띄우면 됩니다.
