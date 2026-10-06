"""交易成本精确计算模块。

费率权威源：`core.backtest.cost_port`（CostPort）。
勿在此硬编码第二套印花税/佣金。
"""

from typing import Dict, Optional, Tuple

from core.backtest.cost_port import factor_cost_defaults

DEFAULT_COST_CONFIG = factor_cost_defaults()


class TransactionCostCalculator:
    """交易成本计算器。"""

    def __init__(self, config: Optional[Dict] = None):
        self.config = {**DEFAULT_COST_CONFIG, **(config or {})}

    def calculate_buy_cost(
        self,
        price: float,
        shares: int,
        stock_code: str = "",
    ) -> Tuple[float, Dict[str, float]]:
        """
        计算买入交易的总成本。
        
        Args:
            price: 成交价格
            shares: 成交数量
            stock_code: 股票代码（用于判断沪/深市）
        
        Returns:
            (总成本, 成本明细)
        """
        gross_amount = price * shares

        # 1. 滑点成本
        slippage = 0.0
        if self.config["use_slippage"]:
            # 买入时价格上浮
            slippage = gross_amount * self.config["slippage_pct"]

        # 2. 佣金
        commission = max(
            gross_amount * self.config["commission_rate"],
            self.config["commission_min"]
        )

        # 3. 过户费（仅沪市）
        transfer_fee = 0.0
        if stock_code.startswith("6") or stock_code.startswith("68"):
            transfer_fee = gross_amount * self.config["transfer_fee_rate"]

        # 汇总
        total_cost = slippage + commission + transfer_fee
        actual_cost = gross_amount + total_cost

        details = {
            "gross_amount": round(gross_amount, 2),
            "slippage": round(slippage, 2),
            "commission": round(commission, 2),
            "transfer_fee": round(transfer_fee, 2),
            "stamp_tax": 0.0,  # 买入无印花税
            "total_cost": round(total_cost, 2),
            "cost_rate": round(total_cost / gross_amount * 100, 4),
        }

        return actual_cost, details

    def calculate_sell_cost(
        self,
        price: float,
        shares: int,
        stock_code: str = "",
    ) -> Tuple[float, Dict[str, float]]:
        """
        计算卖出交易的总成本。
        
        Args:
            price: 成交价格
            shares: 成交数量
            stock_code: 股票代码
        
        Returns:
            (实际到手金额, 成本明细)
        """
        gross_amount = price * shares

        # 1. 滑点成本
        slippage = 0.0
        if self.config["use_slippage"]:
            # 卖出时价格下浮
            slippage = gross_amount * self.config["slippage_pct"]

        # 2. 佣金
        commission = max(
            gross_amount * self.config["commission_rate"],
            self.config["commission_min"]
        )

        # 3. 印花税（仅卖出）
        stamp_tax = gross_amount * self.config["stamp_tax_rate"]

        # 4. 过户费（仅沪市）
        transfer_fee = 0.0
        if stock_code.startswith("6") or stock_code.startswith("68"):
            transfer_fee = gross_amount * self.config["transfer_fee_rate"]

        # 汇总
        total_cost = slippage + commission + stamp_tax + transfer_fee
        net_amount = gross_amount - total_cost

        details = {
            "gross_amount": round(gross_amount, 2),
            "slippage": round(slippage, 2),
            "commission": round(commission, 2),
            "transfer_fee": round(transfer_fee, 2),
            "stamp_tax": round(stamp_tax, 2),
            "total_cost": round(total_cost, 2),
            "cost_rate": round(total_cost / gross_amount * 100, 4),
        }

        return net_amount, details

    def calculate_round_trip_cost(
        self,
        buy_price: float,
        sell_price: float,
        shares: int,
        stock_code: str = "",
    ) -> Dict[str, float]:
        """
        计算一次完整交易（买+卖）的总成本。
        
        Args:
            buy_price: 买入价格
            sell_price: 卖出价格
            shares: 成交数量
            stock_code: 股票代码
        
        Returns:
            成本明细
        """
        buy_cost, buy_details = self.calculate_buy_cost(buy_price, shares, stock_code)
        sell_proceeds, sell_details = self.calculate_sell_cost(sell_price, shares, stock_code)

        total_cost = buy_details["total_cost"] + sell_details["total_cost"]
        gross_profit = sell_price * shares - buy_price * shares
        net_profit = sell_proceeds - buy_cost

        return {
            "buy_cost": round(buy_cost, 2),
            "sell_proceeds": round(sell_proceeds, 2),
            "total_transaction_cost": round(total_cost, 2),
            "cost_rate": round(total_cost / (buy_price * shares) * 100, 4),
            "gross_profit": round(gross_profit, 2),
            "net_profit": round(net_profit, 2),
            "profit_loss_pct": round((net_profit / (buy_price * shares)) * 100, 2),
        }


def calculate_holding_period_cost(
    holding_days: int,
    annual_cost_rate: float = 0.02,
) -> float:
    """
    计算持有期间的资金成本（机会成本/利息成本）。
    
    Args:
        holding_days: 持有天数
        annual_cost_rate: 年化成本率（默认2%）
    
    Returns:
        持有期间的成本比例
    """
    daily_rate = annual_cost_rate / 252  # 每年252个交易日
    return round(daily_rate * holding_days * 100, 4)


def estimate_strategy_costs(
    num_trades: int,
    avg_trade_value: float,
    holding_days: int = 3,
) -> Dict[str, float]:
    """
    估算策略的年度交易成本。
    
    Args:
        num_trades: 每年交易次数
        avg_trade_value: 平均交易金额
        holding_days: 平均持有天数
    
    Returns:
        成本估算明细
    """
    calculator = TransactionCostCalculator()

    # 每笔交易成本（买+卖）
    sample_costs = calculator.calculate_round_trip_cost(
        buy_price=10.0, sell_price=10.0, shares=1000
    )
    per_trade_cost_rate = sample_costs["cost_rate"] / 100

    # 年度总成本
    annual_cost = num_trades * avg_trade_value * per_trade_cost_rate

    # 资金占用成本
    capital_cost = avg_trade_value * num_trades * holding_days / 252 * 0.02

    total_annual_cost = annual_cost + capital_cost

    return {
        "annual_trade_cost": round(annual_cost, 2),
        "annual_capital_cost": round(capital_cost, 2),
        "total_annual_cost": round(total_annual_cost, 2),
        "cost_percentage": round(total_annual_cost / (avg_trade_value * num_trades) * 100, 2),
        "per_trade_cost_rate": round(per_trade_cost_rate * 100, 4),
    }
