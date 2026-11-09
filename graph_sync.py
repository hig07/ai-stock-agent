import warnings
from datetime import date, timedelta
import yfinance as yf
from config import oracle_connect, neo4j_driver
from universe import STOCK_CANDIDATES, ETF_LIST, MY_PORTFOLIO, INDUSTRY_OVERRIDES

warnings.filterwarnings("ignore")

# ==========================================
# 🧠 Oracle(STOCK_SENTIMENT) → Neo4j 지식 그래프 동기화
#
# 온톨로지
#   (:Security:Stock | :Security:ETF {ticker, name, currency})
#   (:Stock)-[:IN_INDUSTRY]->(:Industry)-[:PART_OF]->(:Sector)
#   (:ETF)-[:IN_CATEGORY]->(:Category)
#   (:ETF)-[:HOLDS {weight}]->(:Security)          ETF 상위 보유 종목
#   (:Portfolio {name})-[:WATCHES]->(:Security)      내 관심 포트폴리오
#   (:Security)-[:HAS_ANALYSIS]->(:DailyAnalysis {date, sentiment, reason, open, close, volume})
#
# 모든 쓰기는 MERGE라서 몇 번을 다시 돌려도 중복이 생기지 않는다.
# ==========================================

# DB 접속 정보는 config.py / .env
META_REFRESH_DAYS = 7  # 섹터/산업/ETF 보유종목 같은 메타데이터는 일주일에 한 번만 갱신
PORTFOLIO_NAME = "내 포트폴리오"

CONSTRAINTS = [
    "CREATE CONSTRAINT security_ticker IF NOT EXISTS FOR (s:Security) REQUIRE s.ticker IS UNIQUE",
    "CREATE CONSTRAINT sector_name IF NOT EXISTS FOR (s:Sector) REQUIRE s.name IS UNIQUE",
    "CREATE CONSTRAINT industry_name IF NOT EXISTS FOR (i:Industry) REQUIRE i.name IS UNIQUE",
    "CREATE CONSTRAINT category_name IF NOT EXISTS FOR (c:Category) REQUIRE c.name IS UNIQUE",
    "CREATE CONSTRAINT portfolio_name IF NOT EXISTS FOR (p:Portfolio) REQUIRE p.name IS UNIQUE",
    "CREATE CONSTRAINT analysis_id IF NOT EXISTS FOR (a:DailyAnalysis) REQUIRE a.id IS UNIQUE",
]


def load_analyses():
    connection = oracle_connect()
    cursor = connection.cursor()
    try:
        cursor.execute("""
            SELECT TICKER, TARGET_DATE, COMPANY_NAME, OPEN_PRICE, CLOSE_PRICE,
                   TRADING_VOLUME, MARKET_SENTIMENT, ANALYSIS_REASON
            FROM STOCK_SENTIMENT
        """)
        return [{
            "ticker": r[0], "date": r[1].date().isoformat(), "name": r[2],
            "open": float(r[3]) if r[3] is not None else None,
            "close": float(r[4]) if r[4] is not None else None,
            "volume": int(r[5]) if r[5] is not None else None,
            "sentiment": r[6], "reason": r[7],
        } for r in cursor]
    finally:
        cursor.close()
        connection.close()


def tickers_needing_meta(session, tickers):
    cutoff = (date.today() - timedelta(days=META_REFRESH_DAYS)).isoformat()
    fresh = session.run(
        "MATCH (s:Security) WHERE s.ticker IN $tickers AND s.meta_updated >= $cutoff RETURN s.ticker AS t",
        tickers=list(tickers), cutoff=cutoff
    )
    fresh_set = {r["t"] for r in fresh}
    return [t for t in tickers if t not in fresh_set]


