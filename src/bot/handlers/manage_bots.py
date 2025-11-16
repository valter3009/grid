"""Bot management handler."""
from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
import logging

from src.models.user import User
from src.models.grid_bot import GridBot
from src.services.mexc_service import MEXCService
from src.services.grid_strategy import GridStrategy
from src.services.bot_manager import BotManager
from src.bot.keyboards.inline import (
    get_my_bots_keyboard,
    get_bot_details_keyboard,
    get_stop_bot_keyboard,
    get_back_button
)

logger = logging.getLogger(__name__)

router = Router()


@router.callback_query(F.data == "my_bots")
async def show_my_bots(callback: CallbackQuery, db: AsyncSession):
    """Show user's grid bots."""
    try:
        # Get user
        result = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = result.scalar_one_or_none()

        if not user:
            await callback.answer("Пожалуйста, отправьте /start")
            return

        # Get user's bots
        result = await db.execute(
            select(GridBot).where(GridBot.user_id == user.id).order_by(GridBot.created_at.desc())
        )
        bots = result.scalars().all()

        if not bots:
            text = (
                "📊 Мои боты\n\n"
                "У вас пока нет ботов.\n\n"
                "Создайте своего первого Grid бота для начала автоматической торговли!"
            )
            await callback.message.edit_text(
                text,
                reply_markup=get_back_button("main_menu")
            )
            await callback.answer()
            return

        # Format bots for keyboard
        bots_data = []
        for bot in bots:
            bots_data.append({
                'id': bot.id,
                'symbol': bot.symbol,
                'status': bot.status
            })

        text = (
            f"📊 Мои боты ({len(bots)})\n\n"
            f"Выберите бота для просмотра деталей:"
        )

        await callback.message.edit_text(
            text,
            reply_markup=get_my_bots_keyboard(bots_data)
        )
        await callback.answer()

    except Exception as e:
        logger.error(f"Error showing bots: {e}", exc_info=True)
        await callback.answer("Ошибка при загрузке ботов")


@router.callback_query(F.data.startswith("bot_details:"))
async def show_bot_details(callback: CallbackQuery, db: AsyncSession):
    """Show detailed information about a bot."""
    try:
        bot_id = int(callback.data.split(":")[1])

        # Get bot
        result = await db.execute(
            select(GridBot).where(GridBot.id == bot_id)
        )
        bot = result.scalar_one_or_none()

        if not bot:
            await callback.answer("Бот не найден")
            return

        # Calculate stats
        total_profit = bot.total_profit or 0
        total_trades = bot.completed_cycles or 0

        # Format status
        status_emoji = {
            'active': '🟢',
            'paused': '🟡',
            'stopped': '🔴'
        }.get(bot.status, '⚪')

        status_text = {
            'active': 'Активен',
            'paused': 'На паузе',
            'stopped': 'Остановлен'
        }.get(bot.status, 'Неизвестно')

        # Calculate runtime
        if bot.started_at:
            runtime = datetime.utcnow() - bot.started_at
            days = runtime.days
            hours = runtime.seconds // 3600
            runtime_text = f"{days}д {hours}ч"
        else:
            runtime_text = "—"

        text = (
            f"🤖 Бот #{bot.id}\n\n"
            f"📈 Пара: {bot.symbol}\n"
            f"{status_emoji} Статус: {status_text}\n\n"
            f"💰 Параметры:\n"
            f"• Инвестиция: ${bot.investment_amount:.2f}\n"
            f"• Диапазон: ${bot.lower_price:.2f} - ${bot.upper_price:.2f}\n"
            f"• Уровней сетки: {bot.grid_levels}\n\n"
            f"📊 Статистика:\n"
            f"• Общая прибыль: ${total_profit:.2f}\n"
            f"• Завершено циклов: {total_trades}\n"
            f"• Время работы: {runtime_text}\n\n"
            f"📅 Создан: {bot.created_at.strftime('%d.%m.%Y %H:%M')}"
        )

        await callback.message.edit_text(
            text,
            reply_markup=get_bot_details_keyboard(bot.id, bot.status)
        )
        await callback.answer()

    except Exception as e:
        logger.error(f"Error showing bot details: {e}", exc_info=True)
        await callback.answer("Ошибка при загрузке деталей")


@router.callback_query(F.data.startswith("bot_refresh:"))
async def refresh_bot_details(callback: CallbackQuery, db: AsyncSession):
    """Refresh bot details."""
    try:
        bot_id = int(callback.data.split(":")[1])

        # Just re-show the details
        await show_bot_details(
            CallbackQuery(
                **{**callback.__dict__, 'data': f"bot_details:{bot_id}"}
            ),
            db
        )
        await callback.answer("✅ Обновлено")

    except Exception as e:
        logger.error(f"Error refreshing bot: {e}", exc_info=True)
        await callback.answer("Ошибка при обновлении")


