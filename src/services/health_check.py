"""Health check system with automatic fixing."""
from decimal import Decimal
from typing import List, Dict, Optional
from datetime import datetime
import asyncio
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


class HealthCheck:
    """Health check system with automatic issue resolution."""

    def __init__(
        self,
        db: AsyncSession,
        mexc_service: MEXCService,
        grid_strategy: GridStrategy,
        notification_service
    ):
        """Initialize health check."""
        self.db = db
        self.mexc = mexc_service
        self.grid_strategy = grid_strategy
        self.notification = notification_service

    async def check_bot_health(self, grid_bot_id: int) -> dict:
        """
        Check bot health.

        Checks:
        1. Orphaned assets (bought asset without sell order)
        2. Order count matches settings
        3. Order prices are within range
        4. Sufficient funds for trading
        5. Duplicate orders

        Returns:
            {
                'healthy': bool,
                'issues': List[str],
                'auto_fixed': List[str],
                'needs_attention': List[str]
            }

        Args:
            grid_bot_id: Grid bot ID
        """
        issues = []
        auto_fixed = []
        needs_attention = []

        # Load bot
        result = await self.db.execute(
            select(GridBot).where(GridBot.id == grid_bot_id)
        )
        bot = result.scalar_one_or_none()

        if not bot:
            return {
                'healthy': False,
                'issues': ['Bot not found'],
                'auto_fixed': [],
                'needs_attention': ['Bot not found']
            }

        try:
            # Check 1: Orphaned assets
            orphaned_result = await self._check_orphaned_assets(bot)
            if orphaned_result['has_orphaned']:
                issues.append('Orphaned assets detected')
                if orphaned_result['auto_fixed']:
                    auto_fixed.append('Created sell orders for orphaned assets')
                else:
                    needs_attention.append('Failed to create sell orders for orphaned assets')

            # Check 2: Order count validation
            order_count_result = await self._check_order_count(bot)
            if not order_count_result['valid']:
                issues.append(order_count_result['issue'])
                needs_attention.append('Order count mismatch')

            # Check 3: Order prices in range
            price_result = await self._check_order_prices(bot)
            if price_result['out_of_range']:
                issues.append(f"{len(price_result['out_of_range'])} orders out of range")
                if price_result['cancelled']:
                    auto_fixed.append(f"Cancelled {price_result['cancelled']} out-of-range orders")

            # Check 4: Duplicate orders
            duplicate_result = await self._check_duplicate_orders(bot)
            if duplicate_result['has_duplicates']:
                issues.append('Duplicate orders found')
                if duplicate_result['removed']:
                    auto_fixed.append(f"Removed {duplicate_result['removed']} duplicate orders")

            # Check 5: Sufficient balance
            balance_result = await self._check_balance(bot)
            if not balance_result['sufficient']:
                issues.append('Insufficient balance')
                needs_attention.append(
                    f"Balance too low: {balance_result['current']}, "
                    f"recommended: {balance_result['recommended']}"
                )

            # Determine if healthy
            healthy = len(needs_attention) == 0

            # Log health check
            log_entry = BotLog.create_info(
                message=f"Health check: {'✓ Healthy' if healthy else '⚠ Issues found'}",
                grid_bot_id=grid_bot_id,
                user_id=bot.user_id,
                details={
                    'issues': issues,
                    'auto_fixed': auto_fixed,
                    'needs_attention': needs_attention
                }
            )
            self.db.add(log_entry)
            await self.db.commit()

            return {
                'healthy': healthy,
                'issues': issues,
                'auto_fixed': auto_fixed,
                'needs_attention': needs_attention
            }

        except Exception as e:
            logger.error(f"Error checking bot health: {e}")
            return {
                'healthy': False,
                'issues': [f"Health check error: {str(e)}"],
                'auto_fixed': [],
                'needs_attention': ['Health check failed']
            }

    async def auto_fix_bot(self, grid_bot_id: int, issues: List[str]) -> dict:
        """
        AUTOMATICALLY fix found issues.

        Fixes:
        1. Orphaned assets → create sell order
        2. Missing orders → create
        3. Duplicate orders → cancel extras
        4. Out-of-range orders → cancel

        Args:
            grid_bot_id: Grid bot ID
            issues: List of issues to fix

        Returns:
            {
                'fixed': List[str],
                'failed': List[str]
            }
        """
        fixed = []
        failed = []

        # Load bot
        result = await self.db.execute(
            select(GridBot).where(GridBot.id == grid_bot_id)
        )
        bot = result.scalar_one_or_none()

        if not bot:
            return {'fixed': [], 'failed': ['Bot not found']}

        try:
            # Fix orphaned assets
            if 'Orphaned assets detected' in issues:
                result = await self.handle_orphaned_assets(grid_bot_id)
                if result['success']:
                    fixed.append('Fixed orphaned assets')
                else:
                    failed.append('Failed to fix orphaned assets')

            # Fix duplicate orders
            if 'Duplicate orders found' in issues:
                result = await self._fix_duplicate_orders(bot)
                if result['fixed']:
                    fixed.append(f"Removed {result['count']} duplicates")
                else:
                    failed.append('Failed to fix duplicates')

            return {'fixed': fixed, 'failed': failed}

        except Exception as e:
            logger.error(f"Error auto-fixing bot {grid_bot_id}: {e}")
            return {'fixed': fixed, 'failed': [str(e)]}

    async def handle_orphaned_assets(self, grid_bot_id: int) -> dict:
        """
        AUTOMATICALLY handle orphaned assets.

        If there's BTC on balance without corresponding sell order:
        1. Determine suitable level for sell order
        2. Create sell order automatically
        3. Log action
        4. Notify user

        Algorithm:
        1. Get balance of base_currency
        2. Check all sell orders
        3. IF balance > sum(sell_orders.amount):
               orphaned_amount = balance - sum(sell_orders.amount)

               # Find next free level
               next_free_level = find_next_free_sell_level()
               price = price_levels[next_free_level]

               # Create sell order
               create_limit_order(side='sell', price=price, amount=orphaned_amount)

        Args:
            grid_bot_id: Grid bot ID

        Returns:
            {'success': bool, 'created_orders': int}
        """
        # Load bot
        result = await self.db.execute(
            select(GridBot).where(GridBot.id == grid_bot_id)
        )
        bot = result.scalar_one_or_none()

        if not bot:
            return {'success': False, 'created_orders': 0}

        try:
            # Get base currency balance
            base_currency = bot.base_currency or bot.symbol.split('/')[0]
            balances = await self.mexc.get_balance(bot.user_id)
            balance = balances.get(base_currency, Decimal('0'))

            if balance == 0:
                return {'success': True, 'created_orders': 0}  # No orphaned assets

            # Get all sell orders
            result = await self.db.execute(
                select(GridOrder).where(
                    GridOrder.grid_bot_id == grid_bot_id,
                    GridOrder.side == 'sell',
                    GridOrder.status == 'open'
                )
            )
            sell_orders = result.scalars().all()

            # Calculate total amount in sell orders
            total_in_orders = sum(order.amount for order in sell_orders)

            # Check for orphaned amount
            orphaned_amount = balance - total_in_orders

            if orphaned_amount <= 0:
                return {'success': True, 'created_orders': 0}  # No orphaned assets

            # Get exchange info
            exchange_info = await self.mexc.get_exchange_info(bot.symbol)
            min_amount = exchange_info['min_order_amount']

            if orphaned_amount < min_amount:
                logger.warning(
                    f"Orphaned amount {orphaned_amount} below minimum {min_amount}"
                )
                return {'success': True, 'created_orders': 0}

            # Calculate grid levels
            price_levels = self.grid_strategy.calculate_grid_levels(
                bot.lower_price,
                bot.upper_price,
                bot.grid_levels
            )

            # Find current price
            current_price = await self.mexc.get_current_price(bot.symbol)

            # Find next free sell level above current price
            used_levels = {order.level for order in sell_orders}
            free_level = None

            for i, price in enumerate(price_levels):
                if price > current_price and i not in used_levels:
                    free_level = i
                    break

            if free_level is None:
                logger.warning("No free level found for orphaned asset")
                return {'success': False, 'created_orders': 0}

            # Create sell order
            sell_price = price_levels[free_level]

            order_result = await self.mexc.create_limit_order(
                user_id=bot.user_id,
                symbol=bot.symbol,
                side='sell',
                price=sell_price,
                amount=orphaned_amount
            )

            # Save to DB
            new_order = GridOrder(
                grid_bot_id=grid_bot_id,
                exchange_order_id=order_result['order_id'],
                side='sell',
                order_type='limit',
                level=free_level,
                price=sell_price,
                amount=orphaned_amount,
                total=sell_price * orphaned_amount,
                status='open'
            )
            self.db.add(new_order)

            # Log action
            log_entry = BotLog.create_info(
                message=f"Auto-created sell order for orphaned assets: {orphaned_amount}",
                grid_bot_id=grid_bot_id,
                user_id=bot.user_id,
                details={
                    'orphaned_amount': str(orphaned_amount),
                    'level': free_level,
                    'price': str(sell_price)
                }
            )
            self.db.add(log_entry)
            await self.db.commit()

            # Notify user
            if self.notification:
                await self.notification.notify_error(
                    user_id=bot.user_id,
                    grid_bot_id=grid_bot_id,
                    error_type='info',
                    error_message=(
                        f"Обнаружен актив без sell ордера. "
                        f"Автоматически создан sell ордер: "
                        f"{orphaned_amount} по {sell_price}"
                    )
                )

            logger.info(
                f"Created sell order for orphaned assets: "
                f"{orphaned_amount} @ {sell_price}"
            )

            return {'success': True, 'created_orders': 1}

        except Exception as e:
            logger.error(f"Error handling orphaned assets: {e}")
            return {'success': False, 'created_orders': 0}

    async def _check_orphaned_assets(self, bot: GridBot) -> dict:
        """Check for orphaned assets."""
        try:
            result = await self.handle_orphaned_assets(bot.id)
            return {
                'has_orphaned': result['created_orders'] > 0,
                'auto_fixed': result['success']
            }
        except Exception:
            return {'has_orphaned': False, 'auto_fixed': False}

    async def _check_order_count(self, bot: GridBot) -> dict:
        """Check if order count matches expected."""
        result = await self.db.execute(
            select(GridOrder).where(
                GridOrder.grid_bot_id == bot.id,
                GridOrder.status == 'open'
            )
        )
        open_orders = result.scalars().all()

        expected_orders = bot.grid_levels
        actual_orders = len(open_orders)

        return {
            'valid': actual_orders >= expected_orders * 0.8,  # Allow 20% deviation
            'expected': expected_orders,
            'actual': actual_orders,
            'issue': f"Order count: expected ~{expected_orders}, got {actual_orders}"
        }

    async def _check_order_prices(self, bot: GridBot) -> dict:
        """Check if orders are within price range."""
        result = await self.db.execute(
            select(GridOrder).where(
                GridOrder.grid_bot_id == bot.id,
                GridOrder.status == 'open'
            )
        )
        orders = result.scalars().all()

        out_of_range = []
        cancelled = 0

        for order in orders:
            if order.price < bot.lower_price or order.price > bot.upper_price:
                out_of_range.append(order)

                # Auto-cancel out-of-range orders
                try:
                    await self.mexc.cancel_order(
                        user_id=bot.user_id,
                        symbol=bot.symbol,
                        order_id=order.exchange_order_id
                    )
                    order.status = 'cancelled'
                    cancelled += 1
                except Exception as e:
                    logger.error(f"Failed to cancel out-of-range order: {e}")

        if cancelled > 0:
            await self.db.commit()

        return {
            'out_of_range': out_of_range,
            'cancelled': cancelled
        }

    async def _check_duplicate_orders(self, bot: GridBot) -> dict:
        """Check for duplicate orders at same level."""
        result = await self.db.execute(
            select(GridOrder).where(
                GridOrder.grid_bot_id == bot.id,
                GridOrder.status == 'open'
            )
        )
        orders = result.scalars().all()

        # Group by level and side
        level_orders = {}
        for order in orders:
            key = (order.level, order.side)
            if key not in level_orders:
                level_orders[key] = []
            level_orders[key].append(order)

        # Find duplicates
        has_duplicates = any(len(orders) > 1 for orders in level_orders.values())

        return {
            'has_duplicates': has_duplicates,
            'removed': 0  # Will be fixed in auto_fix_bot
        }

    async def _fix_duplicate_orders(self, bot: GridBot) -> dict:
        """Fix duplicate orders by cancelling extras."""
        result = await self.db.execute(
            select(GridOrder).where(
                GridOrder.grid_bot_id == bot.id,
                GridOrder.status == 'open'
            )
        )
        orders = result.scalars().all()

        level_orders = {}
        for order in orders:
            key = (order.level, order.side)
            if key not in level_orders:
                level_orders[key] = []
            level_orders[key].append(order)

        removed = 0
        for key, dup_orders in level_orders.items():
            if len(dup_orders) > 1:
                # Keep first, cancel rest
                for order in dup_orders[1:]:
                    try:
                        await self.mexc.cancel_order(
                            user_id=bot.user_id,
                            symbol=bot.symbol,
                            order_id=order.exchange_order_id
                        )
                        order.status = 'cancelled'
                        removed += 1
                    except Exception as e:
                        logger.error(f"Failed to cancel duplicate: {e}")

        if removed > 0:
            await self.db.commit()

        return {'fixed': removed > 0, 'count': removed}

    async def _check_balance(self, bot: GridBot) -> dict:
        """Check if user has sufficient balance."""
        try:
            balances = await self.mexc.get_balance(bot.user_id)
            quote_currency = bot.quote_currency or 'USDT'
            current_balance = balances.get(quote_currency, Decimal('0'))

            # Recommend at least 20% of investment as buffer
            recommended = bot.investment_amount * Decimal('0.2')

            return {
                'sufficient': current_balance >= recommended,
                'current': current_balance,
                'recommended': recommended
            }
        except Exception:
            return {
                'sufficient': True,  # Don't fail health check on balance error
                'current': Decimal('0'),
                'recommended': Decimal('0')
            }

    async def run_periodic_health_check(self, db_factory):
        """
        Periodic health check for all active bots.

        Runs every 5 minutes (from config).

        Algorithm:
        1. Load all bots with status = 'active'
        2. For each bot:
           - Run check_bot_health()
           - If issues found → auto_fix_bot()
           - Log results

        Args:
            db_factory: Function to create DB session
        """
        while True:
            try:
                async with db_factory() as db:
                    self.db = db

                    # Load active bots
                    result = await db.execute(
                        select(GridBot).where(GridBot.status == 'active')
                    )
                    active_bots = result.scalars().all()

                    logger.info(f"Running health check for {len(active_bots)} active bots")

                    for bot in active_bots:
                        try:
                            health_result = await self.check_bot_health(bot.id)

                            if not health_result['healthy']:
                                logger.warning(
                                    f"Bot {bot.id} health issues: {health_result['issues']}"
                                )

                                # Auto-fix if possible
                                fix_result = await self.auto_fix_bot(
                                    bot.id,
                                    health_result['issues']
                                )

                                if fix_result['fixed']:
                                    logger.info(
                                        f"Bot {bot.id} auto-fixed: {fix_result['fixed']}"
                                    )

                        except Exception as e:
                            logger.error(f"Error checking bot {bot.id} health: {e}")
                            continue

                # Wait for next check
                await asyncio.sleep(settings.HEALTH_CHECK_INTERVAL)

            except Exception as e:
                logger.error(f"Error in periodic health check: {e}")
                await asyncio.sleep(60)  # Wait 1 minute on error
