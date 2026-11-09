import sys
import json
import contextlib
from config import neo4j_driver
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
import graph_rag

# ==========================================
# 🔌 주식 지식 그래프 MCP 서버 (stdio)
#   Claude Code / Claude Desktop 같은 MCP 클라이언트가 Neo4j 그래프를 직접 조회할 수 있게 도구를 노출한다.
#   ※ stdio 방식은 stdout이 통신 채널이라, 진행 로그는 전부 stderr로 보낸다.
# ==========================================

server = MCPServer(
    name="stock-graph",
    instructions=(
        "사용자의 주식/ETF 지식 그래프(Neo4j)를 조회하는 도구입니다. "
        "구조를 모르면 먼저 get_graph_schema를 호출하고, 직접 Cypher를 짤 수 있으면 run_cypher를, "
        "포트폴리오/종목의 최신 AI 심리는 get_latest_sentiment를 쓰세요. 모든 도구는 읽기 전용입니다."
    ),
)

driver = neo4j_driver()
READ_ONLY = ToolAnnotations(readOnlyHint=True)


def to_json(rows):
    return json.dumps(rows, ensure_ascii=False, default=str)


@server.tool(annotations=READ_ONLY)
def get_graph_schema() -> str:
    """그래프 스키마(노드/관계/속성 설명)와 그래프에 있는 종목 목록(ticker | name | 구분)을 돌려준다."""
    return f"{graph_rag.GRAPH_SCHEMA}\n\n[종목 목록] (ticker | name | 구분)\n{graph_rag.load_ticker_catalog(driver)}"


@server.tool(annotations=READ_ONLY)
def run_cypher(query: str) -> str:
    """읽기 전용 Cypher 쿼리를 실행하고 결과를 JSON 배열로 돌려준다 (최대 200행).
    CREATE/MERGE/SET/DELETE/CALL 등 쓰기 구문은 거부된다. 스키마는 get_graph_schema로 확인."""
    try:
        rows = graph_rag.run_cypher(driver, query)
    except Exception as e:
        # 차단/문법 오류 이유를 클라이언트(LLM)가 보고 쿼리를 고칠 수 있게 그대로 전달
        raise ToolError(str(e)) from e
    if len(rows) > 200:
        return f"{to_json(rows[:200])}\n※ 전체 {len(rows)}행 중 앞의 200행만 반환했습니다. 집계하거나 LIMIT/WHERE로 좁혀 주세요."
    return to_json(rows)


@server.tool(annotations=READ_ONLY)
def get_latest_sentiment(tickers: list[str] | None = None) -> str:
    """종목별 가장 최근 AI 심리 분석(날짜, 긍정/중립/부정, 이유, 종가)을 돌려준다.
    tickers를 비우면 '내 포트폴리오' 종목 전체. 한국 주식은 '005930.KS' 형식."""
    query = """
        MATCH (s:Security)
        WHERE (size($tickers) = 0 AND (:Portfolio)-[:WATCHES]->(s)) OR s.ticker IN $tickers
        OPTIONAL MATCH (s)-[:HAS_ANALYSIS]->(a:DailyAnalysis)
        WITH s, a ORDER BY a.date DESC
        WITH s, collect(a)[0] AS latest
        RETURN s.ticker AS ticker, s.name AS name, latest.date AS date,
               latest.sentiment AS sentiment, latest.close AS close, latest.reason AS reason
        ORDER BY ticker
    """
    rows = graph_rag.run_cypher(driver, query, {"tickers": tickers or []})
    return to_json(rows)


@server.tool(annotations=READ_ONLY)
def ask_stock_graph(question: str) -> str:
    """자연어 질문을 받아 GraphRAG 파이프라인(질문→Cypher→조회→답변)으로 한국어 답변을 돌려준다.
    내부에서 Claude를 2~3번 호출해서 15~30초 걸린다. 직접 Cypher를 짤 수 있으면 run_cypher가 더 빠르다."""
    with contextlib.redirect_stdout(sys.stderr):
        catalog = graph_rag.load_ticker_catalog(driver)
        return graph_rag.ask(driver, catalog, question)


if __name__ == "__main__":
    server.run()
