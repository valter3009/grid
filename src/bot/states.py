"""FSM States for bot dialogs."""
from aiogram.fsm.state import State, StatesGroup


class SetupAPI(StatesGroup):
    """States for API setup flow."""
    waiting_for_api_key = State()
    waiting_for_api_secret = State()


class CreateGridBot(StatesGroup):
    """States for creating grid bot flow."""
    waiting_for_pair = State()
    waiting_for_custom_pair = State()
    waiting_for_lower_price = State()
    waiting_for_upper_price = State()
    waiting_for_grid_levels = State()
    waiting_for_investment = State()
    confirmation = State()
