"""FSM States for bot dialogs."""
from aiogram.fsm.state import State, StatesGroup


class SetupAPI(StatesGroup):
    """States for API setup flow."""
    waiting_for_api_key = State()
    waiting_for_api_secret = State()


class CreateGridBot(StatesGroup):
    """States for creating grid bot flow."""
    # Основное меню настройки
    configuring = State()

    # Настройка торговой пары
    waiting_for_pair = State()
    waiting_for_custom_pair = State()

    # Настройка спреда
    waiting_for_spread = State()

    # Настройка шага между уровнями
    waiting_for_increment = State()

    # Настройка количества buy ордеров
    waiting_for_buy_orders = State()

    # Настройка количества sell ордеров
    waiting_for_sell_orders = State()

    # Настройка начальной цены
    waiting_for_starting_price = State()

    # Настройка размера ордера
    waiting_for_order_size = State()

    # Подтверждение
    confirmation = State()
