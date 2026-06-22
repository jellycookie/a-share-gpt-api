from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import requests
from datetime import datetime
from typing import Any, Dict, List


app = FastAPI(
    title="A Share Data API",
    description="A lightweight A-share and ETF data API using direct public HTTP sources.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://gu.qq.com/",
}


def normalize_code(symbol: str) -> str:
    """
    Convert 512890 to sh512890, 159915 to sz159915, etc.
    """
    code = symbol.strip().lower()

    if code.startswith(("sh", "sz", "bj")):
        return code

    if len(code) != 6 or not code.isdigit():
        raise HTTPException(status_code=400, detail="symbol must be a 6-digit A-share or ETF code")

    # Shanghai stocks / funds / ETFs
    if code.startswith(("5", "6", "9")):
        return "sh" + code

    # Shenzhen stocks / funds / ETFs
    if code.startswith(("0", "1", "2", "3")):
        return "sz" + code

    # Beijing Stock Exchange, basic fallback
    if code.startswith(("4", "8")):
        return "bj" + code

    raise HTTPException(status_code=400, detail="cannot infer market prefix for symbol")


def yyyymmdd_to_yyyy_mm_dd(value: str) -> str:
    """
    Convert 20240101 to 2024-01-01.
    """
    value = value.strip()
    try:
        return datetime.strptime(value, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be YYYYMMDD or YYYY-MM-DD")


def safe_float(value: Any):
    try:
        if value in ("", None, "-"):
            return None
        return float(value)
    except Exception:
        return None


def fetch_tencent_quote(symbol: str) -> Dict[str, Any]:
    """
    Tencent quote endpoint.
    Example: https://qt.gtimg.cn/q=sh512890
    """
    market_symbol = normalize_code(symbol)
    url = f"https://qt.gtimg.cn/q={market_symbol}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        text = resp.content.decode("gbk", errors="ignore")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"quote upstream error: {e}")

    if "~" not in text:
        raise HTTPException(status_code=404, detail=f"no quote data found for {symbol}")

    raw = text.split('"')[1]
    fields = raw.split("~")

    # Tencent fields are not perfectly documented. Keep raw_fields for debugging.
    return {
        "symbol": symbol,
        "market_symbol": market_symbol,
        "name": fields[1] if len(fields) > 1 else None,
        "code": fields[2] if len(fields) > 2 else None,
        "price": safe_float(fields[3]) if len(fields) > 3 else None,
        "previous_close": safe_float(fields[4]) if len(fields) > 4 else None,
        "open": safe_float(fields[5]) if len(fields) > 5 else None,
        "volume": safe_float(fields[6]) if len(fields) > 6 else None,
        "raw_fields": fields,
    }


def fetch_tencent_kline(
    symbol: str,
    start_date: str,
    end_date: str,
    limit: int,
    adjust: str,
) -> List[Dict[str, Any]]:
    """
    Tencent kline endpoint.
    Example:
    https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh512890,day,2024-01-01,2026-06-22,100,qfq
    """
    market_symbol = normalize_code(symbol)
    start = yyyymmdd_to_yyyy_mm_dd(start_date)
    end = yyyymmdd_to_yyyy_mm_dd(end_date)

    if adjust not in ("", "none", "qfq", "hfq"):
        raise HTTPException(status_code=400, detail="adjust must be one of: none, qfq, hfq")

    adjust_param = "" if adjust in ("", "none") else adjust

    urls = []

    if adjust_param:
        urls.append(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            f"?param={market_symbol},day,{start},{end},{limit},{adjust_param}"
        )

    urls.append(
        "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
        f"?param={market_symbol},day,{start},{end},{limit}"
    )

    last_error = None

    for url in urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            last_error = str(e)
            continue

        try:
            stock_data = data.get("data", {}).get(market_symbol, {})

            possible_keys = []
            if adjust_param == "qfq":
                possible_keys.append("qfqday")
            if adjust_param == "hfq":
                possible_keys.append("hfqday")
            possible_keys.append("day")

            rows = None
            used_key = None

            for key in possible_keys:
                if key in stock_data and stock_data[key]:
                    rows = stock_data[key]
                    used_key = key
                    break

            if not rows:
                last_error = f"no kline rows in response, keys={list(stock_data.keys())}"
                continue

            result = []

            for row in rows[-limit:]:
                # Common Tencent format:
                # [date, open, close, high, low, volume, ...]
                item = {
                    "date": row[0] if len(row) > 0 else None,
                    "open": safe_float(row[1]) if len(row) > 1 else None,
                    "close": safe_float(row[2]) if len(row) > 2 else None,
                    "high": safe_float(row[3]) if len(row) > 3 else None,
                    "low": safe_float(row[4]) if len(row) > 4 else None,
                    "volume": safe_float(row[5]) if len(row) > 5 else None,
                    "source_key": used_key,
                    "raw": row,
                }
                result.append(item)

            return result

        except Exception as e:
            last_error = str(e)
            continue

    raise HTTPException(status_code=502, detail=f"kline upstream error: {last_error}")


@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "A Share Data API is running.",
        "version": "2.0.0",
        "endpoints": [
            "/health",
            "/quote?symbol=512890",
            "/etf/history?symbol=512890&start_date=20240101&end_date=20260622&limit=20",
            "/stock/history?symbol=600519&start_date=20240101&end_date=20260622&limit=20",
        ],
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/quote")
def get_quote(
    symbol: str = Query(..., description="6-digit A-share or ETF code, for example 512890"),
):
    return fetch_tencent_quote(symbol)


@app.get("/etf/history")
def get_etf_history(
    symbol: str = Query(..., description="ETF code, for example 512890"),
    start_date: str = Query("20250101", description="Start date, YYYYMMDD"),
    end_date: str = Query("20251231", description="End date, YYYYMMDD"),
    limit: int = Query(60, ge=1, le=300, description="Maximum number of rows"),
    adjust: str = Query("none", description="none, qfq, or hfq"),
):
    rows = fetch_tencent_kline(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        adjust=adjust,
    )

    return {
        "symbol": symbol,
        "market_symbol": normalize_code(symbol),
        "start_date": start_date,
        "end_date": end_date,
        "limit": limit,
        "adjust": adjust,
        "rows": len(rows),
        "source": "tencent",
        "data": rows,
    }


@app.get("/stock/history")
def get_stock_history(
    symbol: str = Query(..., description="Stock code, for example 600519"),
    start_date: str = Query("20250101", description="Start date, YYYYMMDD"),
    end_date: str = Query("20251231", description="End date, YYYYMMDD"),
    limit: int = Query(60, ge=1, le=300, description="Maximum number of rows"),
    adjust: str = Query("none", description="none, qfq, or hfq"),
):
    rows = fetch_tencent_kline(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        adjust=adjust,
    )

    return {
        "symbol": symbol,
        "market_symbol": normalize_code(symbol),
        "start_date": start_date,
        "end_date": end_date,
        "limit": limit,
        "adjust": adjust,
        "rows": len(rows),
        "source": "tencent",
        "data": rows,
    }
