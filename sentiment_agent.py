import re
import sys
import time
import warnings
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from universe import STOCK_CANDIDATES, ETF_LIST, MY_PORTFOLIO
from claude_cli import run_claude, UsageLimitReached
from config import oracle_connect

warnings.filterwarnings("ignore")

# 1. 설정 (DB 접속 정보는 config.py / .env)
CLAUDE_MODEL = "haiku"   # 짧은 JSON 분석이라 Haiku로 충분 (구독 사용량도 가장 적게 씀)
INITIAL_DAYS = 3         # DB에 처음 들어오는 종목: 최근 3영업일
REPAIR_DAYS = 14         # 최근 14일 안에 빠진 날짜가 있으면 매번 다시 채움
AVG_VOLUME_DAYS = 20     # 거래량 비교 기준: 직전 20거래일 평균
CJK_PATTERN = re.compile(r"[぀-ヿ一-鿿]")  # 한자/일본어 문자 (Haiku가 가끔 섞어 씀)

# 2. Claude (구독) 호출 설정 - Claude Code CLI를 헤드리스(-p) 모드로 실행
SYSTEM_PROMPT = (
    "당신은 월스트리트의 수석 AI 애널리스트입니다. "
    "주어진 회사/ETF 정보와 당일 주가 데이터를 근거로 시장 심리를 판단합니다. "
    "모든 답변은 한국어(한글)로 작성하고, 한자·중국어·일본어 문자는 절대 쓰지 않습니다."
)
SENTIMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "market_sentiment": {"type": "string", "enum": ["긍정", "중립", "부정"]},
        "analysis_reason": {"type": "string", "description": "당일 주가/거래량 움직임과 펀더멘털을 연결한 분석 이유 (2문장)"}
    },
    "required": ["market_sentiment", "analysis_reason"],
    "additionalProperties": False
}

PROMPT_TEMPLATE = """회사/ETF: {company} ({ticker})
요약: {summary}

[일일 주가 데이터 - 대상 일자: {target_date}, 통화: {currency}]
- 시가(Open): {open_price}
- 종가(Close): {close_price}
- 전일 대비: {change}
- 거래량(Volume): {volume}
- 직전 20거래일 평균 거래량: {avg_volume}

위 정보와 당일 주가 데이터를 종합하여 시장 심리(긍정/중립/부정)와 그 이유(2문장)를 판단해 주세요."""


def analyze_with_claude(prompt_text):
    return run_claude(prompt_text, SYSTEM_PROMPT, model=CLAUDE_MODEL, json_schema=SENTIMENT_SCHEMA)


# ==========================================
# 🎯 [1부] 개별 주식: Top 30 후보군 중 거래량 Top 10 추출
# ==========================================
def get_top_10_stocks():
    print("🔍 [1부] 개별 주식 인기 30위 중 거래량 Top 10을 분석합니다...")
    data = yf.download(STOCK_CANDIDATES, period="1d", progress=False)
    volumes = data['Volume'].iloc[-1]
    top_10_tickers = volumes.sort_values(ascending=False).head(10).index.tolist()

    print("\n🏆 [1부: 개별 주식] 오늘의 거래량 Top 10 🏆")
    for i, ticker in enumerate(top_10_tickers, 1):
        company_name = yf.Ticker(ticker).info.get('shortName', ticker)
        print(f" {i}위: {ticker} ({company_name})")
    print("==========================================\n")
    return top_10_tickers

# ==========================================
# 🎯 [2부] ETF: 시장의 흐름을 읽는 고정 Top 10 ETF
# ==========================================
def get_top_10_etfs():
    print("🔍 [2부] 시장 전체 흐름을 위한 Top 10 ETF를 분석합니다...")
    print("\n📈 [2부: ETF] 고정 분석 대상 10종목 📈")
    for i, ticker in enumerate(ETF_LIST, 1):
        company_name = yf.Ticker(ticker).info.get('shortName', ticker)
        print(f" {i}. {ticker} ({company_name})")
    print("==========================================\n")
    return ETF_LIST

