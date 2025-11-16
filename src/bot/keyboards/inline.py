"""Inline keyboards for Telegram bot."""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import List, Optional


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Get main menu keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Создать Grid бота", callback_data="create_grid_bot"),
            InlineKeyboardButton(text="📊 Мои боты", callback_data="my_bots")
        ],
        [
            InlineKeyboardButton(text="💼 Баланс", callback_data="balance"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")
        ],
        [
            InlineKeyboardButton(text="❓ Помощь", callback_data="help")
        ]
    ])


def get_trading_pairs_keyboard() -> InlineKeyboardMarkup:
    """Get trading pairs selection keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="BTC/USDT", callback_data="pair:BTC/USDT"),
            InlineKeyboardButton(text="ETH/USDT", callback_data="pair:ETH/USDT"),
            InlineKeyboardButton(text="BNB/USDT", callback_data="pair:BNB/USDT")
        ],
        [
            InlineKeyboardButton(text="SOL/USDT", callback_data="pair:SOL/USDT"),
            InlineKeyboardButton(text="XRP/USDT", callback_data="pair:XRP/USDT"),
            InlineKeyboardButton(text="ADA/USDT", callback_data="pair:ADA/USDT")
        ],
        [
            InlineKeyboardButton(text="🔍 Другая пара", callback_data="pair:custom")
        ],
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
        ]
    ])


def get_price_suggestions_keyboard(current_price: float, is_lower: bool = True) -> InlineKeyboardMarkup:
    """
    Get price suggestions keyboard.

    Args:
        current_price: Current market price
        is_lower: True for lower bound, False for upper bound
    """
    # Convert to float if Decimal
    price = float(current_price)

    if is_lower:
        # Suggest prices below current
        prices = [
            price * 0.90,
            price * 0.95,
            price * 0.97
        ]
    else:
        # Suggest prices above current
        prices = [
            price * 1.03,
            price * 1.05,
            price * 1.10
        ]

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"${price:,.2f}",
                callback_data=f"price:{price}"
            ) for price in prices
        ],
        [
            InlineKeyboardButton(text="✏️ Своя цена", callback_data="price:custom")
        ],
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
        ]
    ])


def get_grid_levels_keyboard() -> InlineKeyboardMarkup:
    """Get grid levels selection keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="6", callback_data="levels:6"),
            InlineKeyboardButton(text="10", callback_data="levels:10"),
            InlineKeyboardButton(text="16", callback_data="levels:16"),
            InlineKeyboardButton(text="20", callback_data="levels:20")
        ],
        [
            InlineKeyboardButton(text="✏️ Своё число (четное)", callback_data="levels:custom")
        ],
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
        ]
    ])


def get_investment_keyboard(available_balance: float) -> InlineKeyboardMarkup:
    """Get investment amount selection keyboard for order size."""
    # Suggest reasonable order sizes
    suggestions = [5, 10, 20, 50]

    buttons = []
    for amount in suggestions:
        buttons.append(
            InlineKeyboardButton(
                text=f"${amount}",
                callback_data=f"investment:{amount}"
            )
        )

    return InlineKeyboardMarkup(inline_keyboard=[
        buttons,
        [
            InlineKeyboardButton(text="✏️ Своя сумма", callback_data="investment:custom")
        ],
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
        ]
    ])


def get_confirmation_keyboard(grid_bot_data: dict) -> InlineKeyboardMarkup:
    """Get confirmation keyboard with bot details."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🚀 Запустить бота", callback_data="confirm:start")
        ],
        [
            InlineKeyboardButton(text="✏️ Изменить", callback_data="confirm:edit"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
        ]
    ])


def get_bot_details_keyboard(grid_bot_id: int, status: str) -> InlineKeyboardMarkup:
    """Get bot details keyboard with action buttons."""
    buttons = []

    if status == "active":
        buttons.append([
            InlineKeyboardButton(text="⏸ Пауза", callback_data=f"bot_pause:{grid_bot_id}"),
            InlineKeyboardButton(text="🛑 Остановить", callback_data=f"bot_stop:{grid_bot_id}")
        ])
    elif status == "paused":
        buttons.append([
            InlineKeyboardButton(text="▶️ Продолжить", callback_data=f"bot_resume:{grid_bot_id}"),
            InlineKeyboardButton(text="🛑 Остановить", callback_data=f"bot_stop:{grid_bot_id}")
        ])

    buttons.extend([
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data=f"bot_refresh:{grid_bot_id}")
        ],
        [
            InlineKeyboardButton(text="🗑 Удалить бота", callback_data=f"bot_delete:{grid_bot_id}")
        ],
        [
            InlineKeyboardButton(text="◀️ Назад", callback_data="my_bots")
        ]
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_my_bots_keyboard(bots: List[dict]) -> InlineKeyboardMarkup:
    """Get my bots list keyboard."""
    buttons = []

    for bot in bots[:10]:  # Limit to 10 bots
        status_emoji = {
            'active': '🟢',
            'paused': '🟡',
            'stopped': '🔴'
        }.get(bot['status'], '⚪')

        buttons.append([
            InlineKeyboardButton(
                text=f"{status_emoji} Bot #{bot['id']} - {bot['symbol']}",
                callback_data=f"bot_details:{bot['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(text="➕ Создать нового", callback_data="create_grid_bot")
    ])
    buttons.append([
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_stop_bot_keyboard(grid_bot_id: int) -> InlineKeyboardMarkup:
    """Get stop bot confirmation keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🛑 Остановить (сохранить активы)",
                callback_data=f"stop_confirm:{grid_bot_id}:keep"
            )
        ],
        [
            InlineKeyboardButton(
                text="💰 Остановить и продать всё",
                callback_data=f"stop_confirm:{grid_bot_id}:sell"
            )
        ],
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"bot_details:{grid_bot_id}")
        ]
    ])


