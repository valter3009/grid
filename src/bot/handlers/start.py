"""Start command handler."""
from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from src.models.user import User
from src.bot.keyboards.inline import get_main_menu_keyboard, get_settings_keyboard
from datetime import datetime

logger = logging.getLogger(__name__)

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, db: AsyncSession):
    """Handle /start command."""
    try:
        # Check if user exists
        result = await db.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
        user = result.scalar_one_or_none()

        if not user:
            # Create new user
            user = User(
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name
            )
            db.add(user)
            await db.commit()

            # Welcome message for new users
            welcome_text = (
                f"👋 Привет, {user.first_name}!\n\n"
                f"Добро пожаловать в Grid Trading Bot!\n\n"
                f"Этот бот поможет вам автоматизировать торговлю на бирже MEXC "
                f"используя стратегию Grid Trading.\n\n"
                f"🔰 Для начала работы:\n"
                f"1. Настройте API ключи MEXC\n"
                f"2. Создайте своего первого Grid бота\n"
                f"3. Получайте пассивный доход!\n\n"
                f"Нажмите ⚙️ Настройки для подключения API ключей."
            )

            await message.answer(
                welcome_text,
                reply_markup=get_main_menu_keyboard()
            )

        else:
            # Update user info
            user.username = message.from_user.username
            user.first_name = message.from_user.first_name
            user.last_name = message.from_user.last_name
            user.last_active_at = datetime.utcnow()
            await db.commit()

            # Returning user message
            if not user.has_api_keys:
                text = (
                    f"С возвращением, {user.first_name}! 👋\n\n"
                    f"Для начала работы настройте API ключи MEXC в разделе ⚙️ Настройки"
                )
            else:
                text = (
                    f"С возвращением, {user.first_name}! 👋\n\n"
                    f"Выберите действие в меню ниже:"
                )

            await message.answer(
                text,
                reply_markup=get_main_menu_keyboard()
            )

        logger.info(f"User {user.telegram_id} started the bot")

    except Exception as e:
        logger.error(f"Error in start command: {e}")
        await message.answer(
            "Произошла ошибка. Попробуйте позже или обратитесь в поддержку."
        )


@router.callback_query(F.data == "main_menu")
async def show_main_menu(callback: CallbackQuery, db: AsyncSession):
    """Show main menu."""
    try:
        # Load user
        result = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = result.scalar_one_or_none()

        if not user:
            await callback.answer("Пожалуйста, отправьте /start")
            return

        text = "🏠 Главное меню\n\nВыберите действие:"

        await callback.message.edit_text(
            text,
            reply_markup=get_main_menu_keyboard()
        )
        await callback.answer()

    except Exception as e:
        logger.error(f"Error showing main menu: {e}")
        await callback.answer("Ошибка при загрузке меню")


@router.callback_query(F.data == "settings")
async def show_settings(callback: CallbackQuery):
    """Show settings menu."""
    text = (
        "⚙️ Настройки\n\n"
        "Выберите, что хотите настроить:"
    )

    await callback.message.edit_text(
        text,
        reply_markup=get_settings_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "help")
async def show_help(callback: CallbackQuery):
    """Show help information."""
    text = (
        "❓ Помощь\n\n"
        "📚 Основные команды:\n"
        "/start - Главное меню\n"
        "/help - Эта справка\n\n"
        "🤖 Grid Trading - это стратегия автоматической торговли:\n"
        "• Бот размещает сетку ордеров на покупку и продажу\n"
        "• При движении цены исполняются ордера\n"
        "• Каждый цикл покупка→продажа приносит прибыль\n\n"
        "💡 Советы:\n"
        "• Выбирайте волатильные пары (BTC, ETH)\n"
        "• Устанавливайте диапазон с запасом\n"
        "• Начинайте с небольших сумм\n"
        "• Следите за уведомлениями\n\n"
        "🔐 Безопасность:\n"
        "• API ключи шифруются\n"
        "• Используйте только spot API\n"
        "• Не давайте права на вывод средств\n\n"
        "📧 Поддержка: @support"
    )

    from src.bot.keyboards.inline import get_back_button
    await callback.message.edit_text(
        text,
        reply_markup=get_back_button("main_menu")
    )
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    """Cancel current action."""
    await state.clear()

    await callback.message.edit_text(
        "❌ Действие отменено.\n\nВыберите действие:",
        reply_markup=get_main_menu_keyboard()
    )
    await callback.answer()


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Handle /help command."""
    text = (
        "❓ Помощь\n\n"
        "Используйте /start для доступа к главному меню."
    )
    await message.answer(text)
