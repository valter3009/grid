"""Validation utilities."""
from decimal import Decimal
from typing import Optional
from src.core.config import settings
import logging

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """Custom validation error."""
    pass


def validate_price_range(lower_price: Decimal, upper_price: Decimal, current_price: Optional[Decimal] = None) -> bool:
    """
    Validate price range for grid bot.

    Args:
        lower_price: Lower boundary price
        upper_price: Upper boundary price
        current_price: Current market price (optional)

    Returns:
        True if valid

    Raises:
        ValidationError: If validation fails
    """
    if lower_price <= 0:
        raise ValidationError("Нижняя граница должна быть больше 0")

    if upper_price <= 0:
        raise ValidationError("Верхняя граница должна быть больше 0")

    if lower_price >= upper_price:
        raise ValidationError("Нижняя граница должна быть меньше верхней")

    # Check minimum range (2%)
    range_percent = (upper_price - lower_price) / lower_price * 100
    if range_percent < 2:
        raise ValidationError("Диапазон слишком узкий (минимум 2%)")

    # Validate current price is within range if provided
    if current_price is not None:
        if current_price < lower_price or current_price > upper_price:
            logger.warning(
                f"Current price {current_price} is outside range "
                f"[{lower_price}, {upper_price}]"
            )

    return True


def validate_grid_levels(grid_levels: int) -> bool:
    """
    Validate number of grid levels.

    Args:
        grid_levels: Number of grid levels

    Returns:
        True if valid

    Raises:
        ValidationError: If validation fails
    """
    if grid_levels < settings.MIN_GRID_LEVELS:
        raise ValidationError(f"Минимальное количество уровней: {settings.MIN_GRID_LEVELS}")

    if grid_levels > settings.MAX_GRID_LEVELS:
        raise ValidationError(f"Максимальное количество уровней: {settings.MAX_GRID_LEVELS}")

    return True


def validate_investment_amount(amount: Decimal, available_balance: Optional[Decimal] = None) -> bool:
    """
    Validate investment amount.

    Args:
        amount: Investment amount in USDT
        available_balance: Available balance (optional)

    Returns:
        True if valid

    Raises:
        ValidationError: If validation fails
    """
    if amount < Decimal(str(settings.MIN_INVESTMENT_USDT)):
        raise ValidationError(f"Минимальная сумма инвестиций: ${settings.MIN_INVESTMENT_USDT}")

    if available_balance is not None and amount > available_balance:
        raise ValidationError(
            f"Недостаточно средств. Доступно: ${available_balance:.2f}, "
            f"требуется: ${amount:.2f}"
        )

    return True


def validate_trading_pair(symbol: str) -> bool:
    """
    Validate trading pair format.

    Args:
        symbol: Trading pair (e.g., BTC/USDT)

    Returns:
        True if valid

    Raises:
        ValidationError: If validation fails
    """
    if not symbol or '/' not in symbol:
        raise ValidationError("Неверный формат торговой пары. Используйте формат: BTC/USDT")

    parts = symbol.split('/')
    if len(parts) != 2:
        raise ValidationError("Неверный формат торговой пары. Используйте формат: BTC/USDT")

    base, quote = parts
    if not base or not quote:
        raise ValidationError("Неверный формат торговой пары")

    return True


def validate_api_key_format(api_key: str, api_secret: str) -> bool:
    """
    Validate API key format.

    Args:
        api_key: API key
        api_secret: API secret

    Returns:
        True if valid

    Raises:
        ValidationError: If validation fails
    """
    if not api_key or not api_key.strip():
        raise ValidationError("API ключ не может быть пустым")

    if not api_secret or not api_secret.strip():
        raise ValidationError("API секрет не может быть пустым")

    if len(api_key) < 10:
        raise ValidationError("API ключ слишком короткий")

    if len(api_secret) < 10:
        raise ValidationError("API секрет слишком короткий")

    return True


def validate_bot_name(name: str) -> bool:
    """
    Validate bot name.

    Args:
        name: Bot name

    Returns:
        True if valid

    Raises:
        ValidationError: If validation fails
    """
    if not name:
        return True  # Name is optional

    if len(name) > 255:
        raise ValidationError("Название бота слишком длинное (максимум 255 символов)")

    return True
