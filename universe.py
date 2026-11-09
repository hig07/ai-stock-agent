# ==========================================
# 🎯 수집 대상 종목 목록 (sentiment_agent.py, graph_sync.py 공용)
# ==========================================

# [1부] 개별 주식 인기 30개 후보군 (이 중 거래량 Top 10만 분석)
STOCK_CANDIDATES = [
    "AAPL", "TSLA", "NVDA", "AMD", "MSFT", "AMZN", "META", "GOOGL", "INTC", "PLTR",
    "SOFI", "BAC", "F", "SMCI", "MU", "T", "VZ", "CSCO", "NFLX", "DIS",
    "CRM", "AVGO", "QCOM", "TXN", "JPM", "V", "MA", "WMT", "JNJ", "PG"
]

# [2부] 시장 흐름을 보는 고정 ETF 10종
ETF_LIST = ["VOO", "SPY", "QQQ", "SCHD", "VTI", "SOXX", "XLE", "XLF", "XLV", "ARKK"]

# [3부] 나의 핵심 관심 포트폴리오 (한국 주식은 yfinance 규격에 맞게 '.KS')
#   아래는 예시. 실제 목록은 같은 폴더에 my_portfolio.py를 만들어
#   MY_PORTFOLIO = [...] 로 적으면 그 값을 쓴다 (my_portfolio.py는 git에 올라가지 않음)
MY_PORTFOLIO = ["VOO", "QQQ", "AAPL", "NVDA", "005930.KS"]
try:
    from my_portfolio import MY_PORTFOLIO
except ImportError:
    pass

# yfinance 업종 분류가 실제 사업과 다른 종목의 (섹터, 산업) 보정 - graph_sync.py에서 사용
#   삼성전자는 yfinance에서 'Consumer Electronics'로 나오지만 메모리 반도체가 핵심 사업
INDUSTRY_OVERRIDES = {
    "005930.KS": ("Technology", "Semiconductors"),
}
