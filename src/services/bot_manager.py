"""Bot Manager for grid trading bots lifecycle management."""
from decimal import Decimal
from datetime import datetime, timedelta
from typing import Optional, Dict, List
import logging
import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.grid_bot import GridBot
from src.models.order import GridOrder
from src.models.bot_log import BotLog
from src.services.mexc_service import MEXCService, MEXCError
from src.services.grid_strategy import GridStrategy, GridStrategyError

logger = logging.getLogger(__name__)


class BotManagerError(Exception):
    """Bot manager error."""
    pass


class BotManager:
    """Manager for grid trading bots lifecycle."""

    def __init__(self, db: AsyncSession, mexc_service: MEXCService, grid_strategy: GridStrategy):
        """Initialize bot manager."""
        self.db = db
        self.mexc = mexc_service
        self.grid_strategy = grid_strategy

    async def create_bot(
        self,
        user_id: int,
        symbol: str,
        lower_price: float,
        upper_price: float,
        grid_levels: int,
        investment_amount: float
    ) -> Optional[GridBot]:
        """
        Create and start new grid bot.

        Args:
            user_id: User ID
            symbol: Trading pair symbol (e.g., BTCUSDT)
            lower_price: Lower price boundary
            upper_price: Upper price boundary
            grid_levels: Number of grid levels
            investment_amount: Total investment amount in USDT

        Returns:
            GridBot instance if created successfully, None otherwise
        """
        try:
            logger.info(f"Creating grid bot for user {user_id}: {symbol}, ${investment_amount}")

            # Create bot record
            grid_bot = GridBot(
                user_id=user_id,
                symbol=symbol,
                lower_price=Decimal(str(lower_price)),
                upper_price=Decimal(str(upper_price)),
                grid_levels=grid_levels,
                investment_amount=Decimal(str(investment_amount)),
                status='active',
                started_at=datetime.utcnow()
            )
            self.db.add(grid_bot)
            await self.db.commit()
            await self.db.refresh(grid_bot)

            logger.info(f"Created bot #{grid_bot.id}, creating initial orders...")

            # Get current price
            current_price = await self.mexc.get_current_price(symbol)
            logger.info(f"Current {symbol} price: ${current_price}")

            # Create initial grid orders
            orders_created = await self.grid_strategy.create_initial_orders(
                grid_bot.id,
                current_price
            )

            if not orders_created:
                logger.error(f"Failed to create initial orders for bot #{grid_bot.id}")
                grid_bot.status = 'stopped'
                await self.db.commit()
                return None

            logger.info(f"Bot #{grid_bot.id} created successfully with {orders_created} orders")

            # Log bot creation
            log = BotLog.create_info(
                message=f'Grid bot created: {symbol}, levels={grid_levels}, investment=${investment_amount}',
                grid_bot_id=grid_bot.id,
                user_id=user_id
            )
            self.db.add(log)
            await self.db.commit()

            return grid_bot

        except Exception as e:
            logger.error(f"Error creating grid bot: {e}", exc_info=True)
            await self.db.rollback()
            return None

    async def create_flat_bot(
        self,
        user_id: int,
        symbol: str,
        flat_spread: Decimal,
        flat_increment: Decimal,
        buy_orders_count: int,
        sell_orders_count: int,
        starting_price: Decimal,
        order_size: Decimal
    ) -> Optional[GridBot]:
        """
        Create and start new flat grid bot.

        Args:
            user_id: User ID
            symbol: Trading pair symbol (e.g., BTC/USDT)
            flat_spread: Spread between buy and sell orders
            flat_increment: Step between grid levels
            buy_orders_count: Number of buy orders
            sell_orders_count: Number of sell orders
            starting_price: Starting price (center of grid)
            order_size: Size of each order in USDT

        Returns:
            GridBot instance if created successfully, None otherwise
        """
        try:
            logger.info(
                f"Creating flat grid bot for user {user_id}: {symbol}, "
                f"spread=${flat_spread}, increment=${flat_increment}, "
                f"buy={buy_orders_count}, sell={sell_orders_count}, "
                f"order_size=${order_size}"
            )

            # Create bot record
            grid_bot = GridBot(
                user_id=user_id,
                symbol=symbol,
                flat_spread=flat_spread,
                flat_increment=flat_increment,
                buy_orders_count=buy_orders_count,
                sell_orders_count=sell_orders_count,
                starting_price=starting_price,
                order_size=order_size,
                grid_type='flat',
                status='active',
                started_at=datetime.utcnow()
            )
            self.db.add(grid_bot)
            await self.db.commit()
            await self.db.refresh(grid_bot)

            logger.info(f"Created flat grid bot #{grid_bot.id}, creating initial orders...")

            # Create initial flat grid orders
            orders_created = await self.grid_strategy.create_flat_grid_orders(
                grid_bot.id,
                starting_price
            )

            if not orders_created:
                logger.error(f"Failed to create initial orders for bot #{grid_bot.id}")
                grid_bot.status = 'stopped'
                await self.db.commit()
                return None

            logger.info(
                f"Flat grid bot #{grid_bot.id} created successfully with "
                f"{orders_created['total_orders']} orders"
            )

            # Log bot creation
            log = BotLog.create_info(
                message=(
                    f'Flat grid bot created: {symbol}, '
                    f'spread=${flat_spread}, increment=${flat_increment}, '
                    f'buy={buy_orders_count}, sell={sell_orders_count}, '
                    f'order_size=${order_size}'
                ),
                grid_bot_id=grid_bot.id,
                user_id=user_id
            )
            self.db.add(log)
            await self.db.commit()

            return grid_bot

        except Exception as e:
            logger.error(f"Error creating flat grid bot: {e}", exc_info=True)
            await self.db.rollback()
            return None

    async def start_bot(self, grid_bot_id: int) -> bool:
        """
        Start grid bot.

        Algorithm:
        1. Load bot settings from DB
        2. Check user balance
        3. Get current asset price
        4. Create initial orders via GridStrategy
        5. Update status = 'active', started_at = NOW()
        6. Start monitoring (OrderMonitor.monitor_bot_orders)
        7. Send notification about successful start

        Args:
            grid_bot_id: Grid bot ID

        Returns:
            True if started successfully

        Raises:
            BotManagerError: If start fails
        """
        # Load bot
        result = await self.db.execute(
            select(GridBot).where(GridBot.id == grid_bot_id)
        )
        bot = result.scalar_one_or_none()

        if not bot:
            raise BotManagerError(f"Grid bot {grid_bot_id} not found")

        try:
            # Check balance
            balances = await self.mexc.get_balance(bot.user_id)
            quote_currency = bot.quote_currency or 'USDT'
            available_balance = balances.get(quote_currency, Decimal('0'))

            if available_balance < bot.investment_amount:
                raise BotManagerError(
                    f"Недостаточно средств. Доступно: {available_balance} {quote_currency}, "
                    f"требуется: {bot.investment_amount}"
                )

            # Get current price
            current_price = await self.mexc.get_current_price(bot.symbol)

            # Validate price is within range
            if current_price < bot.lower_price or current_price > bot.upper_price:
                logger.warning(
                    f"Current price {current_price} is outside grid range "
                    f"[{bot.lower_price}, {bot.upper_price}]"
                )

            # Create initial orders
            orders_result = await self.grid_strategy.create_initial_orders(
                grid_bot_id=grid_bot_id,
                current_price=current_price
            )

            # Update bot status
            bot.status = 'active'
            bot.started_at = datetime.utcnow()
            bot.last_activity_at = datetime.utcnow()
            await self.db.commit()

            # Log success
            log_entry = BotLog.create_info(
                message=f"Bot started with {orders_result['total_orders']} orders",
                grid_bot_id=grid_bot_id,
                user_id=bot.user_id,
                details={
                    'buy_orders': len(orders_result['buy_orders']),
                    'sell_orders': len(orders_result['sell_orders']),
                    'current_price': str(current_price)
                }
            )
            self.db.add(log_entry)
            await self.db.commit()

            logger.info(f"Grid bot {grid_bot_id} started successfully")
            return True

        except (MEXCError, GridStrategyError) as e:
            logger.error(f"Error starting bot {grid_bot_id}: {e}")
            await self.db.rollback()

            # Log error
            log_entry = BotLog.create_error(
                message=f"Failed to start bot: {str(e)}",
                grid_bot_id=grid_bot_id,
                user_id=bot.user_id
            )
            self.db.add(log_entry)
            await self.db.commit()

            raise BotManagerError(f"Не удалось запустить бота: {str(e)}")

        except Exception as e:
            logger.error(f"Unexpected error starting bot {grid_bot_id}: {e}")
            await self.db.rollback()
            raise BotManagerError(f"Ошибка запуска бота: {str(e)}")

    async def stop_bot(self, grid_bot_id: int, sell_all: bool = False) -> dict:
        """
        Stop grid bot.

        Algorithm:
        1. Stop order monitoring
        2. Load all open orders from DB
        3. Cancel all open orders on exchange
        4. Update statuses in DB: status = 'cancelled'
        5. IF sell_all == True:
               Sell all bought asset at market
        6. Update status = 'stopped', stopped_at = NOW()
        7. Send final statistics to user

        Args:
            grid_bot_id: Grid bot ID
            sell_all: Whether to sell all assets at market

        Returns:
            {
                'cancelled_orders': int,
                'final_profit': Decimal,
                'runtime': timedelta
            }

        Raises:
            BotManagerError: If stop fails
        """
        # Load bot
        result = await self.db.execute(
            select(GridBot).where(GridBot.id == grid_bot_id)
        )
        bot = result.scalar_one_or_none()

        if not bot:
            raise BotManagerError(f"Grid bot {grid_bot_id} not found")

        try:
            # Load open orders from DB
            result = await self.db.execute(
                select(GridOrder).where(
                    GridOrder.grid_bot_id == grid_bot_id,
                    GridOrder.status == 'open'
                )
            )
            open_orders = result.scalars().all()

            cancelled_count = 0

            # Cancel all open orders from DB (in parallel for speed!)
            async def cancel_single_order(order):
                """Cancel a single order and update its status."""
                try:
                    await self.mexc.cancel_order(
                        user_id=bot.user_id,
                        symbol=bot.symbol,
                        order_id=order.exchange_order_id
                    )

                    order.status = 'cancelled'
                    order.cancelled_at = datetime.utcnow()
                    logger.info(f"Cancelled order {order.exchange_order_id} on exchange")
                    return True

                except MEXCError as e:
                    logger.warning(f"Failed to cancel order {order.id}: {e}")
                    return False

            # Use rate limiting to avoid overwhelming the exchange
            semaphore = asyncio.Semaphore(10)  # Max 10 concurrent cancellations

            async def cancel_with_limit(order):
                async with semaphore:
                    return await cancel_single_order(order)

            # Execute all cancellations in parallel
            if open_orders:
                logger.info(f"Cancelling {len(open_orders)} orders in parallel...")
                results = await asyncio.gather(*[
                    cancel_with_limit(order) for order in open_orders
                ], return_exceptions=True)

                # Count successful cancellations
                cancelled_count = sum(1 for r in results if r is True)

            # Also cancel any orphaned orders on exchange (not in DB)
            try:
                exchange_orders = await self.mexc.get_open_orders(bot.user_id, bot.symbol)

                # Find orphaned orders (on exchange but not in our DB)
                orphaned_order_ids = []
                for exchange_order in exchange_orders:
                    order_id = exchange_order.get('order_id')
                    if not order_id:
                        logger.warning(f"Exchange order missing ID: {exchange_order}")
                        continue

                    # Check if this order is already cancelled
                    already_cancelled = any(
                        o.exchange_order_id == order_id for o in open_orders
                    )
                    if not already_cancelled:
                        orphaned_order_ids.append(order_id)

                # Cancel orphaned orders in parallel
                if orphaned_order_ids:
                    logger.info(f"Found {len(orphaned_order_ids)} orphaned orders, cancelling in parallel...")

                    async def cancel_orphaned(order_id):
                        try:
                            await self.mexc.cancel_order(
                                user_id=bot.user_id,
                                symbol=bot.symbol,
                                order_id=order_id
                            )
                            logger.info(f"Cancelled orphaned order {order_id} on exchange")
                            return True
                        except MEXCError as e:
                            logger.warning(f"Failed to cancel orphaned order {order_id}: {e}")
                            return False

                    async def cancel_orphaned_with_limit(order_id):
                        async with semaphore:
                            return await cancel_orphaned(order_id)

                    orphaned_results = await asyncio.gather(*[
                        cancel_orphaned_with_limit(order_id) for order_id in orphaned_order_ids
                    ], return_exceptions=True)

                    cancelled_count += sum(1 for r in orphaned_results if r is True)

            except Exception as e:
                logger.warning(f"Failed to check exchange orders: {e}")

            # Save cancelled orders to DB
            if cancelled_count > 0:
                await self.db.commit()
                logger.info(f"Saved {cancelled_count} cancelled orders to DB")

            # Sell all assets if requested
            if sell_all:
                try:
                    base_currency = bot.base_currency or bot.symbol.split('/')[0]
                    balances = await self.mexc.get_balance(bot.user_id)
                    base_balance = balances.get(base_currency, Decimal('0'))

                    if base_balance > 0:
                        # Get exchange info for minimum order
                        exchange_info = await self.mexc.get_exchange_info(bot.symbol)
                        min_amount = exchange_info['min_order_amount']

                        if base_balance >= min_amount:
                            market_order = await self.mexc.create_market_order(
                                user_id=bot.user_id,
                                symbol=bot.symbol,
                                side='sell',
                                amount=base_balance
                            )

                            logger.info(
                                f"Sold {base_balance} {base_currency} at market: "
                                f"{market_order.get('average_price')}"
                            )

                except MEXCError as e:
                    logger.error(f"Failed to sell assets: {e}")
                    # Don't fail the whole stop operation

            # Update bot status
            bot.status = 'stopped'
            bot.stopped_at = datetime.utcnow()
            await self.db.commit()

            # Calculate runtime
            runtime = None
            if bot.started_at and bot.stopped_at:
                runtime = bot.stopped_at - bot.started_at

            # Log stop
            log_entry = BotLog.create_info(
                message=f"Bot stopped. Cancelled {cancelled_count} orders.",
                grid_bot_id=grid_bot_id,
                user_id=bot.user_id,
                details={
                    'cancelled_orders': cancelled_count,
                    'sell_all': sell_all,
                    'final_profit': str(bot.total_profit),
                    'runtime_seconds': int(runtime.total_seconds()) if runtime else 0
                }
            )
            self.db.add(log_entry)
            await self.db.commit()

            logger.info(f"Grid bot {grid_bot_id} stopped successfully")

            return {
                'cancelled_orders': cancelled_count,
                'final_profit': bot.total_profit,
                'runtime': runtime,
                'total_cycles': bot.completed_cycles,
            }

        except Exception as e:
            logger.error(f"Error stopping bot {grid_bot_id}: {e}")
            await self.db.rollback()
            raise BotManagerError(f"Ошибка остановки бота: {str(e)}")

    async def pause_bot(self, grid_bot_id: int) -> bool:
        """
        Pause grid bot.

        - Doesn't create new orders
        - Doesn't cancel existing orders
        - Continues monitoring

        Args:
            grid_bot_id: Grid bot ID

        Returns:
            True if paused successfully
        """
        result = await self.db.execute(
            select(GridBot).where(GridBot.id == grid_bot_id)
        )
        bot = result.scalar_one_or_none()

        if not bot:
            raise BotManagerError(f"Grid bot {grid_bot_id} not found")

        bot.status = 'paused'
        await self.db.commit()

        logger.info(f"Grid bot {grid_bot_id} paused")
        return True

    async def resume_bot(self, grid_bot_id: int) -> bool:
        """
        Resume paused grid bot.

        Args:
            grid_bot_id: Grid bot ID

        Returns:
            True if resumed successfully
        """
        result = await self.db.execute(
            select(GridBot).where(GridBot.id == grid_bot_id)
        )
        bot = result.scalar_one_or_none()

        if not bot:
            raise BotManagerError(f"Grid bot {grid_bot_id} not found")

        if bot.status != 'paused':
            raise BotManagerError("Bot is not paused")

        bot.status = 'active'
        await self.db.commit()

        logger.info(f"Grid bot {grid_bot_id} resumed")
        return True

    async def delete_bot(self, grid_bot_id: int) -> bool:
        """
        Delete grid bot completely.

        Algorithm:
        1. Stop bot if active (cancel all orders)
        2. Delete all related records (orders, logs)
        3. Delete bot from DB

        Args:
            grid_bot_id: Grid bot ID

        Returns:
            True if deleted successfully

        Raises:
            BotManagerError: If deletion fails
        """
        # Load bot
        result = await self.db.execute(
            select(GridBot).where(GridBot.id == grid_bot_id)
        )
        bot = result.scalar_one_or_none()

        if not bot:
            raise BotManagerError(f"Grid bot {grid_bot_id} not found")

        try:
            # Stop bot if active (cancel orders)
            if bot.status in ['active', 'paused']:
                logger.info(f"Stopping active bot {grid_bot_id} before deletion")
                await self.stop_bot(grid_bot_id, sell_all=False)

            # Delete all orders
            await self.db.execute(
                select(GridOrder).where(GridOrder.grid_bot_id == grid_bot_id)
            )
            result = await self.db.execute(
                select(GridOrder).where(GridOrder.grid_bot_id == grid_bot_id)
            )
            orders = result.scalars().all()
            for order in orders:
                await self.db.delete(order)

            # Delete all logs
            result = await self.db.execute(
                select(BotLog).where(BotLog.grid_bot_id == grid_bot_id)
            )
            logs = result.scalars().all()
            for log in logs:
                await self.db.delete(log)

            # Delete bot
            await self.db.delete(bot)
            await self.db.commit()

            logger.info(f"Grid bot {grid_bot_id} deleted successfully")
            return True

        except Exception as e:
            logger.error(f"Error deleting bot {grid_bot_id}: {e}")
            await self.db.rollback()
            raise BotManagerError(f"Ошибка удаления бота: {str(e)}")

    async def get_bot_statistics(self, grid_bot_id: int) -> dict:
        """
        Get bot statistics.

        Returns:
            {
                'total_profit': Decimal,
                'profit_percent': Decimal,
                'completed_cycles': int,
                'open_orders': int,
                'runtime': timedelta,
                'avg_profit_per_day': Decimal
            }

        Raises:
            BotManagerError: If bot not found
        """
        result = await self.db.execute(
            select(GridBot).where(GridBot.id == grid_bot_id)
        )
        bot = result.scalar_one_or_none()

        if not bot:
            raise BotManagerError(f"Grid bot {grid_bot_id} not found")

        # Count open orders
        result = await self.db.execute(
            select(GridOrder).where(
                GridOrder.grid_bot_id == grid_bot_id,
                GridOrder.status == 'open'
            )
        )
        open_orders_count = len(result.scalars().all())

        # Calculate runtime
        runtime = None
        avg_profit_per_day = Decimal('0')

        if bot.started_at:
            end_time = bot.stopped_at or datetime.utcnow()
            runtime = end_time - bot.started_at

            # Calculate average profit per day
            if runtime.total_seconds() > 0:
                days = Decimal(str(runtime.total_seconds() / 86400))
                if days > 0:
                    avg_profit_per_day = bot.total_profit / days

        return {
            'bot_id': bot.id,
            'symbol': bot.symbol,
            'status': bot.status,
            'total_profit': bot.total_profit,
            'profit_percent': bot.total_profit_percent,
            'completed_cycles': bot.completed_cycles,
            'open_orders': open_orders_count,
            'runtime': runtime,
            'avg_profit_per_day': avg_profit_per_day,
            'investment': bot.investment_amount,
            'started_at': bot.started_at,
            'stopped_at': bot.stopped_at,
        }

    async def restore_bots_after_restart(self) -> int:
        """
        Restore active bots after system restart.

        Called on application startup.

        Algorithm:
        1. Load all bots with status = 'active' from DB
        2. For each bot:
           - Sync orders with exchange
           - Run health check
           - Process orders filled during offline
           - Start monitoring

        Returns:
            Number of restored bots
        """
        try:
            # Load active bots
            result = await self.db.execute(
                select(GridBot).where(GridBot.status == 'active')
            )
            active_bots = result.scalars().all()

            restored_count = 0

            for bot in active_bots:
                try:
                    # Sync orders with exchange
                    await self._sync_bot_orders(bot.id)

                    # Log restoration
                    log_entry = BotLog.create_info(
                        message="Bot restored after restart",
                        grid_bot_id=bot.id,
                        user_id=bot.user_id
                    )
                    self.db.add(log_entry)

                    restored_count += 1
                    logger.info(f"Restored bot {bot.id}")

                except Exception as e:
                    logger.error(f"Failed to restore bot {bot.id}: {e}")
                    # Continue with other bots

            await self.db.commit()

            logger.info(f"Restored {restored_count} active bots")
            return restored_count

        except Exception as e:
            logger.error(f"Error restoring bots: {e}")
            return 0

    async def _sync_bot_orders(self, grid_bot_id: int):
        """
        Sync bot orders with exchange.

        Args:
            grid_bot_id: Grid bot ID
        """
        # Load bot
        result = await self.db.execute(
            select(GridBot).where(GridBot.id == grid_bot_id)
        )
        bot = result.scalar_one_or_none()

        if not bot:
            return

        # Load orders from DB marked as 'open'
        result = await self.db.execute(
            select(GridOrder).where(
                GridOrder.grid_bot_id == grid_bot_id,
                GridOrder.status == 'open'
            )
        )
        db_orders = result.scalars().all()

        # Check each order status on exchange
        for order in db_orders:
            try:
                status = await self.mexc.get_order_status(
                    user_id=bot.user_id,
                    symbol=bot.symbol,
                    order_id=order.exchange_order_id
                )

                # If filled during offline, process it
                if status['status'] == 'filled' and order.status == 'open':
                    logger.info(
                        f"Order {order.id} was filled during offline, processing..."
                    )
                    await self.grid_strategy.handle_filled_order(order.id)

            except MEXCError as e:
                logger.warning(f"Failed to check order {order.id} status: {e}")
                continue
