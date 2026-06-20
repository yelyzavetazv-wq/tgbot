import os, uuid, zipfile, re, asyncio, tempfile, logging, contextlib
from html import escape
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, LinkPreviewOptions

# Конфигурация
load_dotenv()
logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("BOT_TOKEN")
GROUP_USERNAME = os.getenv("GROUP_USERNAME")
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
ALLOWED_EXTENSIONS = {'.epub'}

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

class BookForm(StatesGroup):
    choosing_tools = State()

# --- СЕРВИС ОБРАБОТКИ ---
class EpubProcessor:
    @staticmethod
    def validate_path(member_name):
        # Защита от Zip Slip
        if ".." in member_name or member_name.startswith("/"):
            return False
        return True

    @staticmethod
    def get_metadata_and_cover(epub_path):
        meta = {"titles": [], "author": "?", "tags": [], "links": [], "desc": "Описание отсутствует"}
        cover_path = None
        
        with zipfile.ZipFile(epub_path, 'r') as z:
            # Валидация архива
            for name in z.namelist():
                if not EpubProcessor.validate_path(name):
                    raise ValueError("Подозрительный архив")

            # Парсинг метаданных
            opf = next((n for n in z.namelist() if n.endswith('.opf')), None)
            if opf:
                with z.open(opf) as f:
                    soup = BeautifulSoup(f.read(), 'xml')
                    t = soup.find('dc:title')
                    if t and t.text: meta["titles"] = [p.strip() for p in t.text.split('/') if p.strip()]
                    c = soup.find('dc:creator')
                    if c and c.text: meta["author"] = c.text.strip()
                    meta["tags"] = [f"#{re.sub(r'[^a-zA-Zа-яА-Я0-9]', '', tag.text.strip())}" for tag in soup.find_all('dc:subject')]
            
            # Извлечение обложки
            for name in z.namelist():
                if name.lower().endswith(('.jpg', '.jpeg', '.png')):
                    cover_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}.jpg")
                    with open(cover_path, "wb") as f: f.write(z.read(name))
                    break
        return meta, cover_path

    @staticmethod
    def count_chapters(epub_path):
        with zipfile.ZipFile(epub_path, 'r') as z:
            ncx = next((n for n in z.namelist() if n.endswith('.ncx')), None)
            if ncx:
                with z.open(ncx) as f:
                    return len(BeautifulSoup(f.read(), 'xml').find_all('navPoint'))
        return "?"

# --- ХЕНДЛЕРЫ ---
@dp.message(F.document)
async def handle_docs(message: types.Message, state: FSMContext):
    if message.document.file_size > MAX_FILE_SIZE:
        return await message.answer("❌ Файл слишком большой.")
    
    ext = os.path.splitext(message.document.file_name or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return await message.answer("❌ Только .epub")

    tmp_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}.epub")
    await bot.download(message.document, destination=tmp_path)
    
    try:
        meta, cover = await asyncio.to_thread(EpubProcessor.get_metadata_and_cover, tmp_path)
        await state.update_data(path=tmp_path, name=message.document.file_name, meta=meta, cover=cover, 
                                gl="Gemini 3", tr="Gemini 3.5", fl="Нет")
        await message.answer("✅ Файл принят. Настройте инструменты:", reply_markup=get_tools_kb("Gemini 3", "Gemini 3.5", "Нет"))
        await state.set_state(BookForm.choosing_tools)
    except Exception as e:
        await message.answer("Ошибка обработки файла.")
        if os.path.exists(tmp_path): os.remove(tmp_path)

@dp.callback_query(BookForm.choosing_tools)
async def callbacks(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    # Логика переключения инструментов... (оставлена как в вашем коде)
    # ... при завершении используйте блок finally для удаления файлов:
    if call.data == "pub_done":
        # ... (логика публикации)
        for p in [data.get('path'), data.get('cover')]:
            if p and os.path.exists(p): os.remove(p)
        await call.message.edit_text("✅ Опубликовано!")
        await state.clear()
