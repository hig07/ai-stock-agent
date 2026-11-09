// http://localhost:7474 (neo4j / .env의 NEO4J_PASSWORD) 에서 한 줄씩 실행해 보세요.

// 1. 전체 구조 한눈에 보기 (온톨로지 스키마)
CALL db.schema.visualization();

// 2. 내 포트폴리오와 각 종목의 산업/섹터
MATCH (p:Portfolio)-[:WATCHES]->(s:Security)
OPTIONAL MATCH (s)-[:IN_INDUSTRY]->(i:Industry)-[:PART_OF]->(sec:Sector)
RETURN p, s, i, sec;

// 3. 내 포트폴리오 종목을 담고 있는 ETF와 비중 (ETF끼리 겹치는 노출 확인)
MATCH (:Portfolio)-[:WATCHES]->(s:Security)<-[h:HOLDS]-(e:ETF)
RETURN s.ticker AS 종목, collect(e.ticker + ' ' + toString(round(h.weight * 100, 1)) + '%') AS 담고있는ETF
ORDER BY 종목;

// 4. 종목별 최근 AI 심리 분석
MATCH (s:Security)-[:HAS_ANALYSIS]->(a:DailyAnalysis)
WITH s, a ORDER BY a.date DESC
WITH s, collect(a)[0] AS latest
RETURN s.ticker AS 종목, latest.date AS 날짜, latest.sentiment AS 심리, latest.reason AS 이유
ORDER BY 종목;

// 5. 같은 섹터 안에서 최근 심리가 '부정'인 종목
MATCH (sec:Sector)<-[:PART_OF]-(:Industry)<-[:IN_INDUSTRY]-(s:Stock)-[:HAS_ANALYSIS]->(a:DailyAnalysis {sentiment: '부정'})
RETURN sec.name AS 섹터, s.ticker AS 종목, a.date AS 날짜, a.reason AS 이유
ORDER BY a.date DESC;
