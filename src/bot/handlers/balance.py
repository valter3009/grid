"""Balance handler."""
from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from src.models.user import User
from src.services.mexc_service import MEXCService
from src.bot.keyboards.inline import get_back_button

logger = logging.getLogger(__name__)

router = Router()


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

        # Show loading message
        await callback.message.edit_text(
            "⏳ Загружаю баланс...",
            reply_markup=get_back_button("main_menu")
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
            await callback.answer()
            return

        # Filter out zero balances and sort by value
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
            text = "💼 Баланс\n\n"

            # Show USDT first if available
            if 'USDT' in non_zero_balances:
                text += f"💵 USDT: {non_zero_balances['USDT']:.2f}\n\n"

            # Show other currencies
            text += "Другие активы:\n"
            for symbol, amount in sorted(non_zero_balances.items()):
                if symbol != 'USDT':
                    # Format amount based on size
                    if amount >= 1:
                        formatted_amount = f"{amount:.4f}"
                    else:
                        formatted_amount = f"{amount:.8f}"

                    text += f"• {symbol}: {formatted_amount}\n"

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