def get_delete_bot_keyboard(grid_bot_id: int) -> InlineKeyboardMarkup:
    """Get delete bot confirmation keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🗑 Да, удалить бота",
                callback_data=f"delete_confirm:{grid_bot_id}"
            )
        ],
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"bot_details:{grid_bot_id}")
        ]
    ])


def get_settings_keyboard() -> InlineKeyboardMarkup:
    """Get settings keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔑 API ключи MEXC", callback_data="settings_api")
        ],
        [
            InlineKeyboardButton(text="🔔 Уведомления", callback_data="settings_notifications")
        ],
        [
            InlineKeyboardButton(text="🌐 Язык", callback_data="settings_language")
        ],
        [
            InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")
        ]
    ])


def get_back_button(callback_data: str = "main_menu") -> InlineKeyboardMarkup:
    """Get simple back button keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=callback_data)]
    ])


def get_grid_config_keyboard(config: dict) -> InlineKeyboardMarkup:
    """
    Get grid configuration keyboard with parameter indicators.

    Args:
        config: Dictionary with bot configuration
            - pair: Trading pair (e.g., "BTC/USDT")
            - flat_spread: Spread between buy and sell orders
            - flat_increment: Step between grid levels
            - buy_orders_count: Number of buy orders
            - sell_orders_count: Number of sell orders
            - starting_price: Starting price (0 = current market)
            - order_size: Size of each order in USDT

    Returns:
        InlineKeyboardMarkup with configuration buttons
    """
    # Helper to format parameter display
    def format_param(key, label, value, format_fn=None):
        if value is None:
            return f"⚪ {label}"
        formatted = format_fn(value) if format_fn else str(value)
        return f"✅ {label}: {formatted}"

    # Format each parameter
    pair_text = format_param(
        "pair", "Торговая пара", config.get("pair")
    )
    spread_text = format_param(
        "flat_spread", "Спред", config.get("flat_spread"),
        lambda x: f"${float(x):,.0f}"
    )
    increment_text = format_param(
        "flat_increment", "Шаг сетки", config.get("flat_increment"),
        lambda x: f"${float(x):,.0f}"
    )
    buy_orders_text = format_param(
        "buy_orders_count", "Buy ордеров", config.get("buy_orders_count"),
        lambda x: f"{int(x)} шт"
    )
    sell_orders_text = format_param(
        "sell_orders_count", "Sell ордеров", config.get("sell_orders_count"),
        lambda x: f"{int(x)} шт"
    )
    starting_price_text = format_param(
        "starting_price", "Начальная цена", config.get("starting_price"),
        lambda x: "Текущая рыночная" if float(x) == 0 else f"${float(x):,.2f}"
    )
    order_size_text = format_param(
        "order_size", "Размер ордера", config.get("order_size"),
        lambda x: f"${float(x):,.2f}"
    )

    # Check if all parameters are configured
    all_configured = all([
        config.get("pair"),
        config.get("flat_spread") is not None,
        config.get("flat_increment") is not None,
        config.get("buy_orders_count") is not None,
        config.get("sell_orders_count") is not None,
        config.get("starting_price") is not None,
        config.get("order_size") is not None,
    ])

    buttons = [
        [InlineKeyboardButton(text=pair_text, callback_data="config:pair")],
        [InlineKeyboardButton(text=spread_text, callback_data="config:spread")],
        [InlineKeyboardButton(text=increment_text, callback_data="config:increment")],
        [InlineKeyboardButton(text=buy_orders_text, callback_data="config:buy_orders")],
        [InlineKeyboardButton(text=sell_orders_text, callback_data="config:sell_orders")],
        [InlineKeyboardButton(text=starting_price_text, callback_data="config:starting_price")],
        [InlineKeyboardButton(text=order_size_text, callback_data="config:order_size")],
    ]

    # Add create button only if all configured
    if all_configured:
        buttons.append([
            InlineKeyboardButton(text="🚀 Создать бота", callback_data="config:create")
        ])

    # Add cancel button
    buttons.append([
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)
