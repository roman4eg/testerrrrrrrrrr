#!/usr/bin/env python3
"""
Multi-Account Trader для Predict Fun
Автоматична торгівля на 15-хвилинних BTC/USD маркетах з 3 акаунтами
"""

import time
import random
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

    def find_15min_btc_market(self) -> Optional[Dict[str, Any]]:
        """
        Знаходить активний 15-хвилинний BTC/USD маркет

        Returns:
            dict: Дані маркету або None якщо не знайдено
        """
        print("\n🔍 Пошук 15-хвилинного BTC/USD маркету...")

        # Пошук по ключовим словам
        search_query = "btc/usd 15"

        try:
            # Використовуємо пагінацію для пошуку
            max_pages = 5
            for page in range(1, max_pages + 1):
                markets, next_cursor = self.main_bot.get_markets(limit=150)

                for market in markets:
                    title = market.get('title', '').lower()
                    question = market.get('question', '').lower()
                    status = market.get('status')

                    # Перевіряємо що це BTC/USD і 15-хвилинний маркет
                    if (status == 'REGISTERED' and
                        'btc/usd' in title and
                        '15' in title and
                        ('minute' in title or 'min' in title)):

                        print(f"✅ Знайдено маркет: {market.get('title')}")
                        print(f"   ID: {market.get('id')}")
                        print(f"   Status: {status}")
                        return market

                if not next_cursor:
                    break

            print("❌ Не знайдено активних 15-хв BTC/USD маркетів")
            return None

        except Exception as e:
            print(f"❌ Помилка пошуку маркету: {e}")
            return None

    def get_orderbook_spread(self, market_id: str) -> Optional[Tuple[float, float, str, str]]:
        """
        Отримує спред з orderbook для маркету

        Args:
            market_id: ID маркету

        Returns:
            tuple: (spread_up, spread_down, up_token_id, down_token_id) або None
        """
        try:
            orderbook = self.main_bot.get_orderbook(market_id)

            # Знаходимо outcomes для UP та DOWN
            up_outcome = None
            down_outcome = None

            for outcome in orderbook.get('outcomes', []):
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
                market = self.find_15min_btc_market()
                if not market:
                    print("⏳ Чекаю 60 секунд перед наступною спробою...")
                    time.sleep(60)
                    continue

                market_id = str(market.get('id'))
                market_title = market.get('title')

                # TODO: Реалізувати решту логіки
                # 2. Моніторити спред
                # 3. Розмістити ордери
                # 4. Чекати заповнення
                # 5. Чекати завершення маркету
                # 6. Claim позиції
                # 7. Відправити статистику

                print("\n⚠️  Логіка торгівлі ще не реалізована (TODO)")
                print("⏳ Чекаю 60 секунд...")
                time.sleep(60)

            except KeyboardInterrupt:
                print("\n\n⏸️  Зупинка мультиакаунтного режиму...")
                break
            except Exception as e:
                print(f"\n❌ Помилка в раунді #{self.round_number}: {e}")
                print("⏳ Чекаю 60 секунд перед наступною спробою...")
                time.sleep(60)
