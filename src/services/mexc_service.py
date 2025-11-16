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

    async def get_balance(self, user_id: int) -> Dict[str, Decimal]:
        """
        Get user balance from MEXC.

        Args:
            user_id: User ID

        Returns:
            Dictionary of {currency: amount}

        Raises:
            MEXCError: If API call fails
        """
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

    async def get_current_price(self, symbol: str) -> Decimal:
        """
        Get current price for trading pair.

        Args:
            symbol: Trading pair (e.g., BTC/USDT)

        Returns:
            Current price

        Raises:
            MEXCError: If API call fails
        """
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

            return parse_decimal(price)

        except ccxt.BadSymbol as e:
            logger.error(f"Invalid symbol {symbol}: {e}")
            raise MEXCError(f"Неверная торговая пара: {symbol}")

        except Exception as e:
            logger.error(f"Error getting price for {symbol}: {e}")
            raise MEXCError(f"Ошибка получения цены: {str(e)}")

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

            return {
                'min_order_amount': parse_decimal(market['limits']['amount']['min']),
                'min_order_cost': parse_decimal(market['limits']['cost']['min']),
                'price_precision': market.get('precision', {}).get('price', 8),
                'amount_precision': market.get('precision', {}).get('amount', 8),
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

            return {
                'order_id': str(order['id']),
                'status': order['status'],
                'filled': parse_decimal(order.get('filled', 0)),
                'remaining': parse_decimal(order.get('remaining', amount)),
                'fee': parse_decimal(order.get('fee', {}).get('cost', 0)),
                'fee_currency': order.get('fee', {}).get('currency', 'USDT'),
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

    async def create_market_order(
        self,
        user_id: int,
        symbol: str,
        side: str,
        amount: Decimal
    ) -> dict:
        """
        Create market order on MEXC.

        Args:
            user_id: User ID
            symbol: Trading pair
            side: 'buy' or 'sell'
            amount: Order amount

        Returns:
            Order details dict

        Raises:
            MEXCError: If order creation fails
        """
        try:
            exchange = await self._get_exchange(user_id)

            order = await retry_async(
                exchange.create_market_order,
                symbol,
                side,
                float(amount),
                max_retries=2,
                exceptions=(ccxt.NetworkError,)
            )

            return {
                'order_id': str(order['id']),
                'status': order['status'],
                'filled': parse_decimal(order.get('filled', 0)),
                'average_price': parse_decimal(order.get('average', 0)),
                'fee': parse_decimal(order.get('fee', {}).get('cost', 0)),
                'fee_currency': order.get('fee', {}).get('currency', 'USDT'),
                'timestamp': order.get('timestamp'),
                'amount': parse_decimal(order.get('amount', amount)),
            }

        except ccxt.InsufficientFunds as e:
            logger.error(f"Insufficient funds for market order: {e}")
            raise MEXCError("Недостаточно средств")

        except Exception as e:
            logger.error(f"Error creating market order: {e}")
            raise MEXCError(f"Ошибка создания рыночного ордера: {str(e)}")

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
        try:
            exchange = await self._get_exchange(user_id)

            await retry_async(
                exchange.cancel_order,
                order_id,
                symbol,
                max_retries=2,
                exceptions=(ccxt.NetworkError,)
            )

            return True

        except ccxt.OrderNotFound as e:
            logger.warning(f"Order {order_id} not found: {e}")
            return True  # Already cancelled or doesn't exist

        except Exception as e:
            logger.error(f"Error cancelling order {order_id}: {e}")
            raise MEXCError(f"Ошибка отмены ордера: {str(e)}")

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

            return {
                'order_id': str(order['id']),
                'status': order['status'],
                'side': order['side'],
                'price': parse_decimal(order.get('price', 0)),
                'amount': parse_decimal(order.get('amount', 0)),
                'filled': parse_decimal(order.get('filled', 0)),
                'remaining': parse_decimal(order.get('remaining', 0)),
                'fee': parse_decimal(order.get('fee', {}).get('cost', 0)),
                'fee_currency': order.get('fee', {}).get('currency'),
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
                for order in orders
            ]

        except Exception as e:
            logger.error(f"Error getting open orders: {e}")
            raise MEXCError(f"Ошибка получения открытых ордеров: {str(e)}")

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
