"""API setup handler."""
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from src.models.user import User
from src.services.mexc_service import MEXCService
from src.core.security import SecurityManager
from src.bot.keyboards.inline import get_settings_keyboard, get_back_button

logger = logging.getLogger(__name__)

router = Router()


class APISetupStates(StatesGroup):
    """States for API setup flow."""
    waiting_for_api_key = State()
    waiting_for_api_secret = State()


@router.callback_query(F.data == "settings_api")
async def show_api_settings(callback: CallbackQuery, db: AsyncSession):
    """Show API settings."""
    try:
        # Get user
        result = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = result.scalar_one_or_none()

        if not user:
            await callback.answer("Пожалуйста, отправьте /start")
            return

        if user.has_api_keys:
            text = (
                "🔑 API ключи MEXC\n\n"
                "✅ API ключи настроены\n\n"
                "Вы можете:\n"
                "• Обновить ключи\n"
                "• Удалить ключи\n\n"
                "Для обновления отправьте новые ключи."
            )
        else:
            text = (
                "🔑 API ключи MEXC\n\n"
                "❌ API ключи не настроены\n\n"
                "Для работы бота необходимо настроить API ключи MEXC.\n\n"
                "📝 Как получить ключи:\n"
                "1. Зайдите на MEXC → API Management\n"
                "2. Создайте новый Spot API ключ\n"
                "3. НЕ давайте права на вывод средств!\n"
                "4. Отправьте мне API Key\n\n"
                "Отправьте ваш API Key:"
            )

        await callback.message.edit_text(
            text,
            reply_markup=get_back_button("settings")
        )

        if not user.has_api_keys:
            # Start API setup flow
            await callback.answer()
            # Don't set state here, wait for user to send API key

    except Exception as e:
        logger.error(f"Error showing API settings: {e}")
        await callback.answer("Ошибка при загрузке настроек")


@router.message(F.text, APISetupStates.waiting_for_api_key)
async def process_api_key(message: Message, state: FSMContext, db: AsyncSession):
    """Process API key input."""
    try:
        api_key = message.text.strip()

        # Validate format (basic check)
        if len(api_key) < 20:
            await message.answer(
                "❌ API key слишком короткий. Пожалуйста, отправьте корректный API key."
            )
            return

        # Save to state
        await state.update_data(api_key=api_key)
        await state.set_state(APISetupStates.waiting_for_api_secret)

        await message.answer(
            "✅ API Key сохранен\n\n"
            "Теперь отправьте API Secret:"
        )

    except Exception as e:
        logger.error(f"Error processing API key: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")


@router.message(F.text, APISetupStates.waiting_for_api_secret)
async def process_api_secret(message: Message, state: FSMContext, db: AsyncSession):
    """Process API secret and verify credentials."""
    try:
        api_secret = message.text.strip()

        # Validate format
        if len(api_secret) < 20:
            await message.answer(
                "❌ API secret слишком короткий. Пожалуйста, отправьте корректный API secret."
            )
            return

        # Get API key from state
        data = await state.get_data()
        api_key = data.get('api_key')

        if not api_key:
            await message.answer(
                "❌ Произошла ошибка. Начните заново:\n"
                "Отправьте API Key:"
            )
            await state.set_state(APISetupStates.waiting_for_api_key)
            return

        # Test credentials with MEXC
        status_msg = await message.answer("⏳ Проверяю ключи...")

        mexc_service = MEXCService(db)
        is_valid, error = await mexc_service.test_api_keys(api_key, api_secret)

        if not is_valid:
            await status_msg.edit_text(
                f"❌ API ключи недействительны\n\n"
                f"Ошибка: {error}\n\n"
                f"Пожалуйста, проверьте ключи и попробуйте снова.\n"
                f"Отправьте API Key:"
            )
            await state.set_state(APISetupStates.waiting_for_api_key)
            return

        # Encrypt and save to database
        security = SecurityManager()
        encrypted_key = security.encrypt(api_key)
        encrypted_secret = security.encrypt(api_secret)

        # Get user
        result = await db.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
        user = result.scalar_one_or_none()

        if not user:
            await status_msg.edit_text("❌ Пользователь не найден. Отправьте /start")
            await state.clear()
            return

        # Update user with encrypted keys
        user.api_key = encrypted_key
        user.api_secret = encrypted_secret
        await db.commit()

        await status_msg.edit_text(
            "✅ API ключи успешно сохранены и проверены!\n\n"
            "Теперь вы можете создавать Grid ботов.\n\n"
            "Используйте кнопку '➕ Создать Grid бота' в главном меню.",
            reply_markup=get_back_button("main_menu")
        )

        await state.clear()
        logger.info(f"User {user.telegram_id} configured API keys")

    except Exception as e:
        logger.error(f"Error processing API secret: {e}", exc_info=True)
        await message.answer(
            "❌ Произошла ошибка при сохранении ключей.\n"
            "Попробуйте позже или обратитесь в поддержку."
        )
        await state.clear()


# Handler to start API setup flow when user sends message after clicking settings_api
@router.message(F.text)
async def handle_api_key_input(message: Message, state: FSMContext, db: AsyncSession):
    """Handle API key input when no state is set."""
    current_state = await state.get_state()

    # Only process if we're not in any other state
    if current_state is None:
        # Check if user recently clicked on API settings
        result = await db.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
        user = result.scalar_one_or_none()

        if user and not user.has_api_keys:
            # Assume this is API key input
            await state.set_state(APISetupStates.waiting_for_api_key)
            await process_api_key(message, state, db)
