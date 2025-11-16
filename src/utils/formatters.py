"""Formatting utilities for displaying data."""
from decimal import Decimal
from datetime import datetime, timedelta
from typing import Optional


def format_price(price: Decimal, precision: int = 2) -> str:
    """
    Format price for display.

    Args:
        price: Price value
        precision: Number of decimal places

    Returns:
        Formatted price string
    """
    if price is None:
        return "N/A"

    return f"${price:,.{precision}f}"


def format_amount(amount: Decimal, precision: int = 8) -> str:
    """
    Format cryptocurrency amount.

    Args:
        amount: Amount value
        precision: Number of decimal places

    Returns:
        Formatted amount string
    """
    if amount is None:
        return "N/A"

    # Remove trailing zeros
    formatted = f"{amount:.{precision}f}".rstrip('0').rstrip('.')
    return formatted


def format_percent(value: Decimal, precision: int = 2) -> str:
    """
    Format percentage value.

    Args:
        value: Percentage value
        precision: Number of decimal places

    Returns:
        Formatted percentage string
    """
    if value is None:
        return "N/A"

    sign = "+" if value > 0 else ""
    return f"{sign}{value:.{precision}f}%"


def format_profit(profit: Decimal, show_sign: bool = True) -> str:
    """
    Format profit value.

    Args:
        profit: Profit amount
        show_sign: Whether to show + sign for positive values

    Returns:
        Formatted profit string
    """
    if profit is None:
        return "N/A"

    sign = ""
    if show_sign and profit > 0:
        sign = "+"
    elif profit < 0:
        sign = "-"

    abs_profit = abs(profit)
    return f"{sign}${abs_profit:,.2f}"


def format_datetime(dt: Optional[datetime], format_str: str = "%Y-%m-%d %H:%M:%S") -> str:
    """
    Format datetime for display.

    Args:
        dt: Datetime object
        format_str: Format string

    Returns:
        Formatted datetime string
    """
    if dt is None:
        return "N/A"

    return dt.strftime(format_str)


def format_timedelta(td: Optional[timedelta]) -> str:
    """
    Format timedelta as human-readable string.

    Args:
        td: Timedelta object

    Returns:
        Formatted timedelta string
    """
    if td is None:
        return "N/A"

    total_seconds = int(td.total_seconds())
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60

    parts = []
    if days > 0:
        parts.append(f"{days}д")
    if hours > 0:
        parts.append(f"{hours}ч")
    if minutes > 0 or not parts:
        parts.append(f"{minutes}м")

    return " ".join(parts)


def format_runtime(started_at: Optional[datetime], stopped_at: Optional[datetime] = None) -> str:
    """
    Format bot runtime.

    Args:
        started_at: Start datetime
        stopped_at: Stop datetime (None for current time)

    Returns:
        Formatted runtime string
    """
    if started_at is None:
        return "N/A"

    end_time = stopped_at or datetime.utcnow()
    runtime = end_time - started_at
    return format_timedelta(runtime)


def format_order_status(status: str) -> str:
    """
    Format order status with emoji.

    Args:
        status: Order status

    Returns:
        Formatted status string
    """
    status_map = {
        "open": "🟡 Открыт",
        "filled": "✅ Исполнен",
        "cancelled": "❌ Отменен",
        "error": "⚠️ Ошибка",
    }
    return status_map.get(status, status)


def format_bot_status(status: str) -> str:
    """
    Format bot status with emoji.

    Args:
        status: Bot status

    Returns:
        Formatted status string
    """
    status_map = {
        "active": "🟢 Активен",
        "paused": "🟡 Пауза",
        "stopped": "🔴 Остановлен",
    }
    return status_map.get(status, status)


def format_trading_pair(symbol: str) -> str:
    """
    Format trading pair for display.

    Args:
        symbol: Trading pair (e.g., BTC/USDT)

    Returns:
        Formatted trading pair
    """
    return symbol.upper().replace('/', ' / ')


def truncate_string(text: str, max_length: int = 50, suffix: str = "...") -> str:
    """
    Truncate long string.

    Args:
        text: Text to truncate
        max_length: Maximum length
        suffix: Suffix to add if truncated

    Returns:
        Truncated string
    """
    if len(text) <= max_length:
        return text

    return text[:max_length - len(suffix)] + suffix


def format_balance_summary(balances: dict) -> str:
    """
    Format balance summary for display.

    Args:
        balances: Dictionary of balances {currency: amount}

    Returns:
        Formatted balance summary
    """
    if not balances:
        return "Нет данных о балансе"

    lines = []
    for currency, amount in balances.items():
        if amount > 0:
            formatted_amount = format_amount(Decimal(str(amount)))
            lines.append(f"{currency}: {formatted_amount}")

    return "\n".join(lines) if lines else "Баланс пуст"
