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

logger = logging.getLogger(__name__)

router = Router()


async def get_usd_price(mexc_service: MEXCService, symbol: str) -> Decimal:
    """Get USD price for a cryptocurrency symbol."""
    # Stablecoins are always 1 USD
    stablecoins = ['USDT', 'USDC', 'BUSD', 'DAI', 'TUSD', 'USDD', 'FDUSD']
    if symbol in stablecoins:
        return Decimal('1.0')

    # Try to get price from MEXC
    try:
        # Try SYMBOL/USDT pair
        price = await mexc_service.get_current_price(f"{symbol}/USDT")
        return price
    except:
        try:
            # Try SYMBOL/USDC pair
            price = await mexc_service.get_current_price(f"{symbol}/USDC")
            return price
        except:
            # If no price available, return 0
            return Decimal('0')


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
    try:
        # Get user
        result = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = result.scalar_one_or_none()

        if not user:
            await callback.answer("Пожалуйста, отправьте /start")
            return

        if not user.has_api_keys:
            await callback.message.edit_text(
                "❌ Баланс недоступен\n\n"
                "Для просмотра баланса необходимо настроить API ключи MEXC.\n\n"
                "Перейдите в ⚙️ Настройки → 🔑 API ключи",
                reply_markup=get_back_button("main_menu")
            )
            await callback.answer()
            return

        # Show loading message immediately
        await callback.message.edit_text(
            "⏳ Загружаю баланс с MEXC...\n\n"
            "Это может занять несколько секунд.",
            reply_markup=None
        )
        await callback.answer()

        # Get balance from MEXC
        mexc_service = MEXCService(db)
        balances = await mexc_service.get_balance(user.id)

        if not balances:
            await callback.message.edit_text(
                "❌ Не удалось загрузить баланс\n\n"
                "Проверьте настройки API ключей.",
                reply_markup=get_back_button("main_menu")
            )
            await callback.answer()
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
            # Get USD prices for all assets
            assets_with_usd = []
            total_usd = Decimal('0')

            for symbol, amount in non_zero_balances.items():
                usd_price = await get_usd_price(mexc_service, symbol)
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

            text += f"\n📊 Всего активов: {len(non_zero_balances)}"

        await callback.message.edit_text(
            text,
            reply_markup=get_back_button("main_menu")
        )
        await callback.answer()

        logger.info(f"User {user.telegram_id} viewed balance")

    except Exception as e:
        logger.error(f"Error showing balance: {e}", exc_info=True)
        await callback.message.edit_text(
            "❌ Произошла ошибка при загрузке баланса\n\n"
            "Попробуйте позже.",
            reply_markup=get_back_button("main_menu")
        )
        await callback.answer()
