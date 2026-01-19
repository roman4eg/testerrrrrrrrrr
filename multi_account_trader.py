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
from concurrent.futures import ThreadPoolExecutor, as_completed


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
        # Зберігаємо всі 3 боти і бюджети для ротації
        self.all_bots = [main_bot, hedge1_bot, hedge2_bot]
        self.all_budgets = budgets

        # Ініціалізуємо поточні ролі (будуть змінюватись кожен раунд)
        self.main_bot = main_bot
        self.hedge1_bot = hedge1_bot
        self.hedge2_bot = hedge2_bot
        self.budgets = budgets

        self.telegram = telegram_notifier
        self.min_spread = min_spread / 100.0  # Конвертуємо центи в decimal
        self.safety_margin = safety_margin

        # Кешування поточного маркету (щоб не шукати кожен раз)
        self.current_market_id = None
        self.current_market_data = None

        # Статистика для відстеження
        self.round_number = 0
        self.previous_total_balance = None
        self.stats = {
            'total_rounds': 0,
            'total_profit': 0.0,
            'total_cost': 0.0
        }

    def rotate_accounts(self):
        """
        Випадково вибирає який акаунт буде main, а які hedge
        Викликається на початку кожного раунду
        """
        # Випадково перемішуємо акаунти
        indices = [0, 1, 2]
        random.shuffle(indices)

        # Призначаємо ролі
        self.main_bot = self.all_bots[indices[0]]
        self.hedge1_bot = self.all_bots[indices[1]]
        self.hedge2_bot = self.all_bots[indices[2]]

        # Відповідно перемішуємо бюджети
        self.budgets = [
            self.all_budgets[indices[0]],
            self.all_budgets[indices[1]],
            self.all_budgets[indices[2]]
        ]

        print(f"🔄 Ротація акаунтів: Main=Акаунт#{indices[0]+1}, Hedge1=Акаунт#{indices[1]+1}, Hedge2=Акаунт#{indices[2]+1}")

    def find_15min_btc_market(self, debug: bool = False) -> Optional[Dict[str, Any]]:
        """
        Знаходить активний 15-хвилинний BTC/USD маркет
        Використовує кешований маркет якщо він вже знайдений

        Args:
            debug: Якщо True, показує всі BTC/USD маркети

        Returns:
            dict: Дані маркету або None якщо не знайдено
        """
        # Якщо вже є кешований маркет - використовуємо його
        if self.current_market_id and self.current_market_data:
            print(f"\n✅ Використовую кешований маркет: {self.current_market_data.get('title')}")
            print(f"   ID: {self.current_market_id}")
            return self.current_market_data

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
                            # Зберігаємо в кеш
                            self.current_market_id = market.get('id')
                            self.current_market_data = market
                            return market

                if not cursor:
                    break

            # Якщо є хоч якийсь BTC/USD маркет - беремо перший
            if btc_markets_found:
                market = btc_markets_found[0]
                print(f"⚠️  15-хв маркет не знайдено, використовую перший BTC/USD:")
                print(f"   {market.get('title')} (ID: {market.get('id')})")
                # Зберігаємо в кеш
                self.current_market_id = market.get('id')
                self.current_market_data = market
                return market

            print("❌ Не знайдено активних BTC/USD маркетів")
            return None

        except Exception as e:
            print(f"❌ Помилка пошуку маркету: {e}")
            import traceback
            traceback.print_exc()
            return None

    def get_orderbook_spread(self, market_data: Dict[str, Any], debug: bool = False) -> Optional[Tuple[float, float, str, str]]:
        """
        Отримує спред з orderbook для маркету

        Args:
            market_data: Дані маркету (з get_markets)
            debug: Показувати детальний вивід

        Returns:
            tuple: (spread_up, spread_down, up_token_id, down_token_id) або None
        """
        try:
            market_id = str(market_data.get('id'))
            outcomes_list = market_data.get('outcomes', [])

            # Отримуємо повний orderbook маркету (без token_id)
            orderbook = self.main_bot.get_orderbook(market_id)

            # Якщо orderbook має outcomes - використовуємо їх
            if 'outcomes' in orderbook and orderbook['outcomes']:
                outcomes_with_orderbook = orderbook['outcomes']
            else:
                # Якщо немає outcomes в orderbook - використовуємо дані з маркету
                # але orderbook буде в asks/bids напряму
                # Створюємо структуру з доступних даних
                outcomes_with_orderbook = []
                for outcome_info in outcomes_list:
                    outcomes_with_orderbook.append({
                        'name': outcome_info.get('name'),
                        'tokenId': outcome_info.get('onChainId'),  # Використовуємо onChainId як tokenId
                        'asks': orderbook.get('asks', []),
                        'bids': orderbook.get('bids', [])
                    })

            # Знаходимо UP та DOWN в orderbook
            up_outcome = None
            down_outcome = None

            for outcome in outcomes_with_orderbook:
                name = outcome.get('name', '').lower()
                if 'up' in name:
                    up_outcome = outcome
                elif 'down' in name:
                    down_outcome = outcome

            if not up_outcome or not down_outcome:
                return None

            # Отримуємо best ask та best bid для кожного
            up_asks = up_outcome.get('asks', [])
            up_bids = up_outcome.get('bids', [])
            down_asks = down_outcome.get('asks', [])
            down_bids = down_outcome.get('bids', [])

            if not (up_asks and up_bids and down_asks and down_bids):
                return None

            # Best ask = найнижча ціна продажу
            # Best bid = найвища ціна покупки
            # Формат: [[price, size], ...] або [{'price': ..., 'size': ...}, ...]
            if isinstance(up_asks[0], (list, tuple)):
                up_best_ask = float(up_asks[0][0])
                up_best_bid = float(up_bids[0][0])
                down_best_ask = float(down_asks[0][0])
                down_best_bid = float(down_bids[0][0])
            else:
                up_best_ask = float(up_asks[0]['price'])
                up_best_bid = float(up_bids[0]['price'])
                down_best_ask = float(down_asks[0]['price'])
                down_best_bid = float(down_bids[0]['price'])

            # Спред = різниця між ask і bid
            spread_up = up_best_ask - up_best_bid
            spread_down = down_best_ask - down_best_bid

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
        # КРИТИЧНО: рахуємо shares від HEDGE budget (дорогої сторони), а не від main
        # Бо майже завжди обмежувач - це сторона ~0.8-0.95
        # Використовуємо Decimal щоб уникнути float округлень типу 19.999 → 20.01
        from decimal import Decimal, ROUND_FLOOR

        def floor_shares(budget_usd: float, price_usd: float) -> int:
            """Обчислює максимальні shares з округленням вниз (без float похибок)"""
            return int((Decimal(str(budget_usd)) / Decimal(str(price_usd))).to_integral_value(rounding=ROUND_FLOOR))

        # 1. Обчислюємо максимум shares для КОЖНОГО hedge акаунта окремо
        budget_h1_safe = budget_hedge1 * (1 - self.safety_margin)
        budget_h2_safe = budget_hedge2 * (1 - self.safety_margin)

        max_shares_h1 = floor_shares(budget_h1_safe, price_hedge)
        max_shares_h2 = floor_shares(budget_h2_safe, price_hedge)

        # 2. Беремо мінімум (щоб обидва hedge влізли)
        max_shares_per_hedge = min(max_shares_h1, max_shares_h2)

        # 3. Обчислюємо total main shares (треба покрити обидва hedge)
        # Main купує X shares, кожен hedge купує X/2 shares
        main_max_by_hedge = 2 * max_shares_per_hedge  # обмеження від hedge
        budget_main_safe = budget_main * (1 - self.safety_margin)
        main_max_by_budget = floor_shares(budget_main_safe, price_main)  # обмеження від main budget

        total_shares = min(main_max_by_hedge, main_max_by_budget)

        if total_shares <= 0:
            print("❌ Недостатньо budget для торгівлі")
            return None

        # 4. Розподіляємо shares між hedge акаунтами (випадкове співвідношення 35-65%)
        # Випадкове співвідношення від 35% до 65% для першого хеджа
        hedge1_ratio = random.uniform(0.35, 0.65)
        shares_hedge1 = round(total_shares * hedge1_ratio)  # round() замість int() для точнішого розподілу
        shares_hedge2 = total_shares - shares_hedge1

        print(f"   📊 Випадковий розподіл hedge: {shares_hedge1}/{shares_hedge2} ({shares_hedge1/total_shares*100:.1f}%/{shares_hedge2/total_shares*100:.1f}%)")

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

        # Data-neutral перевірка: shares збалансовані (payout однаковий при будь-якому результаті)
        total_cost = cost_main + cost_hedge1 + cost_hedge2
        total_shares_hedge = shares_hedge1 + shares_hedge2

        # Margin per share (якщо ліміти заповняться)
        margin_per_share = 1.0 - (price_main + price_hedge)
        expected_margin = total_shares * margin_per_share

        print(f"   💡 Data-neutral перевірка:")
        print(f"      Shares balance: main={total_shares}, hedge={total_shares_hedge} (збалансовано: {total_shares == total_shares_hedge})")
        print(f"      Total cost: ${total_cost:.2f}")
        print(f"      Payout (незалежно від результату): ${total_shares * 1.0:.2f}")
        print(f"      Price margin: {margin_per_share:.4f} per share")
        print(f"      Expected margin IF filled: ${expected_margin:.2f}")
        print(f"      ⚠️  Це НЕ гарантовано (залежить від виконання лімітних ордерів)")

        return (total_shares, shares_hedge1, shares_hedge2)

    def wait_for_spread(self, market_data: Dict[str, Any], timeout: int = 600) -> Optional[Dict[str, Any]]:
        """
        Очікує достатній спред для входу

        Args:
            market_data: Дані маркету
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
                spread_data = self.get_orderbook_spread(market_data, debug=first_attempt)
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

            print(f"📖 Отримання orderbook...")

            # Predict API повертає ОДИН orderbook для YES outcome
            # DOWN (NO) розраховується через complement: NO = 1 - YES з swap сторін
            yes_orderbook = self.main_bot.get_orderbook(market_id, token_id=str(up_token_id))

            yes_asks = yes_orderbook.get('asks', [])
            yes_bids = yes_orderbook.get('bids', [])

            if not (yes_asks and yes_bids):
                print("❌ Недостатньо даних в YES orderbook")
                return None

            # UP = YES (без змін)
            up_outcome = {
                'tokenId': up_token_id,
                'name': 'UP',
                'asks': yes_asks,
                'bids': yes_bids
            }

            # DOWN = complement (NO = 1 - YES) з swap сторін
            # YES bids → DOWN asks (1 - price)
            # YES asks → DOWN bids (1 - price)
            down_asks_raw = []
            down_bids_raw = []

            # Обробляємо YES bids → DOWN asks
            for bid in yes_bids:
                if isinstance(bid, (list, tuple)):
                    price_yes = float(bid[0])
                    size = bid[1]
                else:
                    price_yes = float(bid['price'])
                    size = bid['size']

                price_no = 1.0 - price_yes
                down_asks_raw.append([price_no, size])

            # Обробляємо YES asks → DOWN bids
            for ask in yes_asks:
                if isinstance(ask, (list, tuple)):
                    price_yes = float(ask[0])
                    size = ask[1]
                else:
                    price_yes = float(ask['price'])
                    size = ask['size']

                price_no = 1.0 - price_yes
                down_bids_raw.append([price_no, size])

            # Сортуємо: asks ascending (найкращий ask = найменша ціна)
            #           bids descending (найкращий bid = найбільша ціна)
            down_asks_raw.sort(key=lambda x: x[0])
            down_bids_raw.sort(key=lambda x: x[0], reverse=True)

            down_outcome = {
                'tokenId': down_token_id,
                'name': 'DOWN',
                'asks': down_asks_raw,
                'bids': down_bids_raw
            }

            # Отримуємо bid/ask для обох сторін
            up_asks = up_outcome.get('asks', [])
            up_bids = up_outcome.get('bids', [])
            down_asks = down_outcome.get('asks', [])
            down_bids = down_outcome.get('bids', [])

            if not (up_asks and up_bids and down_asks and down_bids):
                print("❌ Недостатньо даних в orderbook (немає bid/ask для обох сторін)")
                return None

            # Парсимо ціни (формат може бути [[price, size], ...] або [{'price': ..., 'size': ...}, ...])
            if isinstance(up_asks[0], (list, tuple)):
                best_ask_up = float(up_asks[0][0])
                best_bid_up = float(up_bids[0][0])
                best_ask_down = float(down_asks[0][0])
                best_bid_down = float(down_bids[0][0])
            else:
                best_ask_up = float(up_asks[0]['price'])
                best_bid_up = float(up_bids[0]['price'])
                best_ask_down = float(down_asks[0]['price'])
                best_bid_down = float(down_bids[0]['price'])

            # Debug: показуємо top-of-book
            print(f"\n📖 Top-of-book:")
            print(f"   UP book:   bid=${best_bid_up:.4f}, ask=${best_ask_up:.4f}, spread={best_ask_up - best_bid_up:.4f}")
            print(f"   DOWN book: bid=${best_bid_down:.4f}, ask=${best_ask_down:.4f}, spread={best_ask_down - best_bid_down:.4f}")

            # Перевірка коректності complement розрахунків: mid_up + mid_down ≈ 1.0
            mid_up = (best_bid_up + best_ask_up) / 2.0
            mid_down = (best_bid_down + best_ask_down) / 2.0
            mid_sum = mid_up + mid_down
            print(f"   Перевірка: mid_up={mid_up:.4f} + mid_down={mid_down:.4f} = {mid_sum:.4f} (має бути ≈1.0)")

            # ФІЛЬТР A: Sanity check на complement logic
            # Якщо DOWN правильно порахований через complement, то:
            # best_bid_down ≈ 1 - best_ask_up
            # best_ask_down ≈ 1 - best_bid_up
            expected_bid_down = 1.0 - best_ask_up
            expected_ask_down = 1.0 - best_bid_up
            bid_diff = abs(best_bid_down - expected_bid_down)
            ask_diff = abs(best_ask_down - expected_ask_down)

            if bid_diff > 0.001 or ask_diff > 0.001:
                print(f"\n⚠️  WARNING: Complement розрахунок може бути некоректний")
                print(f"   Expected DOWN: bid={expected_bid_down:.4f}, ask={expected_ask_down:.4f}")
                print(f"   Actual DOWN:   bid={best_bid_down:.4f}, ask={best_ask_down:.4f}")
                print(f"   Diff: bid={bid_diff:.4f}, ask={ask_diff:.4f}")

            # ФІЛЬТР B: Sanity check - перевірка що є валідні bids/asks
            # Примітка: ask_sum може бути > 1 при широкому спреді (ask_sum = 1 + spread)
            # тому перевіряємо просто наявність та розумність цін
            print(f"\n🔍 Sanity checks:")

            if best_bid_up <= 0 or best_ask_up >= 1 or best_bid_down <= 0 or best_ask_down >= 1:
                print(f"❌ Ціни поза валідним діапазоном (0, 1), skip")
                return None

            if best_bid_up >= best_ask_up or best_bid_down >= best_ask_down:
                print(f"❌ Bid >= Ask (інвертований стакан), skip")
                return None

            # ФІЛЬТР B2: Перевірка ask_sum (баланс ринку)
            # Якщо ask_sum далеко від 1, то fair price буде перекошений
            # і навіть order = ask - 2¢ дасть позитивний gift
            ask_sum = best_ask_up + best_ask_down

            if not (0.98 <= ask_sum <= 1.15):
                print(f"❌ ask_sum далеко від 1.0 (ринок перекошений), skip")
                print(f"   На таких ринках ask-based fair дає погану оцінку")
                return None

            # ФІЛЬТР C: Max spread (не тільки min!)
            # На дуже тонких ринках (spread > 15¢) краще не торгувати
            spread_up = best_ask_up - best_bid_up
            spread_down = best_ask_down - best_bid_down
            max_spread = 0.15  # 15 центів

            print(f"   UP spread: {spread_up * 100:.1f}¢ (max: {max_spread * 100:.0f}¢)")
            print(f"   DOWN spread: {spread_down * 100:.1f}¢ (max: {max_spread * 100:.0f}¢)")

            if spread_up > max_spread or spread_down > max_spread:
                print(f"❌ Spread занадто великий (мертвий ринок), skip")
                return None

            # Розрахунок fair price від asks (не від mid!)
            p_ask_up = best_ask_up / (best_ask_up + best_ask_down)
            p_ask_down = 1.0 - p_ask_up

            # Обчислити ордерні ціни (динамічно)
            max_allowed_gift = 0.010  # 1.0 цент максимум

            p_up_order = max(0.01, min(best_ask_up - 0.02, p_ask_up + max_allowed_gift))
            p_down_order = max(0.01, min(best_ask_down - 0.02, p_ask_down + max_allowed_gift))

            # Квантизація ціни до тіку 0.01 (2 знаки після коми)
            import math
            price_tick = 0.01
            p_up_order = max(0.01, math.floor(p_up_order / price_tick) * price_tick)
            p_down_order = max(0.01, math.floor(p_down_order / price_tick) * price_tick)
            # КРИТИЧНО: округлюємо до 2 знаків, щоб уникнути float помилок типу 0.5600000000001
            p_up_order = round(p_up_order, 2)
            p_down_order = round(p_down_order, 2)

            # Обчислити gift
            gift_up = p_up_order - p_ask_up
            gift_down = p_down_order - p_ask_down

            print(f"\n📊 Аналіз сторін:")
            print(f"   UP:   order=${p_up_order:.2f}, gift={gift_up:+.4f}")
            print(f"   DOWN: order=${p_down_order:.2f}, gift={gift_down:+.4f}")

            # Вибір сторони на основі gift
            max_gift = max_allowed_gift + 0.001  # 1.1¢ з маржою на округлення
            valid_sides = []

            if gift_up <= max_gift:
                valid_sides.append(('UP', gift_up, up_outcome, down_outcome))

            if gift_down <= max_gift:
                valid_sides.append(('DOWN', gift_down, down_outcome, up_outcome))

            if not valid_sides:
                print(f"❌ Gift занадто великий, skip")
                return None

            # Вибираємо з мінімальним gift
            if len(valid_sides) == 2:
                valid_sides.sort(key=lambda x: x[1])

            # Вибираємо сторону
            side_choice, chosen_gift, main_outcome, hedge_outcome = valid_sides[0]
            main_side_name = side_choice
            hedge_side_name = "DOWN" if side_choice == "UP" else "UP"

            print(f"\n✅ Вибрано сторону: {side_choice} (gift={chosen_gift:+.4f})")

            # Отримуємо ціни
            if side_choice == 'UP':
                price_main = p_up_order
                best_ask_main = best_ask_up
            else:
                price_main = p_down_order
                best_ask_main = best_ask_down

            print(f"   {main_side_name}: best ask = ${best_ask_main:.2f}, ціна ордера = ${price_main:.2f}")

            # Ціна для хедж = 1 - price_main (data neutral)
            # КРИТИЧНО: округлюємо до 2 знаків, щоб уникнути float помилок типу 0.43999999999995
            price_hedge = round(1.0 - price_main, 2)
            price_hedge = max(0.01, min(0.99, price_hedge))  # Обмежуємо діапазон
            print(f"   {hedge_side_name}: ціна = ${price_hedge:.2f}")
            print(f"   ✓ Перевірка: ${price_main:.2f} + ${price_hedge:.2f} = ${price_main + price_hedge:.2f}")

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

            # Паралельне розміщення всіх 3 ордерів одночасно
            print(f"\n🚀 Розміщую всі 3 ордери одночасно...")
            print(f"   1️⃣ Основний: {main_side_name} @ ${price_main:.2f} × {shares_main} шейрів")
            print(f"   2️⃣ Хедж 1: {hedge_side_name} @ ${price_hedge:.2f} × {shares_hedge1} шейрів")
            print(f"   3️⃣ Хедж 2: {hedge_side_name} @ ${price_hedge:.2f} × {shares_hedge2} шейрів")

            def place_main_order():
                """Розмістити основний ордер"""
                return ('main', self.main_bot.create_order(
                    market_id=market_id,
                    token_id=main_token_id,
                    side="BUY",
                    price=price_main,
                    amount=shares_main
                ))

            def place_hedge1_order():
                """Розмістити хедж ордер 1"""
                return ('hedge1', self.hedge1_bot.create_order(
                    market_id=market_id,
                    token_id=hedge_token_id,
                    side="BUY",
                    price=price_hedge,
                    amount=shares_hedge1
                ))

            def place_hedge2_order():
                """Розмістити хедж ордер 2"""
                return ('hedge2', self.hedge2_bot.create_order(
                    market_id=market_id,
                    token_id=hedge_token_id,
                    side="BUY",
                    price=price_hedge,
                    amount=shares_hedge2
                ))

            try:
                # Запускаємо всі 3 ордери одночасно
                with ThreadPoolExecutor(max_workers=3) as executor:
                    futures = {
                        executor.submit(place_main_order): 'main',
                        executor.submit(place_hedge1_order): 'hedge1',
                        executor.submit(place_hedge2_order): 'hedge2'
                    }

                    # Чекаємо результати всіх ордерів
                    for future in as_completed(futures):
                        order_name = futures[future]
                        try:
                            order_type, result = future.result()
                            orders[order_type] = result
                            order_id = result.get('orderId', 'N/A')

                            if order_type == 'main':
                                print(f"   ✅ Основний ордер #{order_id} розміщено")
                                msg = f"🎯 <b>Основний ордер розміщено</b>\n\nРинок: {market_title}\nСторона: {main_side_name}\nЦіна: ${price_main:.2f}\nКількість: {shares_main} шейрів"
                                self.send_telegram_message(msg)
                            elif order_type == 'hedge1':
                                print(f"   ✅ Хедж ордер 1 #{order_id} розміщено")
                                msg = f"🛡 <b>Хедж ордер 1 розміщено</b>\n\nРинок: {market_title}\nСторона: {hedge_side_name}\nЦіна: ${price_hedge:.2f}\nКількість: {shares_hedge1} шейрів"
                                self.send_telegram_message(msg)
                            elif order_type == 'hedge2':
                                print(f"   ✅ Хедж ордер 2 #{order_id} розміщено")
                                msg = f"🛡 <b>Хедж ордер 2 розміщено</b>\n\nРинок: {market_title}\nСторона: {hedge_side_name}\nЦіна: ${price_hedge:.2f}\nКількість: {shares_hedge2} шейрів"
                                self.send_telegram_message(msg)

                        except Exception as e:
                            print(f"   ❌ Помилка розміщення {order_name}: {e}")
                            return None

                # Перевіряємо, що всі 3 ордери успішно розміщено
                if not all(orders.values()):
                    print("\n❌ Не вдалося розмістити всі ордери")
                    return None

                print("\n✅ Всі 3 ордери успішно розміщено одночасно!")

            except Exception as e:
                print(f"   ❌ Критична помилка паралельного розміщення: {e}")
                return None

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
        check_count = 0

        while (time.time() - start_time) < timeout:
            try:
                check_count += 1
                # Отримуємо конкретний маркет за ID
                market = self.main_bot.get_market_by_id(market_id)

                if not market:
                    print(f"\n⚠️  Не вдалося отримати маркет {market_id}")
                    time.sleep(30)
                    continue

                status = market.get('status')
                resolution = market.get('resolution')

                elapsed = int(time.time() - start_time)
                print(f"   Перевірка #{check_count} ({elapsed}с): статус={status}", end='\r')

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

                # Випадкова ротація акаунтів (хто буде main)
                self.rotate_accounts()
                print()

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
                orderbook_data = self.wait_for_spread(market)
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

                # Очищаємо кеш маркету (наступний раунд шукатиме новий)
                self.current_market_id = None
                self.current_market_data = None
                print("🔄 Кеш маркету очищено, наступний раунд шукатиме новий маркет")

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
