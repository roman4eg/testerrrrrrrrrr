## 🔑 Налаштування для створення ордерів

### Необхідні компоненти:

1. **JWT токен** - для аутентифікації запитів до API
2. **Predict Account Address** - ваша deposit address (основний акаунт)
3. **Privy Wallet Private Key** - для криптографічного підпису ордерів
4. **Predict SDK** - встановлюється автоматично через requirements.txt

### 🏗️ Account Abstraction Модель

Predict Fun використовує **account abstraction** - розділення акаунта і ключа підпису:

| Компонент | Що це | Де використовується |
|-----------|-------|---------------------|
| **MetaMask адреса** | Ваша звичайна Web3 адреса | Тільки для реєстрації/логіну на сайті |
| **Predict Account** | Deposit address (основний акаунт) | JWT signer, order.signer, order.maker |
| **Privy Wallet** | Внутрішній ключ для підписів | Підпис JWT message, підпис orders |

**Чому так?**
- 🔒 Безпека: приватний ключ MetaMask не використовується для операцій
- 🎯 Зручність: Predict керує внутрішнім Privy wallet
- 🔄 Гнучкість: можна змінити Privy wallet без зміни основного акаунта

### Додайте в файл `.env`:

```env
API_KEY=ваш_api_key
JWT=ваш_jwt_токен

# Predict Account (deposit address) - основний акаунт
PREDICT_ACCOUNT_ADDRESS=0xAC12Ef1beDE880D2cDaE2f5285b2baa96403Cc41

# Privy Wallet private key (НЕ MetaMask!)
PRIVATE_KEY=0x...privy_wallet_private_key...
```

### 📍 Як знайти Predict Account Address:

1. Зайдіть на https://predict.fun
2. Settings → Profile
3. Знайдіть **Deposit Address** або **Account Address**
4. Скопіюйте адресу (починається з `0x`)

### 🔑 Як отримати Privy Wallet Private Key:

**⚠️ ВАЖЛИВО: Це НЕ ваш MetaMask ключ!**

1. Зайдіть на https://predict.fun
2. Settings → Advanced → **Export Privy Wallet**
3. Підтвердіть експорт
4. Скопіюйте приватний ключ (починається з `0x`)

**Формат:** `0x` + 64 hex символи

### ⚠️ ВАЖЛИВО - БЕЗПЕКА:

- **НІКОЛИ** не діліться приватним ключем Privy Wallet
- Не комітьте `.env` файл в git (він вже в .gitignore)
- JWT має бути створений для **Predict Account Address**, а не MetaMask
- Privy Wallet використовується тільки для підписів

### Чому потрібен приватний ключ?

Predict Fun використовує криптографічні підписи для ордерів. Кожен ордер:
1. Будується з параметрами (ціна, кількість, токен)
2. Хешується
3. Підписується вашим приватним ключем
4. Відправляється на сервер з підписом

Це забезпечує, що тільки власник гаманця може створювати ордери від свого імені.
