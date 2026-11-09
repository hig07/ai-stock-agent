# AI 주식 분석 에이전트

개인 학습용 프로젝트. yfinance 시세 → Claude AI 심리 분석 → Oracle 적재 → Neo4j 지식 그래프 → GraphRAG / MCP 서버.
설명과 답변은 한국어로.

## 구성
| 파일 | 역할 |
|---|---|
| `sentiment_agent.py` | 배치. 1부 인기 30개 중 거래량 Top 10 → 2부 ETF 10종 → 3부 내 포트폴리오. 빠진 거래일만 분석해 Oracle에 INSERT, 끝나면 `graph_sync` 호출 |
| `universe.py` | 종목 목록 (후보군 / ETF / 내 포트폴리오). 실제 포트폴리오는 `my_portfolio.py`(git 제외)가 있으면 그 값 사용 |
| `claude_cli.py` | Claude 호출 공용 모듈. **API 키 없이 구독으로** `claude -p` 헤드리스 실행 (`claude.exe` 직접 호출, `--json-schema` 구조화 출력) |
| `graph_sync.py` | Oracle → Neo4j 동기화 (MERGE라 반복 실행 안전, 메타데이터는 7일마다 갱신) |
| `graph_rag.py` | GraphRAG: 질문 → Cypher(Sonnet) → 읽기 전용 실행 → 답변 |
| `stock_graph_mcp.py` | MCP 서버 (mcp SDK 2.x `MCPServer`, stdio). 도구: get_graph_schema, run_cypher, get_latest_sentiment, ask_stock_graph. Claude Code user scope에 `stock-graph`로 등록됨 |
| `config.py` | DB 접속 설정. `.env`(python-dotenv) → 환경변수에서 읽음. 비밀번호 기본값 없음 |
| `db/init.sql` | `STOCK_SENTIMENT` 테이블 DDL (compose의 Oracle 최초 생성 시 자동 실행) |
| `graph_queries.cypher` | Neo4j 브라우저용 예제 쿼리 |
| `sentiment_agent_gemini_backup.py`, `test.py` | 예전 Gemini 버전 (참고용, `.gitignore`로 git 제외) |
| `docs/gemini_conversation.md` | 초기 개발 과정의 Gemini 대화 기록 (74턴, git 제외) |

## 실행
```powershell
python sentiment_agent.py            # 전체 배치
python sentiment_agent.py AAPL       # 지정 종목만 (테스트)
python graph_sync.py                 # Neo4j 동기화만
python graph_rag.py ["질문"]          # GraphRAG (인자 없으면 대화형)
docker compose up -d neo4j           # Neo4j만 (로컬 Oracle은 기존 컨테이너 affectionate_moore 사용 중)
```

## 인프라
- Oracle: Docker `gvenzl/oracle-free` (컨테이너 `affectionate_moore`), `localhost:1521/FREEPDB1`, system / `.env`의 `ORACLE_DB_PASSWORD`
  - 테이블 `STOCK_SENTIMENT`: TICKER, TARGET_DATE, COMPANY_NAME, OPEN/CLOSE_PRICE, TRADING_VOLUME, MARKET_SENTIMENT(긍정/중립/부정), ANALYSIS_REASON, CREATED_AT, UNIQUE(TICKER, TARGET_DATE)
- Neo4j 5.26 community: 컨테이너 `stock-knowledge-graph`, http://localhost:7474, bolt 7687, neo4j / `.env`의 `NEO4J_PASSWORD`
- 온톨로지: `(:Security:Stock|:ETF)`, `(:Stock)-[:IN_INDUSTRY]->(:Industry)-[:PART_OF]->(:Sector)`, `(:ETF)-[:IN_CATEGORY]->(:Category)`, `(:ETF)-[:HOLDS {weight 0~1}]->(:Security)`, `(:Portfolio {name:'내 포트폴리오'})-[:WATCHES]->`, `(:Security)-[:HAS_ANALYSIS]->(:DailyAnalysis {date, sentiment, reason, open, close, volume})`

## 설계 결정
- 공유용 git 저장소 기준: 비밀번호는 `.env`(git 제외)에만. 코드/compose에 기본 비밀번호 없음. 공개 문서는 README.md, `.env.example`.
- 배치는 PC가 켜져 있을 때만 실행. 꺼져 있던 공백은 다음 실행 때 채움: 마지막 저장일 이후 + 최근 14일(`REPAIR_DAYS`) 안의 빈 날짜, 단 종목의 첫 저장일 이전은 제외. 신규 종목은 최근 3영업일.
- 장 마감 전 실행 시 당일 봉은 저장하지 않음 (`drop_unfinished_bar`).
- Claude 사용 한도에 걸리면 `UsageLimitReached`로 배치 중단 → 다음 실행 때 이어서.
- 내부 `claude -p` 호출은 `--strict-mcp-config`로 MCP를 로드하지 않음.
- GraphRAG/MCP는 읽기 전용: 쓰기 키워드 정규식 차단 + READ 트랜잭션.
- 한국 주식 티커는 `.KS` (예: 005930.KS 삼성전자).
- 분석 프롬프트에 직전 20거래일 평균 거래량과 배수를 넣음 (`AVG_VOLUME_DAYS`). 분석 이유에 한자/일본어가 섞이면 실패 처리 후 재시도 (Haiku가 가끔 '集中' 같은 한자를 씀).
- yfinance 업종이 실제와 다른 종목은 `universe.INDUSTRY_OVERRIDES`로 보정 (삼성전자 → Semiconductors). 바꾼 뒤엔 해당 종목의 메타데이터 갱신 필요(7일 주기라 즉시 반영 안 됨).

## 남은 일 / 아이디어
- 2026-10-25 검증: 심리는 당일 등락 재진술(94% 일치), 다음 날 예측력 근거 없음 → 상세는 `docs/2026-10-25_mcp_test_and_evaluation.md`
- 프롬프트 버전 컬럼 추가 (10/25에 평균 거래량 추가로 프롬프트 변경됨 → 전후 구분 필요)
- 시세 외 정보 투입 (뉴스, 실적 발표일, SPY 대비 상대 강도), `evaluate.py`로 예측력 검증 정기화
- 새 프롬프트(평균 거래량/한자 검사)의 DB 적재는 10/26 장 마감 이후 배치에서 확인
- 윈도우 작업 스케줄러 등록, 로컬 Oracle을 compose의 `stock-oracle`로 이전(같은 1521 포트라 기존 `affectionate_moore`와 동시 실행 불가)
- `core_business` 등 추가 컬럼, 경쟁사 관계(COMPETES_WITH), 뉴스/RSS 연동
- Spring Boot로 조회 화면 (원래 구상)
