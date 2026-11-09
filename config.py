import os
from pathlib import Path

# ==========================================
# ⚙️ DB 접속 공용 모듈
#   프로젝트 폴더의 .env 파일(없으면 OS 환경변수)에서 접속 정보를 읽는다.
#   .env는 git에 올라가지 않는다. 처음이면 .env.example을 복사해서 만들 것.
# ==========================================

try:
    from dotenv import load_dotenv
    # MCP 서버처럼 다른 폴더에서 실행돼도 찾을 수 있게 이 파일 기준 경로를 쓴다
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass  # python-dotenv가 없으면 OS 환경변수만 사용


def _required(name):
    value = os.environ.get(name)
    if not value:
        # print 대신 예외: MCP 서버(stdio)에서는 stdout 출력이 프로토콜을 깨뜨림
        raise RuntimeError(f"환경변수 {name}가 없습니다. .env.example을 복사해 .env를 만들고 값을 채워 주세요.")
    return value


ORACLE_USER = os.environ.get("ORACLE_USER", "system")
ORACLE_DSN = os.environ.get("ORACLE_DSN", "localhost:1521/FREEPDB1")

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")


def oracle_connect():
    import oracledb
    return oracledb.connect(user=ORACLE_USER, password=_required("ORACLE_DB_PASSWORD"), dsn=ORACLE_DSN)


def neo4j_driver():
    # Neo4j만 쓰는 GraphRAG/MCP는 Oracle 비밀번호 없이도 동작하도록 접속 시점에 확인
    from neo4j import GraphDatabase
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, _required("NEO4J_PASSWORD")))
