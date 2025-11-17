"""MEXC Exchange Service using CCXT."""
import ccxt.async_support as ccxt
from decimal import Decimal
from typing import Optional, Dict, List
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.user import User
from src.core.security import security
from src.utils.helpers import parse_decimal, retry_async, split_symbol
from src.utils.validators import ValidationError
from src.utils.cache import price_cache

logger = logging.getLogger(__name__)


class MEXCError(Exception):
    """MEXC API error."""
    pass


class MEXCService:
    """Service for interacting with MEXC exchange via CCXT."""

    def __init__(self, db: AsyncSession):
        """Initialize MEXC service."""
        self.db = db
        self._exchanges: Dict[int, ccxt.mexc] = {}  # Cache exchanges per user

    async def _get_exchange(self, user_id: int) -> ccxt.mexc:
        """
        Get or create MEXC exchange instance for user.
        Note: Caller must close the exchange after use with await exchange.close()

        Args:
            user_id: User ID

        Returns:
            MEXC exchange instance

        Raises:
            MEXCError: If user has no API keys configured
        """
        # Load user from database
        result = await self.db.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if not user or not user.has_api_keys:
            raise MEXCError("API ключи не настроены. Используйте /settings для настройки.")

        # Decrypt API keys
        api_key, api_secret = security.decrypt_api_credentials(
            user.mexc_api_key,
            user.mexc_api_secret
        )

        # Create exchange instance
        exchange = ccxt.mexc({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',  # Only spot trading
                'createMarketBuyOrderRequiresPrice': False,  # For MEXC market buy orders
            }
        })

        return exchange

    async def test_api_keys(self, api_key: str, api_secret: str) -> dict:
        """
        Test API keys validity.

        Args:
            api_key: API key
            api_secret: API secret

        Returns:
            {
                'valid': bool,
                'balance': dict,
                'permissions': list,
                'error': str (if any)
            }
        """
        exchange = None
        try:
            # Create temporary exchange instance
            exchange = ccxt.mexc({
                'apiKey': api_key,
                'secret': api_secret,
                'enableRateLimit': True,
                'options': {'defaultType': 'spot'}
            })

            # Try to fetch balance
            balance = await exchange.fetch_balance()

            return {
                'valid': True,
                'balance': balance.get('total', {}),
                'permissions': ['spot'],  # MEXC doesn't provide detailed permissions
                'error': None
            }

        except ccxt.AuthenticationError as e:
            logger.error(f"API authentication failed: {e}")
            return {
                'valid': False,
                'balance': {},
                'permissions': [],
                'error': 'Неверные API ключи'
            }

        except ccxt.ExchangeError as e:
            logger.error(f"Exchange error: {e}")
            return {
                'valid': False,
                'balance': {},
                'permissions': [],
                'error': str(e)
            }

        except Exception as e:
            logger.error(f"Unexpected error testing API keys: {e}")
            return {
                'valid': False,
                'balance': {},
                'permissions': [],
                'error': f'Ошибка: {str(e)}'
            }

        finally:
            # Always close the exchange connection
            if exchange:
                await exchange.close()

    async def get_balance(self, user_id: int, use_cache: bool = True) -> Dict[str, Decimal]:
        """
        Get user balance from MEXC.

        Args:
            user_id: User ID
            use_cache: Whether to use cached balance (default: True)

        Returns:
            Dictionary of {currency: amount}

        Raises:
            MEXCError: If API call fails
        """
        # Check cache first (if enabled)
        if use_cache:
            cache_key = f"balance:{user_id}"
            cached_balance = price_cache.get(cache_key)
            if cached_balance is not None:
                logger.debug(f"Balance cache HIT for user {user_id}")
                return cached_balance

        exchange = None
        try:
            exchange = await self._get_exchange(user_id)

            balance_data = await retry_async(
                exchange.fetch_balance,
                max_retries=3,
                exceptions=(ccxt.NetworkError,)
            )

            # Extract non-zero balances
            balances = {}
            for currency, amount in balance_data.get('total', {}).items():
                if amount and amount > 0:
                    balances[currency] = parse_decimal(amount)

            # Cache balance for 30 seconds (balances change less frequently)
            if use_cache:
                price_cache.set(f"balance:{user_id}", balances)
                logger.debug(f"Cached balance for user {user_id}")

            return balances

        except ccxt.AuthenticationError as e:
            logger.error(f"Authentication error getting balance: {e}")
            raise MEXCError("Неверные API ключи")

        except Exception as e:
            logger.error(f"Error getting balance: {e}")
            raise MEXCError(f"Ошибка получения баланса: {str(e)}")

        finally:
            if exchange:
                await exchange.close()

    async def get_current_price(self, symbol: str, use_cache: bool = True) -> Decimal:
        """
        Get current price for trading pair.

        Args:
            symbol: Trading pair (e.g., BTC/USDT)
            use_cache: Whether to use cached price (default: True)

        Returns:
            Current price

        Raises:
            MEXCError: If API call fails
        """
        # Check cache first (if enabled)
        if use_cache:
            cache_key = f"price:{symbol}"
            cached_price = price_cache.get(cache_key)
            if cached_price is not None:
                return cached_price

        exchange = None
        try:
            # Use public API (no auth needed)
            exchange = ccxt.mexc({'enableRateLimit': True})

            ticker = await retry_async(
                exchange.fetch_ticker,
                symbol,
                max_retries=3,
                exceptions=(ccxt.NetworkError,)
            )

            price = ticker.get('last') or ticker.get('close')
            if not price:
                raise MEXCError(f"Не удалось получить цену для {symbol}")

            price_decimal = parse_decimal(price)

            # Cache for 60 seconds
            if use_cache:
                price_cache.set(f"price:{symbol}", price_decimal)

            return price_decimal

        except ccxt.BadSymbol as e:
            logger.error(f"Invalid symbol {symbol}: {e}")
            raise MEXCError(f"Неверная торговая пара: {symbol}")

        except Exception as e:
            logger.error(f"Error getting price for {symbol}: {e}")
            raise MEXCError(f"Ошибка получения цены: {str(e)}")

        finally:
            if exchange:
                await exchange.close()

    async def get_multiple_prices(self, symbols: List[str]) -> dict:
        """
        Get current prices for multiple trading pairs in one request.
        Much faster than calling get_current_price() for each symbol.

        Args:
            symbols: List of trading pairs (e.g., ['BTC/USDT', 'ETH/USDT'])

        Returns:
            Dictionary of {symbol: price}

        Raises:
            MEXCError: If API call fails
        """
        exchange = None
        try:
            # Use public API (no auth needed)
            exchange = ccxt.mexc({'enableRateLimit': True})

            # Fetch all tickers at once (one API call!)
            tickers = await retry_async(
                exchange.fetch_tickers,
                symbols,
                max_retries=3,
                exceptions=(ccxt.NetworkError,)
            )

            # Extract prices
            prices = {}
            for symbol in symbols:
                if symbol in tickers:
                    ticker = tickers[symbol]
                    price = ticker.get('last') or ticker.get('close')
                    if price:
                        prices[symbol] = parse_decimal(price)

            return prices

        except Exception as e:
            logger.error(f"Error getting multiple prices: {e}")
            # Return empty dict, let caller handle missing prices
            return {}

        finally:
            if exchange:
                await exchange.close()

    async def get_exchange_info(self, symbol: str) -> dict:
        """
        Get exchange information for trading pair.

        Args:
            symbol: Trading pair (e.g., BTC/USDT)

        Returns:
            {
                'min_order_amount': Decimal,
                'min_order_cost': Decimal,
                'price_precision': int,
                'amount_precision': int,
                'limits': dict
            }

        Raises:
            MEXCError: If API call fails
        """
        exchange = None
        try:
            exchange = ccxt.mexc({'enableRateLimit': True})

            await exchange.load_markets()

            if symbol not in exchange.markets:
                raise MEXCError(f"Торговая пара {symbol} не найдена на MEXC")

            market = exchange.markets[symbol]

            # Log full market info to debug precision issues
            logger.info(
                f"[MEXC MARKET INFO] {symbol}:\n"
                f"  precision: {market.get('precision')}\n"
                f"  limits: {market.get('limits')}\n"
                f"  info keys: {list(market.get('info', {}).keys())}"
            )

            # CRITICAL FIX: Check if MEXC provides more granular precision in raw 'info'
            # Some exchanges provide step size in the raw API response
            raw_info = market.get('info', {})
            amount_step = raw_info.get('quantityPrecision') or raw_info.get('baseAssetPrecision')

            # Get precision from CCXT normalized data
            amount_precision_ccxt = market.get('precision', {}).get('amount', 8)
            price_precision_ccxt = market.get('precision', {}).get('price', 8)

            # Use raw precision if available and more granular
            if amount_step is not None:
                try:
                    # CRITICAL: Interpret precision value correctly!
                    # If >= 1: it's number of decimal places (e.g., 2 = 0.01 step)
                    # If < 1: it's the step size itself (e.g., 0.01)
                    if amount_step >= 1:
                        # It's decimal places - convert to int
                        amount_precision = int(amount_step)
                        logger.info(f"[MEXC] Raw precision {amount_step} interpreted as {amount_precision} decimal places (step=0.{'0' * (amount_precision-1)}1)")
                    else:
                        # It's a step size - keep as Decimal
                        amount_precision = Decimal(str(amount_step))
                        logger.info(f"[MEXC] Raw precision {amount_step} interpreted as step size {amount_precision}")
                except:
                    amount_precision = amount_precision_ccxt
            else:
                amount_precision = amount_precision_ccxt

            logger.info(f"[MEXC] Final amount_precision for {symbol}: {amount_precision}")

            return {
                'min_order_amount': parse_decimal(market['limits']['amount']['min']),
                'min_order_cost': parse_decimal(market['limits']['cost']['min']),
                'price_precision': price_precision_ccxt,
                'amount_precision': amount_precision,
                'limits': market['limits'],
                'active': market.get('active', True),
                'symbol': symbol,
                'base': market['base'],
                'quote': market['quote'],
            }

        except MEXCError:
            # Re-raise MEXCError without wrapping
            raise

        except Exception as e:
            logger.error(f"Error getting exchange info for {symbol}: {e}")
            raise MEXCError(f"Ошибка получения информации о паре: {str(e)}")

        finally:
            if exchange:
                await exchange.close()

    async def create_limit_order(
        self,
        user_id: int,
        symbol: str,
        side: str,
        price: Decimal,
        amount: Decimal
    ) -> dict:
        """
        Create limit order on MEXC.

        Args:
            user_id: User ID
            symbol: Trading pair
            side: 'buy' or 'sell'
            price: Order price
            amount: Order amount

        Returns:
            {
                'order_id': str,
                'status': str,
                'filled': Decimal,
                'remaining': Decimal,
                'fee': Decimal,
                'fee_currency': str
            }

        Raises:
            MEXCError: If order creation fails
        """
        exchange = None
        try:
            exchange = await self._get_exchange(user_id)

            order = await retry_async(
                exchange.create_limit_order,
                symbol,
                side,
                float(amount),
                float(price),
                max_retries=2,
                exceptions=(ccxt.NetworkError,)
            )

            if not order:
                raise MEXCError("Exchange returned empty response")

            # Handle fee field safely (can be None or dict)
            fee_data = order.get('fee') or {}

            return {
                'order_id': str(order['id']),
                'status': order['status'],
                'filled': parse_decimal(order.get('filled', 0)),
                'remaining': parse_decimal(order.get('remaining', amount)),
                'fee': parse_decimal(fee_data.get('cost', 0)),
                'fee_currency': fee_data.get('currency', 'USDT'),
                'timestamp': order.get('timestamp'),
                'price': parse_decimal(order.get('price', price)),
                'amount': parse_decimal(order.get('amount', amount)),
            }

        except ccxt.InsufficientFunds as e:
            logger.error(f"Insufficient funds for order: {e}")
            raise MEXCError("Недостаточно средств для создания ордера")

        except ccxt.InvalidOrder as e:
            logger.error(f"Invalid order: {e}")
            raise MEXCError(f"Неверные параметры ордера: {str(e)}")

        except ccxt.AuthenticationError as e:
            logger.error(f"Authentication error creating order: {e}")
            raise MEXCError("Ошибка аутентификации. Проверьте API ключи.")

        except Exception as e:
            logger.error(f"Error creating limit order: {e}")
            raise MEXCError(f"Ошибка создания ордера: {str(e)}")

        finally:
            if exchange:
                await exchange.close()

    async def create_market_order(
        self,
        user_id: int,
        symbol: str,
        side: str,
        amount: Decimal,
        price: Decimal = None
    ) -> dict:
        """
        Create market order on MEXC.

        Args:
            user_id: User ID
            symbol: Trading pair
            side: 'buy' or 'sell'
            amount: Order amount (for sell) or cost in quote currency (for buy)
            price: Current price (used for buy orders to calculate cost)

        Returns:
            Order details dict

        Raises:
            MEXCError: If order creation fails
        """
        exchange = None
        try:
            exchange = await self._get_exchange(user_id)

            # For market buy on MEXC, we need to pass cost (amount * price) as the amount parameter
            # when createMarketBuyOrderRequiresPrice is False
            order_amount = float(amount)

            order = await retry_async(
                exchange.create_market_order,
                symbol,
                side,
                order_amount,
                max_retries=2,
                exceptions=(ccxt.NetworkError,)
            )

            if not order or not isinstance(order, dict):
                raise MEXCError("Exchange returned invalid response")

            # Handle fee field safely (can be None or dict)
            fee_data = order.get('fee') or {}

            return {
                'order_id': str(order['id']),
                'status': order['status'],
                'filled': parse_decimal(order.get('filled', 0)),
                'average_price': parse_decimal(order.get('average', 0)),
                'fee': parse_decimal(fee_data.get('cost', 0)),
                'fee_currency': fee_data.get('currency', 'USDT'),
                'timestamp': order.get('timestamp'),
                'amount': parse_decimal(order.get('amount', amount)),
            }

        except ccxt.InsufficientFunds as e:
            logger.error(f"Insufficient funds for market order: {e}")
            raise MEXCError("Недостаточно средств")

        except ccxt.InvalidOrder as e:
            logger.error(f"Invalid market order: {e}")
            raise MEXCError(f"Неверные параметры ордера: {str(e)}")

        except Exception as e:
            logger.error(f"Error creating market order: {e}")
            raise MEXCError(f"Ошибка создания рыночного ордера: {str(e)}")

        finally:
            if exchange:
                await exchange.close()

    async def cancel_order(self, user_id: int, symbol: str, order_id: str) -> bool:
        """
        Cancel order on MEXC.

        Args:
            user_id: User ID
            symbol: Trading pair
            order_id: Exchange order ID

        Returns:
            True if cancelled successfully

        Raises:
            MEXCError: If cancellation fails
        """
        exchange = None
        try:
            exchange = await self._get_exchange(user_id)

            await retry_async(
                exchange.cancel_order,
                order_id,
                symbol,
                max_retries=2,
                exceptions=(ccxt.NetworkError,)
            )

            logger.info(f"Cancelled order {order_id} for {symbol}")
            return True

        except ccxt.OrderNotFound as e:
            logger.warning(f"Order {order_id} not found: {e}")
            return True  # Already cancelled or doesn't exist

        except Exception as e:
            logger.error(f"Error cancelling order {order_id}: {e}")
            raise MEXCError(f"Ошибка отмены ордера: {str(e)}")

        finally:
            if exchange:
                await exchange.close()

    async def get_order_status(self, user_id: int, symbol: str, order_id: str) -> dict:
        """
        Get order status from MEXC.

        Args:
            user_id: User ID
            symbol: Trading pair
            order_id: Exchange order ID

        Returns:
            Order status dict

        Raises:
            MEXCError: If API call fails
        """
        try:
            exchange = await self._get_exchange(user_id)

            order = await retry_async(
                exchange.fetch_order,
                order_id,
                symbol,
                max_retries=3,
                exceptions=(ccxt.NetworkError,)
            )

            # Handle fee field safely (can be None or dict)
            fee_data = order.get('fee') or {}

            return {
                'order_id': str(order['id']),
                'status': order['status'],
                'side': order['side'],
                'price': parse_decimal(order.get('price', 0)),
                'amount': parse_decimal(order.get('amount', 0)),
                'filled': parse_decimal(order.get('filled', 0)),
                'remaining': parse_decimal(order.get('remaining', 0)),
                'fee': parse_decimal(fee_data.get('cost', 0)),
                'fee_currency': fee_data.get('currency'),
                'timestamp': order.get('timestamp'),
                'average_price': parse_decimal(order.get('average', 0)),
            }

        except ccxt.OrderNotFound as e:
            logger.error(f"Order {order_id} not found: {e}")
            raise MEXCError(f"Ордер не найден: {order_id}")

        except Exception as e:
            logger.error(f"Error getting order status: {e}")
            raise MEXCError(f"Ошибка получения статуса ордера: {str(e)}")

    async def get_open_orders(self, user_id: int, symbol: Optional[str] = None) -> List[dict]:
        """
        Get all open orders for user.

        Args:
            user_id: User ID
            symbol: Trading pair (optional, all if None)

        Returns:
            List of open orders

        Raises:
            MEXCError: If API call fails
        """
        exchange = None
        try:
            exchange = await self._get_exchange(user_id)

            orders = await retry_async(
                exchange.fetch_open_orders,
                symbol,
                max_retries=3,
                exceptions=(ccxt.NetworkError,)
            )

            return [
                {
                    'order_id': str(order['id']),
                    'symbol': order['symbol'],
                    'status': order['status'],
                    'side': order['side'],
                    'price': parse_decimal(order.get('price', 0)),
                    'amount': parse_decimal(order.get('amount', 0)),
                    'filled': parse_decimal(order.get('filled', 0)),
                    'remaining': parse_decimal(order.get('remaining', 0)),
                    'timestamp': order.get('timestamp'),
                }
                for order in orders if order
            ]

        except Exception as e:
            logger.error(f"Error getting open orders: {e}")
            raise MEXCError(f"Ошибка получения открытых ордеров: {str(e)}")

        finally:
            if exchange:
                await exchange.close()

    async def close_all(self):
        """Close all exchange connections."""
        for exchange in self._exchanges.values():
            try:
                await exchange.close()
            except Exception as e:
                logger.error(f"Error closing exchange connection: {e}")

        self._exchanges.clear()

    def clear_cache(self, user_id: Optional[int] = None):
        """
        Clear exchange cache.

        Args:
            user_id: User ID to clear (all if None)
        """
        if user_id:
            self._exchanges.pop(user_id, None)
        else:
            self._exchanges.clear()
