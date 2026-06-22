from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import akshare as ak
import pandas as pd
import math
from typing import Any

app = FastAPI(
    title="A Share ETF Data API",
    description="A simple API for ChatGPT Actions to fetch China A-share ETF data from AKShare.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def clean_value(value: Any):
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    return value


def dataframe_to_records(df: pd.DataFrame, max_rows: int = 500):
    df = df.copy()
    df = df.tail(max_rows)

    records = []
    for _, row in df.iterrows():
        item = {}
        for col in df.columns:
            item[str(col)] = clean_value(row[col])
        records.append(item)
    return records


@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "A Share ETF Data API is running.",
        "endpoints": [
            "/etf/history?symbol=512890&start_date=20240101&end_date=20260622",
            "/etf/spot",
            "/stock/spot",
        ],
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/etf/history")
def get_etf_history(
    symbol: str = Query(..., description="ETF code, for example 512890"),
    start_date: str = Query("20240101", description="Start date in YYYYMMDD format"),
    end_date: str = Query("20261231", description="End date in YYYYMMDD format"),
    limit: int = Query(300, ge=1, le=1000, description="Maximum number of rows to return"),
):
    try:
        df = ak.fund_etf_hist_em(
            symbol=symbol,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="",
        )

        if df is None or df.empty:
            raise HTTPException(status_code=404, detail="No ETF history data found.")

        return {
            "symbol": symbol,
            "start_date": start_date,
            "end_date": end_date,
            "rows": len(df),
            "returned_rows": min(len(df), limit),
            "data": dataframe_to_records(df, max_rows=limit),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/etf/spot")
def get_etf_spot(
    limit: int = Query(200, ge=1, le=1000, description="Maximum number of rows to return")
):
    try:
        df = ak.fund_etf_spot_em()

        if df is None or df.empty:
            raise HTTPException(status_code=404, detail="No ETF spot data found.")

        return {
            "rows": len(df),
            "returned_rows": min(len(df), limit),
            "data": dataframe_to_records(df, max_rows=limit),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stock/spot")
def get_stock_spot(
    limit: int = Query(200, ge=1, le=1000, description="Maximum number of rows to return")
):
    try:
        df = ak.stock_zh_a_spot_em()

        if df is None or df.empty:
            raise HTTPException(status_code=404, detail="No A-share spot data found.")

        return {
            "rows": len(df),
            "returned_rows": min(len(df), limit),
            "data": dataframe_to_records(df, max_rows=limit),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
