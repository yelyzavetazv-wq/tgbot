import os
import uuid
import zipfile
import asyncio
import tempfile
import contextlib
import re
from html import escape
from aiogram import Bot, Dispatcher, F, types  # ВОТ ЗДЕСЬ ОШИБКА БЫЛА - types должен быть
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, LinkPreviewOptions
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
GROUP_USERNAME = os.getenv("GROUP_USERNAME")
MAX_FILE_SIZE = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = {'.epub', '.fb2', '.txt', '.pdf', '.doc', '.docx', '.mobi'}

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class BookForm(StatesGroup):
    waiting = State()

def clean_filename(name: str) -> str:
    base, ext = os.path.splitext(name)
    return re.sub(r'\s+', '_', base) + ext.lower()

@contextlib.asynccontextmanager
async def managed_file(path: str):
    try: yield path
    finally:
        if os.path.exists(path): os.remove(path)

def parse_metadata(epub_path: str) -> dict:
    data = {"title_ru": "Без названия", "title_en": "", "title_orig": "", "author": "Неизвестен", 
            "chapters": "?", "tags": [], "desc": "Описание отсутствует", "links": []}
    try:
        with zipfile.ZipFile(epub_path, 'r') as z:
            for name in z.namelist():
                if name.endswith('.opf'):
                    with z.open(name) as f:
                        s = BeautifulSoup(f.read(), 'xml')
                        t = s.find('dc:title')
                        if t and t.text:
                            parts = [p.strip() for p in t.text.split('/')]
                            data["title_ru"] = parts[0]
                            if len(parts) > 1: data["title_en"] = parts[1]
                            if len(parts) > 2: data["title_orig"] = parts[2]
                        c = s.find('dc:creator')
                        if c: data["author"] = c.text.strip()
                        data["tags"] = [f"#{tag.text.strip().replace(' ', '_')}" for tag in s.find_all('dc:subject')]
                        d = s.find('dc:description')
                        if d: data["desc"] = d.get_text(strip=True)[:2000]
                        pub = s.find('dc:publisher')
                        if pub and pub.text:
                            data["links"] = [l for l in pub.text.split() if l.startswith('http')]
                    break
            for name in z.namelist():
                if name.endswith('.ncx'):
                    with z.open(name) as f:
                        s = BeautifulSoup(f.read(), 'xml')
                        data["chapters"] = len(s.find_all('navPoint'))
    except: pass
    return data

@dp.message(F.document)
async def handle_docs(message: types.Message):
    if os.path.splitext(message.document.file_name)[1].lower() not in ALLOWED_EXTENSIONS:
        return await message.answer("❌ Формат не поддерживается.")
    
    status_msg = await message.answer("⏳ Загрузка файла...")
    tmp_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}{os.path.splitext(message.document.file_name)[1]}")
    await bot.download(message.document, destination=tmp_path)
    
    await status_msg.edit_text("⏳ Парсинг метаданных...")
    meta = await asyncio.to_thread(parse_metadata, tmp_path)
    
    await status_msg.edit_text("🚀 Публикация на канал...")
    
    # Сообщение 1: Обложка (если есть)
    cover_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}.jpg")
    try:
        with zipfile.ZipFile(tmp_path) as z:
            with z.open("cover.jpeg") as f:
                with open(cover_path, "wb") as out: out.write(f.read())
        await bot.send_photo(GROUP_USERNAME, FSInputFile(cover_path))
    except: pass 
    
    # Сообщение 2: Информация
    text = [f"🏴‍☠️ {escape(meta['title_ru'])}"]
    if meta['title_en']: text.append(f"🇬🇧 {escape(meta['title_en'])}")
    if meta['title_orig']: text.append(f"🌐 {escape(meta['title_orig'])}")
    text.append(f"\n✍️ Автор: {escape(meta['author'])}")
    text.append(f"\n📊 Глав: {meta['chapters']}")
    text.append(f"\n📌 Статус: в процессе")
    if meta['tags']: text.append(f"\n🏷 Теги: {' '.join(meta['tags'])}")
    text.append(f"\n\n📖 Описание:\n{escape(meta['desc'])}")
    for link in meta['links']: text.append(f"\n🔗 {link}")
    
    await bot.send_message(GROUP_USERNAME, "\n".join(text), parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
    
    # Сообщение 3: Файл
    await bot.send_document(
        GROUP_USERNAME, 
        FSInputFile(tmp_path, filename=clean_filename(message.document.file_name)),
        caption="🤖 Глоссарий: Gemini 3 Flash\n🤖 Перевод: Gemini 3.5 Flash"
    )
    
    await status_msg.edit_text("✅ Готово!")
    if os.path.exists(tmp_path): os.remove(tmp_path)

async def on_startup(bot: Bot):
    await bot.set_webhook(f"{os.getenv('WEBHOOK_URL')}/webhook")

# --- ЗАПУСК ---

async def set_my_webhook():
    webhook_url = f"{os.getenv('WEBHOOK_URL')}/webhook"
    await bot.set_webhook(webhook_url)
    print(f"Вебхук успешно установлен на: {webhook_url}")

if __name__ == "__main__":
    # Создаем приложение
    app = web.Application()
    
    # Регистрируем хендлер для webhook
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path="/webhook")
    
    # Настраиваем приложение
    setup_application(app, dp, bot=bot)
    
    # ВАЖНО: Мы сначала запускаем установку вебхука, а потом сервер
    # Используем loop.run_until_complete для установки вебхука перед запуском сервера
    loop = asyncio.get_event_loop()
    loop.run_until_complete(set_my_webhook())
    
    # Запуск сервера
    port = int(os.environ.get("PORT", 8080))
    web.run_app(app, host="0.0.0.0", port=port)
