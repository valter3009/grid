# Crypto Grid Trading Telegram Bot

Telegram бот для автоматизированной криптовалютной торговли с использованием Grid Trading стратегии на бирже MEXC (spot рынок).

## 🎯 Возможности

- ✅ Автоматическая Grid Trading стратегия
- ✅ Простой диалоговый интерфейс создания ботов
- ✅ Мониторинг ордеров 24/7
- ✅ Уведомления о прибыли и событиях
- ✅ Автоматическое восстановление после перезапуска
- ✅ Health check система с авто-исправлением
- ✅ Шифрование API ключей

## 🚀 Быстрый старт

### 1. Настройка окружения

Создайте `.env` файл:

```bash
cp .env.example .env
```

Заполните переменные:

```env
TELEGRAM_BOT_TOKEN=7807194370:AAH0bbTu_HwaOXUOoT2DU9oGS0xDIHSSjSs

# Database
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=crypto_grid_bot
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres123

# Generate key:
# python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
ENCRYPTION_KEY=your_fernet_key_here
SECRET_KEY=your_secret_key_here
```

### 2. Запуск с Docker

```bash
docker-compose up -d
```

### 3. Запуск без Docker

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python src/main.py
```

## 📖 Как использовать

1. Отправьте `/start` боту в Telegram
2. Настройте API ключи MEXC: ⚙️ Настройки → 🔑 API ключи
3. Создайте Grid бота: ➕ Создать Grid бота
4. Следите за прибылью! 💰

## 🔒 Безопасность

- API ключи шифруются (Fernet/AES)
- Используйте только Spot API
- Не давайте права на вывод средств

## 📊 Мониторинг

```bash
# Логи
tail -f logs/bot.log

# Database
docker exec -it crypto-grid-bot-postgres-1 psql -U postgres -d crypto_grid_bot
```

Made with ❤️ for crypto traders
