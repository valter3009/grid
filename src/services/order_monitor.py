"""Order monitoring service."""
import asyncio
from typing import Dict, Optional
from decimal import Decimal
from datetime import datetime
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.grid_bot import GridBot
from src.models.order import GridOrder
from src.models.bot_log import BotLog
from src.services.mexc_service import MEXCService, MEXCError
from src.services.grid_strategy import GridStrategy
from src.core.config import settings

logger = logging.getLogger(__name__)


class OrderMonitor:
    """Background order monitoring service."""

    def __init__(
        self,
        db_factory,
        mexc_service: MEXCService,
        grid_strategy: GridStrategy,
        notification_service
    ):
        """
        Initialize order monitor.

        Args:
            db_factory: Function to create new DB session
            mexc_service: MEXC service instance
            grid_strategy: Grid strategy instance
            notification_service: Notification service instance
        """
        self.db_factory = db_factory
        self.mexc = mexc_service
        self.grid_strategy = grid_strategy
        self.notification = notification_service
        self.active_monitors: Dict[int, asyncio.Task] = {}
        self.check_interval = settings.ORDER_CHECK_INTERVAL

    async def monitor_bot_orders(self, grid_bot_id: int):
        """
        Infinite loop monitoring orders for specific bot.

        Algorithm (every 10 seconds):
        1. Check if bot is still active (status = 'active')
        2. Load open orders from DB (status = 'open')
        3. For each order:
           - Get status from exchange
           - IF status == 'filled':
               * Update in DB
               * Call GridStrategy.handle_filled_order()
        4. Update last_activity_at

        Error handling:
        - Network errors → retry with exponential backoff
        - Invalid API key → stop bot, notify
        - Insufficient funds → log, notify
        - Critical error → stop monitoring, notify

        Runs while:
        - bot.status == 'active'
        - No critical errors

        Args:
            grid_bot_id: Grid bot ID
        """
        logger.info(f"Starting monitoring for bot {grid_bot_id}")

        retry_delay = 1.0
        max_retry_delay = 60.0

        while True:
            try:
                # Create new DB session for each iteration
                async with self.db_factory() as db:
                    # Load bot
                    result = await db.execute(
                        select(GridBot).where(GridBot.id == grid_bot_id)
                    )
                    bot = result.scalar_one_or_none()

                    if not bot:
                        logger.warning(f"Bot {grid_bot_id} not found, stopping monitoring")
                        break

                    if bot.status != 'active':
                        logger.info(f"Bot {grid_bot_id} is not active, stopping monitoring")
                        break

                    # Load open orders
                    result = await db.execute(
                        select(GridOrder).where(
                            GridOrder.grid_bot_id == grid_bot_id,
                            GridOrder.status == 'open'
                        )
                    )
                    open_orders = result.scalars().all()

                    if not open_orders:
                        logger.debug(f"No open orders for bot {grid_bot_id}")
                        await asyncio.sleep(self.check_interval)
                        continue

                    # Check each order
                    for order in open_orders:
                        try:
                            # Get order status from exchange
                            status = await self.mexc.get_order_status(
                                user_id=bot.user_id,
                                symbol=bot.symbol,
                                order_id=order.exchange_order_id
                            )

                            # If filled, process it
                            if status['status'] == 'filled':
                                logger.info(
                                    f"Order {order.id} filled: {order.side} "
                                    f"{order.amount} @ {order.price}"
                                )

                                # Update fee if available
                                if status.get('fee'):
                                    order.fee = status['fee']
                                    order.fee_currency = status.get('fee_currency')

                                # Handle filled order
                                result = await self.grid_strategy.handle_filled_order(order.id)

                                # Send notification
                                if self.notification:
                                    await self.notification.notify_order_filled(
                                        user_id=bot.user_id,
                                        grid_bot_id=grid_bot_id,
                                        order={
                                            'id': order.id,
                                            'side': order.side,
                                            'price': order.price,
                                            'amount': order.amount,
                                        },
                                        new_order=result.get('new_order'),
                                        profit=result.get('profit')
                                    )

                                # Check for profit milestone
                                if result.get('cycle_completed') and bot.total_profit_percent > 0:
                                    # Check if we reached a milestone (5%, 10%, etc.)
                                    profit_milestones = [5, 10, 15, 20, 25, 30, 50, 75, 100]
                                    current_milestone = int(bot.total_profit_percent // 5) * 5

                                    if current_milestone in profit_milestones:
                                        await self.notification.notify_profit_milestone(
                                            user_id=bot.user_id,
                                            grid_bot_id=grid_bot_id,
                                            profit=bot.total_profit,
                                            percent=Decimal(str(current_milestone))
                                        )

                        except MEXCError as e:
                            logger.error(f"MEXC error checking order {order.id}: {e}")

                            # Check if it's an authentication error
                            if 'authentication' in str(e).lower() or 'api key' in str(e).lower():
                                logger.error(f"API key invalid for bot {grid_bot_id}, stopping")

                                # Stop bot
                                bot.status = 'stopped'
                                await db.commit()

                                # Notify user
                                if self.notification:
                                    await self.notification.notify_error(
                                        user_id=bot.user_id,
                                        grid_bot_id=grid_bot_id,
                                        error_type='invalid_api_key',
                                        error_message='API ключи невалидны. Проверьте настройки.'
                                    )

                                return  # Exit monitoring

                            # Continue with next order
                            continue

                    # Update bot activity
                    bot.last_activity_at = datetime.utcnow()
                    await db.commit()

                    # Reset retry delay on success
                    retry_delay = 1.0

                # Sleep before next check
                await asyncio.sleep(self.check_interval)

            except asyncio.CancelledError:
                logger.info(f"Monitoring cancelled for bot {grid_bot_id}")
                break

            except Exception as e:
                logger.error(f"Error monitoring bot {grid_bot_id}: {e}")

                # Exponential backoff
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)

                # Continue monitoring
                continue

        logger.info(f"Stopped monitoring for bot {grid_bot_id}")

    def start_monitoring(self, grid_bot_id: int):
        """
        Start monitoring for bot.

        Creates asyncio.Task and stores in self.active_monitors

        Args:
            grid_bot_id: Grid bot ID
        """
        if grid_bot_id in self.active_monitors:
            logger.warning(f"Monitoring already active for bot {grid_bot_id}")
            return

        task = asyncio.create_task(self.monitor_bot_orders(grid_bot_id))
        self.active_monitors[grid_bot_id] = task

        logger.info(f"Started monitoring for bot {grid_bot_id}")

    def stop_monitoring(self, grid_bot_id: int):
        """
        Stop monitoring for bot.

        Args:
            grid_bot_id: Grid bot ID
        """
        if grid_bot_id not in self.active_monitors:
            logger.warning(f"No active monitoring for bot {grid_bot_id}")
            return

        task = self.active_monitors.pop(grid_bot_id)
        task.cancel()

        logger.info(f"Stopped monitoring for bot {grid_bot_id}")

    def is_monitoring(self, grid_bot_id: int) -> bool:
        """
        Check if bot is being monitored.

        Args:
            grid_bot_id: Grid bot ID

        Returns:
            True if monitoring is active
        """
        return grid_bot_id in self.active_monitors

    async def stop_all(self):
        """Stop all monitoring tasks."""
        for grid_bot_id, task in list(self.active_monitors.items()):
            task.cancel()

        self.active_monitors.clear()
        logger.info("Stopped all monitoring tasks")
