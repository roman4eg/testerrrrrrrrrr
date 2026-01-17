#!/usr/bin/env python3
"""
Multi-Account Trader для Predict Fun
Автоматична торгівля на 15-хвилинних BTC/USD маркетах з 3 акаунтами
"""

import time
import random
import re
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime


class MultiAccountTrader:
    """
    Клас для мультиакаунтної торгівлі на 15-хв BTC/USD маркетах

    Стратегія:
    1. Знайти активний 15-хв BTC/USD маркет
    2. Моніторити спред >= min_spread
    3. Випадково вибрати UP/DOWN
    4. Розмістити основний ордер: buy по (best_ask - 2¢)
    5. Розмістити 2 хедж ордери на протилежну сторону (60/40 розподіл)
    6. Чекати заповнення всіх ордерів
    7. Чекати завершення маркету
    8. Claim позиції
    9. Повторити з наступним маркетом
    """

    def __init__(
        self,
        main_bot,
        hedge1_bot,
        hedge2_bot,
        budgets: List[float],
        telegram_notifier=None,
        min_spread: int = 3,
        safety_margin: float = 0.02
    ):
        """
        Ініціалізація мультиакаунтного трейдера

        Args:
            main_bot: PredictFunBot instance для основного акаунта
            hedge1_bot: PredictFunBot instance для 1-го хедж акаунта
            hedge2_bot: PredictFunBot instance для 2-го хедж акаунта
            budgets: [main_budget, hedge1_budget, hedge2_budget] в USD
            telegram_notifier: TelegramNotifier для повідомлень
            min_spread: Мінімальний спред в центах (за замовчуванням 3)
            safety_margin: Safety margin для розрахунку shares (за замовчуванням 2%)
        """
        self.main_bot = main_bot
        self.hedge1_bot = hedge1_bot
        self.hedge2_bot = hedge2_bot
        self.budgets = budgets
        self.telegram = telegram_notifier
        self.min_spread = min_spread / 100.0  # Конвертуємо центи в decimal
        self.safety_margin = safety_margin

        # Статистика для відстеження
        self.round_number = 0
        self.previous_total_balance = None
        self.stats = {
            'total_rounds': 0,
            'total_profit': 0.0,
            'total_cost': 0.0
        }

    def find_15min_btc_market(self, debug: bool = False) -> Optional[Dict[str, Any]]:
        """
        Знаходить активний 15-хвилинний BTC/USD маркет

        Args:
            debug: Якщо True, показує всі BTC/USD маркети

        Returns:
            dict: Дані маркету або None якщо не знайдено
        """
        print("\n🔍 Пошук 15-хвилинного BTC/USD маркету...")

        try:
            # Використовуємо пагінацію для пошуку
            max_pages = 5
            cursor = None
            btc_markets_found = []

            for page in range(1, max_pages + 1):
                markets, cursor = self.main_bot.get_markets(limit=150, after=cursor)

                for market in markets:
                    title = market.get('title', '')
                    title_lower = title.lower()
                    status = market.get('status')

                    # Шукаємо BTC/USD маркети
                    if status == 'REGISTERED' and 'btc/usd' in title_lower:
                        btc_markets_found.append(market)

                        # Перевіряємо чи це 15-хвилинний маркет
                        # Варіанти: "15 minutes", "15-minute", "15min", "2:45-3:00" (різниця 15 хв)
                        is_15min = False

                        # Простий варіант: "15" + "minute"/"min"
                        if '15' in title and ('minute' in title_lower or 'min' in title_lower):
                            is_15min = True

                        # Формат часу: "X:XX-X:XX" - перевіряємо різницю
                        time_match = re.search(r'(\d+):(\d+)-(\d+):(\d+)', title)
                        if time_match:
                            start_hour, start_min, end_hour, end_min = map(int, time_match.groups())
                            start_total = start_hour * 60 + start_min
                            end_total = end_hour * 60 + end_min
                            diff = end_total - start_total
                            if diff == 15:
                                is_15min = True

                        if is_15min:
                            print(f"✅ Знайдено маркет: {title}")
                            print(f"   ID: {market.get('id')}")
                            print(f"   Status: {status}")
                            return market

                if not cursor:
                    break

            # Debug: показуємо всі BTC/USD маркети що знайшли
            if debug and btc_markets_found:
                print(f"\n🔍 DEBUG: Знайдено {len(btc_markets_found)} BTC/USD маркетів:")
                for m in btc_markets_found[:10]:  # Перші 10
                    print(f"   - {m.get('title')} (ID: {m.get('id')})")

            # Якщо є хоч якийсь BTC/USD маркет - беремо перший
            if btc_markets_found:
                market = btc_markets_found[0]
                print(f"⚠️  15-хв маркет не знайдено, використовую перший BTC/USD:")
                print(f"   {market.get('title')} (ID: {market.get('id')})")
                return market

            print("❌ Не знайдено активних BTC/USD маркетів")
            return None

        except Exception as e:
            print(f"❌ Помилка пошуку маркету: {e}")
            import traceback
            traceback.print_exc()
            return None

    def get_orderbook_spread(self, market_id: str, debug: bool = False) -> Optional[Tuple[float, float, str, str]]:
        """
        Отримує спред з orderbook для маркету

        Args:
            market_id: ID маркету
            debug: Показувати детальний вивід

        Returns:
            tuple: (spread_up, spread_down, up_token_id, down_token_id) або None
        """
        try:
            # Спочатку отримуємо інформацію про маркет щоб дізнатись про outcomes
            markets, _ = self.main_bot.get_markets(limit=150)
            market_data = None

            for market in markets:
                if str(market.get('id')) == str(market_id):
                    market_data = market
                    break

            if not market_data:
                if debug:
                    print(f"⚠️  DEBUG: Не знайдено маркет з ID {market_id}")
                return None

            outcomes_list = market_data.get('outcomes', [])

            if debug:
                print(f"\n🔍 DEBUG: Market structure:")
                print(f"   Market ID: {market_data.get('id')}")
                print(f"   Title: {market_data.get('title')}")
                print(f"   Outcomes count: {len(outcomes_list)}")
                for i, outcome in enumerate(outcomes_list):
                    print(f"   [{i}] {outcome.get('name')} (tokenId: {outcome.get('tokenId')})")

            # Знаходимо UP та DOWN outcomes
            up_outcome_info = None
            down_outcome_info = None

            for outcome in outcomes_list:
                name = outcome.get('name', '').lower()
                if 'up' in name:
                    up_outcome_info = outcome
                elif 'down' in name:
                    down_outcome_info = outcome

            if not up_outcome_info or not down_outcome_info:
                if debug:
                    print(f"\n⚠️  DEBUG: Не знайдено UP/DOWN outcomes в маркеті")
                return None

            # Тепер отримуємо orderbook для кожного outcome
            up_orderbook = self.main_bot.get_orderbook(market_id, token_id=up_outcome_info['tokenId'])
            down_orderbook = self.main_bot.get_orderbook(market_id, token_id=down_outcome_info['tokenId'])

            if debug:
                print(f"\n🔍 DEBUG: Orderbook structure:")
                print(f"   UP orderbook keys: {list(up_orderbook.keys())}")
                print(f"   DOWN orderbook keys: {list(down_orderbook.keys())}")

            # Створюємо структуру outcome з orderbook даними
            up_outcome = {
                'name': up_outcome_info['name'],
                'tokenId': up_outcome_info['tokenId'],
                'asks': up_orderbook.get('asks', []),
                'bids': up_orderbook.get('bids', [])
            }

            down_outcome = {
                'name': down_outcome_info['name'],
                'tokenId': down_outcome_info['tokenId'],
                'asks': down_orderbook.get('asks', []),
                'bids': down_orderbook.get('bids', [])
            }

            # Отримуємо best ask та best bid для кожного
            up_asks = up_outcome.get('asks', [])
            up_bids = up_outcome.get('bids', [])
            down_asks = down_outcome.get('asks', [])
            down_bids = down_outcome.get('bids', [])

            if debug:
                print(f"\n🔍 DEBUG Orderbook:")
                print(f"   UP asks: {len(up_asks)}, bids: {len(up_bids)}")
                print(f"   DOWN asks: {len(down_asks)}, bids: {len(down_bids)}")
                if up_asks:
                    print(f"   UP best ask: ${up_asks[0].get('price', 'N/A')}")
                if up_bids:
                    print(f"   UP best bid: ${up_bids[0].get('price', 'N/A')}")
                if down_asks:
                    print(f"   DOWN best ask: ${down_asks[0].get('price', 'N/A')}")
                if down_bids:
                    print(f"   DOWN best bid: ${down_bids[0].get('price', 'N/A')}")

            if not (up_asks and up_bids and down_asks and down_bids):
                if debug:
                    print(f"⚠️  Недостатньо даних в orderbook")
                return None

            # Best ask = найнижча ціна продажу
            # Best bid = найвища ціна покупки
            up_best_ask = float(up_asks[0]['price'])
            up_best_bid = float(up_bids[0]['price'])
            down_best_ask = float(down_asks[0]['price'])
            down_best_bid = float(down_bids[0]['price'])

            # Спред = різниця між ask і bid
            spread_up = up_best_ask - up_best_bid
            spread_down = down_best_ask - down_best_bid

            if debug:
                print(f"\n🔍 DEBUG Spreads:")
                print(f"   UP spread: {spread_up * 100:.1f}¢")
                print(f"   DOWN spread: {spread_down * 100:.1f}¢")

            up_token_id = up_outcome.get('tokenId')
            down_token_id = down_outcome.get('tokenId')

            return (spread_up, spread_down, up_token_id, down_token_id)

        except Exception as e:
            print(f"⚠️  Помилка отримання orderbook: {e}")
            if debug:
                import traceback
                traceback.print_exc()
            return None

    def calculate_shares(
        self,
        price_main: float,
        price_hedge: float,
        budget_main: float,
        budget_hedge1: float,
        budget_hedge2: float
    ) -> Optional[Tuple[int, int, int]]:
        """
        Розраховує кількість shares для кожного акаунта

        Args:
            price_main: Ціна для основного акаунта
            price_hedge: Ціна для хедж акаунтів
            budget_main: Budget основного акаунта
            budget_hedge1: Budget 1-го хедж акаунта
            budget_hedge2: Budget 2-го хедж акаунта

        Returns:
            tuple: (shares_main, shares_hedge1, shares_hedge2) або None
        """
        # Розраховуємо максимальну кількість shares для основного акаунта
        max_shares_main = int((budget_main * (1 - self.safety_margin)) / price_main)

        # Розраховуємо максимальну кількість shares для хедж акаунтів (сумарно)
        total_hedge_budget = budget_hedge1 + budget_hedge2
        max_shares_hedge = int((total_hedge_budget * (1 - self.safety_margin)) / price_hedge)

        # Беремо мінімум з двох
        total_shares = min(max_shares_main, max_shares_hedge)

        if total_shares <= 0:
            print("❌ Недостатньо budget для торгівлі")
            return None

        # Розподіляємо shares між двома хедж акаунтами (60/40 або випадково)
        # Використаємо фіксований 60/40 розподіл
        shares_hedge1 = int(total_shares * 0.6)
        shares_hedge2 = total_shares - shares_hedge1

        # Перевіряємо що кожен акаунт може оплатити свою частину
        cost_main = total_shares * price_main
        cost_hedge1 = shares_hedge1 * price_hedge
        cost_hedge2 = shares_hedge2 * price_hedge

        if cost_main > budget_main:
            print(f"⚠️  Основний акаунт: недостатньо budget (потрібно ${cost_main:.2f}, є ${budget_main:.2f})")
            return None

        if cost_hedge1 > budget_hedge1:
            print(f"⚠️  Hedge1: недостатньо budget (потрібно ${cost_hedge1:.2f}, є ${budget_hedge1:.2f})")
            return None

        if cost_hedge2 > budget_hedge2:
            print(f"⚠️  Hedge2: недостатньо budget (потрібно ${cost_hedge2:.2f}, є ${budget_hedge2:.2f})")
            return None

        return (total_shares, shares_hedge1, shares_hedge2)

    def wait_for_spread(self, market_id: str, timeout: int = 600) -> Optional[Dict[str, Any]]:
        """
        Очікує достатній спред для входу

        Args:
            market_id: ID маркету
            timeout: Максимальний час очікування в секундах (за замовчуванням 10 хв)

        Returns:
            dict: Дані orderbook або None при таймауті
        """
        print(f"\n📊 Моніторинг спреду (мінімум {self.min_spread * 100}¢)...")
        start_time = time.time()
        first_attempt = True
        attempt_count = 0

        while (time.time() - start_time) < timeout:
            try:
                attempt_count += 1
                # Показуємо детальний debug тільки на першій спробі
                spread_data = self.get_orderbook_spread(market_id, debug=first_attempt)
                first_attempt = False

                if not spread_data:
                    elapsed = int(time.time() - start_time)
                    print(f"   Спроба #{attempt_count} - orderbook недоступний (минуло {elapsed}с)...", end='\r')
                    time.sleep(5)
                    continue

                spread_up, spread_down, up_token_id, down_token_id = spread_data

                print(f"   UP спред: {spread_up * 100:.1f}¢, DOWN спред: {spread_down * 100:.1f}¢", end='\r')

                # Перевіряємо чи обидва спреди >= min_spread
                if spread_up >= self.min_spread and spread_down >= self.min_spread:
                    print(f"\n✅ Достатній спред знайдено!")
                    print(f"   UP: {spread_up * 100:.1f}¢, DOWN: {spread_down * 100:.1f}¢")
                    return {
                        'spread_up': spread_up,
                        'spread_down': spread_down,
                        'up_token_id': up_token_id,
                        'down_token_id': down_token_id
                    }

                time.sleep(5)

            except Exception as e:
                print(f"\n⚠️  Помилка моніторингу спреду: {e}")
                time.sleep(5)

        print(f"\n⏰ Таймаут очікування спреду ({timeout}с)")
        return None

    def send_telegram_message(self, message: str):
        """Відправляє повідомлення в Telegram якщо налаштовано"""
        if self.telegram and self.telegram.chat_id:
            try:
                self.telegram.send_message(message)
            except Exception as e:
                print(f"⚠️  Помилка відправки в Telegram: {e}")

    def place_orders_strategy(self, market_id: str, market_title: str, orderbook_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Розміщує ордери згідно стратегії: основний + 2 хедж

        Returns:
            dict: Дані розміщених ордерів або None при помилці
        """
        print("\n🎲 Розміщення ордерів...")

        try:
            # Використовуємо дані з orderbook_data (вже отримані в wait_for_spread)
            up_token_id = orderbook_data.get('up_token_id')
            down_token_id = orderbook_data.get('down_token_id')

            if not up_token_id or not down_token_id:
                print("❌ Немає даних про token IDs")
                return None

            # Отримуємо свіжі дані orderbook для кожного outcome
            up_orderbook = self.main_bot.get_orderbook(market_id, token_id=up_token_id)
            down_orderbook = self.main_bot.get_orderbook(market_id, token_id=down_token_id)

            # Створюємо структури outcome
            up_outcome = {
                'tokenId': up_token_id,
                'name': 'UP',
                'asks': up_orderbook.get('asks', []),
                'bids': up_orderbook.get('bids', [])
            }

            down_outcome = {
                'tokenId': down_token_id,
                'name': 'DOWN',
                'asks': down_orderbook.get('asks', []),
                'bids': down_orderbook.get('bids', [])
            }

            if not up_outcome['asks'] or not down_outcome['asks']:
                print("❌ Недостатньо даних в orderbook")
                return None

            # Випадково вибираємо сторону для основного акаунта
            side_choice = random.choice(['UP', 'DOWN'])
            print(f"   Вибрано сторону: {side_choice}")

            # Визначаємо основну та хедж сторони
            if side_choice == 'UP':
                main_outcome = up_outcome
                hedge_outcome = down_outcome
                main_side_name = "UP"
                hedge_side_name = "DOWN"
            else:
                main_outcome = down_outcome
                hedge_outcome = up_outcome
                main_side_name = "DOWN"
                hedge_side_name = "UP"

            # Отримуємо best ask для основної сторони
            main_asks = main_outcome.get('asks', [])
            if not main_asks:
                print(f"❌ Немає asks для {main_side_name}")
                return None

            best_ask_main = float(main_asks[0]['price'])

            # Ціна для основного = best_ask - 2¢ (0.02)
            price_main = max(0.01, best_ask_main - 0.02)
            print(f"   {main_side_name}: best ask = ${best_ask_main:.2f}, ціна ордера = ${price_main:.2f}")

            # Ціна для хедж = 1 - price_main (data neutral)
            price_hedge = 1.0 - price_main
            print(f"   {hedge_side_name}: ціна = ${price_hedge:.2f}")

            # Розраховуємо shares
            shares_result = self.calculate_shares(
                price_main=price_main,
                price_hedge=price_hedge,
                budget_main=self.budgets[0],
                budget_hedge1=self.budgets[1],
                budget_hedge2=self.budgets[2]
            )

            if not shares_result:
                print("❌ Не вдалося розрахувати shares")
                return None

            shares_main, shares_hedge1, shares_hedge2 = shares_result

            print(f"\n📊 Розрахунок shares:")
            print(f"   Main: {shares_main} шейрів по ${price_main:.2f} = ${shares_main * price_main:.2f}")
            print(f"   Hedge1: {shares_hedge1} шейрів по ${price_hedge:.2f} = ${shares_hedge1 * price_hedge:.2f}")
            print(f"   Hedge2: {shares_hedge2} шейрів по ${price_hedge:.2f} = ${shares_hedge2 * price_hedge:.2f}")

            # Розміщуємо ордери
            orders = {
                'main': None,
                'hedge1': None,
                'hedge2': None
            }

            main_token_id = main_outcome.get('tokenId')
            hedge_token_id = hedge_outcome.get('tokenId')

            # 1. Основний ордер
            print(f"\n1️⃣ Розміщую основний ордер ({main_side_name})...")
            try:
                result_main = self.main_bot.create_order(
                    market_id=market_id,
                    token_id=main_token_id,
                    side="BUY",
                    price=price_main,
                    amount=shares_main
                )
                orders['main'] = result_main
                order_id = result_main.get('orderId', 'N/A')
                print(f"   ✅ Ордер #{order_id} розміщено")

                # Telegram повідомлення
                msg = f"🎯 <b>Основний ордер розміщено</b>\n\nРинок: {market_title}\nСторона: {main_side_name}\nЦіна: ${price_main:.2f}\nКількість: {shares_main} шейрів"
                self.send_telegram_message(msg)

            except Exception as e:
                print(f"   ❌ Помилка: {e}")
                return None

            # 2. Хедж ордер 1
            print(f"\n2️⃣ Розміщую хедж ордер 1 ({hedge_side_name})...")
            try:
                result_hedge1 = self.hedge1_bot.create_order(
                    market_id=market_id,
                    token_id=hedge_token_id,
                    side="BUY",
                    price=price_hedge,
                    amount=shares_hedge1
                )
                orders['hedge1'] = result_hedge1
                order_id = result_hedge1.get('orderId', 'N/A')
                print(f"   ✅ Ордер #{order_id} розміщено")

                # Telegram повідомлення
                msg = f"🛡 <b>Хедж ордер 1 розміщено</b>\n\nРинок: {market_title}\nСторона: {hedge_side_name}\nЦіна: ${price_hedge:.2f}\nКількість: {shares_hedge1} шейрів"
                self.send_telegram_message(msg)

            except Exception as e:
                print(f"   ❌ Помилка: {e}")
                # TODO: скасувати основний ордер
                return None

            # 3. Хедж ордер 2
            print(f"\n3️⃣ Розміщую хедж ордер 2 ({hedge_side_name})...")
            try:
                result_hedge2 = self.hedge2_bot.create_order(
                    market_id=market_id,
                    token_id=hedge_token_id,
                    side="BUY",
                    price=price_hedge,
                    amount=shares_hedge2
                )
                orders['hedge2'] = result_hedge2
                order_id = result_hedge2.get('orderId', 'N/A')
                print(f"   ✅ Ордер #{order_id} розміщено")

                # Telegram повідомлення
                msg = f"🛡 <b>Хедж ордер 2 розміщено</b>\n\nРинок: {market_title}\nСторона: {hedge_side_name}\nЦіна: ${price_hedge:.2f}\nКількість: {shares_hedge2} шейрів"
                self.send_telegram_message(msg)

            except Exception as e:
                print(f"   ❌ Помилка: {e}")
                # TODO: скасувати попередні ордери
                return None

            print("\n✅ Всі 3 ордери успішно розміщено!")

            return {
                'orders': orders,
                'main_side': main_side_name,
                'hedge_side': hedge_side_name,
                'shares_main': shares_main,
                'shares_hedge1': shares_hedge1,
                'shares_hedge2': shares_hedge2,
                'price_main': price_main,
                'price_hedge': price_hedge
            }

        except Exception as e:
            print(f"❌ Помилка розміщення ордерів: {e}")
            import traceback
            traceback.print_exc()
            return None

    def wait_for_order_fills(self, orders_data: Dict[str, Any], market_title: str, timeout: int = 900) -> bool:
        """
        Очікує заповнення всіх ордерів

        Args:
            orders_data: Дані розміщених ордерів
            market_title: Назва маркету
            timeout: Таймаут в секундах (за замовчуванням 15 хв)

        Returns:
            bool: True якщо всі заповнено, False при таймауті
        """
        print("\n⏳ Очікування заповнення ордерів...")

        orders = orders_data['orders']
        main_order_id = orders['main'].get('orderId')
        hedge1_order_id = orders['hedge1'].get('orderId')
        hedge2_order_id = orders['hedge2'].get('orderId')

        filled = {'main': False, 'hedge1': False, 'hedge2': False}
        start_time = time.time()

        while (time.time() - start_time) < timeout:
            try:
                # Перевіряємо кожен ордер
                if not filled['main']:
                    positions_main = self.main_bot.get_my_positions()
                    # Якщо є позиції - ордер заповнено
                    if len(positions_main) > 0:
                        filled['main'] = True
                        print(f"   ✅ Основний ордер заповнено")

                if not filled['hedge1']:
                    positions_hedge1 = self.hedge1_bot.get_my_positions()
                    if len(positions_hedge1) > 0:
                        filled['hedge1'] = True
                        print(f"   ✅ Хедж ордер 1 заповнено")

                if not filled['hedge2']:
                    positions_hedge2 = self.hedge2_bot.get_my_positions()
                    if len(positions_hedge2) > 0:
                        filled['hedge2'] = True
                        print(f"   ✅ Хедж ордер 2 заповнено")

                # Якщо всі заповнено
                if all(filled.values()):
                    print("\n🎉 Всі ордери заповнено!")

                    # Telegram повідомлення
                    msg = f"✅ <b>Всі позиції відкрито!</b>\n\nРинок: {market_title}\n\nОсновний: {orders_data['main_side']} {orders_data['shares_main']} шейрів\nХедж 1: {orders_data['hedge_side']} {orders_data['shares_hedge1']} шейрів\nХедж 2: {orders_data['hedge_side']} {orders_data['shares_hedge2']} шейрів"
                    self.send_telegram_message(msg)

                    return True

                time.sleep(10)  # Перевіряємо кожні 10 секунд

            except Exception as e:
                print(f"⚠️  Помилка перевірки ордерів: {e}")
                time.sleep(10)

        print(f"\n⏰ Таймаут очікування заповнення ({timeout}с)")
        return False

    def wait_for_market_resolution(self, market_id: str, market_title: str, timeout: int = 1800) -> bool:
        """
        Очікує завершення маркету

        Args:
            market_id: ID маркету
            market_title: Назва маркету
            timeout: Таймаут (за замовчуванням 30 хв)

        Returns:
            bool: True якщо завершено, False при таймауті
        """
        print(f"\n⏳ Очікування завершення маркету...")
        print(f"   Перевірка кожні 30 секунд")

        start_time = time.time()

        while (time.time() - start_time) < timeout:
            try:
                # Отримуємо всі маркети і шукаємо наш
                markets, _ = self.main_bot.get_markets(limit=150)

                for market in markets:
                    if str(market.get('id')) == market_id:
                        status = market.get('status')
                        resolution = market.get('resolution')

                        if status == 'RESOLVED' and resolution is not None:
                            winner_name = resolution.get('name', 'N/A')
                            print(f"\n✅ Маркет завершено! Переможець: {winner_name}")

                            # Telegram повідомлення
                            msg = f"🏁 <b>Маркет завершено!</b>\n\nРинок: {market_title}\nРезультат: {winner_name}"
                            self.send_telegram_message(msg)

                            return True

                time.sleep(30)  # Перевіряємо кожні 30 секунд

            except Exception as e:
                print(f"⚠️  Помилка перевірки статусу: {e}")
                time.sleep(30)

        print(f"\n⏰ Таймаут очікування завершення ({timeout}с)")
        return False

    def claim_all_positions(self, market_id: str) -> Dict[str, bool]:
        """
        Claim позиції для всіх 3 акаунтів

        Args:
            market_id: ID маркету

        Returns:
            dict: Статус claim для кожного акаунта
        """
        print("\n💰 Claim позицій...")

        results = {'main': False, 'hedge1': False, 'hedge2': False}

        # Claim для основного акаунта
        print("   Акаунт 1 (Main)...")
        try:
            claimable_main = self.main_bot.get_claimable_positions()
            for position in claimable_main:
                if str(position.get('market', {}).get('id')) == market_id:
                    self.main_bot.redeem_position(position)
                    results['main'] = True
                    print("      ✅ Claimed")
        except Exception as e:
            print(f"      ⚠️  Помилка: {e}")

        # Claim для хедж 1
        print("   Акаунт 2 (Hedge1)...")
        try:
            claimable_hedge1 = self.hedge1_bot.get_claimable_positions()
            for position in claimable_hedge1:
                if str(position.get('market', {}).get('id')) == market_id:
                    self.hedge1_bot.redeem_position(position)
                    results['hedge1'] = True
                    print("      ✅ Claimed")
        except Exception as e:
            print(f"      ⚠️  Помилка: {e}")

        # Claim для хедж 2
        print("   Акаунт 3 (Hedge2)...")
        try:
            claimable_hedge2 = self.hedge2_bot.get_claimable_positions()
            for position in claimable_hedge2:
                if str(position.get('market', {}).get('id')) == market_id:
                    self.hedge2_bot.redeem_position(position)
                    results['hedge2'] = True
                    print("      ✅ Claimed")
        except Exception as e:
            print(f"      ⚠️  Помилка: {e}")

        return results

    def get_balances_summary(self) -> Dict[str, float]:
        """
        Отримує баланси всіх 3 акаунтів

        Returns:
            dict: Баланси кожного акаунта
        """
        balances = {
            'main': 0.0,
            'hedge1': 0.0,
            'hedge2': 0.0,
            'total': 0.0
        }

        try:
            balance_main = self.main_bot.get_balance()
            balances['main'] = balance_main.get('fundsAvailable', 0.0) + balance_main.get('portfolioValue', 0.0)
        except:
            pass

        try:
            balance_hedge1 = self.hedge1_bot.get_balance()
            balances['hedge1'] = balance_hedge1.get('fundsAvailable', 0.0) + balance_hedge1.get('portfolioValue', 0.0)
        except:
            pass

        try:
            balance_hedge2 = self.hedge2_bot.get_balance()
            balances['hedge2'] = balance_hedge2.get('fundsAvailable', 0.0) + balance_hedge2.get('portfolioValue', 0.0)
        except:
            pass

        balances['total'] = balances['main'] + balances['hedge1'] + balances['hedge2']

        return balances

    def run(self):
        """
        Головний цикл торгівлі
        """
        print("\n🚀 Запуск мультиакаунтного режиму торгівлі")
        print(f"💰 Budget: Main=${self.budgets[0]}, Hedge1=${self.budgets[1]}, Hedge2=${self.budgets[2]}")
        print(f"📊 Мінімальний спред: {self.min_spread * 100}¢")
        print(f"🔒 Safety margin: {self.safety_margin * 100}%")
        print("\n" + "="*60 + "\n")

        while True:
            try:
                self.round_number += 1
                print(f"\n{'='*60}")
                print(f"🔄 РАУНД #{self.round_number}")
                print(f"{'='*60}\n")

                # 1. Знайти 15-хв BTC/USD маркет
                # Debug для першого раунду щоб побачити які маркети є
                debug_mode = (self.round_number == 1)
                market = self.find_15min_btc_market(debug=debug_mode)
                if not market:
                    print("⏳ Чекаю 60 секунд перед наступною спробою...")
                    time.sleep(60)
                    continue

                market_id = str(market.get('id'))
                market_title = market.get('title')

                # 2. Моніторити спред
                orderbook_data = self.wait_for_spread(market_id)
                if not orderbook_data:
                    print("⏭️  Пропускаю раунд (таймаут спреду)")
                    continue

                # 3. Розмістити ордери
                orders_data = self.place_orders_strategy(market_id, market_title, orderbook_data)
                if not orders_data:
                    print("⏭️  Пропускаю раунд (помилка розміщення ордерів)")
                    continue

                # 4. Чекати заповнення
                filled = self.wait_for_order_fills(orders_data, market_title)
                if not filled:
                    print("⏭️  Пропускаю раунд (ордери не заповнено)")
                    # TODO: скасувати незаповнені ордери
                    continue

                # 5. Чекати завершення маркету
                resolved = self.wait_for_market_resolution(market_id, market_title)
                if not resolved:
                    print("⚠️  Маркет не завершився вчасно, але продовжуємо...")

                # 6. Claim позиції
                claim_results = self.claim_all_positions(market_id)
                print(f"\n📊 Claim результати: Main={claim_results['main']}, Hedge1={claim_results['hedge1']}, Hedge2={claim_results['hedge2']}")

                # 7. Отримати баланси та відправити статистику
                current_balances = self.get_balances_summary()

                print(f"\n💰 БАЛАНСИ ПІСЛЯ РАУНДУ:")
                print(f"   Акаунт 1: ${current_balances['main']:.2f}")
                print(f"   Акаунт 2: ${current_balances['hedge1']:.2f}")
                print(f"   Акаунт 3: ${current_balances['hedge2']:.2f}")
                print(f"   Загалом: ${current_balances['total']:.2f}")

                # Розрахунок витрат раунду
                if self.previous_total_balance is not None:
                    round_cost = self.previous_total_balance - current_balances['total']
                    self.stats['total_cost'] += round_cost

                    print(f"\n📉 Витрати раунду: ${round_cost:.2f}")
                    print(f"📊 Загальні витрати: ${self.stats['total_cost']:.2f}")

                    # Telegram повідомлення з підсумками
                    msg = f"""
📊 <b>Раунд #{self.round_number} завершено</b>

💰 Баланси:
• Акаунт 1: ${current_balances['main']:.2f}
• Акаунт 2: ${current_balances['hedge1']:.2f}
• Акаунт 3: ${current_balances['hedge2']:.2f}

💎 Загалом: ${current_balances['total']:.2f}

📉 Витрати раунду: ${round_cost:.2f}
📊 Загальні витрати: ${self.stats['total_cost']:.2f}
"""
                    self.send_telegram_message(msg)

                else:
                    # Перший раунд - зберігаємо початковий баланс
                    msg = f"""
📊 <b>Раунд #{self.round_number} завершено</b>

💰 Баланси:
• Акаунт 1: ${current_balances['main']:.2f}
• Акаунт 2: ${current_balances['hedge1']:.2f}
• Акаунт 3: ${current_balances['hedge2']:.2f}

💎 Загалом: ${current_balances['total']:.2f}
"""
                    self.send_telegram_message(msg)

                self.previous_total_balance = current_balances['total']
                self.stats['total_rounds'] += 1

                print(f"\n✅ Раунд #{self.round_number} завершено успішно!")
                print("⏭️  Переходжу до наступного маркету...")
                time.sleep(10)  # Невелика пауза перед наступним раундом

            except KeyboardInterrupt:
                print("\n\n⏸️  Зупинка мультиакаунтного режиму...")
                break
            except Exception as e:
                print(f"\n❌ Помилка в раунді #{self.round_number}: {e}")
                print("⏳ Чекаю 60 секунд перед наступною спробою...")
                time.sleep(60)