# ==========================================
# 🎯 [3부] 사용자 지정: 핵심 관심 포트폴리오
# ==========================================
def get_my_portfolio():
    print("🔍 [3부] 나의 핵심 관심 포트폴리오를 분석합니다...")
    print(f"\n💎 [3부: 내 포트폴리오] 관심 종목 {len(MY_PORTFOLIO)}개 💎")
    for i, ticker in enumerate(MY_PORTFOLIO, 1):
        company_name = yf.Ticker(ticker).info.get('shortName', ticker)
        print(f" {i}. {ticker} ({company_name})")
    print("==========================================\n")
    return MY_PORTFOLIO


def drop_unfinished_bar(hist, ticker):
    # 장중에 실행하면 오늘 봉은 아직 확정 전이므로 제외 (다음 실행 때 확정값으로 수집)
    if hist.empty:
        return hist
    now = pd.Timestamp.now(tz=hist.index.tz)
    close_time = (15, 40) if ticker.endswith((".KS", ".KQ")) else (16, 15)
    if hist.index[-1].date() == now.date() and (now.hour, now.minute) < close_time:
        return hist.iloc[:-1]
    return hist


def add_indicators(hist):
    # 전일 대비 등락률, 직전 N거래일 평균 거래량(당일 제외)
    hist["Change"] = hist["Close"].pct_change() * 100
    hist["AvgVolume"] = hist["Volume"].rolling(AVG_VOLUME_DAYS).mean().shift(1)
    return hist


def find_missing_rows(ticker, stock, cursor):
    """DB에 없는 확정 거래일만 골라서 돌려준다 (중간에 실패해서 비어 있는 날짜 포함)."""
    cursor.execute("SELECT MIN(TARGET_DATE), MAX(TARGET_DATE) FROM STOCK_SENTIMENT WHERE TICKER = :1", [ticker])
    first_date, last_date = cursor.fetchone()

    if last_date is None:
        # 처음 수집하는 종목: 넉넉히 받아서 최근 N영업일만 사용 (주말/휴일 고려)
        hist = add_indicators(drop_unfinished_bar(stock.history(period="3mo"), ticker))
        print(f" └─ 📅 DB 데이터 없음. 최근 {INITIAL_DAYS}영업일 수집!")
        return hist.tail(INITIAL_DAYS)

    # 마지막 저장일 이후 + 최근 REPAIR_DAYS일 안의 구멍까지 같이 확인 (첫 저장일 이전은 수집 대상 아님)
    check_from = min(last_date + timedelta(days=1), datetime.now() - timedelta(days=REPAIR_DAYS))
    check_from = max(check_from, first_date)
    # 전일 대비/20일 평균 거래량 계산용으로 40일 더 앞에서부터 받아온다
    hist = stock.history(start=(check_from - timedelta(days=40)).strftime('%Y-%m-%d'))
    hist = add_indicators(drop_unfinished_bar(hist, ticker))

    cursor.execute(
        "SELECT TARGET_DATE FROM STOCK_SENTIMENT WHERE TICKER = :1 AND TARGET_DATE >= :2",
        [ticker, check_from.replace(hour=0, minute=0, second=0, microsecond=0)]
    )
    saved_dates = {row[0].date() for row in cursor}
    mask = [(d.date() >= check_from.date()) and (d.date() not in saved_dates) for d in hist.index]
    return hist[mask]


