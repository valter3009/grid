"""Grid Trading Strategy implementation."""
from decimal import Decimal
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.grid_bot import GridBot
from src.models.order import GridOrder
from src.models.bot_log import BotLog
from src.services.mexc_service import MEXCService, MEXCError
from src.utils.helpers import parse_decimal, round_down, split_symbol
from src.utils.validators import validate_price_range, validate_grid_levels

logger = logging.getLogger(__name__)


class GridStrategyError(Exception):
    """Grid strategy error."""
    pass


class GridStrategy:
    """Grid Trading strategy implementation."""

    def __init__(self, db: AsyncSession, mexc_service: MEXCService):
        """Initialize grid strategy."""
        self.db = db
        self.mexc = mexc_service

    @staticmethod
    def calculate_grid_levels(
        lower_price: Decimal,
        upper_price: Decimal,
        grid_levels: int,
        grid_type: str = 'arithmetic'
    ) -> List[Decimal]:
        """
        Calculate grid price levels.

        For MVP only arithmetic (equal intervals):
        step = (upper_price - lower_price) / grid_levels
        levels = [lower_price + step * i for i in range(grid_levels + 1)]

        Args:
            lower_price: Lower boundary price
            upper_price: Upper boundary price
            grid_levels: Number of grid levels
            grid_type: Grid type (only 'arithmetic' for MVP)

        Returns:
            List of price levels

        Raises:
            GridStrategyError: If grid type is not supported
        """
        # Validate inputs
        validate_price_range(lower_price, upper_price)
        validate_grid_levels(grid_levels)

        if grid_type != 'arithmetic':
            raise GridStrategyError("Только arithmetic grid поддерживается в MVP")

        # Calculate step
        step = (upper_price - lower_price) / Decimal(str(grid_levels))

        # Generate levels
        levels = []
        for i in range(grid_levels + 1):
            level = lower_price + (step * Decimal(str(i)))
            levels.append(level)

        return levels

    @staticmethod
    def calculate_order_amounts(
        order_size: Decimal,
        grid_levels: int,
        current_price: Decimal,
        price_levels: List[Decimal],
        amount_precision: int = 8,
        min_order_amount: Decimal = Decimal('0')
    ) -> Dict[int, Decimal]:
        """
        Calculate order amounts for each grid level based on fixed order size.

        New Algorithm:
        1. Each order (buy and sell) has the same size in quote currency (USDT)
        2. For buy orders: amount = order_size / price
        3. For sell orders: amount = order_size / price
        4. Grid levels are split 50/50: half below current price, half above
        5. Ensure all amounts meet minimum order requirements

        Args:
            order_size: Size of each order in quote currency (USDT)
            grid_levels: Number of grid levels (must be even)
            current_price: Current market price
            price_levels: List of grid price levels
            amount_precision: Precision for amounts
            min_order_amount: Minimum order amount from exchange

        Returns:
            Dictionary {level_index: amount_in_base_currency}
        """
        amounts = {}

        # Grid levels are split evenly: half buy, half sell
        # Price levels are arranged from low to high
        # We want equal number of buy and sell orders
        num_buy_levels = grid_levels // 2
        num_sell_levels = grid_levels // 2

        # Calculate amounts for buy orders (lower price levels)
        for i in range(num_buy_levels):
            price = price_levels[i]
            # Each order has the same USDT value
            amount = order_size / price
            amount = round_down(amount, amount_precision)

            # Ensure amount meets minimum requirement
            if amount < min_order_amount:
                logger.warning(
                    f"Calculated amount {amount} is less than minimum {min_order_amount}, "
                    f"using minimum amount"
                )
                amount = min_order_amount

            amounts[i] = amount

        # Calculate amounts for sell orders (higher price levels)
        # Sell orders start from the middle of price_levels
        for i in range(num_sell_levels):
            level_idx = num_buy_levels + i
            price = price_levels[level_idx]
            # Each order has the same USDT value
            amount = order_size / price
            amount = round_down(amount, amount_precision)

            # Ensure amount meets minimum requirement
            if amount < min_order_amount:
                logger.warning(
                    f"Calculated amount {amount} is less than minimum {min_order_amount}, "
                    f"using minimum amount"
                )
                amount = min_order_amount

            amounts[level_idx] = amount

        return amounts

    async def create_initial_orders(
        self,
        grid_bot_id: int,
        current_price: Decimal
    ) -> Dict[str, any]:
        """
        Create initial orders when starting grid bot.

        IMPORTANT: Solution #3 - Buy asset at market price immediately.

        Algorithm:
        1. Get bot parameters from DB
        2. Calculate grid levels
        3. Determine current level (closest to current_price)
        4. Create BUY limit orders BELOW current_price
        5. Buy asset for SELL orders AT MARKET
        6. Create SELL limit orders ABOVE current_price

        Args:
            grid_bot_id: Grid bot ID
            current_price: Current market price

        Returns:
            {
                'buy_orders': [...],
                'sell_orders': [...],
                'market_order': {...}
            }

        Raises:
            GridStrategyError: If order creation fails
        """
        # Load bot from DB
        result = await self.db.execute(
            select(GridBot).where(GridBot.id == grid_bot_id)
        )
        bot = result.scalar_one_or_none()

        if not bot:
            raise GridStrategyError(f"Grid bot {grid_bot_id} not found")

        try:
            # Get exchange info
            exchange_info = await self.mexc.get_exchange_info(bot.symbol)
            amount_precision = exchange_info['amount_precision']
            price_precision = exchange_info['price_precision']
            min_order_amount = exchange_info['min_order_amount']

            logger.info(
                f"Exchange info for {bot.symbol}: "
                f"min_amount={min_order_amount}, "
                f"amount_precision={amount_precision}, "
                f"price_precision={price_precision}"
            )

            # Calculate grid levels
            price_levels = self.calculate_grid_levels(
                bot.lower_price,
                bot.upper_price,
                bot.grid_levels,
                bot.grid_type
            )

            # Calculate order amounts (bot.investment_amount now stores order_size)
            order_amounts = self.calculate_order_amounts(
                bot.investment_amount,  # This is actually order_size now
                bot.grid_levels,
                current_price,
                price_levels,
                amount_precision,
                min_order_amount
            )

            buy_orders = []
            sell_orders = []
            market_order = None

            # Grid is split 50/50: first half are buy orders, second half are sell orders
            num_buy_levels = bot.grid_levels // 2

            # Create BUY limit orders (first half of price_levels - lower prices)
            for level_idx, amount in order_amounts.items():
                if level_idx < num_buy_levels:
                    price = round_down(price_levels[level_idx], price_precision)

                    try:
                        order = await self.mexc.create_limit_order(
                            user_id=bot.user_id,
                            symbol=bot.symbol,
                            side='buy',
                            price=price,
                            amount=amount
                        )

                        # Save to DB
                        db_order = GridOrder(
                            grid_bot_id=grid_bot_id,
                            exchange_order_id=order['order_id'],
                            side='buy',
                            order_type='limit',
                            level=level_idx,
                            price=price,
                            amount=amount,
                            total=price * amount,
                            status='open'
                        )
                        self.db.add(db_order)
                        buy_orders.append(order)

                        logger.info(
                            f"Created buy order at level {level_idx}: "
                            f"{amount} @ {price}"
                        )

                    except MEXCError as e:
                        logger.error(f"Failed to create buy order at level {level_idx}: {e}")
                        # Continue with other orders

            # Calculate total amount needed for sell orders (second half of levels)
            total_sell_amount = Decimal('0')
            for level_idx, amount in order_amounts.items():
                if level_idx >= num_buy_levels:
                    total_sell_amount += amount

            # Buy base currency for sell orders if needed
            if total_sell_amount > 0:
                try:
                    # Add 2% buffer for safety (to cover fees and slippage)
                    buy_amount = total_sell_amount * Decimal('1.02')
                    buy_amount = round_down(buy_amount, amount_precision)

                    # Ensure buy_amount is not zero
                    if buy_amount < min_order_amount:
                        buy_amount = min_order_amount

                    # For market buy, we need to pass cost in quote currency (USDT)
                    # MEXC requires cost = amount * price for market buy orders
                    cost = buy_amount * current_price
                    cost = round_down(cost, 2)  # USDT has 2 decimals precision

                    # Ensure cost is positive
                    if cost <= 0:
                        logger.error(f"Calculated cost is {cost}, too small for market order")
                        raise MEXCError("Order size too small")

                    logger.info(
                        f"Buying {buy_amount} {bot.symbol.split('/')[0]} for sell orders "
                        f"(cost: ${cost}, needed: {total_sell_amount})"
                    )

                    # Buy base currency at market price (pass cost, not amount)
                    market_order = await self.mexc.create_market_order(
                        user_id=bot.user_id,
                        symbol=bot.symbol,
                        side='buy',
                        amount=cost  # Pass cost in USDT for market buy
                    )

                    logger.info(
                        f"Bought {buy_amount} at market price: "
                        f"{market_order.get('average_price', 'N/A')}"
                    )

                except MEXCError as e:
                    logger.error(f"Failed to buy base currency for sell orders: {e}")
                    # If we can't buy base currency, we can't create sell orders
                    # But we still have buy orders created, so bot is partially functional
                    logger.warning("Sell orders will not be created due to market buy failure")
                    total_sell_amount = Decimal('0')  # Skip sell order creation

            # Create SELL limit orders (second half of price_levels - higher prices)
            if total_sell_amount > 0:
                for level_idx, amount in order_amounts.items():
                    if level_idx >= num_buy_levels:
                        price = round_down(price_levels[level_idx], price_precision)

                        try:
                            order = await self.mexc.create_limit_order(
                                user_id=bot.user_id,
                                symbol=bot.symbol,
                                side='sell',
                                price=price,
                                amount=amount
                            )

                            # Save to DB
                            db_order = GridOrder(
                                grid_bot_id=grid_bot_id,
                                exchange_order_id=order['order_id'],
                                side='sell',
                                order_type='limit',
                                level=level_idx,
                                price=price,
                                amount=amount,
                                total=price * amount,
                                status='open'
                            )
                            self.db.add(db_order)
                            sell_orders.append(order)

                            logger.info(
                                f"Created sell order at level {level_idx}: "
                                f"{amount} @ {price}"
                            )

                        except MEXCError as e:
                            logger.error(f"Failed to create sell order at level {level_idx}: {e}")
                            # Continue with other orders

            # Commit all orders to DB
            await self.db.commit()

            # Update bot statistics
            bot.total_buy_orders = len(buy_orders)
            bot.total_sell_orders = len(sell_orders)
            await self.db.commit()

            return {
                'buy_orders': buy_orders,
                'sell_orders': sell_orders,
                'market_order': market_order,
                'total_orders': len(buy_orders) + len(sell_orders)
            }

        except Exception as e:
            logger.error(f"Error creating initial orders: {e}")
            await self.db.rollback()
            raise GridStrategyError(f"Ошибка создания ордеров: {str(e)}")

    async def handle_filled_order(self, order_id: int) -> dict:
        """
        Handle filled order and create counter order.

        Algorithm:
        1. Load order from DB
        2. Update status = 'filled', filled_at = NOW()
        3. IF order.side == 'buy':
               Create SELL order at next level up
        4. ELIF order.side == 'sell':
               Create BUY order at next level down
               Calculate profit from cycle
               Update bot statistics
        5. Send notification to user

        Args:
            order_id: Order ID in database

        Returns:
            {
                'new_order': {...},
                'profit': Decimal (if sell),
                'cycle_completed': bool
            }

        Raises:
            GridStrategyError: If processing fails
        """
        # Load order from DB
        result = await self.db.execute(
            select(GridOrder).where(GridOrder.id == order_id)
        )
        order = result.scalar_one_or_none()

        if not order:
            raise GridStrategyError(f"Order {order_id} not found")

        # Load bot
        result = await self.db.execute(
            select(GridBot).where(GridBot.id == order.grid_bot_id)
        )
        bot = result.scalar_one_or_none()

        if not bot:
            raise GridStrategyError(f"Grid bot {order.grid_bot_id} not found")

        try:
            # Update order status
            order.status = 'filled'
            order.filled_at = datetime.utcnow()

            # Get exchange info
            exchange_info = await self.mexc.get_exchange_info(bot.symbol)
            amount_precision = exchange_info['amount_precision']
            price_precision = exchange_info['price_precision']

            # Calculate grid levels
            price_levels = self.calculate_grid_levels(
                bot.lower_price,
                bot.upper_price,
                bot.grid_levels,
                bot.grid_type
            )

            new_order = None
            profit = None
            cycle_completed = False

            if order.is_buy:
                # BUY order filled → create SELL order above
                next_level = order.level + 1

                if next_level < len(price_levels):
                    sell_price = round_down(price_levels[next_level], price_precision)
                    sell_amount = order.amount

                    # Create sell order
                    mexc_order = await self.mexc.create_limit_order(
                        user_id=bot.user_id,
                        symbol=bot.symbol,
                        side='sell',
                        price=sell_price,
                        amount=sell_amount
                    )

                    # Save to DB
                    new_order = GridOrder(
                        grid_bot_id=bot.id,
                        exchange_order_id=mexc_order['order_id'],
                        side='sell',
                        order_type='limit',
                        level=next_level,
                        price=sell_price,
                        amount=sell_amount,
                        total=sell_price * sell_amount,
                        status='open',
                        paired_order_id=order.id
                    )
                    self.db.add(new_order)

                    logger.info(
                        f"Created sell order at level {next_level} "
                        f"after buy filled: {sell_amount} @ {sell_price}"
                    )

            elif order.is_sell:
                # SELL order filled → create BUY order below
                prev_level = order.level - 1

                if prev_level >= 0:
                    buy_price = round_down(price_levels[prev_level], price_precision)
                    buy_amount = order.amount

                    # Create buy order
                    mexc_order = await self.mexc.create_limit_order(
                        user_id=bot.user_id,
                        symbol=bot.symbol,
                        side='buy',
                        price=buy_price,
                        amount=buy_amount
                    )

                    # Save to DB
                    new_order = GridOrder(
                        grid_bot_id=bot.id,
                        exchange_order_id=mexc_order['order_id'],
                        side='buy',
                        order_type='limit',
                        level=prev_level,
                        price=buy_price,
                        amount=buy_amount,
                        total=buy_price * buy_amount,
                        status='open'
                    )
                    self.db.add(new_order)

                    logger.info(
                        f"Created buy order at level {prev_level} "
                        f"after sell filled: {buy_amount} @ {buy_price}"
                    )

                # Calculate profit if there's a paired buy order
                if order.paired_order_id:
                    result = await self.db.execute(
                        select(GridOrder).where(GridOrder.id == order.paired_order_id)
                    )
                    paired_buy_order = result.scalar_one_or_none()

                    if paired_buy_order:
                        profit = self.calculate_profit(paired_buy_order, order)
                        order.profit = profit

                        # Update bot statistics
                        bot.total_profit += profit
                        bot.total_profit_percent = (bot.total_profit / bot.investment_amount) * 100
                        bot.completed_cycles += 1
                        cycle_completed = True

                        logger.info(f"Cycle completed! Profit: {profit}")

            # Update bot activity
            bot.last_activity_at = datetime.utcnow()

            # Commit changes
            await self.db.commit()

            return {
                'new_order': {
                    'id': new_order.id if new_order else None,
                    'side': new_order.side if new_order else None,
                    'price': new_order.price if new_order else None,
                    'amount': new_order.amount if new_order else None,
                } if new_order else None,
                'profit': profit,
                'cycle_completed': cycle_completed,
                'filled_order': {
                    'id': order.id,
                    'side': order.side,
                    'price': order.price,
                    'amount': order.amount,
                }
            }

        except Exception as e:
            logger.error(f"Error handling filled order {order_id}: {e}")
            await self.db.rollback()
            raise GridStrategyError(f"Ошибка обработки ордера: {str(e)}")

    @staticmethod
    def calculate_profit(buy_order: GridOrder, sell_order: GridOrder) -> Decimal:
        """
        Calculate profit from a completed cycle.

        profit = (sell_price * sell_amount) - (buy_price * buy_amount)
                 - buy_fee - sell_fee

        Args:
            buy_order: Buy order
            sell_order: Sell order

        Returns:
            Profit in quote currency (USDT)
        """
        buy_cost = buy_order.price * buy_order.amount + (buy_order.fee or Decimal('0'))
        sell_revenue = sell_order.price * sell_order.amount - (sell_order.fee or Decimal('0'))

        profit = sell_revenue - buy_cost

        return profit