@router.callback_query(F.data.startswith("bot_pause:"))
async def pause_bot(callback: CallbackQuery, db: AsyncSession):
    """Pause a bot."""
    try:
        bot_id = int(callback.data.split(":")[1])

        # Get bot
        result = await db.execute(
            select(GridBot).where(GridBot.id == bot_id)
        )
        bot = result.scalar_one_or_none()

        if not bot:
            await callback.answer("Бот не найден")
            return

        if bot.status != 'active':
            await callback.answer("Бот не активен")
            return

        # Pause bot
        bot.status = 'paused'
        await db.commit()

        await callback.answer("⏸ Бот поставлен на паузу")

        # Refresh details
        callback.data = f"bot_details:{bot_id}"
        await show_bot_details(callback, db)

        logger.info(f"Bot {bot_id} paused")

    except Exception as e:
        logger.error(f"Error pausing bot: {e}", exc_info=True)
        await callback.answer("Ошибка при постановке на паузу")


@router.callback_query(F.data.startswith("bot_resume:"))
async def resume_bot(callback: CallbackQuery, db: AsyncSession):
    """Resume a paused bot."""
    try:
        bot_id = int(callback.data.split(":")[1])

        # Get bot
        result = await db.execute(
            select(GridBot).where(GridBot.id == bot_id)
        )
        bot = result.scalar_one_or_none()

        if not bot:
            await callback.answer("Бот не найден")
            return

        if bot.status != 'paused':
            await callback.answer("Бот не на паузе")
            return

        # Resume bot
        bot.status = 'active'
        await db.commit()

        await callback.answer("▶️ Бот возобновлен")

        # Refresh details
        callback.data = f"bot_details:{bot_id}"
        await show_bot_details(callback, db)

        logger.info(f"Bot {bot_id} resumed")

    except Exception as e:
        logger.error(f"Error resuming bot: {e}", exc_info=True)
        await callback.answer("Ошибка при возобновлении")


@router.callback_query(F.data.startswith("bot_stop:"))
async def confirm_stop_bot(callback: CallbackQuery, db: AsyncSession):
    """Show confirmation for stopping bot."""
    try:
        bot_id = int(callback.data.split(":")[1])

        text = (
            "🛑 Остановка бота\n\n"
            "Выберите вариант остановки:\n\n"
            "1️⃣ Сохранить активы - отменить все ордера, но оставить купленные монеты на балансе\n\n"
            "2️⃣ Продать всё - отменить ордера и продать все купленные монеты по рыночной цене"
        )

        await callback.message.edit_text(
            text,
            reply_markup=get_stop_bot_keyboard(bot_id)
        )
        await callback.answer()

    except Exception as e:
        logger.error(f"Error showing stop confirmation: {e}", exc_info=True)
        await callback.answer("Ошибка")


@router.callback_query(F.data.startswith("stop_confirm:"))
async def stop_bot(callback: CallbackQuery, db: AsyncSession):
    """Stop a bot."""
    try:
        parts = callback.data.split(":")
        bot_id = int(parts[1])
        mode = parts[2]  # 'keep' or 'sell'

        # Get bot and user
        result = await db.execute(
            select(GridBot).where(GridBot.id == bot_id)
        )
        bot = result.scalar_one_or_none()

        if not bot:
            await callback.answer("Бот не найден")
            return

        # Show progress message
        await callback.message.edit_text(
            "⏳ Останавливаю бота...\n"
            "Это может занять некоторое время.",
            reply_markup=None
        )

        # Initialize services
        mexc_service = MEXCService(db)
        grid_strategy = GridStrategy(db, mexc_service)
        bot_manager = BotManager(db, mexc_service, grid_strategy)

        # Stop bot
        sell_all = (mode == 'sell')
        success = await bot_manager.stop_bot(bot_id, sell_all=sell_all)

        if success:
            await callback.message.edit_text(
                "✅ Бот успешно остановлен\n\n"
                f"{'Все активы проданы' if sell_all else 'Активы сохранены на балансе'}",
                reply_markup=get_back_button("my_bots")
            )
            logger.info(f"Bot {bot_id} stopped (sell_all={sell_all})")
        else:
            await callback.message.edit_text(
                "❌ Ошибка при остановке бота\n\n"
                "Попробуйте позже или обратитесь в поддержку.",
                reply_markup=get_back_button("my_bots")
            )

        await callback.answer()

    except Exception as e:
        logger.error(f"Error stopping bot: {e}", exc_info=True)
        await callback.message.edit_text(
            "❌ Произошла ошибка при остановке бота",
            reply_markup=get_back_button("my_bots")
        )
        await callback.answer()
