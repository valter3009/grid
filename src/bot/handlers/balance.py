"""Balance handler."""
from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from decimal import Decimal
import logging

from src.models.user import User
from src.services.mexc_service import MEXCService
from src.bot.keyboards.inline import get_back_button
from src.utils.cache import price_cache

logger = logging.getLogger(__name__)

router = Router()


async def get_usd_prices_batch(mexc_service: MEXCService, symbols: list) -> dict:
    """
    Get USD prices for multiple symbols efficiently.
    Uses cache and batch API requests.

    Args:
        mexc_service: MEXC service instance
        symbols: List of cryptocurrency symbols (e.g., ['BTC', 'ETH', 'SOL'])

    Returns:
        Dictionary of {symbol: price_in_usd}
    """
    stablecoins = ['USDT', 'USDC', 'BUSD', 'DAI', 'TUSD', 'USDD', 'FDUSD']
    prices = {}
    symbols_to_fetch = []

    # First pass: check cache and handle stablecoins
    for symbol in symbols:
        if symbol in stablecoins:
            prices[symbol] = Decimal('1.0')
            continue

        # Check cache
        cache_key = f"usd_price:{symbol}"
        cached_price = price_cache.get(cache_key)
        if cached_price is not None:
            prices[symbol] = cached_price
        else:
            symbols_to_fetch.append(symbol)

    # If all prices are cached, return immediately
    if not symbols_to_fetch:
        return prices

    # Build trading pairs to fetch (try /USDT first)
    pairs_to_fetch = [f"{symbol}/USDT" for symbol in symbols_to_fetch]

    try:
        # Fetch all prices in ONE API call (much faster!)
        batch_prices = await mexc_service.get_multiple_prices(pairs_to_fetch)

        # Process results
        for symbol in symbols_to_fetch:
            pair = f"{symbol}/USDT"
            if pair in batch_prices:
                price = batch_prices[pair]
                prices[symbol] = price
                # Cache for 60 seconds
                price_cache.set(f"usd_price:{symbol}", price)
            else:
                # Try USDC pair as fallback
                try:
                    usdc_pair = f"{symbol}/USDC"
                    price = await mexc_service.get_current_price(usdc_pair)
                    prices[symbol] = price
                    price_cache.set(f"usd_price:{symbol}", price)
                except:
                    # Price not available
                    prices[symbol] = Decimal('0')

    except Exception as e:
        logger.error(f"Error fetching batch prices: {e}")
        # Fallback: set remaining to 0
        for symbol in symbols_to_fetch:
            if symbol not in prices:
                prices[symbol] = Decimal('0')

    return prices


def format_usd(value: float) -> str:
    """Format USD value with smart decimal places."""
    if value >= 1:
        # For values >= 1, show 2 decimals
        return f"{value:,.2f}"
    elif value >= 0.01:
        # For values >= 0.01, show up to 4 decimals
        return f"{value:.4f}".rstrip('0').rstrip('.')
    else:
        # For small values, show up to 8 decimals
        return f"{value:.8f}".rstrip('0').rstrip('.')


def format_amount(value: float, currency: str) -> str:
    """Format crypto amount with smart decimal places."""
    stablecoins = ['USDT', 'USDC', 'BUSD', 'DAI', 'TUSD', 'USDD', 'FDUSD']

    if currency in stablecoins:
        # Stablecoins: 2 decimals
        return f"{value:.2f}"
    else:
        # Crypto: up to 8 decimals, trim trailing zeros
        return f"{value:.8f}".rstrip('0').rstrip('.')


@router.callback_query(F.data == "balance")
async def show_balance(callback: CallbackQuery, db: AsyncSession):
    """Show user balance."""
    # Answer callback immediately to avoid timeout
    await callback.answer()

    try:
        # Get user
        result = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = result.scalar_one_or_none()

        if not user:
            await callback.message.edit_text(
                "Пожалуйста, отправьте /start",
                reply_markup=get_back_button("main_menu")
            )
            return

        if not user.has_api_keys:
            await callback.message.edit_text(
                "❌ Баланс недоступен\n\n"
                "Для просмотра баланса необходимо настроить API ключи MEXC.\n\n"
                "Перейдите в ⚙️ Настройки → 🔑 API ключи",
                reply_markup=get_back_button("main_menu")
            )
            return

        # Show loading message immediately
        await callback.message.edit_text(
            "⏳ Загружаю баланс с MEXC...\n\n"
            "Это может занять несколько секунд.",
            reply_markup=None
        )

        # Get balance from MEXC
        mexc_service = MEXCService(db)
        balances = await mexc_service.get_balance(user.id)

        if not balances:
            await callback.message.edit_text(
                "❌ Не удалось загрузить баланс\n\n"
                "Проверьте настройки API ключей.",
                reply_markup=get_back_button("main_menu")
            )
            return

        # Filter out zero balances
        non_zero_balances = {
            symbol: amount for symbol, amount in balances.items()
            if amount > 0
        }

        if not non_zero_balances:
            text = (
                "💼 Баланс\n\n"
                "Ваш баланс пуст.\n\n"
                "Пополните счет на MEXC для начала торговли."
            )
        else:
            # Get all symbols
            symbols = list(non_zero_balances.keys())

            # Fetch all USD prices in ONE batch request (with cache!)
            # This is MUCH faster than individual requests
            usd_prices = await get_usd_prices_batch(mexc_service, symbols)

            # Calculate USD values for each asset
            assets_with_usd = []
            total_usd = Decimal('0')

            for symbol, amount in non_zero_balances.items():
                usd_price = usd_prices.get(symbol, Decimal('0'))
                usd_value = Decimal(str(amount)) * usd_price
                total_usd += usd_value

                assets_with_usd.append({
                    'symbol': symbol,
                    'amount': amount,
                    'usd_value': float(usd_value)
                })

            # Sort by USD value (highest first)
            assets_with_usd.sort(key=lambda x: x['usd_value'], reverse=True)

            # Build message
            text = f"💼 Баланс: ${format_usd(float(total_usd))}\n\n"
            text += "Активы:\n"

            for asset in assets_with_usd:
                symbol = asset['symbol']
                amount = asset['amount']
                usd_value = asset['usd_value']

                formatted_amount = format_amount(float(amount), symbol)
                formatted_usd = format_usd(usd_value)

                text += f"• {symbol}: {formatted_amount} (${formatted_usd})\n"

            text += f"\n📊 Всего активов: {len(assets_with_usd)}"

        await callback.message.edit_text(
            text,
            reply_markup=get_back_button("main_menu")
        )

        logger.info(f"User {user.telegram_id} viewed balance")

    except Exception as e:
        logger.error(f"Error showing balance: {e}", exc_info=True)
        await callback.message.edit_text(
            "❌ Произошла ошибка при загрузке баланса\n\n"
            "Попробуйте позже.",
            reply_markup=get_back_button("main_menu")
        )