# ==========================================
# 🎯 공통 데이터 수집 및 DB 저장 로직
# ==========================================
def process_tickers(ticker_list, cursor, connection, done):
    for ticker in ticker_list:
        if ticker in done:
            continue  # 1~3부에 중복으로 나오는 종목은 한 번만 처리
        done.add(ticker)
        print(f"🚀 [{ticker}] 수집 및 AI 분석 시작")

        stock = yf.Ticker(ticker)
        try:
            info = stock.info
        except Exception:
            info = {}
        company_name = info.get('longName', ticker)
        summary = (info.get('longBusinessSummary') or info.get('description') or '')[:500]
        currency = info.get('currency', 'USD')

        rows = find_missing_rows(ticker, stock, cursor)
        if rows.empty:
            print(f" └─ ✅ 추가할 밀린 데이터가 없습니다.\n")
            continue
        print(f" └─ 📅 빠진 거래일 {len(rows)}개: {rows.index[0].date()} ~ {rows.index[-1].date()}")

        for date_index, row in rows.iterrows():
            target_date_str = date_index.strftime('%Y-%m-%d')
            open_price = round(row['Open'], 2)
            close_price = round(row['Close'], 2)
            volume = int(row['Volume'])
            change = "알 수 없음" if pd.isna(row['Change']) else f"{row['Change']:+.2f}%"
            if pd.isna(row['AvgVolume']) or row['AvgVolume'] == 0:
                avg_volume = "알 수 없음"
            else:
                avg_volume = f"{int(row['AvgVolume'])} (당일 거래량은 평균의 {volume / row['AvgVolume']:.2f}배)"

            print(f"    🤖 [{target_date_str}] AI 분석 중...")
            prompt_text = PROMPT_TEMPLATE.format(
                company=company_name, ticker=ticker, summary=summary, target_date=target_date_str,
                currency=currency, open_price=open_price, close_price=close_price,
                change=change, volume=volume, avg_volume=avg_volume
            )

            # 일시적인 오류(타임아웃 등)는 한 번 더 시도, 그래도 실패하면 이 날짜는 건너뜀
            # (건너뛴 날짜는 REPAIR_DAYS 안이면 다음 실행 때 자동으로 다시 채워짐)
            for attempt in range(2):
                try:
                    parsed_data = analyze_with_claude(prompt_text)
                    if CJK_PATTERN.search(parsed_data["analysis_reason"]):
                        raise RuntimeError("분석 이유에 한자/일본어 문자가 섞여 있습니다.")
                    cursor.execute("""
                        INSERT INTO STOCK_SENTIMENT
                        (TICKER, TARGET_DATE, COMPANY_NAME, OPEN_PRICE, CLOSE_PRICE, TRADING_VOLUME, MARKET_SENTIMENT, ANALYSIS_REASON)
                        VALUES (:1, TO_DATE(:2, 'YYYY-MM-DD'), :3, :4, :5, :6, :7, :8)
                    """, (
                        ticker, target_date_str, company_name, open_price, close_price, volume,
                        parsed_data["market_sentiment"], parsed_data["analysis_reason"]
                    ))
                    connection.commit()
                    print(f"    ✅ DB 적재 완료! ({parsed_data['market_sentiment']})")
                    break
                except UsageLimitReached:
                    raise
                except Exception as e:
                    if attempt == 0:
                        print(f"    ⚠️ 실패, 5초 후 한 번 더 시도합니다: {e}")
                        time.sleep(5)
                    else:
                        print(f"    🚨 분석/적재 실패 (다음 실행 때 다시 채웁니다): {e}")
        print()

# ==========================================
# 🎯 메인 실행부
# ==========================================
def main():
    connection = oracle_connect()
    cursor = connection.cursor()
    done = set()
    try:
        if len(sys.argv) > 1:
            # 테스트용: python sentiment_agent.py AAPL 005930.KS  → 지정한 종목만 처리
            process_tickers(sys.argv[1:], cursor, connection, done)
        else:
            # [1부] 개별 주식 Top 10 → [2부] 고정 ETF Top 10 → [3부] 나의 관심 포트폴리오
            process_tickers(get_top_10_stocks(), cursor, connection, done)
            process_tickers(get_top_10_etfs(), cursor, connection, done)
            process_tickers(get_my_portfolio(), cursor, connection, done)
    except UsageLimitReached as e:
        print(f"\n⛔ Claude 사용 한도에 도달해서 배치를 멈춥니다. 한도가 풀린 뒤 다시 실행하면 이어서 채웁니다.\n   ({str(e)[:200]})")
    finally:
        cursor.close()
        connection.close()

    # Oracle에 쌓인 결과를 지식 그래프(Neo4j)에도 반영 - Neo4j가 꺼져 있어도 배치는 정상 종료
    try:
        import graph_sync
        graph_sync.main()
    except Exception as e:
        print(f"\nℹ️ Neo4j 동기화는 건너뜁니다: {e}")

    print("\n🏁 모든 주식, ETF 및 포트폴리오 배치 작업이 종료되었습니다.")


if __name__ == "__main__":
    main()
