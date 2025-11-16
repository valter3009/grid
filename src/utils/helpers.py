"""Helper utilities."""
from decimal import Decimal, ROUND_DOWN
from typing import Optional, List
import asyncio
import logging

logger = logging.getLogger(__name__)


def parse_decimal(value: any, default: Decimal = Decimal('0')) -> Decimal:
    """
    Safely parse value to Decimal.

    Args:
        value: Value to parse
        default: Default value if parsing fails

    Returns:
        Decimal value
    """
    if value is None:
        return default

    if isinstance(value, Decimal):
        return value

    try:
        return Decimal(str(value))
    except Exception as e:
        logger.warning(f"Failed to parse decimal from {value}: {e}")
        return default


def round_down(value: Decimal, precision: int) -> Decimal:
    """
    Round down decimal to specified precision.

    Args:
        value: Value to round
        precision: Number of decimal places

    Returns:
        Rounded value
    """
    # Ensure precision is int (convert from float if needed)
    precision = int(precision)
    quantize_value = Decimal(10) ** -precision
    return value.quantize(quantize_value, rounding=ROUND_DOWN)


def calculate_order_amount(
    investment: Decimal,
    price: Decimal,
    num_orders: int,
    precision: int = 8
) -> Decimal:
    """
    Calculate order amount for grid level.

    Args:
        investment: Total investment amount
        price: Price at this level
        num_orders: Number of orders to create
        precision: Amount precision

    Returns:
        Order amount
    """
    if num_orders == 0 or price == 0:
        return Decimal('0')

    # Divide investment equally
    amount_per_order = investment / Decimal(str(num_orders))

    # Calculate amount in base currency
    amount = amount_per_order / price

    # Round down to precision
    return round_down(amount, precision)


def split_symbol(symbol: str) -> tuple[str, str]:
    """
    Split trading pair into base and quote currencies.

    Args:
        symbol: Trading pair (e.g., BTC/USDT)

    Returns:
        Tuple of (base_currency, quote_currency)
    """
    parts = symbol.split('/')
    if len(parts) != 2:
        raise ValueError(f"Invalid symbol format: {symbol}")

    return parts[0], parts[1]


async def retry_async(
    func,
    *args,
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
    **kwargs
):
    """
    Retry async function with exponential backoff.

    Args:
        func: Async function to retry
        *args: Function arguments
        max_retries: Maximum number of retries
        delay: Initial delay in seconds
        backoff: Backoff multiplier
        exceptions: Exceptions to catch
        **kwargs: Function keyword arguments

    Returns:
        Function result

    Raises:
        Last exception if all retries fail
    """
    current_delay = delay

    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except exceptions as e:
            if attempt == max_retries:
                logger.error(f"All {max_retries} retries failed for {func.__name__}: {e}")
                raise

            logger.warning(
                f"Attempt {attempt + 1}/{max_retries} failed for {func.__name__}: {e}. "
                f"Retrying in {current_delay}s..."
            )
            await asyncio.sleep(current_delay)
            current_delay *= backoff


def calculate_grid_profit_potential(
    lower_price: Decimal,
    upper_price: Decimal,
    grid_levels: int,
    fee_percent: Decimal = Decimal('0.1')
) -> Decimal:
    """
    Calculate potential profit per cycle for grid bot.

    Args:
        lower_price: Lower boundary price
        upper_price: Upper boundary price
        grid_levels: Number of grid levels
        fee_percent: Trading fee percentage

    Returns:
        Profit percentage per cycle
    """
    if grid_levels == 0:
        return Decimal('0')

    # Calculate grid step
    grid_step = (upper_price - lower_price) / Decimal(str(grid_levels))

    # Average price
    avg_price = (lower_price + upper_price) / 2

    # Profit per grid step
    step_profit_percent = (grid_step / avg_price) * 100

    # Subtract fees (buy + sell)
    net_profit = step_profit_percent - (fee_percent * 2)

    return max(net_profit, Decimal('0'))


def get_price_precision(price: Decimal) -> int:
    """
    Determine appropriate precision for price.

    Args:
        price: Price value

    Returns:
        Suggested precision
    """
    if price >= 1000:
        return 2
    elif price >= 100:
        return 2
    elif price >= 10:
        return 2
    elif price >= 1:
        return 4
    elif price >= 0.1:
        return 6
    else:
        return 8


def get_amount_precision(amount: Decimal) -> int:
    """
    Determine appropriate precision for amount.

    Args:
        amount: Amount value

    Returns:
        Suggested precision
    """
    if amount >= 1000:
        return 2
    elif amount >= 1:
        return 4
    else:
        return 8


def chunk_list(items: List, chunk_size: int) -> List[List]:
    """
    Split list into chunks.

    Args:
        items: List to split
        chunk_size: Size of each chunk

    Returns:
        List of chunks
    """
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


def safe_divide(numerator: Decimal, denominator: Decimal, default: Decimal = Decimal('0')) -> Decimal:
    """
    Safely divide two decimals.

    Args:
        numerator: Numerator
        denominator: Denominator
        default: Default value if division fails

    Returns:
        Division result or default
    """
    if denominator == 0:
        return default

    try:
        return numerator / denominator
    except Exception as e:
        logger.warning(f"Division failed: {e}")
        return default
