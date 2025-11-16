"""Grid bot creation handler."""
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from src.models.user import User
from src.services.mexc_service import MEXCService
from src.services.grid_strategy import GridStrategy
from src.services.bot_manager import BotManager
from src.bot.keyboards.inline import (
    get_trading_pairs_keyboard,
    get_price_suggestions_keyboard,
    get_grid_levels_keyboard,
    get_investment_keyboard,
    get_confirmation_keyboard,
    get_back_button
)

logger = logging.getLogger(__name__)

router = Router()


class CreateBotStates(StatesGroup):
    """States for bot creation flow."""
    waiting_for_pair = State()
    waiting_for_custom_pair = State()
    waiting_for_lower_price = State()
    waiting_for_custom_lower_price = State()
    waiting_for_upper_price = State()
    waiting_for_custom_upper_price = State()
    waiting_for_grid_levels = State()
    waiting_for_custom_grid_levels = State()
    waiting_for_investment = State()
    waiting_for_custom_investment = State()
    confirmation = State()


@router.callback_query(F.data == "create_grid_bot")
async def start_bot_creation(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    """Start grid bot creation flow."""
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
                "❌ Для создания бота необходимо настроить API ключи\n\n"
                "Перейдите в ⚙️ Настройки → 🔑 API ключи",
                reply_markup=get_back_button("main_menu")
            )
            await callback.answer()
            return

        # Start creation flow
        await state.set_state(CreateBotStates.waiting_for_pair)

        text = (
            "➕ Создание Grid бота\n\n"
            "Шаг 1/5: Выберите торговую пару\n\n"
            "Grid торговля работает лучше всего на волатильных парах с хорошей ликвидностью."
        )

        await callback.message.edit_text(
            text,
            reply_markup=get_trading_pairs_keyboard()
        )
        await callback.answer()

    except Exception as e:
        logger.error(f"Error starting bot creation: {e}", exc_info=True)
        await callback.answer("Ошибка при создании бота")


