import re
import sys
import json
from neo4j import READ_ACCESS
from claude_cli import run_claude, UsageLimitReached
from config import neo4j_driver

# ==========================================
# 🔎 GraphRAG: 자연어 질문 → Cypher(Text2Cypher) → Neo4j 조회 → 근거 기반 답변
#   python graph_rag.py                       대화형 모드
#   python graph_rag.py "엔비디아를 담은 ETF는?"   한 번만 질문
# ==========================================

CYPHER_MODEL = "sonnet"   # 쿼리 생성은 정확도가 중요해서 Sonnet
ANSWER_MODEL = "sonnet"
MAX_ROWS = 50             # 답변 생성에 넘길 최대 결과 행 수

GRAPH_SCHEMA = """[노드]
(:Security {ticker, name, currency})  모든 종목/ETF의 공통 라벨. ticker가 고유키
  - 추가 라벨 :Stock (개별 주식) 또는 :ETF
  - 한국 주식 티커는 '005930.KS' 형식, name은 영문 (예: 'Samsung Electronics Co., Ltd.')
  - ETF 보유종목으로만 등장하는 종목은 :Stock/:ETF 라벨 없이 :Security만 있을 수 있음
(:Sector {name})       예: 'Technology', 'Healthcare' (영문)
(:Industry {name})     예: 'Semiconductors', 'Consumer Electronics' (영문)
(:Category {name})     ETF 분류. 예: 'Large Blend', 'Technology'
(:Portfolio {name})    사용자의 관심 포트폴리오. name = '내 포트폴리오'
(:DailyAnalysis {id, ticker, date, sentiment, reason, open, close, volume})
  - date는 Neo4j date 타입 (비교 예: a.date >= date('2026-10-01'), 최근: a.date >= date() - duration('P7D'))
  - sentiment는 '긍정' / '중립' / '부정' 중 하나, reason은 한국어 분석 이유
  - 분석 대상은 매일 '내 포트폴리오' + 고정 ETF 10종 + 인기 주식 30개 중 그날 거래량 Top 10뿐이라,
    그래프에 있어도 분석이 없거나 띄엄띄엄 있는 종목이 있다 (예: Top 10에 못 든 날은 분석 없음)

[관계]
(:Stock)-[:IN_INDUSTRY]->(:Industry)-[:PART_OF]->(:Sector)
(:ETF)-[:IN_CATEGORY]->(:Category)
(:ETF)-[:HOLDS {weight}]->(:Security)      weight는 0~1 비율 (0.083 = 8.3%), ETF 상위 보유종목만 있음
(:Portfolio)-[:WATCHES]->(:Security)
(:Security)-[:HAS_ANALYSIS]->(:DailyAnalysis)"""

CYPHER_EXAMPLES = """질문: 내 포트폴리오 종목을 담고 있는 ETF와 비중은?
MATCH (:Portfolio)-[:WATCHES]->(s:Security)<-[h:HOLDS]-(e:ETF)
RETURN s.ticker AS ticker, s.name AS name, e.ticker AS etf, round(h.weight * 100, 2) AS weight_pct
ORDER BY ticker, weight_pct DESC

질문: 반도체 산업 종목들의 최근 AI 심리는?
MATCH (s:Stock)-[:IN_INDUSTRY]->(i:Industry {name: 'Semiconductors'})
MATCH (s)-[:HAS_ANALYSIS]->(a:DailyAnalysis)
WITH s, a ORDER BY a.date DESC
WITH s, collect(a)[0] AS latest
RETURN s.ticker AS ticker, s.name AS name, latest.date AS date, latest.sentiment AS sentiment, latest.reason AS reason"""

CYPHER_SYSTEM = f"""당신은 Neo4j Cypher 전문가입니다. 사용자의 한국어 질문을 아래 그래프 스키마에 맞는 읽기 전용 Cypher 쿼리 하나로 바꿉니다.

{GRAPH_SCHEMA}

[규칙]
- MATCH / OPTIONAL MATCH / WITH / WHERE / RETURN / ORDER BY / LIMIT 등 조회 구문만 사용 (CREATE, MERGE, SET, DELETE, CALL 금지)
- 스키마에 없는 라벨/관계/속성은 쓰지 않는다
- 한국어 회사명(예: 엔비디아, 삼성전자)은 아래 [종목 목록]을 보고 ticker로 바꿔서 조회한다
- 섹터/산업/카테고리 이름은 영문이다
- RETURN 컬럼에는 알아보기 쉬운 별칭을 붙이고, 결과가 많을 수 있으면 LIMIT {MAX_ROWS}를 붙인다
- 그래프로 답할 수 없는 질문이면 cypher를 빈 문자열로 두고 reason에 이유를 쓴다

[예시]
{CYPHER_EXAMPLES}"""

