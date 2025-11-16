"""Notification service for sending messages to users."""
from decimal import Decimal
from typing import Optional, Dict
from datetime import datetime, timedelta
import logging
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from src.utils.formatters import (
    format_price,
    format_amount,
    format_profit,
    format_percent,
    format_runtime,
    format_bot_status
)

logger = logging.getLogger(__name__)


class NotificationService:
    """Service for sending notifications to users."""

    def __init__(self, bot: Bot):
        """
        Initialize notification service.

        Args:
            bot: Aiogram Bot instance
        """
        self.bot = bot

    async def notify_order_filled(
        self,
        user_id: int,
        grid_bot_id: int,
        order: dict,
        new_order: Optional[dict] = None,
        profit: Optional[Decimal] = None
    ):
        """
        Notify user about filled order.

        Args:
            user_id: User Telegram ID
            grid_bot_id: Grid bot ID
            order: Filled order details
            new_order: New counter order (optional)
            profit: Profit from cycle (optional, for sell orders)
        """
        try:
            side = order['side']
            price = order['price']
            amount = order['amount']

            if side == 'buy':
                # Buy order filled template
                message = (
                    f"📊 Grid Bot #{grid_bot_id}\n\n"
                    f"✅ Buy ордер исполнен!\n\n"
                    f"💰 Куплено: {format_amount(amount)}\n"
                    f"💵 По цене: {format_price(price)}\n"
                    f"💳 Потрачено: {format_price(price * amount)}\n"
                )

                if new_order:
                    message += (
                        f"\n➡️ Создан Sell ордер: {format_amount(new_order['amount'])} "
                        f"по {format_price(new_order['price'])}\n"
                    )

                    # Calculate expected profit
                    if new_order.get('price') and order.get('price'):
                        expected_profit = (new_order['price'] - order['price']) * order['amount']
                        message += f"\n🎯 Прибыль за цикл: ~{format_profit(expected_profit)}"

            else:  # sell
                # Sell order filled template
                message = (
                    f"📊 Grid Bot #{grid_bot_id}\n\n"
                    f"✅ Sell ордер исполнен!\n\n"
                    f"💰 Продано: {format_amount(amount)}\n"
                    f"💵 По цене: {format_price(price)}\n"
                    f"💳 Получено: {format_price(price * amount)}\n"
                )

                if new_order:
                    message += (
                        f"\n➡️ Создан Buy ордер: {format_amount(new_order['amount'])} "
                        f"по {format_price(new_order['price'])}\n"
                    )

                if profit is not None:
                    message += f"\n🎉 Прибыль за цикл: {format_profit(profit)}\n"

            # Add inline keyboard
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="📊 Посмотреть бота",
                    callback_data=f"bot_details:{grid_bot_id}"
                )]
            ])

            await self.bot.send_message(
                chat_id=user_id,
                text=message,
                reply_markup=keyboard
            )

            logger.info(f"Sent order filled notification to user {user_id}")

        except Exception as e:
            logger.error(f"Error sending order filled notification: {e}")

    async def notify_profit_milestone(
        self,
        user_id: int,
        grid_bot_id: int,
        profit: Decimal,
        percent: Decimal
    ):
        """
        Notify user about profit milestone.

        Args:
            user_id: User Telegram ID
            grid_bot_id: Grid bot ID
            profit: Current profit
            percent: Profit percentage
        """
        try:
            message = (
                f"🎉 Grid Bot #{grid_bot_id}\n\n"
                f"💰 Прибыль достигла {format_percent(percent)}!\n\n"
                f"Текущая прибыль: {format_profit(profit)} ({format_percent(percent)})\n\n"
                f"Так держать! 🚀"
            )

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="📊 Посмотреть детали",
                    callback_data=f"bot_details:{grid_bot_id}"
                )]
            ])

            await self.bot.send_message(
                chat_id=user_id,
                text=message,
                reply_markup=keyboard
            )

            logger.info(f"Sent profit milestone notification to user {user_id}")

        except Exception as e:
            logger.error(f"Error sending profit milestone notification: {e}")

    async def notify_error(
        self,
        user_id: int,
        grid_bot_id: int,
        error_type: str,
        error_message: str,
        details: Optional[dict] = None
    ):
        """
        Notify user about error.

        Args:
            user_id: User Telegram ID
            grid_bot_id: Grid bot ID
            error_type: Error type (insufficient_funds, api_error, etc.)
            error_message: Error message
            details: Additional details (optional)
        """
        try:
            # Error type specific messages
            error_templates = {
                'insufficient_funds': (
                    "⚠️ Grid Bot #{bot_id}\n\n"
                    "Недостаточно средств для создания ордера!\n\n"
                    "Пожалуйста, пополните баланс или остановите бота."
                ),
                'api_error': (
                    "⚠️ Grid Bot #{bot_id}\n\n"
                    "Ошибка API биржи!\n\n"
                    "{message}"
                ),
                'invalid_api_key': (
                    "🔴 Grid Bot #{bot_id}\n\n"
                    "API ключи невалидны!\n\n"
                    "Бот остановлен. Пожалуйста, обновите API ключи в настройках."
                ),
                'order_creation_failed': (
                    "⚠️ Grid Bot #{bot_id}\n\n"
                    "Не удалось создать ордер!\n\n"
                    "{message}"
                ),
            }

            template = error_templates.get(
                error_type,
                "⚠️ Grid Bot #{bot_id}\n\nОшибка: {message}"
            )

            message = template.format(bot_id=grid_bot_id, message=error_message)

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="📊 Посмотреть бота",
                    callback_data=f"bot_details:{grid_bot_id}"
                )],
                [InlineKeyboardButton(
                    text="⚙️ Настройки",
                    callback_data="settings"
                )]
            ])

            await self.bot.send_message(
                chat_id=user_id,
                text=message,
                reply_markup=keyboard
            )

            logger.info(f"Sent error notification to user {user_id}: {error_type}")

        except Exception as e:
            logger.error(f"Error sending error notification: {e}")

    async def notify_bot_started(
        self,
        user_id: int,
        grid_bot_id: int,
        stats: dict
    ):
        """
        Notify user about successful bot start.

        Args:
            user_id: User Telegram ID
            grid_bot_id: Grid bot ID
            stats: Bot statistics
        """
        try:
            total_orders = stats.get('total_orders', 0)
            investment = stats.get('investment', Decimal('0'))

            message = (
                f"🎉 Grid Bot #{grid_bot_id} запущен!\n\n"
                f"📊 Активных ордеров: {total_orders}\n"
                f"💰 В работе: {format_price(investment)}\n\n"
                f"🔔 Буду присылать уведомления о:\n"
                f"• Исполненных ордерах\n"
                f"• Заработанной прибыли\n"
                f"• Важных событиях"
            )

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="📊 Посмотреть статус",
                    callback_data=f"bot_details:{grid_bot_id}"
                )],
                [InlineKeyboardButton(
                    text="🏠 В главное меню",
                    callback_data="main_menu"
                )]
            ])

            await self.bot.send_message(
                chat_id=user_id,
                text=message,
                reply_markup=keyboard
            )

            logger.info(f"Sent bot started notification to user {user_id}")

        except Exception as e:
            logger.error(f"Error sending bot started notification: {e}")

    async def notify_bot_stopped(
        self,
        user_id: int,
        grid_bot_id: int,
        stats: dict
    ):
        """
        Notify user about bot stop with final statistics.

        Args:
            user_id: User Telegram ID
            grid_bot_id: Grid bot ID
            stats: Final statistics
        """
        try:
            total_profit = stats.get('final_profit', Decimal('0'))
            profit_percent = stats.get('profit_percent', Decimal('0'))
            runtime = stats.get('runtime')
            cycles = stats.get('total_cycles', 0)
            cancelled_orders = stats.get('cancelled_orders', 0)

            message = (
                f"🔴 Grid Bot #{grid_bot_id} остановлен\n\n"
                f"📊 Итоговая статистика:\n\n"
                f"💰 Общая прибыль: {format_profit(total_profit)}\n"
                f"📈 ROI: {format_percent(profit_percent)}\n"
                f"🔄 Завершено циклов: {cycles}\n"
                f"❌ Отменено ордеров: {cancelled_orders}\n"
            )

            if runtime:
                message += f"⏱ Время работы: {format_runtime(None, runtime)}\n"

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="📊 Мои боты",
                    callback_data="my_bots"
                )],
                [InlineKeyboardButton(
                    text="🏠 В главное меню",
                    callback_data="main_menu"
                )]
            ])

            await self.bot.send_message(
                chat_id=user_id,
                text=message,
                reply_markup=keyboard
            )

            logger.info(f"Sent bot stopped notification to user {user_id}")

        except Exception as e:
            logger.error(f"Error sending bot stopped notification: {e}")

    async def send_daily_summary(self, user_id: int, bots_stats: list):
        """
        Send daily summary to user.

        Args:
            user_id: User Telegram ID
            bots_stats: List of bot statistics
        """
        try:
            if not bots_stats:
                return

            total_profit = sum(bot['profit'] for bot in bots_stats)
            active_bots = len([b for b in bots_stats if b['status'] == 'active'])

            message = (
                f"📊 Ежедневная сводка\n"
                f"📅 {datetime.utcnow().strftime('%Y-%m-%d')}\n\n"
                f"🤖 Активных ботов: {active_bots}\n"
                f"💰 Прибыль за 24ч: {format_profit(total_profit)}\n\n"
                f"Боты:\n"
            )

            for bot in bots_stats:
                message += (
                    f"\n• Bot #{bot['id']}: {format_profit(bot['profit'])} "
                    f"({bot['cycles']} циклов)"
                )

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="📊 Посмотреть ботов",
                    callback_data="my_bots"
                )]
            ])

            await self.bot.send_message(
                chat_id=user_id,
                text=message,
                reply_markup=keyboard
            )

            logger.info(f"Sent daily summary to user {user_id}")

        except Exception as e:
            logger.error(f"Error sending daily summary: {e}")
