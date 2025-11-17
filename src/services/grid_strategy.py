"""Grid Trading Strategy implementation."""
from decimal import Decimal
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import logging
import math
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.grid_bot import GridBot
from src.models.order import GridOrder
from src.models.bot_log import BotLog
from src.services.mexc_service import MEXCService, MEXCError
from src.utils.helpers import parse_decimal, round_down, split_symbol
from src.utils.validators import validate_price_range, validate_grid_levels

logger = logging.getLogger(__name__)


def _get_precision_unit(amount_precision) -> Decimal:
    """
    Calculate precision unit from either decimal places (int) or step size (float).

    Args:
        amount_precision: Either number of decimal places (int >= 1) or step size (float < 1)

    Returns:
        Precision unit as Decimal (e.g., 0.001 for 3 decimal places)

    Examples:
        >>> _get_precision_unit(3)  # 3 decimal places
        Decimal('0.001')
        >>> _get_precision_unit(0.001)  # step size of 0.001
        Decimal('0.001')
        >>> _get_precision_unit(8)  # 8 decimal places
        Decimal('0.00000001')
    """
    # Convert to float for comparison
    precision_value = float(amount_precision)

    # If precision is >= 1, it's the number of decimal places (int)
    if precision_value >= 1:
        decimal_places = int(precision_value)
    else:
        # If precision is < 1, it's a step size (float), calculate decimal places
        # For 0.001: log10(0.001) = -3, so decimal_places = 3
        # For 0.01: log10(0.01) = -2, so decimal_places = 2
        if precision_value > 0:
            decimal_places = -int(math.floor(math.log10(precision_value)))
        else:
            # Edge case: if precision is 0 or invalid, default to 8
            decimal_places = 8

    return Decimal('10') ** -decimal_places


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
            # Each order should have the same USDT value (order_size)
            amount = order_size / price
            amount = round_down(amount, amount_precision)

            # Ensure amount meets minimum requirement
            if amount < min_order_amount:
                amount = min_order_amount

            # Also ensure the order cost meets the order_size
            # If amount * price < order_size, increase amount
            order_cost = amount * price
            if order_cost < order_size:
                # Recalculate amount to meet exact order_size
                amount = order_size / price
                # Round up to next precision to ensure we meet minimum cost
                amount = round_down(amount, amount_precision)
                # Add one more unit of precision to be safe
                precision_unit = _get_precision_unit(amount_precision)
                amount = amount + precision_unit

                logger.info(
                    f"Adjusted amount to {amount} to meet order_size ${order_size} "
                    f"at price ${price}"
                )

            amounts[i] = amount

        # Calculate amounts for sell orders (higher price levels)
        # Sell orders start from the middle of price_levels
        for i in range(num_sell_levels):
            level_idx = num_buy_levels + i
            price = price_levels[level_idx]
            # Each order should have the same USDT value (order_size)
            amount = order_size / price
            amount = round_down(amount, amount_precision)

            # Ensure amount meets minimum requirement
            if amount < min_order_amount:
                amount = min_order_amount

            # Also ensure the order cost meets the order_size
            order_cost = amount * price
            if order_cost < order_size:
                # Recalculate amount to meet exact order_size
                amount = order_size / price
                # Round up to next precision to ensure we meet minimum cost
                amount = round_down(amount, amount_precision)
                # Add one more unit of precision to be safe
                precision_unit = _get_precision_unit(amount_precision)
                amount = amount + precision_unit

                logger.info(
                    f"Adjusted amount to {amount} to meet order_size ${order_size} "
                    f"at price ${price}"
                )

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

    async def create_flat_grid_orders(
        self,
        grid_bot_id: int,
        starting_price: Decimal
    ) -> Dict[str, any]:
        """
        Create initial orders for flat grid bot.

        Flat grid algorithm:
        1. Place buy orders BELOW starting_price with flat_increment steps
        2. Place sell orders ABOVE starting_price with flat_increment steps
        3. Buy base currency at market for sell orders
        4. Each order has the same size (order_size in USDT)

        Args:
            grid_bot_id: Grid bot ID
            starting_price: Starting price (center of grid)

        Returns:
            {
                'buy_orders': [...],
                'sell_orders': [...],
                'market_order': {...},
                'total_orders': int
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

        if bot.grid_type != 'flat':
            raise GridStrategyError("This method is only for flat grid bots")

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

            buy_orders = []
            sell_orders = []
            market_order = None

            # Create BUY limit orders (below starting price)
            for i in range(1, bot.buy_orders_count + 1):
                # Calculate price: starting_price - (i * flat_increment)
                price = starting_price - (bot.flat_increment * Decimal(str(i)))
                price = round_down(price, price_precision)

                # Calculate amount: order_size / price
                amount = bot.order_size / price
                amount = round_down(amount, amount_precision)

                # Ensure amount meets minimum requirement
                if amount < min_order_amount:
                    amount = min_order_amount

                # CRITICAL FIX: Ensure order cost meets order_size after rounding
                order_cost = amount * price
                if order_cost < bot.order_size:
                    # Recalculate to meet exact order_size
                    amount = bot.order_size / price
                    amount = round_down(amount, amount_precision)
                    # Add one precision unit to ensure we meet minimum cost
                    precision_unit = Decimal('10') ** -int(amount_precision)
                    amount = amount + precision_unit

                    logger.debug(
                        f"Adjusted buy amount to {amount} to meet order_size "
                        f"${bot.order_size} at price ${price}"
                    )

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
                        level=i,  # Level 1, 2, 3, ...
                        price=price,
                        amount=amount,
                        total=price * amount,
                        status='open'
                    )
                    self.db.add(db_order)
                    buy_orders.append(order)

                    logger.info(
                        f"Created buy order at level {i}: "
                        f"{amount} @ ${price} (${price * amount:.2f})"
                    )

                except MEXCError as e:
                    logger.error(f"Failed to create buy order at level {i}: {e}")
                    # Continue with other orders

            # Calculate total amount needed for sell orders
            total_sell_amount = Decimal('0')
            for i in range(1, bot.sell_orders_count + 1):
                price = starting_price + (bot.flat_increment * Decimal(str(i)))
                amount = bot.order_size / price
                amount = round_down(amount, amount_precision)
                if amount < min_order_amount:
                    amount = min_order_amount
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

                    # For market buy, we need to pass cost in quote currency
                    cost = buy_amount * starting_price

                    # Get quote currency precision (USDT/USDC = 2, BTC = 8, etc)
                    quote_currency = bot.symbol.split('/')[1] if '/' in bot.symbol else 'USDT'
                    # Most stablecoins use 2 decimals, others use 8
                    quote_precision = 2 if quote_currency in ['USDT', 'USDC', 'BUSD', 'DAI'] else 8
                    cost = round_down(cost, quote_precision)

                    # Ensure cost is positive
                    if cost <= 0:
                        logger.error(f"Calculated cost is {cost}, too small for market order")
                        raise MEXCError("Order size too small")

                    logger.info(
                        f"Buying {buy_amount} {bot.symbol.split('/')[0]} for sell orders "
                        f"(cost: ${cost} {quote_currency}, needed: {total_sell_amount})"
                    )

                    # Buy base currency at market price (pass cost in quote currency)
                    market_order = await self.mexc.create_market_order(
                        user_id=bot.user_id,
                        symbol=bot.symbol,
                        side='buy',
                        amount=cost  # Pass cost in quote currency for market buy
                    )

                    logger.info(
                        f"Bought {buy_amount} at market price: "
                        f"{market_order.get('average_price', 'N/A')}"
                    )

                except MEXCError as e:
                    logger.error(f"Failed to buy base currency for sell orders: {e}")
                    # If we can't buy base currency, we can't create sell orders
                    logger.warning("Sell orders will not be created due to market buy failure")
                    total_sell_amount = Decimal('0')  # Skip sell order creation

            # Create SELL limit orders (above starting price)
            if total_sell_amount > 0:
                for i in range(1, bot.sell_orders_count + 1):
                    # Calculate price: starting_price + (i * flat_increment)
                    price = starting_price + (bot.flat_increment * Decimal(str(i)))
                    price = round_down(price, price_precision)

                    # Calculate amount: order_size / price
                    amount = bot.order_size / price
                    amount = round_down(amount, amount_precision)

                    # Ensure amount meets minimum requirement
                    if amount < min_order_amount:
                        amount = min_order_amount

                    # CRITICAL FIX: Ensure order cost meets order_size after rounding
                    order_cost = amount * price
                    if order_cost < bot.order_size:
                        # Recalculate to meet exact order_size
                        amount = bot.order_size / price
                        amount = round_down(amount, amount_precision)
                        # Add one precision unit to ensure we meet minimum cost
                        precision_unit = Decimal('10') ** -int(amount_precision)
                        amount = amount + precision_unit

                        logger.debug(
                            f"Adjusted sell amount to {amount} to meet order_size "
                            f"${bot.order_size} at price ${price}"
                        )

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
                            level=i,  # Level 1, 2, 3, ...
                            price=price,
                            amount=amount,
                            total=price * amount,
                            status='open'
                        )
                        self.db.add(db_order)
                        sell_orders.append(order)

                        logger.info(
                            f"Created sell order at level {i}: "
                            f"{amount} @ ${price} (${price * amount:.2f})"
                        )

                    except MEXCError as e:
                        logger.error(f"Failed to create sell order at level {i}: {e}")
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
            logger.error(f"Error creating flat grid orders: {e}")
            await self.db.rollback()
            raise GridStrategyError(f"Ошибка создания ордеров: {str(e)}")

    async def handle_filled_order_flat(self, order_id: int) -> dict:
        """
        Handle filled order for FLAT GRID bot and create counter order.

        Flat Grid Algorithm:
        1. Load order from DB
        2. Update status = 'filled', filled_at = NOW()
        3. IF order.side == 'buy':
               Create SELL order at: filled_price + flat_spread
        4. ELIF order.side == 'sell':
               Create BUY order at: filled_price - flat_spread
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

        if bot.grid_type != 'flat':
            raise GridStrategyError("This method is only for flat grid bots")

        try:
            # Update order status
            order.status = 'filled'
            order.filled_at = datetime.utcnow()

            # Get exchange info
            exchange_info = await self.mexc.get_exchange_info(bot.symbol)
            amount_precision = exchange_info['amount_precision']
            price_precision = exchange_info['price_precision']
            min_order_amount = exchange_info['min_order_amount']

            new_order = None
            profit = None
            cycle_completed = False

            if order.is_buy:
                # BUY order filled → create SELL order above at filled_price + flat_spread
                sell_price = order.price + bot.flat_spread
                sell_price = round_down(sell_price, price_precision)

                # Calculate amount based on order_size / price
                sell_amount = bot.order_size / sell_price
                sell_amount = round_down(sell_amount, amount_precision)

                # Ensure amount meets minimum requirement
                if sell_amount < min_order_amount:
                    sell_amount = min_order_amount

                # CRITICAL FIX: Ensure order cost meets order_size after rounding
                order_cost = sell_amount * sell_price
                if order_cost < bot.order_size:
                    # Recalculate to meet exact order_size
                    sell_amount = bot.order_size / sell_price
                    sell_amount = round_down(sell_amount, amount_precision)
                    # Add one precision unit to ensure we meet minimum cost
                    precision_unit = Decimal('10') ** -int(amount_precision)
                    sell_amount = sell_amount + precision_unit

                try:
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
                        level=order.level,  # Keep same level for flat grid
                        price=sell_price,
                        amount=sell_amount,
                        total=sell_price * sell_amount,
                        status='open',
                        paired_order_id=order.id
                    )
                    self.db.add(new_order)

                    logger.info(
                        f"[Flat Grid] Created sell order after buy filled: "
                        f"{sell_amount} @ ${sell_price} (spread: ${bot.flat_spread})"
                    )

                except Exception as e:
                    logger.error(f"Failed to create sell order after buy filled: {e}")
                    # Continue anyway - order is filled

            elif order.is_sell:
                # SELL order filled → create BUY order below at filled_price - flat_spread
                buy_price = order.price - bot.flat_spread
                buy_price = round_down(buy_price, price_precision)

                # Ensure buy price is positive
                if buy_price <= 0:
                    logger.warning(
                        f"Calculated buy price {buy_price} is <= 0, skipping buy order creation"
                    )
                else:
                    # Calculate amount based on order_size / price
                    buy_amount = bot.order_size / buy_price
                    buy_amount = round_down(buy_amount, amount_precision)

                    # Ensure amount meets minimum requirement
                    if buy_amount < min_order_amount:
                        buy_amount = min_order_amount

                    # CRITICAL FIX: Ensure order cost meets order_size after rounding
                    order_cost = buy_amount * buy_price
                    if order_cost < bot.order_size:
                        # Recalculate to meet exact order_size
                        buy_amount = bot.order_size / buy_price
                        buy_amount = round_down(buy_amount, amount_precision)
                        # Add one precision unit to ensure we meet minimum cost
                        precision_unit = Decimal('10') ** -int(amount_precision)
                        buy_amount = buy_amount + precision_unit

                    try:
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
                            level=order.level,  # Keep same level for flat grid
                            price=buy_price,
                            amount=buy_amount,
                            total=buy_price * buy_amount,
                            status='open'
                        )
                        self.db.add(new_order)

                        logger.info(
                            f"[Flat Grid] Created buy order after sell filled: "
                            f"{buy_amount} @ ${buy_price} (spread: ${bot.flat_spread})"
                        )

                    except Exception as e:
                        logger.error(f"Failed to create buy order after sell filled: {e}")
                        # Continue anyway - order is filled

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

                        # For flat grid, calculate profit percent based on total capital
                        total_capital = (bot.buy_orders_count + bot.sell_orders_count) * bot.order_size
                        bot.total_profit_percent = (bot.total_profit / total_capital) * 100
                        bot.completed_cycles += 1
                        cycle_completed = True

                        logger.info(f"[Flat Grid] Cycle completed! Profit: ${profit}")

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
            logger.error(f"Error handling flat grid filled order {order_id}: {e}")
            await self.db.rollback()
            raise GridStrategyError(f"Ошибка обработки ордера: {str(e)}")

    async def handle_filled_order(self, order_id: int) -> dict:
        """
        Handle filled order and create counter order.

        This method routes to the appropriate handler based on grid type:
        - Flat grid → handle_filled_order_flat()
        - Range grid → handles inline

        Algorithm for Range Grid:
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
        # Load order from DB to check grid type
        result = await self.db.execute(
            select(GridOrder).where(GridOrder.id == order_id)
        )
        order = result.scalar_one_or_none()

        if not order:
            raise GridStrategyError(f"Order {order_id} not found")

        # Load bot to check grid type
        result = await self.db.execute(
            select(GridBot).where(GridBot.id == order.grid_bot_id)
        )
        bot = result.scalar_one_or_none()

        if not bot:
            raise GridStrategyError(f"Grid bot {order.grid_bot_id} not found")

        # Route to appropriate handler based on grid type
        if bot.grid_type == 'flat':
            logger.info(f"Routing to flat grid handler for order {order_id}")
            return await self.handle_filled_order_flat(order_id)

        # Range grid logic (original implementation)
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
