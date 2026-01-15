#!/usr/bin/env python3
"""
Predict Fun Bot - MVP Version
Отримує список активних ордерів через Predict Fun API
"""

import os
import sys
import json
import requests
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

    def get_active_orders(self) -> dict:
        """
        Отримує список активних ордерів

        Returns:
            dict: Відповідь API з активними ордерами
        """
        url = f"{self.base_url}/orders"

        try:
            response = requests.get(url, headers=self.headers, timeout=30)
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

    def display_orders(self, orders_data: dict):
        """
        Виводить інформацію про ордери в зручному форматі

        Args:
            orders_data: Дані про ордери з API
        """
        print("\n" + "="*60)
        print("📊 АКТИВНІ ОРДЕРИ")
        print("="*60 + "\n")

        # Перевіряємо різні можливі структури відповіді
        orders = []

        if isinstance(orders_data, dict):
            if 'orders' in orders_data:
                orders = orders_data['orders']
            elif 'data' in orders_data:
                orders = orders_data['data']
            else:
                # Якщо структура невідома, виводимо сирий JSON
                print(json.dumps(orders_data, indent=2, ensure_ascii=False))
                return
        elif isinstance(orders_data, list):
            orders = orders_data

        if not orders:
            print("📭 Активних ордерів не знайдено")
            print("\n" + "="*60)
            return

        for i, order in enumerate(orders, 1):
            print(f"Ордер #{i}")
            print("-" * 40)

            # Виводимо основну інформацію про ордер
            for key, value in order.items():
                if isinstance(value, dict):
                    print(f"  {key}:")
                    for sub_key, sub_value in value.items():
                        print(f"    {sub_key}: {sub_value}")
                else:
                    print(f"  {key}: {value}")

            print()

        print(f"Всього активних ордерів: {len(orders)}")
        print("="*60 + "\n")


def main():
    """Головна функція"""
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
    print(f"🔗 API: https://api.predict.fun/v1")

    bot = PredictFunBot(api_key)

    # Отримуємо та відображаємо активні ордери
    print("📡 Отримання активних ордерів...")
    orders = bot.get_active_orders()
    bot.display_orders(orders)

    print("✅ Готово!")


if __name__ == "__main__":
    main()
