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
import time
import signal
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable
from dotenv import load_dotenv
import websocket

# Predict SDK imports
try:
    from predict_sdk import OrderBuilder, ChainId, Side, BuildOrderInput, LimitHelperInput
    from eth_account import Account
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    print("⚠️  Попередження: Predict SDK не встановлено. Виконайте: pip install predict-sdk eth-account web3")
    print("   Створення ордерів буде недоступне без SDK.\n")


def clear_screen():
    """Очищає екран консолі (cross-platform)"""
    os.system('cls' if os.name == 'nt' else 'clear')


def signal_handler(sig, frame):
    """Обробник сигналу для graceful shutdown"""
    print("\n\n⏸️  Моніторинг зупинено")
    sys.exit(0)


class PredictFunWebSocket:
    """WebSocket клієнт для real-time моніторингу Predict Fun"""

    def __init__(self, api_key: str, on_orderbook_update: Optional[Callable] = None,
                 on_error: Optional[Callable] = None, debug: bool = False):
        """
        Ініціалізація WebSocket клієнта

        Args:
            api_key: API ключ для аутентифікації
            on_orderbook_update: Callback для оновлень orderbook
            on_error: Callback для помилок
            debug: Включити debug логування
        """
        self.api_key = api_key
        self.ws_url = f"wss://ws.predict.fun/ws?apiKey={api_key}"
        self.ws = None
        self.on_orderbook_update = on_orderbook_update
        self.on_error_callback = on_error
        self.debug = debug
        self.connected = False
        self.subscribed_topics = set()
        self.request_id_counter = 0
        self.heartbeat_thread = None
        self.should_run = True
        self.reconnect_delay = 2  # Початкова затримка для reconnect
        self.max_reconnect_delay = 60  # Максимальна затримка
        self.last_heartbeat = None

    def _log(self, message: str, level: str = "INFO"):
        """Логування повідомлень"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix = {
            "INFO": "ℹ️",
            "ERROR": "❌",
            "DEBUG": "🐛",
            "SUCCESS": "✅",
            "WARNING": "⚠️"
        }.get(level, "📝")

        if level == "DEBUG" and not self.debug:
            return

        print(f"[{timestamp}] {prefix} {message}")

    def _get_next_request_id(self) -> int:
        """Генерує наступний request ID"""
        self.request_id_counter += 1
        return self.request_id_counter

    def _send_message(self, message: Dict[str, Any]):
        """Надсилає JSON повідомлення через WebSocket"""
        try:
            if self.ws and self.connected:
                json_message = json.dumps(message)
                self.ws.send(json_message)
                if self.debug:
                    self._log(f"Sent: {json_message}", "DEBUG")
        except Exception as e:
            self._log(f"Помилка при надсиланні повідомлення: {e}", "ERROR")

    def subscribe(self, topic: str):
        """
        Підписатися на topic

        Args:
            topic: Topic для підписки (наприклад, "predictOrderbook/123")
        """
        if topic in self.subscribed_topics:
            self._log(f"Вже підписані на {topic}", "WARNING")
            return

        message = {
            "method": "subscribe",
            "requestId": self._get_next_request_id(),
            "params": [topic]
        }
        self._send_message(message)
        self.subscribed_topics.add(topic)
        self._log(f"Підписка на: {topic}", "INFO")

    def unsubscribe(self, topic: str):
        """
        Відписатися від topic

        Args:
            topic: Topic для відписки
        """
        if topic not in self.subscribed_topics:
            self._log(f"Не підписані на {topic}", "WARNING")
            return

        message = {
            "method": "unsubscribe",
            "requestId": self._get_next_request_id(),
            "params": [topic]
        }
        self._send_message(message)
        self.subscribed_topics.remove(topic)
        self._log(f"Відписка від: {topic}", "INFO")

    def _send_heartbeat(self, timestamp: int):
        """Надсилає heartbeat відповідь серверу"""
        message = {
            "method": "heartbeat",
            "data": timestamp
        }
        self._send_message(message)
        if self.debug:
            self._log(f"Heartbeat sent: {timestamp}", "DEBUG")

    def _on_message(self, ws, message):
        """Обробник вхідних повідомлень"""
        try:
            data = json.loads(message)

            if self.debug:
                self._log(f"Received: {message}", "DEBUG")

            msg_type = data.get("type")
            topic = data.get("topic")

            # Обробка heartbeat від сервера
            if msg_type == "M" and topic == "heartbeat":
                timestamp = data.get("data")
                self.last_heartbeat = time.time()
                self._send_heartbeat(timestamp)
                return

            # Обробка відповіді на subscribe/unsubscribe
            if msg_type == "R":
                request_id = data.get("requestId")
                success = data.get("success", False)
                error = data.get("error")

                if success:
                    self._log(f"Request {request_id} успішно виконано", "SUCCESS")
                else:
                    self._log(f"Request {request_id} помилка: {error}", "ERROR")
                return

            # Обробка оновлення orderbook
            if msg_type == "M" and topic and topic.startswith("predictOrderbook/"):
                orderbook_data = data.get("data")
                if self.on_orderbook_update and orderbook_data:
                    self.on_orderbook_update(topic, orderbook_data)
                return

        except json.JSONDecodeError as e:
            self._log(f"Не вдалося розпарсити JSON: {e}", "ERROR")
        except Exception as e:
            self._log(f"Помилка обробки повідомлення: {e}", "ERROR")
            if self.on_error_callback:
                self.on_error_callback(e)

    def _on_error(self, ws, error):
        """Обробник помилок WebSocket"""
        self._log(f"WebSocket помилка: {error}", "ERROR")
        if self.on_error_callback:
            self.on_error_callback(error)

    def _on_close(self, ws, close_status_code, close_msg):
        """Обробник закриття з'єднання"""
        self.connected = False
        self._log(f"WebSocket закрито (код: {close_status_code}, повідомлення: {close_msg})", "WARNING")

        # Спроба автоматичного переподключення
        if self.should_run:
            self._log(f"Спроба переподключення через {self.reconnect_delay} секунд...", "INFO")
            time.sleep(self.reconnect_delay)

            # Exponential backoff
            self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)

            if self.should_run:
                self.connect()

    def _on_open(self, ws):
        """Обробник успішного підключення"""
        self.connected = True
        self.reconnect_delay = 2  # Скидаємо delay після успішного підключення
        self._log("WebSocket підключено!", "SUCCESS")

        # Відновлюємо підписки після переподключення
        if self.subscribed_topics:
            self._log("Відновлення підписок...", "INFO")
            topics_to_resubscribe = list(self.subscribed_topics)
            self.subscribed_topics.clear()
            for topic in topics_to_resubscribe:
                self.subscribe(topic)

    def connect(self):
        """Встановлює WebSocket з'єднання"""
        try:
            self._log("Підключення до WebSocket...", "INFO")

            # Створюємо WebSocket з обробниками
            self.ws = websocket.WebSocketApp(
                self.ws_url,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
                on_open=self._on_open
            )

            # Запускаємо WebSocket в окремому потоці
            ws_thread = threading.Thread(target=self.ws.run_forever, daemon=True)
            ws_thread.start()

            # Чекаємо на підключення
            timeout = 10
            start_time = time.time()
            while not self.connected and time.time() - start_time < timeout:
                time.sleep(0.1)

            if not self.connected:
                raise Exception("Не вдалося встановити з'єднання за відведений час")

        except Exception as e:
            self._log(f"Помилка підключення: {e}", "ERROR")
            if self.on_error_callback:
                self.on_error_callback(e)
            raise

    def disconnect(self):
        """Закриває WebSocket з'єднання"""
        self.should_run = False
        if self.ws:
            self._log("Закриття WebSocket з'єднання...", "INFO")
            self.ws.close()
            self.connected = False
            self.ws = None

    def is_connected(self) -> bool:
        """Перевіряє чи активне з'єднання"""
        return self.connected


