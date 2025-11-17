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


def calculate_order_amount_for_cost(
    order_size: Decimal,
    price: Decimal,
    amount_precision,
    min_order_amount: Decimal
) -> Decimal:
    """
    Calculate order amount to achieve EXACT cost in quote currency.

    This ensures all orders have the same cost in quote currency (e.g., 5 USDT),
    while the amount in base currency (e.g., SOL) varies by price level.

    Args:
        order_size: Target cost in quote currency (e.g., 5 USDT)
        price: Order price (e.g., 130.5 USDT/SOL)
        amount_precision: Exchange precision for amount
        min_order_amount: Minimum order amount

    Returns:
        Amount in base currency that will cost >= order_size in quote currency

    Example:
        order_size = 5 USDT, price = 130 USDT/SOL
        Initial amount = 5 / 130 = 0.0384615 SOL
        After round_down(0.001) = 0.038 SOL → cost = 4.94 USDT ❌
        After adjustment = 0.039 SOL → cost = 5.07 USDT ✓
    """
    # Calculate ideal amount
    amount = order_size / price

    # Round down to exchange precision
    amount = round_down(amount, amount_precision)

    # Determine precision step
    if isinstance(amount_precision, (float, Decimal)):
        precision_step = Decimal(str(amount_precision))
    else:
        precision_step = Decimal('10') ** -int(amount_precision)

    # CRITICAL: Increase amount until cost >= order_size
    # This ensures consistent cost in quote currency across all orders
    cost = amount * price
    max_iterations = 100  # Safety check
    iterations = 0

    while cost < order_size and iterations < max_iterations:
        amount += precision_step
        cost = amount * price
        iterations += 1

    # Ensure amount meets exchange minimum requirement
    if amount < min_order_amount:
        amount = min_order_amount

    return amount


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
            # Calculate amount to achieve exact cost in quote currency
            amount = calculate_order_amount_for_cost(
                order_size=order_size,
                price=price,
                amount_precision=amount_precision,
                min_order_amount=min_order_amount
            )
            amounts[i] = amount

        # Calculate amounts for sell orders (higher price levels)
        # Sell orders start from the middle of price_levels
        for i in range(num_sell_levels):
            level_idx = num_buy_levels + i
            price = price_levels[level_idx]
            # Calculate amount to achieve exact cost in quote currency
            amount = calculate_order_amount_for_cost(
                order_size=order_size,
                price=price,
                amount_precision=amount_precision,
                min_order_amount=min_order_amount
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
                    # Add 3% buffer for safety (to cover fees and slippage)
                    buy_amount = total_sell_amount * Decimal('1.03')
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

            # Prepare all BUY order parameters (parallel creation)
            import asyncio
            buy_order_params = []
            for i in range(1, bot.buy_orders_count + 1):
                # Calculate price: starting_price - (i * flat_increment)
                price = starting_price - (bot.flat_increment * Decimal(str(i)))
                price = round_down(price, price_precision)

                # Calculate amount to achieve exact cost in quote currency
                amount = calculate_order_amount_for_cost(
                    order_size=bot.order_size,
                    price=price,
                    amount_precision=amount_precision,
                    min_order_amount=min_order_amount
                )

                buy_order_params.append({
                    'level': i,
                    'price': price,
                    'amount': amount
                })

            # Create helper function to create single order
            async def create_buy_order(params):
                try:
                    order = await self.mexc.create_limit_order(
                        user_id=bot.user_id,
                        symbol=bot.symbol,
                        side='buy',
                        price=params['price'],
                        amount=params['amount']
                    )

                    # Save to DB
                    db_order = GridOrder(
                        grid_bot_id=grid_bot_id,
                        exchange_order_id=order['order_id'],
                        side='buy',
                        order_type='limit',
                        level=params['level'],
                        price=params['price'],
                        amount=params['amount'],
                        total=params['price'] * params['amount'],
                        status='open'
                    )
                    self.db.add(db_order)

                    logger.info(
                        f"Created buy order at level {params['level']}: "
                        f"{params['amount']} @ ${params['price']} (${params['price'] * params['amount']:.2f})"
                    )
                    return order

                except MEXCError as e:
                    logger.error(f"Failed to create buy order at level {params['level']}: {e}")
                    return None

            # Create all BUY orders in parallel (with rate limiting)
            # MEXC allows ~20 requests/sec, we use 10 concurrent to be safe
            semaphore = asyncio.Semaphore(10)

            async def create_with_limit(params):
                async with semaphore:
                    return await create_buy_order(params)

            # Execute all in parallel
            buy_results = await asyncio.gather(*[
                create_with_limit(params) for params in buy_order_params
            ], return_exceptions=True)

            # Filter successful orders
            buy_orders = [order for order in buy_results if order and not isinstance(order, Exception)]
            logger.info(f"Created {len(buy_orders)}/{len(buy_order_params)} buy orders successfully")

            # Calculate total amount needed for sell orders
            # Use same calculation method to ensure accuracy
            total_sell_amount = Decimal('0')
            for i in range(1, bot.sell_orders_count + 1):
                price = starting_price + (bot.flat_increment * Decimal(str(i)))
                price = round_down(price, price_precision)
                amount = calculate_order_amount_for_cost(
                    order_size=bot.order_size,
                    price=price,
                    amount_precision=amount_precision,
                    min_order_amount=min_order_amount
                )
                total_sell_amount += amount

            # Buy base currency for sell orders if needed
            if total_sell_amount > 0:
                try:
                    # Add 3% buffer for safety (to cover fees and slippage)
                    buy_amount = total_sell_amount * Decimal('1.03')
                    buy_amount = round_down(buy_amount, amount_precision)

                    # Ensure buy_amount is not zero
                    if buy_amount < min_order_amount:
                        buy_amount = min_order_amount

                    # For market buy, we need to pass cost in quote currency (USDT)
                    cost = buy_amount * starting_price
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
                    logger.warning("Sell orders will not be created due to market buy failure")
                    total_sell_amount = Decimal('0')  # Skip sell order creation

            # Create SELL limit orders (above starting price) - PARALLEL
            if total_sell_amount > 0:
                # Prepare all SELL order parameters
                sell_order_params = []
                for i in range(1, bot.sell_orders_count + 1):
                    # Calculate price: starting_price + (i * flat_increment)
                    price = starting_price + (bot.flat_increment * Decimal(str(i)))
                    price = round_down(price, price_precision)

                    # Calculate amount to achieve exact cost in quote currency
                    amount = calculate_order_amount_for_cost(
                        order_size=bot.order_size,
                        price=price,
                        amount_precision=amount_precision,
                        min_order_amount=min_order_amount
                    )

                    sell_order_params.append({
                        'level': i,
                        'price': price,
                        'amount': amount
                    })

                # Create helper function to create single sell order
                async def create_sell_order(params):
                    try:
                        order = await self.mexc.create_limit_order(
                            user_id=bot.user_id,
                            symbol=bot.symbol,
                            side='sell',
                            price=params['price'],
                            amount=params['amount']
                        )

                        # Save to DB
                        db_order = GridOrder(
                            grid_bot_id=grid_bot_id,
                            exchange_order_id=order['order_id'],
                            side='sell',
                            order_type='limit',
                            level=params['level'],
                            price=params['price'],
                            amount=params['amount'],
                            total=params['price'] * params['amount'],
                            status='open'
                        )
                        self.db.add(db_order)

                        logger.info(
                            f"Created sell order at level {params['level']}: "
                            f"{params['amount']} @ ${params['price']} (${params['price'] * params['amount']:.2f})"
                        )
                        return order

                    except MEXCError as e:
                        logger.error(f"Failed to create sell order at level {params['level']}: {e}")
                        return None

                # Create all SELL orders in parallel (with rate limiting)
                async def create_sell_with_limit(params):
                    async with semaphore:
                        return await create_sell_order(params)

                # Execute all in parallel
                sell_results = await asyncio.gather(*[
                    create_sell_with_limit(params) for params in sell_order_params
                ], return_exceptions=True)

                # Filter successful orders
                sell_orders = [order for order in sell_results if order and not isinstance(order, Exception)]
                logger.info(f"Created {len(sell_orders)}/{len(sell_order_params)} sell orders successfully")

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

                # Calculate amount to achieve exact cost in quote currency
                sell_amount = calculate_order_amount_for_cost(
                    order_size=bot.order_size,
                    price=sell_price,
                    amount_precision=amount_precision,
                    min_order_amount=min_order_amount
                )

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
                    # Calculate amount to achieve exact cost in quote currency
                    buy_amount = calculate_order_amount_for_cost(
                        order_size=bot.order_size,
                        price=buy_price,
                        amount_precision=amount_precision,
                        min_order_amount=min_order_amount
                    )

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
