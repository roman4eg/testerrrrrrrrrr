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

    def get_markets(self, limit: int = 10, after: Optional[str] = None) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """
        Отримує список ринків з підтримкою pagination

        Args:
            limit: Максимальна кількість ринків для отримання
            after: Cursor для pagination (отримати ринки після цього курсора)

        Returns:
            tuple: (список ринків, cursor для наступної сторінки)
        """
        params = {"first": limit}
        if after:
            params["after"] = after

        data = self._make_request("/markets", params=params)
        return data.get("data", []), data.get("cursor")

    def filter_active_markets(self, markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Фільтрує тільки активні ринки (не RESOLVED)

        Args:
            markets: Список ринків

        Returns:
            list: Список активних ринків
        """
        return [m for m in markets if m.get("status") not in ["RESOLVED", "CLOSED"]]

    def find_active_markets(self, min_active: int = 1, max_limit: int = 150, max_pages: int = 5) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Автоматично шукає активні ринки, використовуючи pagination

        Args:
            min_active: Мінімальна кількість активних ринків для пошуку
            max_limit: Максимальний ліміт для запиту (API дозволяє максимум 150)
            max_pages: Максимальна кількість сторінок для перегляду

        Returns:
            tuple: (всі ринки, активні ринки)
        """
        all_markets = []
        cursor = None
        total_checked = 0

        for page in range(max_pages):
            print(f"   Пошук активних ринків (сторінка {page + 1}, перевіряю {max_limit} ринків)...")
            markets, cursor = self.get_markets(limit=max_limit, after=cursor)

            if not markets:
                print(f"   ℹ️  Більше ринків не знайдено")
                break

            all_markets.extend(markets)
            total_checked += len(markets)
            active_markets = self.filter_active_markets(all_markets)

            print(f"      Перевірено: {total_checked} ринків, активних: {len(active_markets)}")

            if len(active_markets) >= min_active:
                print(f"   ✅ Знайдено {len(active_markets)} активних ринків з {len(all_markets)} загальних\n")
                return all_markets, active_markets

            # Якщо немає cursor, це остання сторінка
            if not cursor:
                print(f"   ℹ️  Досягнуто кінець списку ринків")
                break

        # Якщо не знайшли, повертаємо всі зібрані ринки
        active_markets = self.filter_active_markets(all_markets)
        if all_markets:
            print(f"   ⚠️  Перевірено {len(all_markets)} ринків, але активних не знайдено\n")
        return all_markets, active_markets

    def get_orderbook(self, market_id: str, token_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Отримує книгу ордерів для конкретного ринку або outcome

        Args:
            market_id: ID ринку
            token_id: ID токена outcome (опціонально)

        Returns:
            dict: Книга ордерів
        """
        endpoint = f"/markets/{market_id}/orderbook"
        params = {}
        if token_id:
            params["tokenId"] = token_id

        return self._make_request(endpoint, params=params)

    def display_markets(self, markets: List[Dict[str, Any]], verbose: bool = False):
        """
        Виводить список ринків

        Args:
            markets: Список ринків
            verbose: Якщо True, показує детальну інформацію про структуру
        """
        print("\n" + "="*80)
        print("📈 ДОСТУПНІ РИНКИ")
        print("="*80 + "\n")

        if not markets:
            print("📭 Ринків не знайдено")
            print("\n" + "="*80)
            return

        for i, market in enumerate(markets, 1):
            status = market.get('status', 'N/A')
            status_icon = "✅" if status not in ["RESOLVED", "CLOSED"] else "🔒"

            print(f"{i}. {status_icon} Market ID: {market.get('id', 'N/A')} [{status}]")
            print(f"   Питання: {market.get('question', 'N/A')}")
            print(f"   Категорія: {market.get('category', {}).get('name', 'N/A')}")
            print(f"   Закінчення: {market.get('endDate', 'N/A')}")

            # Показуємо outcomes з tokenId
            outcomes = market.get('outcomes', [])
            if outcomes and verbose:
                print(f"   Outcomes:")
                for outcome in outcomes:
                    print(f"     - {outcome.get('title', 'N/A')} (tokenId: {outcome.get('onChainId', 'N/A')})")

            if verbose:
                print(f"   [DEBUG] Всі ключі: {list(market.keys())}")
            print()

        print(f"Всього ринків: {len(markets)}")
        print("="*80 + "\n")

    def display_orderbook(self, orderbook: Dict[str, Any], market_info: Optional[Dict] = None, outcome_name: str = ""):
        """
        Виводить книгу ордерів для outcome

        Args:
            orderbook: Дані книги ордерів
            market_info: Інформація про ринок (опціонально)
            outcome_name: Назва outcome (Up/Down/Yes/No)
        """
        print("\n" + "="*80)
        if outcome_name:
            print(f"📊 КНИГА ОРДЕРІВ - {outcome_name.upper()}")
        else:
            print("📊 КНИГА ОРДЕРІВ")
        if market_info:
            print(f"Ринок: {market_info.get('question', 'N/A')}")
        print("="*80 + "\n")

        # Перевіряємо структуру відповіді
        # API може повертати: {bids: [], asks: []} або {buy: [], sell: []}
        bids = orderbook.get("bids", orderbook.get("buy", []))
        asks = orderbook.get("asks", orderbook.get("sell", []))

        # Bids (Buy orders)
        print("🟢 ОРДЕРИ НА КУПІВЛЮ (BIDS)")
        print("-" * 80)
        if bids:
            print(f"{'Ціна':<20} {'Кількість':<25}")
            print("-" * 80)
            for order in bids[:10]:  # Показуємо топ-10
                # API повертає [price, size] масиви
                if isinstance(order, list) and len(order) >= 2:
                    price = order[0]
                    size = order[1]
                    print(f"{price:<20} {size:<25}")
                elif isinstance(order, dict):
                    price = order.get("price", "N/A")
                    size = order.get("size", "N/A")
                    print(f"{price:<20} {size:<25}")
        else:
            print("Немає ордерів на купівлю")

        print("\n")

        # Asks (Sell orders)
        print("🔴 ОРДЕРИ НА ПРОДАЖ (ASKS)")
        print("-" * 80)
        if asks:
            print(f"{'Ціна':<20} {'Кількість':<25}")
            print("-" * 80)
            for order in asks[:10]:  # Показуємо топ-10
                # API повертає [price, size] масиви
                if isinstance(order, list) and len(order) >= 2:
                    price = order[0]
                    size = order[1]
                    print(f"{price:<20} {size:<25}")
                elif isinstance(order, dict):
                    price = order.get("price", "N/A")
                    size = order.get("size", "N/A")
                    print(f"{price:<20} {size:<25}")
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
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Показати детальну інформацію (tokenId, outcomes, etc.)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Показати повну JSON структуру першого ринку для дебагу"
    )
    parser.add_argument(
        "--show-all",
        action="store_true",
        help="Показати всі ринки, включно із завершеними (RESOLVED)"
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

    # Якщо вказано --show-all або конкретний limit, використовуємо прямий запит
    if args.show_all or (args.limit != 10):
        all_markets, _ = bot.get_markets(limit=args.limit)
        if not all_markets:
            print("❌ Не вдалося отримати список ринків")
            sys.exit(1)
        markets = all_markets if args.show_all else bot.filter_active_markets(all_markets)
        if len(markets) < len(all_markets) and not args.show_all:
            print(f"ℹ️  Показано {len(markets)} активних ринків з {len(all_markets)} загальних (використовуйте --show-all для всіх)\n")
    else:
        # Автоматичний пошук активних ринків з pagination
        all_markets, markets = bot.find_active_markets()

    if not markets:
        print("⚠️  Активних ринків не знайдено. Використовуйте --show-all для перегляду всіх ринків або --limit <N> для збільшення вибірки.")
        sys.exit(1)

    # Режим дебагу - показуємо повну структуру першого ринку
    if args.debug:
        print("\n" + "="*80)
        print("🐛 DEBUG: Повна структура першого ринку")
        print("="*80)
        print(json.dumps(markets[0], indent=2, ensure_ascii=False))
        print("="*80 + "\n")
        return

    # Якщо запитано тільки список ринків
    if args.list_markets:
        bot.display_markets(markets, verbose=args.verbose)
        print("✅ Готово!")
        return

    # Визначаємо market_id
    if args.market_id:
        market_id = args.market_id
        # Шукаємо інформацію про ринок у всіх ринках (не тільки активних)
        market_info = next((m for m in all_markets if str(m.get("id")) == str(market_id)), None)
        if not market_info:
            print(f"⚠️  Ринок {market_id} не знайдено в списку доступних ринків")
            sys.exit(1)
        # Попереджуємо, якщо ринок не активний
        if market_info.get("status") in ["RESOLVED", "CLOSED"]:
            print(f"⚠️  УВАГА: Ринок має статус '{market_info.get('status')}' та може не мати активної книги ордерів!")
            print(f"   Спробуємо отримати orderbook, але це може призвести до помилки 404.\n")
    else:
        # Беремо перший активний ринок
        if not markets:
            print("❌ Немає активних ринків для відображення")
            sys.exit(1)
        market_info = markets[0]
        market_id = market_info.get("id")
        print(f"ℹ️  Використовуємо перший активний ринок: {market_id}")
        print(f"   Питання: {market_info.get('question', 'N/A')}")
        print(f"   Статус: {market_info.get('status', 'N/A')}\n")

    # Отримуємо outcomes
    outcomes = market_info.get("outcomes", [])

    if not outcomes:
        print(f"⚠️  У ринку {market_id} немає outcomes")
        sys.exit(1)

    # Отримуємо та відображаємо книгу ордерів для кожного outcome
    print(f"📡 Отримання книги ордерів для ринку {market_id}...")
    print(f"   Знайдено {len(outcomes)} outcomes\n")

    for outcome in outcomes:
        outcome_name = outcome.get("name", "Unknown")
        token_id = outcome.get("onChainId")

        print(f"📡 Завантаження orderbook для '{outcome_name}'...")

        try:
            # Спробуємо з tokenId
            orderbook = bot.get_orderbook(market_id, token_id=token_id)
            bot.display_orderbook(orderbook, market_info, outcome_name=outcome_name)
        except Exception as e:
            print(f"❌ Помилка при отриманні orderbook для '{outcome_name}': {e}\n")
            # Спробуємо без tokenId (базовий orderbook)
            try:
                orderbook = bot.get_orderbook(market_id)
                bot.display_orderbook(orderbook, market_info, outcome_name=outcome_name)
            except Exception as e2:
                print(f"❌ Також не вдалося отримати базовий orderbook: {e2}\n")

    print("✅ Готово!")
    print("\n💡 Підказки:")
    print("   - Використовуйте --list-markets для перегляду активних ринків")
    print("   - Використовуйте --show-all для перегляду всіх ринків (включно із завершеними)")
    print("   - Використовуйте --market-id <ID> для вибору конкретного ринку")
    print("   - Використовуйте --limit <N> для зміни кількості ринків у списку")
    print("   - Використовуйте --verbose для детальної інформації про ринки")
    print("   - Використовуйте --debug для перегляду повної JSON структури ринку")


if __name__ == "__main__":
    main()