class PredictFunBot:
    """Клас для роботи з Predict Fun API"""

    def __init__(self, api_key: str, jwt_token: Optional[str] = None, private_key: Optional[str] = None,
                 predict_account_address: Optional[str] = None, base_url: str = "https://api.predict.fun/v1"):
        """
        Ініціалізація бота

        Args:
            api_key: API ключ для аутентифікації
            jwt_token: JWT токен для аутентифікованих операцій (опціонально)
            private_key: Privy Wallet приватний ключ для підпису ордерів (опціонально)
            predict_account_address: Predict Account (deposit address) - основна адреса акаунта (опціонально)
            base_url: Базова URL API (за замовчуванням mainnet)
        """
        self.api_key = api_key
        self.jwt_token = jwt_token
        self.private_key = private_key
        self.predict_account_address = predict_account_address
        self.base_url = base_url
        self.headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json"
        }
        # Додаємо Authorization header якщо є JWT токен
        if jwt_token:
            self.headers["Authorization"] = f"Bearer {jwt_token}"

        # Ініціалізуємо OrderBuilder якщо є приватний ключ і SDK
        self.order_builder = None
        self.maker_address = None
        if private_key and SDK_AVAILABLE:
            try:
                # Privy wallet account для підписів
                privy_account = Account.from_key(private_key)

                # Predict account address як maker (якщо вказано)
                # Інакше використовуємо privy address
                if predict_account_address:
                    self.maker_address = predict_account_address
                    # Спробуємо ініціалізувати з predict account
                    # SDK може підтримувати predict_account параметр
                    try:
                        self.order_builder = OrderBuilder.make(
                            ChainId.BNB_MAINNET,
                            privy_account,
                            predict_account=predict_account_address
                        )
                    except TypeError:
                        # Якщо SDK не підтримує predict_account параметр,
                        # використовуємо стандартну ініціалізацію
                        self.order_builder = OrderBuilder.make(ChainId.BNB_MAINNET, privy_account)
                else:
                    self.order_builder = OrderBuilder.make(ChainId.BNB_MAINNET, privy_account)
                    self.maker_address = privy_account.address

            except Exception as e:
                print(f"⚠️  Попередження: Не вдалося ініціалізувати OrderBuilder: {e}")
                print("   Створення ордерів буде недоступне.\n")

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
            # Debug logging для POST запитів
            if method == "POST" and params:
                print(f"🐛 DEBUG: Відправка {method} запиту на {url}")
                print(f"🐛 DEBUG: Payload: {json.dumps(params, indent=2)}")
                print(f"🐛 DEBUG: Headers: {json.dumps({k: v for k, v in self.headers.items() if k != 'Authorization'}, indent=2)}")

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
            dict: Книга ордерів (дані з поля "data" відповіді API)
        """
        endpoint = f"/markets/{market_id}/orderbook"
        params = {}
        if token_id:
            params["tokenId"] = token_id

        response = self._make_request(endpoint, params=params)
        # API повертає {"success": true, "data": {...}}, повертаємо тільки data
        return response.get("data", response)

    def create_order(self, market_id: str, token_id: str, side: str, price: float, amount: float) -> Dict[str, Any]:
        """
        Створює новий ордер з криптографічним підписом (потрібен JWT токен і приватний ключ)

        Args:
            market_id: ID ринку
            token_id: ID токена outcome (onChainId)
            side: "BUY" або "SELL"
            price: Ціна (від 0.01 до 0.99)
            amount: Кількість токенів

        Returns:
            dict: Відповідь API з деталями створеного ордера
        """
        # Перевірка наявності необхідних компонентів
        if not self.jwt_token:
            raise Exception("JWT токен не налаштований. Додайте JWT= у файл .env")

        if not self.order_builder:
            raise Exception(
                "OrderBuilder не ініціалізовано. Переконайтеся що:\n"
                "1. Встановлено SDK: pip install predict-sdk eth-account web3\n"
                "2. Додано PRIVATE_KEY= у файл .env"
            )

        # Валідація параметрів
        if side not in ["BUY", "SELL"]:
            raise ValueError("side має бути 'BUY' або 'SELL'")

        if not (0.01 <= price <= 0.99):
            raise ValueError("price має бути між 0.01 та 0.99")

        if amount <= 0:
            raise ValueError("amount має бути більше 0")

        try:
            # Отримуємо інформацію про ринок для feeRateBps
            market_info = self._make_request(f"/markets/{market_id}")
            fee_rate_bps = int(market_info.get("feeRateBps", 100))

            # Конвертуємо в wei (множимо на 10^18)
            price_wei = int(price * 10**18)
            quantity_wei = int(amount * 10**18)

            # Визначаємо сторону для SDK
            sdk_side = Side.BUY if side == "BUY" else Side.SELL

            # Розраховуємо amounts для LIMIT ордера
            amounts = self.order_builder.get_limit_order_amounts(
                LimitHelperInput(
                    side=sdk_side,
                    price_per_share_wei=price_wei,
                    quantity_wei=quantity_wei,
                )
            )

            # Будуємо ордер
            order = self.order_builder.build_order(
                "LIMIT",
                BuildOrderInput(
                    side=sdk_side,
                    token_id=str(token_id),
                    maker_amount=str(amounts.maker_amount),
                    taker_amount=str(amounts.taker_amount),
                    fee_rate_bps=fee_rate_bps,
                ),
            )

            # Будуємо EIP-712 typed data
            typed_data = self.order_builder.build_typed_data(
                order,
                is_neg_risk=False,
                is_yield_bearing=False,
            )

            # Підписуємо typed data
            signed_order = self.order_builder.sign_typed_data_order(typed_data)

            # Конвертуємо SignedOrder об'єкт в словник для JSON серіалізації
            # Спробуємо різні методи конвертації
            if hasattr(signed_order, 'to_dict'):
                order_dict = signed_order.to_dict()
            elif hasattr(signed_order, 'dict'):
                order_dict = signed_order.dict()
            elif hasattr(signed_order, '__dict__'):
                order_dict = vars(signed_order)
            else:
                # Якщо нічого не спрацювало, пробуємо dataclasses
                from dataclasses import asdict
                order_dict = asdict(signed_order)

            # Конвертуємо ключі з snake_case в camelCase для API
            def snake_to_camel(snake_str):
                components = snake_str.split('_')
                return components[0] + ''.join(x.title() for x in components[1:])

            order_dict_camel = {snake_to_camel(k): v for k, v in order_dict.items() if v is not None}

            # Формуємо payload для API
            payload = {
                "data": {
                    "pricePerShare": str(price_wei),  # Відправляємо в wei, не decimal
                    "strategy": "LIMIT",
                    "slippageBps": "0",
                    "isFillOrKill": False,
                    "order": order_dict_camel
                }
            }

            # Відправляємо на сервер
            endpoint = "/orders"
            return self._make_request(endpoint, method="POST", params=payload)

        except Exception as e:
            raise Exception(f"Помилка створення ордера: {str(e)}")

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """
        Скасовує існуючий ордер (потрібен JWT токен)

        Args:
            order_id: ID ордера для скасування

        Returns:
            dict: Відповідь API з підтвердженням скасування
        """
        if not self.jwt_token:
            raise Exception("JWT токен не налаштований. Додайте JWT= у файл .env")

        endpoint = f"/orders/{order_id}"
        return self._make_request(endpoint, method="DELETE")

    def get_my_orders(self, market_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Отримує список власних ордерів (потрібен JWT токен)

        Args:
            market_id: Фільтр за ID ринку (опціонально)
            status: Фільтр за статусом: "OPEN", "FILLED", "CANCELLED" (опціонально)

        Returns:
            list: Список ордерів користувача
        """
        if not self.jwt_token:
            raise Exception("JWT токен не налаштований. Додайте JWT= у файл .env")

        endpoint = "/orders"
        params = {}
        if market_id:
            params["marketId"] = market_id
        if status:
            params["status"] = status

        response = self._make_request(endpoint, params=params)
        return response.get("data", [])

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

    def display_orderbook_compact(self, orderbook: Dict[str, Any], outcome_name: str = "", market_question: str = ""):
        """
        Компактне відображення книги ордерів для live моніторингу

        Args:
            orderbook: Дані книги ордерів
            outcome_name: Назва outcome (Up/Down/Yes/No)
            market_question: Питання ринку
        """
        bids = orderbook.get("bids", orderbook.get("buy", []))
        asks = orderbook.get("asks", orderbook.get("sell", []))

        # DEBUG: тимчасово показуємо структуру
        # print(f"   [DEBUG] bids type: {type(bids)}, len: {len(bids) if isinstance(bids, list) else 'N/A'}")
        # print(f"   [DEBUG] asks type: {type(asks)}, len: {len(asks) if isinstance(asks, list) else 'N/A'}")

        # Заголовок
        print(f"\n📊 {outcome_name.upper()} | {market_question[:60]}...")
        print("-" * 80)

        # Топ-5 asks (у зворотному порядку, щоб найнижча ціна була зверху)
        print("🔴 ASKS (Sell)")
        if asks:
            for order in reversed(asks[:5]):
                if isinstance(order, list) and len(order) >= 2:
                    print(f"   {order[0]:<10.2f} | {order[1]:<10.2f}")
        else:
            print("   Немає ордерів")

        print("-" * 80)

        # Топ-5 bids
        print("🟢 BIDS (Buy)")
        if bids:
            for order in bids[:5]:
                if isinstance(order, list) and len(order) >= 2:
                    print(f"   {order[0]:<10.2f} | {order[1]:<10.2f}")
        else:
            print("   Немає ордерів")

    def display_my_orders(self, orders: List[Dict[str, Any]]):
        """
        Виводить список власних ордерів

        Args:
            orders: Список ордерів
        """
        print("\n" + "="*80)
        print("📋 МОЇ ОРДЕРИ")
        print("="*80 + "\n")

        if not orders:
            print("📭 У вас немає ордерів")
            print("\n" + "="*80)
            return

        for i, order in enumerate(orders, 1):
            order_id = order.get('id', 'N/A')
            market_id = order.get('marketId', 'N/A')
            side = order.get('side', 'N/A')
            price = order.get('price', 'N/A')
            amount = order.get('amount', 'N/A')
            filled = order.get('filledAmount', '0')
            status = order.get('status', 'N/A')

            side_icon = "🟢" if side == "BUY" else "🔴"
            status_icon = {"OPEN": "⏳", "FILLED": "✅", "CANCELLED": "❌"}.get(status, "❓")

            print(f"{i}. {side_icon} {side} | {status_icon} {status}")
            print(f"   Order ID: {order_id}")
            print(f"   Market ID: {market_id}")
            print(f"   Ціна: {price}")
            print(f"   Кількість: {amount} (Заповнено: {filled})")
            print()

        print(f"Всього ордерів: {len(orders)}")
        print("="*80 + "\n")

    def monitor_orderbook(self, market_id: str, market_info: Dict[str, Any], interval: int = 10):
        """
        Моніторинг orderbook в реальному часі з автооновленням

        Args:
            market_id: ID ринку
            market_info: Інформація про ринок
            interval: Інтервал оновлення в секундах
        """
        # Реєструємо обробник сигналу для Ctrl+C
        signal.signal(signal.SIGINT, signal_handler)

        outcomes = market_info.get("outcomes", [])
        iteration = 0

        print(f"\n🔄 Запуск моніторингу orderbook для ринку {market_id}")
        print(f"   Інтервал оновлення: {interval} секунд")
        print(f"   Натисніть Ctrl+C для зупинки\n")

        time.sleep(2)  # Короткаузатримка перед початком

        try:
            while True:
                iteration += 1
                clear_screen()

                # Заголовок
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print("="*80)
                print(f"🤖 PREDICT FUN BOT - LIVE MONITOR #{iteration}")
                print(f"⏰ Оновлено: {now}")
                print(f"🔗 Ринок ID: {market_id}")
                print(f"❓ Питання: {market_info.get('question', 'N/A')}")
                print("="*80)

                # Отримуємо та відображаємо orderbook для кожного outcome
                for outcome in outcomes:
                    outcome_name = outcome.get("name", "Unknown")
                    token_id = outcome.get("onChainId")

                    try:
                        orderbook = self.get_orderbook(market_id, token_id=token_id)
                        self.display_orderbook_compact(
                            orderbook,
                            outcome_name=outcome_name,
                            market_question=market_info.get('question', 'N/A')
                        )
                    except Exception as e:
                        print(f"\n❌ Помилка для '{outcome_name}': {e}")

                print("\n" + "="*80)
                print(f"⏳ Наступне оновлення через {interval} секунд... (Ctrl+C для зупинки)")

                time.sleep(interval)

        except KeyboardInterrupt:
            signal_handler(None, None)

    def monitor_orderbook_websocket(self, market_id: str, market_info: Dict[str, Any], debug: bool = False):
        """
        Моніторинг orderbook через WebSocket в реальному часі

        Args:
            market_id: ID ринку
            market_info: Інформація про ринок
            debug: Включити debug логування
        """
        # Змінна для зберігання останніх даних orderbook для кожного outcome
        latest_orderbooks = {}
        update_count = 0
        outcomes = market_info.get("outcomes", [])

        # Створюємо mapping outcome_name -> tokenId
        outcome_map = {outcome.get("name", "Unknown"): outcome.get("onChainId") for outcome in outcomes}

        def on_orderbook_update(topic: str, data: Dict[str, Any]):
            """Callback для оновлень orderbook"""
            nonlocal update_count

            # Оновлюємо orderbook для всіх outcomes через REST API
            # (оскільки WebSocket дає тільки загальний orderbook)
            for outcome_name, token_id in outcome_map.items():
                try:
                    orderbook = self.get_orderbook(market_id, token_id=token_id)
                    latest_orderbooks[outcome_name] = orderbook
                except Exception as e:
                    if debug:
                        print(f"Помилка отримання orderbook для {outcome_name}: {e}")

            update_count += 1

            # Очищаємо екран і відображаємо оновлені orderbook
            clear_screen()

            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print("="*80)
            print(f"🤖 PREDICT FUN BOT - WEBSOCKET MONITOR (Оновлення #{update_count})")
            print(f"⏰ Останнє оновлення: {now}")
            print(f"🔗 Ринок ID: {market_id}")
            print(f"❓ Питання: {market_info.get('question', 'N/A')}")
            print(f"🔌 WebSocket: Підключено")
            print("="*80)

            # Відображаємо orderbook для кожного outcome
            for outcome_name in sorted(latest_orderbooks.keys()):
                orderbook = latest_orderbooks[outcome_name]
                self.display_orderbook_compact(
                    orderbook,
                    outcome_name=outcome_name,
                    market_question=market_info.get('question', 'N/A')
                )

            print("\n" + "="*80)
            print("⏳ Очікування наступного оновлення... (Ctrl+C для зупинки)")

        def on_error(error):
            """Callback для помилок"""
            print(f"\n❌ Помилка WebSocket: {error}")

        # Створюємо WebSocket клієнт
        print(f"\n🔌 Запуск WebSocket моніторингу для ринку {market_id}")
        print(f"   Натисніть Ctrl+C для зупинки\n")

        ws_client = PredictFunWebSocket(
            api_key=self.api_key,
            on_orderbook_update=on_orderbook_update,
            on_error=on_error,
            debug=debug
        )

        try:
            # Підключаємося
            ws_client.connect()

            # Підписуємося на orderbook
            topic = f"predictOrderbook/{market_id}"
            ws_client.subscribe(topic)

            print(f"\n✅ Підписано на оновлення orderbook")
            print(f"⏳ Очікування оновлень від сервера...\n")

            # Чекаємо на оновлення (або Ctrl+C)
            signal.signal(signal.SIGINT, signal_handler)

            while True:
                time.sleep(1)

        except KeyboardInterrupt:
            print("\n\n⏸️  Зупинка моніторингу...")
        except Exception as e:
            print(f"\n❌ Помилка: {e}")
        finally:
            print("🔌 Закриття WebSocket з'єднання...")
            ws_client.disconnect()
            print("✅ WebSocket закрито")


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
    parser.add_argument(
        "--watch",
        type=int,
        metavar="SECONDS",
        help="Моніторинг orderbook в реальному часі з інтервалом оновлення (в секундах)"
    )
    parser.add_argument(
        "--search",
        type=str,
        metavar="QUERY",
        help="Пошук ринку за назвою або slug (наприклад, 'BTC/USD 11:30' або 'btc-usd-11-30')"
    )
    parser.add_argument(
        "--websocket",
        action="store_true",
        help="Використовувати WebSocket для real-time моніторингу замість polling (працює з --watch або без нього)"
    )
    parser.add_argument(
        "--create-order",
        action="store_true",
        help="Створити новий ордер (потрібен JWT токен)"
    )
    parser.add_argument(
        "--side",
        type=str,
        choices=["BUY", "SELL"],
        help="Тип ордера: BUY або SELL (для --create-order)"
    )
    parser.add_argument(
        "--price",
        type=float,
        help="Ціна ордера від 0.01 до 0.99 (для --create-order)"
    )
    parser.add_argument(
        "--amount",
        type=float,
        help="Кількість токенів (для --create-order)"
    )
    parser.add_argument(
        "--token-id",
        type=str,
        help="Token ID outcome (для --create-order)"
    )
    parser.add_argument(
        "--my-orders",
        action="store_true",
        help="Показати список власних ордерів (потрібен JWT токен)"
    )
    parser.add_argument(
        "--cancel-order",
        type=str,
        metavar="ORDER_ID",
        help="Скасувати ордер за ID (потрібен JWT токен)"
    )
    args = parser.parse_args()

    # Завантажуємо змінні середовища
    load_dotenv()

    # Отримуємо API ключ
    api_key = os.getenv("API_KEY")
    jwt_token = os.getenv("JWT")
    private_key = os.getenv("PRIVATE_KEY")
    predict_account_address = os.getenv("PREDICT_ACCOUNT_ADDRESS")

    if not api_key:
        print("❌ Помилка: API_KEY не знайдено в .env файлі")
        print("\n📝 Інструкція:")
        print("1. Створіть файл .env в кореневій директорії")
        print("2. Додайте рядок: API_KEY=ваш_апі_ключ")
        print("3. Запустіть бота знову")
        sys.exit(1)

    # Перевіряємо JWT для команд що його потребують
    if (args.create_order or args.my_orders or args.cancel_order) and not jwt_token:
        print("❌ Помилка: JWT токен не знайдено в .env файлі")
        print("\n📝 Інструкція:")
        print("1. Відкрийте файл .env")
        print("2. Додайте рядок: JWT=ваш_jwt_токен")
        print("3. JWT токен можна отримати підписавши повідомлення вашим гаманцем")
        print("4. Дивіться документацію: https://dev.predict.fun/doc-663127")
        sys.exit(1)

    # Перевіряємо PRIVATE_KEY і PREDICT_ACCOUNT_ADDRESS для створення ордерів
    if args.create_order:
        if not private_key:
            print("❌ Помилка: PRIVATE_KEY не знайдено в .env файлі")
            print("\n📝 Інструкція:")
            print("1. Відкрийте файл .env")
            print("2. Додайте рядок: PRIVATE_KEY=0x...")
            print("3. Це має бути Privy Wallet приватний ключ")
            print("4. Отримати: predict.fun → Settings → Advanced → Export Privy Wallet")
            print("⚠️  ВАЖЛИВО: Тримайте приватний ключ в безпеці!")
            sys.exit(1)

        if not predict_account_address:
            print("❌ Помилка: PREDICT_ACCOUNT_ADDRESS не знайдено в .env файлі")
            print("\n📝 Інструкція:")
            print("1. Відкрийте файл .env")
            print("2. Додайте рядок: PREDICT_ACCOUNT_ADDRESS=0x...")
            print("3. Це ваша deposit address (Predict Account)")
            print("4. Знайти: predict.fun → Settings → Profile → Deposit Address")
            print("⚠️  ВАЖЛИВО: JWT має бути створений для цієї ж адреси!")
            sys.exit(1)

    # Створюємо екземпляр бота
    print("🤖 Запуск Predict Fun Bot...")
    print(f"🔗 API: https://api.predict.fun/v1")
    if jwt_token:
        print("🔐 JWT: Налаштовано")
    if private_key and args.create_order:
        print("🔑 Private Key: Налаштовано")
    if predict_account_address and args.create_order:
        print(f"📍 Predict Account: {predict_account_address}")
    print()

    bot = PredictFunBot(api_key, jwt_token=jwt_token, private_key=private_key,
                        predict_account_address=predict_account_address)

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

    # Якщо запитано пошук
    if args.search:
        search_query = args.search.lower()
        found_markets = [
            m for m in markets
            if search_query in m.get('question', '').lower()
            or search_query in m.get('categorySlug', '').lower()
        ]

        if not found_markets:
            print(f"⚠️  Не знайдено ринків за запитом: '{args.search}'")
            print(f"   Перевірено {len(markets)} активних ринків")
            print(f"\n💡 Спробуйте:")
            print(f"   - Використати частину назви (наприклад, 'BTC' або '11:30')")
            print(f"   - Використати --show-all --limit 100 для пошуку серед всіх ринків")
            sys.exit(1)

        print(f"\n🔍 Знайдено {len(found_markets)} ринків за запитом: '{args.search}'\n")
        bot.display_markets(found_markets, verbose=True)
        print("✅ Готово!")
        return

    # Якщо запитано тільки список ринків
    if args.list_markets:
        bot.display_markets(markets, verbose=args.verbose)
        print("✅ Готово!")
        return

    # Команда: показати мої ордери
    if args.my_orders:
        print("📡 Отримання списку ваших ордерів...")
        try:
            orders = bot.get_my_orders(market_id=args.market_id)
            bot.display_my_orders(orders)
            print("✅ Готово!")
        except Exception as e:
            print(f"❌ Помилка: {e}")
        return

    # Команда: скасувати ордер
    if args.cancel_order:
        print(f"🗑️  Скасування ордера {args.cancel_order}...")
        try:
            result = bot.cancel_order(args.cancel_order)
            print(f"✅ Ордер успішно скасовано!")
            if args.debug:
                print(json.dumps(result, indent=2, ensure_ascii=False))
        except Exception as e:
            print(f"❌ Помилка: {e}")
        return

    # Команда: створити ордер
    if args.create_order:
        # Перевіряємо обов'язкові параметри
        if not all([args.market_id, args.token_id, args.side, args.price is not None, args.amount is not None]):
            print("❌ Помилка: Для створення ордера потрібні всі параметри:")
            print("   --market-id, --token-id, --side, --price, --amount")
            print("\nПриклад:")
            print("   python predict_bot.py --create-order --market-id 3115 \\")
            print("     --token-id 72215613815361659147666180202980724709625996146459314242337906397782699197017 \\")
            print("     --side BUY --price 0.50 --amount 10")
            sys.exit(1)

        print(f"📝 Створення ордера...")
        print(f"   Market ID: {args.market_id}")
        print(f"   Token ID: {args.token_id[:20]}...")
        print(f"   Сторона: {args.side}")
        print(f"   Ціна: {args.price}")
        print(f"   Кількість: {args.amount}\n")

        try:
            result = bot.create_order(
                market_id=args.market_id,
                token_id=args.token_id,
                side=args.side,
                price=args.price,
                amount=args.amount
            )
            print("✅ Ордер успішно створено!")
            print(f"   Order ID: {result.get('data', {}).get('id', 'N/A')}")
            if args.debug:
                print("\n🐛 Повна відповідь:")
                print(json.dumps(result, indent=2, ensure_ascii=False))
        except Exception as e:
            print(f"❌ Помилка: {e}")
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

    # Якщо включено режим моніторингу
    if args.watch or args.websocket:
        # WebSocket моніторинг (якщо вказано --websocket або --websocket --watch)
        if args.websocket:
            bot.monitor_orderbook_websocket(market_id, market_info, debug=args.debug)
            return
        # Polling моніторинг (якщо вказано тільки --watch без --websocket)
        else:
            if args.watch < 1:
                print("❌ Інтервал оновлення має бути не менше 1 секунди")
                sys.exit(1)
            bot.monitor_orderbook(market_id, market_info, interval=args.watch)
            return

    # Отримуємо та відображаємо книгу ордерів для кожного outcome (одноразово)
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

    print("✅ Готово!")
    print("\n💡 Підказки:")
    print("   - Використовуйте --list-markets для перегляду активних ринків")
    print("   - Використовуйте --show-all для перегляду всіх ринків (включно із завершеними)")
    print("   - Використовуйте --market-id <ID> для вибору конкретного ринку")
    print("   - Використовуйте --watch <SECONDS> для моніторингу через polling (інтервал в секундах)")
    print("   - Використовуйте --websocket для real-time моніторингу через WebSocket")
    print("   - Використовуйте --limit <N> для зміни кількості ринків у списку")
    print("   - Використовуйте --verbose для детальної інформації про ринки")
    print("   - Використовуйте --debug для перегляду повної JSON структури ринку та WebSocket логів")


if __name__ == "__main__":
    main()
