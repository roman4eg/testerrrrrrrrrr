#!/usr/bin/env python3
"""
Predict Fun Bot - MVP Version
Отримує список активних ордерів (orderbook) через Predict Fun API
"""

import os
import sys
import json
import argparse
import requests
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv


class PredictFunBot:
    """Клас для роботи з Predict Fun API"""

    def __init__(self, api_key: str, base_url: str = "https://api.predict.fun/v1"):
        """
        Ініціалізація бота

        Args:
            api_key: API ключ для аутентифікації
            base_url: Базова URL API (за замовчуванням mainnet)
        """
        self.api_key = api_key
        self.base_url = base_url
        self.headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json"
        }

    def _make_request(self, endpoint: str, method: str = "GET", params: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Виконує HTTP запит до API

        Args:
            endpoint: API endpoint (без base_url)
            method: HTTP метод (GET, POST, etc.)
            params: Параметри запиту

        Returns:
            dict: Відповідь API
        """
        url = f"{self.base_url}{endpoint}"

        try:
            if method == "GET":
                response = requests.get(url, headers=self.headers, params=params, timeout=30)
            else:
                response = requests.request(method, url, headers=self.headers, json=params, timeout=30)

            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            print(f"❌ HTTP помилка: {e}")
            print(f"Статус код: {response.status_code}")
            if response.text:
                print(f"Відповідь: {response.text}")
            sys.exit(1)
        except requests.exceptions.ConnectionError:
            print("❌ Помилка підключення до API")
            sys.exit(1)
        except requests.exceptions.Timeout:
            print("❌ Таймаут запиту")
            sys.exit(1)
        except requests.exceptions.RequestException as e:
            print(f"❌ Помилка запиту: {e}")
            sys.exit(1)

    def get_markets(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Отримує список ринків

        Args:
            limit: Максимальна кількість ринків для отримання

        Returns:
            list: Список ринків
        """
        data = self._make_request("/markets", params={"first": limit})
        return data.get("data", [])

    def get_orderbook(self, market_id: str) -> Dict[str, Any]:
        """
        Отримує книгу ордерів для конкретного ринку

        Args:
            market_id: ID ринку

        Returns:
            dict: Книга ордерів
        """
        return self._make_request(f"/markets/{market_id}/orderbook")

    def display_markets(self, markets: List[Dict[str, Any]]):
        """
        Виводить список ринків

        Args:
            markets: Список ринків
        """
        print("\n" + "="*80)
        print("📈 ДОСТУПНІ РИНКИ")
        print("="*80 + "\n")

        if not markets:
            print("📭 Ринків не знайдено")
            print("\n" + "="*80)
            return

        for i, market in enumerate(markets, 1):
            print(f"{i}. Market ID: {market.get('id', 'N/A')}")
            print(f"   Питання: {market.get('question', 'N/A')}")
            print(f"   Категорія: {market.get('category', {}).get('name', 'N/A')}")
            print(f"   Закінчення: {market.get('endDate', 'N/A')}")
            print()

        print(f"Всього ринків: {len(markets)}")
        print("="*80 + "\n")

    def display_orderbook(self, orderbook: Dict[str, Any], market_info: Optional[Dict] = None):
        """
        Виводить книгу ордерів

        Args:
            orderbook: Дані книги ордерів
            market_info: Інформація про ринок (опціонально)
        """
        print("\n" + "="*80)
        print("📊 КНИГА ОРДЕРІВ")
        if market_info:
            print(f"Ринок: {market_info.get('question', 'N/A')}")
        print("="*80 + "\n")

        # Buy orders (Bids)
        buy_orders = orderbook.get("buy", [])
        print("🟢 ОРДЕРИ НА КУПІВЛЮ (BUY)")
        print("-" * 80)
        if buy_orders:
            print(f"{'Ціна':<20} {'Кількість':<25} {'Загальна сума':<25}")
            print("-" * 80)
            for order in buy_orders[:10]:  # Показуємо топ-10
                price = order.get("price", "N/A")
                size = order.get("size", "N/A")
                total = order.get("total", "N/A")
                print(f"{price:<20} {size:<25} {total:<25}")
        else:
            print("Немає ордерів на купівлю")

        print("\n")

        # Sell orders (Asks)
        sell_orders = orderbook.get("sell", [])
        print("🔴 ОРДЕРИ НА ПРОДАЖ (SELL)")
        print("-" * 80)
        if sell_orders:
            print(f"{'Ціна':<20} {'Кількість':<25} {'Загальна сума':<25}")
            print("-" * 80)
            for order in sell_orders[:10]:  # Показуємо топ-10
                price = order.get("price", "N/A")
                size = order.get("size", "N/A")
                total = order.get("total", "N/A")
                print(f"{price:<20} {size:<25} {total:<25}")
        else:
            print("Немає ордерів на продаж")

        print("\n" + "="*80 + "\n")


def main():
    """Головна функція"""
    # Парсинг аргументів командного рядка
    parser = argparse.ArgumentParser(description="Predict Fun Bot - Отримання книги ордерів")
    parser.add_argument(
        "--market-id",
        type=str,
        help="ID ринку для отримання книги ордерів (якщо не вказано, показує перший доступний ринок)"
    )
    parser.add_argument(
        "--list-markets",
        action="store_true",
        help="Показати список доступних ринків"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Кількість ринків для відображення (за замовчуванням: 10)"
    )
    args = parser.parse_args()

    # Завантажуємо змінні середовища
    load_dotenv()

    # Отримуємо API ключ
    api_key = os.getenv("API_KEY")

    if not api_key:
        print("❌ Помилка: API_KEY не знайдено в .env файлі")
        print("\n📝 Інструкція:")
        print("1. Створіть файл .env в кореневій директорії")
        print("2. Додайте рядок: API_KEY=ваш_апі_ключ")
        print("3. Запустіть бота знову")
        sys.exit(1)

    # Створюємо екземпляр бота
    print("🤖 Запуск Predict Fun Bot...")
    print(f"🔗 API: https://api.predict.fun/v1\n")

    bot = PredictFunBot(api_key)

    # Отримуємо список ринків
    print("📡 Отримання списку ринків...")
    markets = bot.get_markets(limit=args.limit)

    if not markets:
        print("❌ Не вдалося отримати список ринків")
        sys.exit(1)

    # Якщо запитано тільки список ринків
    if args.list_markets:
        bot.display_markets(markets)
        print("✅ Готово!")
        return

    # Визначаємо market_id
    if args.market_id:
        market_id = args.market_id
        # Шукаємо інформацію про ринок
        market_info = next((m for m in markets if m.get("id") == market_id), None)
        if not market_info:
            print(f"⚠️  Ринок {market_id} не знайдено в списку доступних ринків")
            market_info = None
    else:
        # Беремо перший доступний ринок
        market_info = markets[0]
        market_id = market_info.get("id")
        print(f"ℹ️  Використовуємо перший доступний ринок: {market_id}")
        print(f"   Питання: {market_info.get('question', 'N/A')}\n")

    # Отримуємо та відображаємо книгу ордерів
    print(f"📡 Отримання книги ордерів для ринку {market_id}...")
    orderbook = bot.get_orderbook(market_id)
    bot.display_orderbook(orderbook, market_info)

    print("✅ Готово!")
    print("\n💡 Підказки:")
    print("   - Використовуйте --list-markets для перегляду всіх ринків")
    print("   - Використовуйте --market-id <ID> для вибору конкретного ринку")
    print("   - Використовуйте --limit <N> для зміни кількості ринків у списку")


if __name__ == "__main__":
    main()
