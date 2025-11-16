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
            InlineKeyboardButton(text="5", callback_data="levels:5"),
            InlineKeyboardButton(text="10", callback_data="levels:10"),
            InlineKeyboardButton(text="15", callback_data="levels:15"),
            InlineKeyboardButton(text="20", callback_data="levels:20")
        ],
        [
            InlineKeyboardButton(text="✏️ Своё число", callback_data="levels:custom")
        ],
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
        ]
    ])


def get_investment_keyboard(available_balance: float) -> InlineKeyboardMarkup:
    """Get investment amount selection keyboard."""
    suggestions = [
        min(100, available_balance),
        min(500, available_balance),
        min(1000, available_balance)
    ]

    buttons = []
    for amount in suggestions:
        if amount > 0:
            buttons.append(
                InlineKeyboardButton(
                    text=f"${amount:.0f}",
                    callback_data=f"investment:{amount}"
                )
            )

    return InlineKeyboardMarkup(inline_keyboard=[
        buttons,
        [
            InlineKeyboardButton(text="✏️ Своя сумма", callback_data="investment:custom"),
            InlineKeyboardButton(text="💯 Всё", callback_data=f"investment:{available_balance}")
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
