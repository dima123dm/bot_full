import asyncio
import logging
import os
import requests
import json
from telegram import Bot as PTBBot
from telegram.error import RetryAfter, NetworkError, TelegramError
from transliterate import translit
import re
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import time
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums.parse_mode import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from config_reader import config
from bot.handlers import setup_routers
from db import Base, _engine

# Создаем директории
os.makedirs("logs", exist_ok=True)
os.makedirs("temp", exist_ok=True)

# Настройка логирования (для Bot 2, но можно использовать общее)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(funcName)s) %(message)s",
    handlers=[
        logging.FileHandler("logs/logs.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

# Конфиг для Bot 1
FH_TOKEN = "45366da4b0dee2b0aa5cd82f18a2b7ed1b432154"
TG_TOKEN_BOT1 = "7898155636:AAFpfP3IlbzjD0nKdZMg1W1oE9F7HWCcNJg"
CHAT_ID = -4842178495
CHECK_INTERVAL = 30  # 5 минут
SENT_FILE = "sent_projects.json"

# Инициализация Bot 1 (используем PTBBot, чтобы избежать конфликта имен с aiogram.Bot)
bot1 = PTBBot(token=TG_TOKEN_BOT1)


def load_sent_projects():
    """Загружает ID отправленных проектов."""
    if os.path.exists(SENT_FILE):
        with open(SENT_FILE, 'r') as f:
            return set(json.load(f))
    return set()


def save_sent_projects(sent_ids):
    """Сохраняет ID в файл."""
    with open(SENT_FILE, 'w') as f:
        json.dump(list(sent_ids), f)


# Функция для проверки интернета
def check_internet():
    try:
        requests.get('https://google.com', timeout=5)
        return True
    except:
        return False


# Функция для запроса к API с retry при интернет-ошибках
@retry(retry=retry_if_exception_type(
    (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.RequestException)),
       wait=wait_exponential(multiplier=1, min=4, max=30),  # Задержка: 4с, 8с, 16с... до 30с
       stop=stop_after_attempt(10),  # Макс 10 попыток
       reraise=True)
def get_new_projects():
    """Получает новые проекты с Freelancehunt API."""
    url = "https://api.freelancehunt.com/v2/projects?sort_field=created&limit=20"
    headers = {"Authorization": f"Bearer {FH_TOKEN}"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"Ошибка API: {response.status_code} - {response.text}")
            return []
        return response.json().get('data', [])
    except Exception as e:
        print(f"Ошибка запроса: {e}")
        return []


def slugify(text):
    """Преобразует текст в URL-safe формат."""
    # Транслитерация кириллицы в латиницу (используем 'ru' для русского)
    text = translit(text, 'ru', reversed=True)
    # Замена пробелов и специальных символов на дефисы
    text = re.sub(r'[^a-zA-Z0-9]+', '-', text.lower())
    # Удаление лишних дефисов
    text = re.sub(r'-+', '-', text).strip('-')
    return text


# Функция для отправки сообщения с retry при flood и сетевых ошибках
async def send_notification(project):
    """Отправляет уведомление в Telegram."""
    attrs = project.get('attributes', {})
    title = attrs.get('name', 'Без названия')
    budget = attrs.get('budget', None)
    budget_amount = budget.get('amount', 'Не указан') if budget is not None else 'Не указан'
    currency = budget.get('currency', 'UAH') if budget is not None else 'UAH'
    desc = attrs.get('description', '')[:200] + ("..." if len(attrs.get('description', '')) > 200 else "")
    # Формируем правильную ссылку
    slug = slugify(title)
    link = f"https://freelancehunt.com/project/{slug}/{project['id']}.html"

    message = f"🆕 Новый проект!\n\n**{title}**\n💰 Бюджет: {budget_amount} {currency}\n📝 Описание: {desc}\n🔗 Ссылка: {link}"

    attempts = 0
    max_attempts = 10
    while attempts < max_attempts:
        try:
            await bot1.send_message(chat_id=CHAT_ID, text=message, parse_mode='Markdown')
            print(f"Отправлено: {title}")
            return True  # Успех
        except RetryAfter as e:
            wait_time = e.retry_after + 1  # Ждём указанное время +1с
            print(f"Flood control: жду {wait_time} секунд...")
            await asyncio.sleep(wait_time)
        except NetworkError as e:
            print(f"Сетевая ошибка: {e}. Жду 10 секунд и retry...")
            await asyncio.sleep(10)
        except TelegramError as e:
            print(f"Telegram ошибка: {e}. Жду 5 секунд и retry...")
            await asyncio.sleep(5)
        attempts += 1
    print(f"Не удалось отправить {title} после {max_attempts} попыток.")
    return False


async def bot1_main():
    sent_ids = load_sent_projects()
    print("Бот 1 запущен. Мониторинг новых проектов...")

    while True:
        # Проверка интернета перед запросом
        if not check_internet():
            print("Нет интернета. Жду 10 секунд и повторяю...")
            await asyncio.sleep(10)
            continue

        projects = get_new_projects()
        new_projects = [p for p in projects if p['id'] not in sent_ids]

        for project in new_projects:
            success = await send_notification(project)
            if success:
                sent_ids.add(project['id'])
            # Если не отправлено, попробуем снова в следующем цикле

        if new_projects:
            save_sent_projects(sent_ids)
            print(f"Найдено {len(new_projects)} новых проектов.")

        print("Новых проектов нет или они отправлены. Жду следующего цикла...")
        await asyncio.sleep(CHECK_INTERVAL)


# Инициализация Bot 2
bot2 = Bot(
    token=config.BOT_TOKEN.get_secret_value(),
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=MemoryStorage())

scheduler = AsyncIOScheduler()
scheduler.configure(timezone="Europe/Moscow")


async def start_polling() -> None:
    scheduler.start()
    dp.include_router(setup_routers())
    await bot2.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot2, scheduler=scheduler)


@dp.startup()
async def on_startup() -> None:
    async with _engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)


@dp.shutdown()
async def on_shutdown() -> None:
    await _engine.dispose()
    scheduler.shutdown(wait=False)


async def main_combined():
    # Запускаем Bot 1 как задачу
    bot1_task = asyncio.create_task(bot1_main())

    # Запускаем Bot 2 (polling блокирует, так что он пойдет после)
    await start_polling()

    # Ждем завершения задач (хотя polling не завершится)
    await bot1_task


if __name__ == "__main__":
    asyncio.run(main_combined())