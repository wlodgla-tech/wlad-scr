"""
telegram_notifier.py
=====================
Надсилає текстові сповіщення в Telegram через Bot API.

Налаштування (змінні середовища):
    TELEGRAM_BOT_TOKEN — токен бота від @BotFather
    TELEGRAM_CHAT_ID   — ID чату/каналу, куди слати повідомлення

Якщо токен/chat_id не задані — функція не падає з помилкою, а друкує
повідомлення в консоль (корисно під час локальної розробки).
"""

import os
import requests

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

API_URL_TEMPLATE = "https://api.telegram.org/bot{token}/sendMessage"


def send_alert(message: str) -> bool:
    """Надсилає `message` в Telegram. Повертає True при успіху, False інакше.

    Обробляє мережеві помилки (немає інтернету, таймаут, Telegram API
    недоступний) — ніколи не кидає виняток назовні, лише логує проблему.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[telegram_notifier] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID не задані. "
              "Повідомлення виведено в консоль замість відправки:")
        print(message)
        return False

    url = API_URL_TEMPLATE.format(token=TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(url, data=payload, timeout=15)
        response.raise_for_status()
        return True
    except requests.exceptions.Timeout:
        print("[telegram_notifier] Помилка: запит до Telegram перевищив час очікування.")
    except requests.exceptions.ConnectionError:
        print("[telegram_notifier] Помилка: немає з'єднання з мережею / Telegram недоступний.")
    except requests.exceptions.HTTPError as e:
        print(f"[telegram_notifier] Telegram API повернув помилку: {e}")
    except requests.exceptions.RequestException as e:
        print(f"[telegram_notifier] Неочікувана мережева помилка: {e}")
    return False


if __name__ == "__main__":
    # швидкий тест: python telegram_notifier.py
    ok = send_alert("✅ Тестове повідомлення від telegram_notifier.py")
    print("Успішно відправлено" if ok else "Не вдалося відправити (див. повідомлення вище)")
