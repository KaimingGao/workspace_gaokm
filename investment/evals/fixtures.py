"""Golden eval 离线 fixture（行情/基本面样本）。"""

MOUTAI_QUOTE = {
    "success": True,
    "stock_code": "600519",
    "stock_name": "贵州茅台",
    "market": "CN",
    "price": "1500.00元",
    "change": "+1.20%",
    "price_raw": 1500.0,
    "change_raw": 1.2,
    "volume": "1000手",
    "currency": "CNY",
}

WULIANGYE_QUOTE = {
    "success": True,
    "stock_code": "000858",
    "stock_name": "五粮液",
    "market": "CN",
    "price": "120.00元",
    "change": "+0.80%",
    "price_raw": 120.0,
    "change_raw": 0.8,
    "volume": "5000手",
    "currency": "CNY",
}

CMB_QUOTE = {
    "success": True,
    "stock_code": "600036",
    "stock_name": "招商银行",
    "market": "CN",
    "price": "35.00元",
    "change": "+0.50%",
    "price_raw": 35.0,
    "change_raw": 0.5,
    "volume": "8000手",
    "currency": "CNY",
}

KUAISHOU_QUOTE = {
    "success": True,
    "stock_code": "01024",
    "stock_name": "快手-W",
    "market": "HK",
    "price": "HK$43.28",
    "change": "-7.76%",
    "price_raw": 43.28,
    "change_raw": -7.76,
    "currency": "HKD",
}

CATL_QUOTE = {
    "success": True,
    "stock_code": "300750",
    "stock_name": "宁德时代",
    "market": "CN",
    "price": "180.00元",
    "change": "+2.00%",
    "price_raw": 180.0,
    "change_raw": 2.0,
    "currency": "CNY",
}

POSITION_HOLDINGS = [
    {"stock_code": "600519", "stock_name": "贵州茅台", "shares": 100, "cost": 1400},
    {"stock_code": "000858", "stock_name": "五粮液", "shares": 200, "cost": 120},
]

CN_SPOT_MOUTAI = {
    "代码": "600519",
    "名称": "贵州茅台",
    "最新价": 1500,
    "涨跌幅": 1.0,
    "市盈率-动态": 22.0,
    "市净率": 7.5,
}

CN_FIN_MOUTAI = {
    "roe": 28.0,
    "revenue_growth": 12.0,
    "profit_growth": 10.0,
    "as_of": "2024-12-31",
    "source": "mock",
}