CYPHER_SCHEMA = {
    "type": "object",
    "properties": {
        "cypher": {"type": "string"},
        "reason": {"type": "string", "description": "이 쿼리를 고른 이유 (한 문장)"}
    },
    "required": ["cypher", "reason"],
    "additionalProperties": False
}

ANSWER_SYSTEM = """당신은 주식 지식 그래프를 근거로 답하는 애널리스트입니다.
- 반드시 주어진 조회 결과에 있는 사실만 근거로 한국어로 답한다
- 결과에 없는 내용은 추측하지 말고 '그래프에 데이터가 없다'고 말한다
- 비중은 %로, 날짜는 YYYY-MM-DD로 표기하고, 필요하면 표나 목록으로 정리한다
- 투자 권유는 하지 않는다"""

WRITE_PATTERN = re.compile(r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|CALL|LOAD|FOREACH)\b", re.IGNORECASE)


def load_ticker_catalog(driver):
    # 한국어 회사명을 ticker로 바꿀 수 있도록 그래프에 있는 종목 목록을 프롬프트에 넣는다
    with driver.session(default_access_mode=READ_ACCESS) as session:
        rows = session.run("""
            MATCH (s:Security) WHERE s:Stock OR s:ETF
            RETURN s.ticker AS t, s.name AS n, CASE WHEN s:ETF THEN 'ETF' ELSE '주식' END AS k
            ORDER BY t
        """)
        return "\n".join(f"{r['t']} | {r['n']} | {r['k']}" for r in rows)


def run_cypher(driver, cypher, params=None):
    if WRITE_PATTERN.search(cypher):
        raise ValueError("읽기 전용이 아닌 구문이 포함되어 있어 실행하지 않습니다.")
    with driver.session(default_access_mode=READ_ACCESS) as session:
        # 읽기 트랜잭션으로 실행 - 혹시 쓰기 구문이 있어도 DB가 거부한다
        return session.execute_read(lambda tx: [r.data() for r in tx.run(cypher, params or {})])


def generate_cypher(question, catalog, previous=None, error=None):
    prompt = f"[종목 목록] (ticker | name | 구분)\n{catalog}\n\n질문: {question}"
    if previous:
        prompt += f"\n\n이전에 만든 쿼리가 실패했습니다. 고쳐서 다시 만들어 주세요.\n쿼리:\n{previous}\n에러:\n{error}"
    return run_claude(prompt, CYPHER_SYSTEM, model=CYPHER_MODEL, json_schema=CYPHER_SCHEMA)


def ask(driver, catalog, question):
    print("🧩 Cypher 생성 중...")
    plan = generate_cypher(question, catalog)
    cypher = plan["cypher"].strip()
    if not cypher:
        return f"그래프로 답할 수 없는 질문입니다: {plan['reason']}"

    rows = None
    for attempt in range(2):
        print(f"\n[Cypher]\n{cypher}\n")
        try:
            rows = run_cypher(driver, cypher)
            break
        except Exception as e:
            if attempt == 1:
                return f"쿼리 실행에 실패했습니다: {e}"
            print(f"⚠️ 쿼리 실패, 수정을 요청합니다: {e}")
            cypher = generate_cypher(question, catalog, previous=cypher, error=str(e))["cypher"].strip()

    print(f"📊 조회 결과 {len(rows)}행 → 답변 생성 중...")
    result_json = json.dumps(rows[:MAX_ROWS], ensure_ascii=False, default=str)
    note = f"(전체 {len(rows)}행 중 {MAX_ROWS}행만 표시)" if len(rows) > MAX_ROWS else ""
    prompt = f"질문: {question}\n\n실행한 Cypher:\n{cypher}\n\n조회 결과 {note}:\n{result_json}"
    return run_claude(prompt, ANSWER_SYSTEM, model=ANSWER_MODEL)


def main():
    driver = neo4j_driver()
    try:
        driver.verify_connectivity()
        catalog = load_ticker_catalog(driver)

        if len(sys.argv) > 1:
            print(ask(driver, catalog, " ".join(sys.argv[1:])))
            return

        print("🔎 주식 지식 그래프 GraphRAG - 질문을 입력하세요 (종료: 빈 줄 또는 exit)")
        while True:
            question = input("\n❓ 질문> ").strip()
            if not question or question.lower() in ("exit", "quit", "q"):
                break
            try:
                print(f"\n💬 {ask(driver, catalog, question)}")
            except UsageLimitReached:
                print("⛔ Claude 사용 한도에 도달했습니다. 잠시 후 다시 시도하세요.")
                break
            except Exception as e:
                print(f"🚨 오류: {e}")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
