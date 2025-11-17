"""Grid bot creation handler with flat grid configuration."""
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from decimal import Decimal
import logging

from src.models.user import User
from src.services.mexc_service import MEXCService
from src.services.grid_strategy import GridStrategy
from src.services.bot_manager import BotManager
from src.bot.states import CreateGridBot
from src.bot.keyboards.inline import (
    get_grid_config_keyboard,
    get_trading_pairs_keyboard,
    get_back_button
)
from src.utils.helpers import split_symbol

logger = logging.getLogger(__name__)

router = Router()


# Helper functions
def get_quote_currency(symbol: str) -> str:
    """Extract quote currency from trading pair (e.g., BTC/USDT -> USDT)."""
    try:
        _, quote = split_symbol(symbol)
        return quote
    except:
        return 'USDT'  # Default fallback


def format_currency(value: float, currency: str) -> str:
    """Format currency value based on currency type."""
    stablecoins = ['USDT', 'USDC', 'BUSD', 'DAI', 'TUSD', 'USDD', 'FDUSD']

    if currency in stablecoins:
        # Stablecoins: 2 decimals
        return f"{value:,.2f}"
    else:
        # Crypto: up to 8 decimals, trim trailing zeros
        formatted = f"{value:.8f}".rstrip('0').rstrip('.')
        return formatted


# Инструкции для каждого параметра
INSTRUCTIONS = {
    "pair": (
        "📈 <b>Торговая пара</b>\n\n"
        "Выберите криптовалютную пару для торговли.\n"
        "Рекомендуются волатильные пары с хорошей ликвидностью.\n\n"
        "Пример: BTC/USDT"
    ),
    "flat_spread": (
        "💰 <b>Спред между Buy и Sell ордерами</b>\n\n"
        "Это разница в цене между buy и sell ордерами на одном уровне.\n"
        "Определяет вашу минимальную прибыль с одного цикла.\n\n"
        "Пример: при спреде $2000:\n"
        "• Buy ордер на $98,000\n"
        "• Sell ордер на $100,000\n\n"
        "Рекомендация: 1-3% от текущей цены\n\n"
        "Введите спред в долларах (например: 2000):"
    ),
    "flat_increment": (
        "📊 <b>Шаг между уровнями сетки</b>\n\n"
        "Расстояние между соседними ордерами.\n"
        "Чем меньше шаг, тем плотнее сетка.\n\n"
        "Пример: при шаге $1000:\n"
        "• Buy 1 на $98,000\n"
        "• Buy 2 на $97,000\n"
        "• Buy 3 на $96,000\n\n"
        "Рекомендация: 0.5-2% от текущей цены\n\n"
        "Введите шаг в долларах (например: 1000):"
    ),
    "buy_orders_count": (
        "🟢 <b>Количество Buy ордеров</b>\n\n"
        "Сколько ордеров на покупку разместить ниже начальной цены.\n"
        "Больше ордеров = больше покрытие диапазона.\n\n"
        "Рекомендация: 10-30 ордеров\n\n"
        "Введите количество buy ордеров (например: 25):"
    ),
    "sell_orders_count": (
        "🔴 <b>Количество Sell ордеров</b>\n\n"
        "Сколько ордеров на продажу разместить выше начальной цены.\n"
        "Обычно равно количеству buy ордеров.\n\n"
        "Рекомендация: 10-30 ордеров\n\n"
        "Введите количество sell ордеров (например: 25):"
    ),
    "starting_price": (
        "🎯 <b>Начальная цена</b>\n\n"
        "Центральная точка вашей сетки.\n"
        "От неё будут размещаться buy ордера (ниже) и sell ордера (выше).\n\n"
        "• Введите 0 для использования текущей рыночной цены\n"
        "• Или введите конкретную цену\n\n"
        "Рекомендация: используйте текущую рыночную (0)\n\n"
        "Введите начальную цену (например: 0 или 95000):"
    ),
    "order_size": (
        "💵 <b>Размер одного ордера</b>\n\n"
        "Сумма в котируемой валюте для каждого ордера (buy и sell).\n"
        "Все ордера будут одинакового размера.\n\n"
        "Минимум: обычно $5-10 в зависимости от биржи\n"
        "Рекомендация: $10-50 для начала\n\n"
        "Введите размер ордера (например: 10):"
    )
}


