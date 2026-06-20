import os
import uuid
import zipfile
import asyncio
import tempfile
import logging
from html import escape
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import FSInputFile, LinkPreviewOptions
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from bs4 import BeautifulSoup
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
GROUP_USERNAME = os.getenv("GROUP_USERNAME")
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- БЛОК ПАРСИНГА И ПУБЛИКАЦИИ ---

def clean_filename(name: str) -> str:
    base, ext = os.path.splitext(name)
    return re.sub(r'\s+', '_', base) + ext.lower()

async def publish_book(message: types.Message, path: str):
    # 1. Парсинг (здесь ваша логика из предыдущих сообщений)
    # ... (вставьте сюда функцию parse_metadata, которую мы обсуждали)
    
    # 2. Публикация в канал
    await message.answer("🚀 Публикую в канал...")
    
    # Отправка фото (обложки)
    try:
        await bot.send_photo(GROUP_USERNAME, FSInputFile(path)) # Упрощено для примера
    except Exception as e:
        logging.error(f"Ошибка отправки фото: {e}")

    # Отправка сообщения с описанием
    await bot.send_message(
        GROUP_USERNAME, 
        f"📖 <b>Новая книга готова!</b>\n\nЗдесь будет ваше описание...", 
        parse_mode="HTML"
    )

    # Отправка файла
    await bot.send_document(
        GROUP_USERNAME, 
        FSInputFile(path, filename=message.document.file_name.replace(" ", "_")),
        caption="🤖 Глоссарий: Gemini 3\n🤖 Перевод: Gemini 3.5"
    )

# --- ОСНОВНЫЕ ОБРАБОТЧИКИ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет! Присылай файл EPUB.")

@dp.message(F.document)
async def handle_docs(message: types.Message):
    tmp_path = os.path.join(tempfile.gettempdir(), message.document.file_name)
    await bot.download(message.document, destination=tmp_path)
    
    # Вызов блока публикации
    await publish_book(message, tmp_path)
    await message.answer("✅ Готово!")
    
    if os.path.exists(tmp_path): os.remove(tmp_path)

# --- ЗАПУСК ---

async def on_startup(bot: Bot):
    await bot.set_webhook(f"{os.getenv('WEBHOOK_URL')}/webhook")

if __name__ == "__main__":
    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path="/webhook")
    dp.startup.register(on_startup)
    setup_application(app, dp, bot=bot)
    web.run_app(app, port=int(os.environ.get("PORT", 8080)))