def sync_meta(session, ticker):
    stock = yf.Ticker(ticker)
    info = stock.info
    params = {
        "ticker": ticker,
        "name": info.get("longName") or info.get("shortName") or ticker,
        "currency": info.get("currency"),
        "today": date.today().isoformat(),
    }

    if info.get("quoteType") == "ETF":
        session.run("""
            MERGE (s:Security {ticker: $ticker})
            SET s:ETF, s.name = $name, s.currency = $currency, s.meta_updated = $today
        """, **params)
        if info.get("category"):
            session.run("""
                MATCH (s:Security {ticker: $ticker})
                MERGE (c:Category {name: $category})
                MERGE (s)-[:IN_CATEGORY]->(c)
            """, ticker=ticker, category=info["category"])

        try:
            holdings = stock.funds_data.top_holdings
        except Exception:
            holdings = None
        if holdings is not None and not holdings.empty:
            rows = [{"ticker": sym, "name": h["Name"], "weight": float(h["Holding Percent"])}
                    for sym, h in holdings.iterrows()]
            # 보유 비중은 바뀌므로 기존 HOLDS를 지우고 다시 그린다
            session.run("MATCH (:Security {ticker: $ticker})-[r:HOLDS]->() DELETE r", ticker=ticker)
            session.run("""
                MATCH (etf:Security {ticker: $ticker})
                UNWIND $rows AS row
                MERGE (h:Security {ticker: row.ticker})
                ON CREATE SET h.name = row.name
                MERGE (etf)-[r:HOLDS]->(h)
                SET r.weight = row.weight
            """, ticker=ticker, rows=rows)
    else:
        session.run("""
            MERGE (s:Security {ticker: $ticker})
            SET s:Stock, s.name = $name, s.currency = $currency, s.meta_updated = $today
        """, **params)
        sector, industry = INDUSTRY_OVERRIDES.get(ticker, (info.get("sector"), info.get("industry")))
        if sector and industry:
            # 업종이 바뀌었을 수 있으므로 기존 IN_INDUSTRY를 지우고 다시 연결한다
            session.run("MATCH (:Security {ticker: $ticker})-[r:IN_INDUSTRY]->() DELETE r", ticker=ticker)
            session.run("""
                MATCH (s:Security {ticker: $ticker})
                MERGE (sec:Sector {name: $sector})
                MERGE (ind:Industry {name: $industry})
                MERGE (ind)-[:PART_OF]->(sec)
                MERGE (s)-[:IN_INDUSTRY]->(ind)
            """, ticker=ticker, sector=sector, industry=industry)


def main():
    print("\n🧠 Neo4j 지식 그래프 동기화 시작")
    analyses = load_analyses()

    driver = neo4j_driver()
    try:
        driver.verify_connectivity()
        with driver.session() as session:
            for stmt in CONSTRAINTS:
                session.run(stmt)

            # 1) 종목 메타데이터 (섹터/산업/ETF 카테고리/보유종목)
            tickers = list(dict.fromkeys(
                [a["ticker"] for a in analyses] + STOCK_CANDIDATES + ETF_LIST + MY_PORTFOLIO
            ))
            stale = tickers_needing_meta(session, tickers)
            print(f" └─ 🏷️ 메타데이터 갱신 대상 {len(stale)}개 / 전체 {len(tickers)}개")
            for ticker in stale:
                try:
                    sync_meta(session, ticker)
                except Exception as e:
                    print(f"    ⚠️ [{ticker}] 메타데이터 실패: {e}")

            # 2) 내 포트폴리오
            session.run("""
                MERGE (p:Portfolio {name: $name})
                WITH p
                OPTIONAL MATCH (p)-[old:WATCHES]->()
                DELETE old
                WITH DISTINCT p
                UNWIND $tickers AS t
                MERGE (s:Security {ticker: t})
                MERGE (p)-[:WATCHES]->(s)
            """, name=PORTFOLIO_NAME, tickers=MY_PORTFOLIO)

            # 3) 일별 AI 분석 결과
            session.run("""
                UNWIND $rows AS row
                MERGE (s:Security {ticker: row.ticker})
                ON CREATE SET s.name = row.name
                MERGE (a:DailyAnalysis {id: row.ticker + '|' + row.date})
                SET a.ticker = row.ticker, a.date = date(row.date), a.sentiment = row.sentiment,
                    a.reason = row.reason, a.open = row.open, a.close = row.close, a.volume = row.volume
                MERGE (s)-[:HAS_ANALYSIS]->(a)
            """, rows=analyses)
            print(f" └─ 📊 일별 분석 {len(analyses)}건 반영")

            counts = session.run("MATCH (n) RETURN count(n) AS nodes").single()["nodes"]
            rels = session.run("MATCH ()-[r]->() RETURN count(r) AS rels").single()["rels"]
            print(f" └─ ✅ 동기화 완료: 노드 {counts}개, 관계 {rels}개 (http://localhost:7474 에서 확인)")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