@router.callback_query(F.data == "create_grid_bot")
async def start_bot_creation(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    """Start grid bot creation with configuration menu."""
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

        # Initialize empty configuration
        await state.update_data(
            pair=None,
            flat_spread=None,
            flat_increment=None,
            buy_orders_count=None,
            sell_orders_count=None,
            starting_price=None,
            order_size=None
        )
        await state.set_state(CreateGridBot.configuring)

        # Show configuration menu with instructions
        text = (
            "➕ <b>Создание Grid бота</b>\n\n"
            "Настройте параметры бота для торговли.\n"
            "Нажимайте на кнопки ниже для настройки каждого параметра.\n\n"
            "ℹ️ <b>Как работает Grid бот:</b>\n"
            "• Размещает сетку buy и sell ордеров\n"
            "• Покупает при падении, продаёт при росте\n"
            "• Зарабатывает на колебаниях цены\n\n"
            "⚠️ <b>Важно:</b> Настройте все параметры перед созданием!"
        )

        data = await state.get_data()
        await callback.message.edit_text(
            text,
            reply_markup=get_grid_config_keyboard(data),
            parse_mode="HTML"
        )
        await callback.answer()

    except Exception as e:
        logger.error(f"Error starting bot creation: {e}", exc_info=True)
        await callback.answer("Ошибка при создании бота")


# === НАСТРОЙКА ТОРГОВОЙ ПАРЫ ===

@router.callback_query(F.data == "config:pair", CreateGridBot.configuring)
async def config_pair(callback: CallbackQuery, state: FSMContext):
    """Configure trading pair."""
    await callback.message.edit_text(
        INSTRUCTIONS["pair"],
        reply_markup=get_trading_pairs_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(CreateGridBot.waiting_for_pair)
    await callback.answer()


@router.callback_query(F.data.startswith("pair:"), CreateGridBot.waiting_for_pair)
async def process_pair_selection(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    """Process trading pair selection."""
    try:
        pair_value = callback.data.split(":")[1]

        if pair_value == "custom":
            await callback.message.edit_text(
                "✏️ Введите торговую пару\n\n"
                "Формат: BTC/USDT\n"
                "Убедитесь, что пара существует на MEXC.",
                reply_markup=get_back_button("back_to_config")
            )
            await state.set_state(CreateGridBot.waiting_for_custom_pair)
            await callback.answer()
            return

        # Validate pair with MEXC
        mexc_service = MEXCService(db)
        current_price = await mexc_service.get_current_price(pair_value)

        if current_price is None:
            await callback.answer("❌ Не удалось получить цену для этой пары")
            return

        # Save to state
        await state.update_data(
            pair=pair_value,
            current_price=float(current_price)
        )

        # Return to config menu
        await state.set_state(CreateGridBot.configuring)
        data = await state.get_data()

        text = (
            "➕ <b>Создание Grid бота</b>\n\n"
            f"✅ Пара: {pair_value}\n"
            f"💰 Текущая цена: ${current_price:,.2f}\n\n"
            "Продолжайте настройку параметров:"
        )

        await callback.message.edit_text(
            text,
            reply_markup=get_grid_config_keyboard(data),
            parse_mode="HTML"
        )
        await callback.answer()

    except Exception as e:
        logger.error(f"Error processing pair: {e}", exc_info=True)
        await callback.answer("Ошибка при выборе пары")


@router.message(F.text, CreateGridBot.waiting_for_custom_pair)
async def process_custom_pair(message: Message, state: FSMContext, db: AsyncSession):
    """Process custom trading pair input."""
    try:
        pair = message.text.strip().upper()

        if '/' not in pair:
            await message.answer("❌ Неверный формат. Используйте формат: BTC/USDT")
            return

        # Validate with MEXC
        mexc_service = MEXCService(db)
        current_price = await mexc_service.get_current_price(pair)

        if current_price is None:
            await message.answer(
                f"❌ Пара {pair} не найдена на MEXC или недоступна.\n"
                f"Попробуйте другую пару."
            )
            return

        # Save and return to config
        await state.update_data(
            pair=pair,
            current_price=float(current_price)
        )
        await state.set_state(CreateGridBot.configuring)

        data = await state.get_data()
        text = (
            "➕ <b>Создание Grid бота</b>\n\n"
            f"✅ Пара: {pair}\n"
            f"💰 Текущая цена: ${current_price:,.2f}\n\n"
            "Продолжайте настройку параметров:"
        )

        await message.answer(
            text,
            reply_markup=get_grid_config_keyboard(data),
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Error processing custom pair: {e}", exc_info=True)
        await message.answer("Ошибка при проверке пары")


# === НАСТРОЙКА СПРЕДА ===

@router.callback_query(F.data == "config:spread", CreateGridBot.configuring)
async def config_spread(callback: CallbackQuery, state: FSMContext):
    """Configure flat spread."""
    data = await state.get_data()
    current_price = data.get("current_price", 0)
    pair = data.get("pair", "")

    # Get quote currency for formatting
    quote_currency = get_quote_currency(pair) if pair else 'USDT'

    # Use dynamic example if pair is selected
    if current_price > 0 and pair:
        recommended = current_price * 0.02  # 2% от текущей цены
        buy_price = current_price - recommended
        sell_price = current_price + recommended

        text = (
            "💰 <b>Спред между Buy и Sell ордерами</b>\n\n"
            "Это разница в цене между buy и sell ордерами на одном уровне.\n"
            "Определяет вашу минимальную прибыль с одного цикла.\n\n"
            f"Пример для {pair}:\n"
            f"• Текущая цена: ${format_currency(current_price, quote_currency)}\n"
            f"• При спреде ${format_currency(recommended, quote_currency)}:\n"
            f"  - Buy ордер на ${format_currency(buy_price, quote_currency)}\n"
            f"  - Sell ордер на ${format_currency(sell_price, quote_currency)}\n\n"
            "Рекомендация: 1-3% от текущей цены\n\n"
            "Введите спред в долларах:"
        )
    else:
        text = INSTRUCTIONS["flat_spread"]

    await callback.message.edit_text(
        text,
        reply_markup=get_back_button("back_to_config"),
        parse_mode="HTML"
    )
    await state.set_state(CreateGridBot.waiting_for_spread)
    await callback.answer()


@router.message(F.text, CreateGridBot.waiting_for_spread)
async def process_spread(message: Message, state: FSMContext):
    """Process spread input."""
    try:
        spread = float(message.text.strip())

        if spread <= 0:
            await message.answer("❌ Спред должен быть положительным числом")
            return

        # Save and return to config
        await state.update_data(flat_spread=spread)
        await state.set_state(CreateGridBot.configuring)

        data = await state.get_data()
        text = (
            "➕ <b>Создание Grid бота</b>\n\n"
            f"✅ Спред установлен: ${spread:,.0f}\n\n"
            "Продолжайте настройку параметров:"
        )

        await message.answer(
            text,
            reply_markup=get_grid_config_keyboard(data),
            parse_mode="HTML"
        )

    except ValueError:
        await message.answer("❌ Введите корректное число")
    except Exception as e:
        logger.error(f"Error processing spread: {e}", exc_info=True)
        await message.answer("Ошибка")


# === НАСТРОЙКА ШАГА СЕТКИ ===

@router.callback_query(F.data == "config:increment", CreateGridBot.configuring)
async def config_increment(callback: CallbackQuery, state: FSMContext):
    """Configure flat increment."""
    data = await state.get_data()
    current_price = data.get("current_price", 0)
    pair = data.get("pair", "")

    # Get quote currency for formatting
    quote_currency = get_quote_currency(pair) if pair else 'USDT'

    # Use dynamic example if pair is selected
    if current_price > 0 and pair:
        recommended = current_price * 0.01  # 1% от текущей цены
        buy1 = current_price - recommended
        buy2 = current_price - (recommended * 2)
        buy3 = current_price - (recommended * 3)

        text = (
            "📊 <b>Шаг между уровнями сетки</b>\n\n"
            "Расстояние между соседними ордерами.\n"
            "Чем меньше шаг, тем плотнее сетка.\n\n"
            f"Пример для {pair}:\n"
            f"• Текущая цена: ${format_currency(current_price, quote_currency)}\n"
            f"• При шаге ${format_currency(recommended, quote_currency)}:\n"
            f"  - Buy 1 на ${format_currency(buy1, quote_currency)}\n"
            f"  - Buy 2 на ${format_currency(buy2, quote_currency)}\n"
            f"  - Buy 3 на ${format_currency(buy3, quote_currency)}\n\n"
            "Рекомендация: 0.5-2% от текущей цены\n\n"
            "Введите шаг в долларах:"
        )
    else:
        text = INSTRUCTIONS["flat_increment"]

    await callback.message.edit_text(
        text,
        reply_markup=get_back_button("back_to_config"),
        parse_mode="HTML"
    )
    await state.set_state(CreateGridBot.waiting_for_increment)
    await callback.answer()


@router.message(F.text, CreateGridBot.waiting_for_increment)
async def process_increment(message: Message, state: FSMContext):
    """Process increment input."""
    try:
        increment = float(message.text.strip())

        if increment <= 0:
            await message.answer("❌ Шаг должен быть положительным числом")
            return

        # Save and return to config
        await state.update_data(flat_increment=increment)
        await state.set_state(CreateGridBot.configuring)

        data = await state.get_data()
        text = (
            "➕ <b>Создание Grid бота</b>\n\n"
            f"✅ Шаг сетки установлен: ${increment:,.0f}\n\n"
            "Продолжайте настройку параметров:"
        )

        await message.answer(
            text,
            reply_markup=get_grid_config_keyboard(data),
            parse_mode="HTML"
        )

    except ValueError:
        await message.answer("❌ Введите корректное число")
    except Exception as e:
        logger.error(f"Error processing increment: {e}", exc_info=True)
        await message.answer("Ошибка")


# === НАСТРОЙКА КОЛИЧЕСТВА BUY ОРДЕРОВ ===

@router.callback_query(F.data == "config:buy_orders", CreateGridBot.configuring)
async def config_buy_orders(callback: CallbackQuery, state: FSMContext):
    """Configure buy orders count."""
    await callback.message.edit_text(
        INSTRUCTIONS["buy_orders_count"],
        reply_markup=get_back_button("back_to_config"),
        parse_mode="HTML"
    )
    await state.set_state(CreateGridBot.waiting_for_buy_orders)
    await callback.answer()


@router.message(F.text, CreateGridBot.waiting_for_buy_orders)
async def process_buy_orders(message: Message, state: FSMContext):
    """Process buy orders count input."""
    try:
        count = int(message.text.strip())

        if count < 1:
            await message.answer("❌ Количество ордеров должно быть минимум 1")
            return

        if count > 100:
            await message.answer("❌ Максимальное количество ордеров: 100")
            return

        # Save and return to config
        await state.update_data(buy_orders_count=count)
        await state.set_state(CreateGridBot.configuring)

        data = await state.get_data()
        text = (
            "➕ <b>Создание Grid бота</b>\n\n"
            f"✅ Количество buy ордеров: {count}\n\n"
            "Продолжайте настройку параметров:"
        )

        await message.answer(
            text,
            reply_markup=get_grid_config_keyboard(data),
            parse_mode="HTML"
        )

    except ValueError:
        await message.answer("❌ Введите целое число")
    except Exception as e:
        logger.error(f"Error processing buy orders count: {e}", exc_info=True)
        await message.answer("Ошибка")


# === НАСТРОЙКА КОЛИЧЕСТВА SELL ОРДЕРОВ ===

@router.callback_query(F.data == "config:sell_orders", CreateGridBot.configuring)
async def config_sell_orders(callback: CallbackQuery, state: FSMContext):
    """Configure sell orders count."""
    await callback.message.edit_text(
        INSTRUCTIONS["sell_orders_count"],
        reply_markup=get_back_button("back_to_config"),
        parse_mode="HTML"
    )
    await state.set_state(CreateGridBot.waiting_for_sell_orders)
    await callback.answer()


@router.message(F.text, CreateGridBot.waiting_for_sell_orders)
async def process_sell_orders(message: Message, state: FSMContext):
    """Process sell orders count input."""
    try:
        count = int(message.text.strip())

        if count < 1:
            await message.answer("❌ Количество ордеров должно быть минимум 1")
            return

        if count > 100:
            await message.answer("❌ Максимальное количество ордеров: 100")
            return

        # Save and return to config
        await state.update_data(sell_orders_count=count)
        await state.set_state(CreateGridBot.configuring)

        data = await state.get_data()
        text = (
            "➕ <b>Создание Grid бота</b>\n\n"
            f"✅ Количество sell ордеров: {count}\n\n"
            "Продолжайте настройку параметров:"
        )

        await message.answer(
            text,
            reply_markup=get_grid_config_keyboard(data),
            parse_mode="HTML"
        )

    except ValueError:
        await message.answer("❌ Введите целое число")
    except Exception as e:
        logger.error(f"Error processing sell orders count: {e}", exc_info=True)
        await message.answer("Ошибка")


# === НАСТРОЙКА НАЧАЛЬНОЙ ЦЕНЫ ===

@router.callback_query(F.data == "config:starting_price", CreateGridBot.configuring)
async def config_starting_price(callback: CallbackQuery, state: FSMContext):
    """Configure starting price."""
    data = await state.get_data()
    current_price = data.get("current_price", 0)

    text = INSTRUCTIONS["starting_price"]
    if current_price > 0:
        text += f"\n💡 Текущая рыночная цена: ${current_price:,.2f}"

    await callback.message.edit_text(
        text,
        reply_markup=get_back_button("back_to_config"),
        parse_mode="HTML"
    )
    await state.set_state(CreateGridBot.waiting_for_starting_price)
    await callback.answer()


@router.message(F.text, CreateGridBot.waiting_for_starting_price)
async def process_starting_price(message: Message, state: FSMContext):
    """Process starting price input."""
    try:
        price = float(message.text.strip())

        if price < 0:
            await message.answer("❌ Цена не может быть отрицательной")
            return

        # Save and return to config
        await state.update_data(starting_price=price)
        await state.set_state(CreateGridBot.configuring)

        data = await state.get_data()
        price_text = "Текущая рыночная" if price == 0 else f"${price:,.2f}"
        text = (
            "➕ <b>Создание Grid бота</b>\n\n"
            f"✅ Начальная цена: {price_text}\n\n"
            "Продолжайте настройку параметров:"
        )

        await message.answer(
            text,
            reply_markup=get_grid_config_keyboard(data),
            parse_mode="HTML"
        )

    except ValueError:
        await message.answer("❌ Введите корректное число")
    except Exception as e:
        logger.error(f"Error processing starting price: {e}", exc_info=True)
        await message.answer("Ошибка")


# === НАСТРОЙКА РАЗМЕРА ОРДЕРА ===

@router.callback_query(F.data == "config:order_size", CreateGridBot.configuring)
async def config_order_size(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    """Configure order size."""
    # Get user balance
    result = await db.execute(
        select(User).where(User.telegram_id == callback.from_user.id)
    )
    user = result.scalar_one_or_none()

    # Get quote currency from selected pair
    data = await state.get_data()
    quote_currency = 'USDT'  # Default
    if 'pair' in data:
        quote_currency = get_quote_currency(data['pair'])

    mexc_service = MEXCService(db)
    balances = await mexc_service.get_balance(user.id)
    balance = balances.get(quote_currency, 0)

    text = INSTRUCTIONS["order_size"]
    text += f"\n💼 Доступно на балансе: {format_currency(float(balance), quote_currency)} {quote_currency}"

    await callback.message.edit_text(
        text,
        reply_markup=get_back_button("back_to_config"),
        parse_mode="HTML"
    )
    await state.set_state(CreateGridBot.waiting_for_order_size)
    await callback.answer()


@router.message(F.text, CreateGridBot.waiting_for_order_size)
async def process_order_size(message: Message, state: FSMContext):
    """Process order size input."""
    try:
        size = float(message.text.strip())

        if size <= 0:
            await message.answer("❌ Размер ордера должен быть положительным числом")
            return

        if size < 5:
            await message.answer("❌ Минимальный размер ордера: $5")
            return

        # Save and return to config
        await state.update_data(order_size=size)
        await state.set_state(CreateGridBot.configuring)

        data = await state.get_data()
        text = (
            "➕ <b>Создание Grid бота</b>\n\n"
            f"✅ Размер ордера: ${size:,.2f}\n\n"
            "Продолжайте настройку параметров:"
        )

        await message.answer(
            text,
            reply_markup=get_grid_config_keyboard(data),
            parse_mode="HTML"
        )

    except ValueError:
        await message.answer("❌ Введите корректное число")
    except Exception as e:
        logger.error(f"Error processing order size: {e}", exc_info=True)
        await message.answer("Ошибка")


# === СОЗДАНИЕ БОТА ===

@router.callback_query(F.data == "config:create", CreateGridBot.configuring)
async def create_bot(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    """Create the bot after all parameters are configured."""
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

        # Calculate required balance
        buy_count = data["buy_orders_count"]
        sell_count = data["sell_orders_count"]
        order_size = data["order_size"]
        pair = data["pair"]

        # Extract quote currency from pair
        quote_currency = get_quote_currency(pair)

        # For flat grid:
        # - Need quote currency for buy orders: buy_count * order_size
        # - Need to buy base currency for sell orders: sell_count * order_size
        total_required = (buy_count + sell_count) * order_size

        # Check balance
        mexc_service = MEXCService(db)
        balances = await mexc_service.get_balance(user.id)
        quote_balance = balances.get(quote_currency, 0)

        # Show confirmation with balance check
        spread = data["flat_spread"]
        increment = data["flat_increment"]
        starting_price = data["starting_price"]
        current_price = data.get("current_price", 0)

        # Calculate price range
        if starting_price == 0:
            starting_price = current_price

        lowest_buy = starting_price - (increment * buy_count)
        highest_sell = starting_price + (increment * sell_count)

        text = (
            "📋 <b>Подтверждение создания бота</b>\n\n"
            f"📈 Пара: {pair}\n"
            f"💰 Текущая цена: ${format_currency(current_price, quote_currency)}\n"
            f"🎯 Начальная цена: ${format_currency(starting_price, quote_currency)}\n\n"
            f"📊 Параметры сетки:\n"
            f"• Спред: ${format_currency(spread, quote_currency)}\n"
            f"• Шаг сетки: ${format_currency(increment, quote_currency)}\n"
            f"• Buy ордеров: {buy_count} шт\n"
            f"• Sell ордеров: {sell_count} шт\n"
            f"• Размер ордера: ${format_currency(order_size, quote_currency)}\n\n"
            f"📉 Диапазон цен:\n"
            f"• Самый низкий buy: ${format_currency(lowest_buy, quote_currency)}\n"
            f"• Самый высокий sell: ${format_currency(highest_sell, quote_currency)}\n\n"
            f"💵 <b>Требуется средств:</b>\n"
            f"• Buy ордера: {buy_count} × ${format_currency(order_size, quote_currency)} = ${format_currency(buy_count * order_size, quote_currency)}\n"
            f"• Sell ордера: {sell_count} × ${format_currency(order_size, quote_currency)} = ${format_currency(sell_count * order_size, quote_currency)}\n"
            f"• <b>Всего: ${format_currency(total_required, quote_currency)} {quote_currency}</b>\n\n"
            f"💼 Доступно: ${format_currency(float(quote_balance), quote_currency)} {quote_currency}\n"
        )

        if quote_balance < total_required:
            text += (
                f"\n❌ <b>Недостаточно средств!</b>\n"
                f"Не хватает: ${format_currency(total_required - float(quote_balance), quote_currency)} {quote_currency}\n\n"
                f"Пополните баланс или уменьшите параметры бота."
            )
            await callback.message.edit_text(
                text,
                reply_markup=get_back_button("main_menu"),
                parse_mode="HTML"
            )
            await callback.answer()
            return

        text += "\n✅ Средств достаточно! Можно создавать бота."

        # Create inline keyboard with confirmation
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Подтвердить и создать", callback_data="confirm:create_flat")],
            [InlineKeyboardButton(text="◀️ Назад к настройкам", callback_data="confirm:back")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
        ])

        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        await state.set_state(CreateGridBot.confirmation)
        await callback.answer()

    except Exception as e:
        logger.error(f"Error in create_bot: {e}", exc_info=True)
        await callback.answer("Ошибка при проверке параметров")


@router.callback_query(F.data == "confirm:back", CreateGridBot.confirmation)
async def back_to_config(callback: CallbackQuery, state: FSMContext):
    """Return to configuration menu."""
    await state.set_state(CreateGridBot.configuring)
    data = await state.get_data()

    text = (
        "➕ <b>Создание Grid бота</b>\n\n"
        "Продолжайте настройку параметров:"
    )

    await callback.message.edit_text(
        text,
        reply_markup=get_grid_config_keyboard(data),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "confirm:create_flat", CreateGridBot.confirmation)
async def confirm_create_flat(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    """Confirm and create flat grid bot."""
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

        await callback.answer()

        # Show progress
        await callback.message.edit_text(
            "⏳ Создаю бота и размещаю ордера...\n"
            "Это может занять некоторое время.",
            parse_mode="HTML"
        )

        # Initialize services
        mexc_service = MEXCService(db)
        grid_strategy = GridStrategy(db, mexc_service)
        bot_manager = BotManager(db, mexc_service, grid_strategy)

        # Get current price if starting_price is 0
        starting_price = data["starting_price"]
        if starting_price == 0:
            current_price = await mexc_service.get_current_price(data["pair"])
            starting_price = float(current_price)

        # Create flat grid bot
        grid_bot = await bot_manager.create_flat_bot(
            user_id=user.id,
            symbol=data["pair"],
            flat_spread=Decimal(str(data["flat_spread"])),
            flat_increment=Decimal(str(data["flat_increment"])),
            buy_orders_count=data["buy_orders_count"],
            sell_orders_count=data["sell_orders_count"],
            starting_price=Decimal(str(starting_price)),
            order_size=Decimal(str(data["order_size"]))
        )

        if grid_bot:
            buy_count = data["buy_orders_count"]
            sell_count = data["sell_orders_count"]
            order_size = data["order_size"]
            total_invested = (buy_count + sell_count) * order_size

            await callback.message.edit_text(
                "✅ <b>Grid бот успешно создан и запущен!</b>\n\n"
                f"🤖 Бот #{grid_bot.id}\n"
                f"📈 {data['pair']}\n"
                f"💵 Размер ордера: ${order_size:,.2f}\n"
                f"🔢 Ордеров: {buy_count} buy + {sell_count} sell\n"
                f"💰 Всего задействовано: ${total_invested:,.2f}\n\n"
                f"📊 Режим: Flat Grid\n"
                f"• Спред: ${data['flat_spread']:,.0f}\n"
                f"• Шаг: ${data['flat_increment']:,.0f}\n\n"
                f"💡 Бот начнет зарабатывать на колебаниях цены.\n\n"
                f"Просмотреть статус: 📊 Мои боты",
                reply_markup=get_back_button("main_menu"),
                parse_mode="HTML"
            )
            logger.info(f"User {user.telegram_id} created flat grid bot {grid_bot.id}")
        else:
            await callback.message.edit_text(
                "❌ <b>Ошибка при создании бота</b>\n\n"
                "Возможные причины:\n"
                "• Недостаточно средств\n"
                "• Проблемы с API ключами\n"
                "• Технические проблемы MEXC\n\n"
                "Попробуйте позже или обратитесь в поддержку.",
                reply_markup=get_back_button("main_menu"),
                parse_mode="HTML"
            )

        await state.clear()

    except Exception as e:
        logger.error(f"Error creating flat grid bot: {e}", exc_info=True)
        try:
            await callback.message.edit_text(
                "❌ <b>Произошла ошибка при создании бота</b>\n\n"
                f"Ошибка: {str(e)}\n\n"
                "Попробуйте позже.",
                reply_markup=get_back_button("main_menu"),
                parse_mode="HTML"
            )
        except Exception:
            await callback.message.answer(
                "❌ <b>Произошла ошибка при создании бота</b>\n\n"
                "Попробуйте позже.",
                reply_markup=get_back_button("main_menu"),
                parse_mode="HTML"
            )
        await state.clear()


# === НАЗАД И ОТМЕНА ===

@router.callback_query(F.data == "back_to_config")
async def back_to_config_menu(callback: CallbackQuery, state: FSMContext):
    """Return to configuration menu without resetting settings."""
    # Get current config
    data = await state.get_data()

    # Return to configuring state
    await state.set_state(CreateGridBot.configuring)

    text = (
        "➕ <b>Создание Grid бота</b>\n\n"
        "Настройте параметры бота:"
    )

    await callback.message.edit_text(
        text,
        reply_markup=get_grid_config_keyboard(data),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cancel_creation(callback: CallbackQuery, state: FSMContext):
    """Cancel bot creation."""
    await state.clear()
    await callback.message.edit_text(
        "❌ Создание бота отменено.",
        reply_markup=get_back_button("main_menu")
    )
    await callback.answer()
