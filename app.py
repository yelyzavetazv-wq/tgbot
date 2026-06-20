import os, uuid, zipfile, re, asyncio, tempfile, logging
from html import escape
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, LinkPreviewOptions
from aiogram.client.default import DefaultBotProperties

# Конфиг (используй свои переменные окружения)
TOKEN = os.getenv("BOT_TOKEN")
GROUP_USERNAME = os.getenv("GROUP_USERNAME")
ALLOWED_EXTENSIONS = {'.epub', '.fb2', '.txt', '.doc', '.docx'}
MAX_FILE_SIZE = 20 * 1024 * 1024

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

class UploadState(StatesGroup):
    waiting = State()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def is_safe(file_name):
    ext = os.path.splitext(file_name)[1].lower()
    return ext in ALLOWED_EXTENSIONS

# --- ЛОГИКА ОЖИДАНИЯ ---
async def start_timer(chat_id, state: FSMContext, message: types.Message):
    for i in range(6): # 30 секунд (6 * 5)
        await asyncio.sleep(5)
        bar = "|" * (i + 1) + "." * (5 - i)
        try:
            await message.edit_text(f"⏳ Ожидаю файлы... [{bar}]")
        except: break
    await publish_everything(chat_id, state, message)

async def publish_everything(chat_id, state: FSMContext, msg_status: types.Message):
    data = await state.get_data()
    files = data.get("files", [])
    if not files: return await msg_status.edit_text("❌ Нет файлов для публикации.")

    # 1. Поиск первого EPUB для парсинга
    epub_files = [f for f in files if f['name'].endswith('.epub')]
    main_epub = epub_files[0] if epub_files else None
    
    # Парсинг (аналогично твоей логике)
    # ... (здесь будет вызов функций extract_metadata и extract_cover для main_epub)

    # 2. Публикация в канал (те самые 3 поста)
    # ... (логика bot.send_photo, bot.send_message, bot.send_document)

    # 3. Публикация остальных файлов
    for f in files:
        if main_epub and f['path'] == main_epub['path']: continue
        await bot.send_document(GROUP_USERNAME, document=FSInputFile(f['path'], filename=f['name']))

    await msg_status.edit_text("✅ Опубликовано!")
    await state.clear()

# --- ХЕНДЛЕРЫ ---
@dp.message(F.document)
async def handle_docs(message: types.Message, state: FSMContext):
    if message.chat.type != "private": return
    if not is_safe(message.document.file_name) or message.document.file_size > MAX_FILE_SIZE:
        return await message.answer("❌ Файл запрещен или слишком большой.")

    data = await state.get_data()
    files = data.get("files", [])
    
    path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}_{message.document.file_name}")
    await bot.download(message.document, destination=path)
    files.append({"path": path, "name": message.document.file_name})
    
    if not data.get("timer_started"):
        status_msg = await message.answer("⏳ Ожидаю файлы... [.....]")
        await state.update_data(files=files, timer_started=True)
        asyncio.create_task(start_timer(message.chat.id, state, status_msg))
    else:
        await state.update_data(files=files)

if __name__ == "__main__":
    # ---- РЕЖИМ СЕРВЕРА (RENDER) ----
    # Убираем все попытки запустить start_polling!
    
    port_num = int(os.environ.get("PORT", 8080))
    app = web.Application()
    
    # Регистрация вебхука
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path="/webhook")
    
    # Важно: эта функция сама настроит бот при старте
    setup_application(app, dp, bot=bot)
    
    # Запуск
    web.run_app(app, host="0.0.0.0", port=port_num)