@router.callback_query(F.data.startswith("pair:"), CreateBotStates.waiting_for_pair)
async def process_pair_selection(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    """Process trading pair selection."""
    try:
        pair_value = callback.data.split(":")[1]

        if pair_value == "custom":
            await callback.message.edit_text(
                "✏️ Введите торговую пару\n\n"
                "Формат: BTC/USDT\n"
                "Убедитесь, что пара существует на MEXC.",
                reply_markup=get_back_button("cancel")
            )
            await state.set_state(CreateBotStates.waiting_for_custom_pair)
            await callback.answer()
            return

        # Validate pair and get current price
        # Keep the slash format for CCXT API (BTC/USDT)
        symbol = pair_value

        mexc_service = MEXCService(db)
        current_price = await mexc_service.get_current_price(symbol)

        if current_price is None:
            await callback.answer("❌ Не удалось получить цену для этой пары")
            return

        # Save to state
        await state.update_data(
            symbol=symbol,
            display_symbol=pair_value,
            current_price=current_price
        )

        # Move to lower price selection
        await state.set_state(CreateBotStates.waiting_for_lower_price)

        text = (
            f"✅ Пара: {pair_value}\n"
            f"💰 Текущая цена: ${current_price:,.2f}\n\n"
            f"Шаг 2/5: Установите нижнюю границу диапазона\n\n"
            f"Выберите минимальную цену для вашей Grid сетки.\n"
            f"Рекомендуется установить на 3-10% ниже текущей цены."
        )

        await callback.message.edit_text(
            text,
            reply_markup=get_price_suggestions_keyboard(current_price, is_lower=True)
        )
        await callback.answer()

    except Exception as e:
        logger.error(f"Error processing pair: {e}", exc_info=True)
        await callback.answer("Ошибка при выборе пары")


@router.message(F.text, CreateBotStates.waiting_for_custom_pair)
async def process_custom_pair(message: Message, state: FSMContext, db: AsyncSession):
    """Process custom trading pair input."""
    try:
        pair = message.text.strip().upper()

        # Basic validation
        if '/' not in pair:
            await message.answer(
                "❌ Неверный формат. Используйте формат: BTC/USDT"
            )
            return

        # Keep slash format for CCXT API
        symbol = pair

        # Validate with MEXC
        mexc_service = MEXCService(db)
        current_price = await mexc_service.get_current_price(symbol)

        if current_price is None:
            await message.answer(
                f"❌ Пара {pair} не найдена на MEXC или недоступна.\n"
                f"Попробуйте другую пару."
            )
            return

        # Save and continue
        await state.update_data(
            symbol=symbol,
            display_symbol=pair,
            current_price=current_price
        )
        await state.set_state(CreateBotStates.waiting_for_lower_price)

        text = (
            f"✅ Пара: {pair}\n"
            f"💰 Текущая цена: ${current_price:,.2f}\n\n"
            f"Шаг 2/5: Установите нижнюю границу диапазона\n\n"
            f"Выберите минимальную цену для вашей Grid сетки."
        )

        await message.answer(
            text,
            reply_markup=get_price_suggestions_keyboard(current_price, is_lower=True)
        )

    except Exception as e:
        logger.error(f"Error processing custom pair: {e}", exc_info=True)
        await message.answer("Ошибка при проверке пары")


@router.callback_query(F.data.startswith("price:"), CreateBotStates.waiting_for_lower_price)
async def process_lower_price(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    """Process lower price selection."""
    try:
        price_value = callback.data.split(":")[1]

        if price_value == "custom":
            data = await state.get_data()
            await callback.message.edit_text(
                f"✏️ Введите нижнюю границу цены\n\n"
                f"Текущая цена: ${data['current_price']:,.2f}\n"
                f"Введите цену ниже текущей:",
                reply_markup=get_back_button("cancel")
            )
            await state.set_state(CreateBotStates.waiting_for_custom_lower_price)
            await callback.answer()
            return

        lower_price = float(price_value)
        data = await state.get_data()

        if lower_price >= data['current_price']:
            await callback.answer("❌ Нижняя граница должна быть ниже текущей цены")
            return

        await state.update_data(lower_price=lower_price)
        await state.set_state(CreateBotStates.waiting_for_upper_price)

        text = (
            f"✅ Нижняя граница: ${lower_price:,.2f}\n\n"
            f"Шаг 3/5: Установите верхнюю границу диапазона\n\n"
            f"Выберите максимальную цену для вашей Grid сетки.\n"
            f"Рекомендуется установить на 3-10% выше текущей цены."
        )

        await callback.message.edit_text(
            text,
            reply_markup=get_price_suggestions_keyboard(data['current_price'], is_lower=False)
        )
        await callback.answer()

    except Exception as e:
        logger.error(f"Error processing lower price: {e}", exc_info=True)
        await callback.answer("Ошибка при установке цены")


@router.message(F.text, CreateBotStates.waiting_for_custom_lower_price)
async def process_custom_lower_price(message: Message, state: FSMContext, db: AsyncSession):
    """Process custom lower price input."""
    try:
        try:
            lower_price = float(message.text.strip())
        except ValueError:
            await message.answer("❌ Введите корректное число")
            return

        data = await state.get_data()

        if lower_price <= 0:
            await message.answer("❌ Цена должна быть положительным числом")
            return

        if lower_price >= data['current_price']:
            await message.answer(
                f"❌ Нижняя граница (${lower_price:.2f}) должна быть ниже текущей цены (${data['current_price']:,.2f})"
            )
            return

        await state.update_data(lower_price=lower_price)
        await state.set_state(CreateBotStates.waiting_for_upper_price)

        text = (
            f"✅ Нижняя граница: ${lower_price:,.2f}\n\n"
            f"Шаг 3/5: Установите верхнюю границу диапазона"
        )

        await message.answer(
            text,
            reply_markup=get_price_suggestions_keyboard(data['current_price'], is_lower=False)
        )

    except Exception as e:
        logger.error(f"Error processing custom lower price: {e}", exc_info=True)
        await message.answer("Ошибка")


@router.callback_query(F.data.startswith("price:"), CreateBotStates.waiting_for_upper_price)
async def process_upper_price(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    """Process upper price selection."""
    try:
        price_value = callback.data.split(":")[1]

        if price_value == "custom":
            data = await state.get_data()
            await callback.message.edit_text(
                f"✏️ Введите верхнюю границу цены\n\n"
                f"Текущая цена: ${data['current_price']:,.2f}\n"
                f"Введите цену выше текущей:",
                reply_markup=get_back_button("cancel")
            )
            await state.set_state(CreateBotStates.waiting_for_custom_upper_price)
            await callback.answer()
            return

        upper_price = float(price_value)
        data = await state.get_data()

        if upper_price <= data['current_price']:
            await callback.answer("❌ Верхняя граница должна быть выше текущей цены")
            return

        if upper_price <= data['lower_price']:
            await callback.answer("❌ Верхняя граница должна быть выше нижней")
            return

        await state.update_data(upper_price=upper_price)
        await state.set_state(CreateBotStates.waiting_for_grid_levels)

        text = (
            f"✅ Диапазон: ${data['lower_price']:,.2f} - ${upper_price:,.2f}\n\n"
            f"Шаг 4/5: Выберите количество уровней Grid сетки\n\n"
            f"Больше уровней = больше ордеров = больше потенциальной прибыли, но меньше прибыли с каждого ордера.\n\n"
            f"Рекомендуется: 10-20 уровней"
        )

        await callback.message.edit_text(
            text,
            reply_markup=get_grid_levels_keyboard()
        )
        await callback.answer()

    except Exception as e:
        logger.error(f"Error processing upper price: {e}", exc_info=True)
        await callback.answer("Ошибка при установке цены")


@router.message(F.text, CreateBotStates.waiting_for_custom_upper_price)
async def process_custom_upper_price(message: Message, state: FSMContext, db: AsyncSession):
    """Process custom upper price input."""
    try:
        try:
            upper_price = float(message.text.strip())
        except ValueError:
            await message.answer("❌ Введите корректное число")
            return

        data = await state.get_data()

        if upper_price <= 0:
            await message.answer("❌ Цена должна быть положительным числом")
            return

        if upper_price <= data['current_price']:
            await message.answer(
                f"❌ Верхняя граница (${upper_price:.2f}) должна быть выше текущей цены (${data['current_price']:,.2f})"
            )
            return

        if upper_price <= data['lower_price']:
            await message.answer(
                f"❌ Верхняя граница (${upper_price:.2f}) должна быть выше нижней (${data['lower_price']:,.2f})"
            )
            return

        await state.update_data(upper_price=upper_price)
        await state.set_state(CreateBotStates.waiting_for_grid_levels)

        text = (
            f"✅ Диапазон: ${data['lower_price']:,.2f} - ${upper_price:,.2f}\n\n"
            f"Шаг 4/5: Выберите количество уровней Grid сетки"
        )

        await message.answer(
            text,
            reply_markup=get_grid_levels_keyboard()
        )

    except Exception as e:
        logger.error(f"Error processing custom upper price: {e}", exc_info=True)
        await message.answer("Ошибка")


@router.callback_query(F.data.startswith("levels:"), CreateBotStates.waiting_for_grid_levels)
async def process_grid_levels(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    """Process grid levels selection."""
    try:
        levels_value = callback.data.split(":")[1]

        if levels_value == "custom":
            await callback.message.edit_text(
                "✏️ Введите количество уровней Grid сетки\n\n"
                "Рекомендуется: 5-50 уровней\n"
                "Введите число:",
                reply_markup=get_back_button("cancel")
            )
            await state.set_state(CreateBotStates.waiting_for_custom_grid_levels)
            await callback.answer()
            return

        grid_levels = int(levels_value)

        if grid_levels < 2 or grid_levels > 100:
            await callback.answer("❌ Количество уровней должно быть от 2 до 100")
            return

        if grid_levels % 2 != 0:
            await callback.answer("❌ Количество уровней должно быть четным числом")
            return

        await state.update_data(grid_levels=grid_levels)

        # Get user and balance
        result = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = result.scalar_one_or_none()

        mexc_service = MEXCService(db)
        balances = await mexc_service.get_balance(user.id)
        usdt_balance = balances.get('USDT', 0)

        await state.set_state(CreateBotStates.waiting_for_investment)

        data = await state.get_data()
        text = (
            f"✅ Уровней сетки: {grid_levels} ({grid_levels//2} buy + {grid_levels//2} sell)\n\n"
            f"Шаг 5/5: Укажите сумму одного ордера (USDT)\n\n"
            f"💼 Доступно: ${usdt_balance:.2f} USDT\n\n"
            f"Каждый ордер (buy и sell) будет на эту сумму.\n"
            f"Всего потребуется: ~${grid_levels * 10:.0f} USDT для {grid_levels} ордеров по $10"
        )

        await callback.message.edit_text(
            text,
            reply_markup=get_investment_keyboard(usdt_balance)
        )
        await callback.answer()

    except Exception as e:
        logger.error(f"Error processing grid levels: {e}", exc_info=True)
        await callback.answer("Ошибка при выборе уровней")


@router.message(F.text, CreateBotStates.waiting_for_custom_grid_levels)
async def process_custom_grid_levels(message: Message, state: FSMContext, db: AsyncSession):
    """Process custom grid levels input."""
    try:
        try:
            grid_levels = int(message.text.strip())
        except ValueError:
            await message.answer("❌ Введите целое число")
            return

        if grid_levels < 2:
            await message.answer("❌ Минимальное количество уровней: 2")
            return

        if grid_levels > 100:
            await message.answer("❌ Максимальное количество уровней: 100")
            return

        if grid_levels % 2 != 0:
            await message.answer("❌ Количество уровней должно быть четным числом (чтобы разделить поровну между buy и sell)")
            return

        await state.update_data(grid_levels=grid_levels)

        # Get balance
        result = await db.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
        user = result.scalar_one_or_none()

        mexc_service = MEXCService(db)
        balances = await mexc_service.get_balance(user.id)
        usdt_balance = balances.get('USDT', 0)

        await state.set_state(CreateBotStates.waiting_for_investment)

        text = (
            f"✅ Уровней сетки: {grid_levels} ({grid_levels//2} buy + {grid_levels//2} sell)\n\n"
            f"Шаг 5/5: Укажите сумму одного ордера (USDT)\n\n"
            f"💼 Доступно: ${usdt_balance:.2f} USDT\n\n"
            f"Каждый ордер будет на эту сумму.\n"
            f"Всего потребуется: ~${grid_levels * 10:.0f} USDT для {grid_levels} ордеров по $10"
        )

        await message.answer(
            text,
            reply_markup=get_investment_keyboard(usdt_balance)
        )

    except Exception as e:
        logger.error(f"Error processing custom grid levels: {e}", exc_info=True)
        await message.answer("Ошибка")


@router.callback_query(F.data.startswith("investment:"), CreateBotStates.waiting_for_investment)
async def process_investment(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    """Process investment amount selection."""
    try:
        investment_value = callback.data.split(":")[1]

        if investment_value == "custom":
            await callback.message.edit_text(
                "✏️ Введите сумму инвестиции (USDT)\n\n"
                "Введите сумму в USDT:",
                reply_markup=get_back_button("cancel")
            )
            await state.set_state(CreateBotStates.waiting_for_custom_investment)
            await callback.answer()
            return

        investment = float(investment_value)

        # Get user balance
        result = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = result.scalar_one_or_none()

        mexc_service = MEXCService(db)
        balances = await mexc_service.get_balance(user.id)
        usdt_balance = balances.get('USDT', 0)

        if investment > usdt_balance:
            await callback.answer(f"❌ Недостаточно средств. Доступно: ${usdt_balance:.2f}")
            return

        if investment < 10:
            await callback.answer("❌ Минимальная инвестиция: $10")
            return

        await state.update_data(investment_amount=investment)
        await state.set_state(CreateBotStates.confirmation)

        # Show confirmation
        data = await state.get_data()
        text = (
            "📋 Подтверждение создания бота\n\n"
            f"📈 Пара: {data['display_symbol']}\n"
            f"💰 Текущая цена: ${data['current_price']:,.2f}\n"
            f"📊 Диапазон: ${data['lower_price']:,.2f} - ${data['upper_price']:,.2f}\n"
            f"🔢 Уровней: {data['grid_levels']}\n"
            f"💵 Инвестиция: ${investment:.2f} USDT\n\n"
            f"⚠️ Убедитесь, что все параметры верны перед запуском."
        )

        await callback.message.edit_text(
            text,
            reply_markup=get_confirmation_keyboard(data)
        )
        await callback.answer()

    except Exception as e:
        logger.error(f"Error processing investment: {e}", exc_info=True)
        await callback.answer("Ошибка при установке суммы")


@router.message(F.text, CreateBotStates.waiting_for_custom_investment)
async def process_custom_investment(message: Message, state: FSMContext, db: AsyncSession):
    """Process custom investment amount input."""
    try:
        try:
            investment = float(message.text.strip())
        except ValueError:
            await message.answer("❌ Введите корректное число")
            return

        if investment < 10:
            await message.answer("❌ Минимальная инвестиция: $10")
            return

        # Get balance
        result = await db.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
        user = result.scalar_one_or_none()

        mexc_service = MEXCService(db)
        balances = await mexc_service.get_balance(user.id)
        usdt_balance = balances.get('USDT', 0)

        if investment > usdt_balance:
            await message.answer(
                f"❌ Недостаточно средств\n"
                f"Доступно: ${usdt_balance:.2f} USDT"
            )
            return

        await state.update_data(investment_amount=investment)
        await state.set_state(CreateBotStates.confirmation)

        # Show confirmation
        data = await state.get_data()
        text = (
            "📋 Подтверждение создания бота\n\n"
            f"📈 Пара: {data['display_symbol']}\n"
            f"💰 Текущая цена: ${data['current_price']:,.2f}\n"
            f"📊 Диапазон: ${data['lower_price']:,.2f} - ${data['upper_price']:,.2f}\n"
            f"🔢 Уровней: {data['grid_levels']}\n"
            f"💵 Инвестиция: ${investment:.2f} USDT\n\n"
            f"⚠️ Убедитесь, что все параметры верны перед запуском."
        )

        await message.answer(
            text,
            reply_markup=get_confirmation_keyboard(data)
        )

    except Exception as e:
        logger.error(f"Error processing custom investment: {e}", exc_info=True)
        await message.answer("Ошибка")


@router.callback_query(F.data == "confirm:start", CreateBotStates.confirmation)
async def confirm_and_start_bot(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    """Confirm and start the grid bot."""
    try:
        data = await state.get_data()

        # Get user
        result = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = result.scalar_one_or_none()

        if not user:
            await callback.answer("Пользователь не найден")
            await state.clear()
            return

        # Answer callback immediately to avoid timeout
        await callback.answer()

        # Show progress
        await callback.message.edit_text(
            "⏳ Создаю бота и размещаю ордера...\n"
            "Это может занять несколько секунд.",
            reply_markup=None
        )

        # Initialize services
        mexc_service = MEXCService(db)
        grid_strategy = GridStrategy(db, mexc_service)
        bot_manager = BotManager(db, mexc_service, grid_strategy)

        # Create bot
        grid_bot = await bot_manager.create_bot(
            user_id=user.id,
            symbol=data['symbol'],
            lower_price=data['lower_price'],
            upper_price=data['upper_price'],
            grid_levels=data['grid_levels'],
            investment_amount=data['investment_amount']
        )

        if grid_bot:
            await callback.message.edit_text(
                "✅ Grid бот успешно создан и запущен!\n\n"
                f"🤖 Бот #{grid_bot.id}\n"
                f"📈 {data['display_symbol']}\n"
                f"💰 Инвестиция: ${data['investment_amount']:.2f}\n"
                f"🔢 Уровней сетки: {data['grid_levels']}\n\n"
                f"📊 Режим: Neutral Grid\n"
                f"• Buy ордера размещены ниже текущей цены\n"
                f"• Sell ордера размещены выше текущей цены\n\n"
                f"💡 Бот начнет зарабатывать когда цена будет двигаться в диапазоне сетки.\n\n"
                f"Просмотреть статус: 📊 Мои боты",
                reply_markup=get_back_button("main_menu")
            )
            logger.info(f"User {user.telegram_id} created bot {grid_bot.id}")
        else:
            await callback.message.edit_text(
                "❌ Ошибка при создании бота\n\n"
                "Возможные причины:\n"
                "• Недостаточно средств\n"
                "• Проблемы с API ключами\n"
                "• Технические проблемы MEXC\n\n"
                "Попробуйте позже или обратитесь в поддержку.",
                reply_markup=get_back_button("main_menu")
            )

        await state.clear()

    except Exception as e:
        logger.error(f"Error creating bot: {e}", exc_info=True)
        try:
            await callback.message.edit_text(
                "❌ Произошла ошибка при создании бота\n\n"
                "Попробуйте позже.",
                reply_markup=get_back_button("main_menu")
            )
        except Exception:
            # If edit fails, send new message
            await callback.message.answer(
                "❌ Произошла ошибка при создании бота\n\n"
                "Попробуйте позже.",
                reply_markup=get_back_button("main_menu")
            )
        await state.clear()


@router.callback_query(F.data == "confirm:edit", CreateBotStates.confirmation)
async def edit_bot_config(callback: CallbackQuery, state: FSMContext):
    """Allow user to edit bot configuration."""
    await callback.answer("Функция редактирования будет добавлена позже. Пока отмените и создайте заново.")
