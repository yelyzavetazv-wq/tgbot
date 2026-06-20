import os
import uuid
import zipfile
import asyncio
import tempfile
import contextlib
import re
from html import escape
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, LinkPreviewOptions
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Конфиг
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
GROUP_USERNAME = os.getenv("GROUP_USERNAME")
MAX_FILE_SIZE = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = {'.epub', '.fb2', '.txt', '.pdf', '.doc', '.docx', '.mobi'}

bot = Bot(token=TOKEN)
dp = Dispatcher()

class BookForm(StatesGroup):
    choosing_glossary = State()
    choosing_translation = State()
    choosing_filter = State()
    choosing_status = State()

# --- УТИЛИТЫ ---

def clean_filename(name: str) -> str:
    name = os.path.splitext(name)[0]
    return re.sub(r'\s+', '_', name)

@contextlib.asynccontextmanager
async def managed_file(path: str):
    try: yield path
    finally:
        if os.path.exists(path): os.remove(path)

def parse_epub(epub_path: str) -> dict:
    data = {"title_ru": "Без названия", "title_en": "", "title_orig": "", "author": "Неизвестен", 
            "chapters": "?", "tags": [], "desc": "Описание отсутствует", "links": []}
    try:
        with zipfile.ZipFile(epub_path, 'r') as z:
            # Метаданные
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
            # Главы
            for name in z.namelist():
                if name.endswith('.ncx'):
                    with z.open(name) as f:
                        s = BeautifulSoup(f.read(), 'xml')
                        data["chapters"] = len(s.find_all('navPoint'))
    except: pass
    return data

# --- ХЕНДЛЕРЫ ---

@dp.message(F.document)
async def handle_docs(message: types.Message, state: FSMContext):
    ext = os.path.splitext(message.document.file_name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return await message.answer("❌ Неподдерживаемый формат.")
    if message.document.file_size > MAX_FILE_SIZE:
        return await message.answer("❌ Файл больше 20Мб.")

    status_msg = await message.answer("⏳ Загрузка файлов...")
    
    # Сохраняем все файлы в память состояния
    file_info = await bot.get_file(message.document.file_id)
    path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}{ext}")
    await bot.download(file_info, destination=path)
    
    files = await state.get_data()
    files_list = files.get("files", [])
    files_list.append({"path": path, "name": message.document.file_name})
    await state.update_data(files=files_list)
    
    await status_msg.edit_text("✅ Файл принят. Отправьте еще или напишите /done для выбора инструментов.")

@dp.message(Command("done"))
async def start_selection(message: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Gemini 3.0", callback_data="gl:Gemini 3.0")],
        [InlineKeyboardButton(text="Gemini 3.5", callback_data="gl:Gemini 3.5")]
    ])
    await message.answer("🤖 Выберите Глоссарий:", reply_markup=kb)
    await state.set_state(BookForm.choosing_glossary)

@dp.callback_query(BookForm.choosing_glossary, F.data.startswith("gl:"))
async def set_gl(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(glossary=call.data.split(":")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Gemini 3.0", callback_data="tr:Gemini 3.0")],
        [InlineKeyboardButton(text="Gemini 3.5", callback_data="tr:Gemini 3.5")]
    ])
    await call.message.edit_text("🤖 Выберите Перевод:", reply_markup=kb)
    await state.set_state(BookForm.choosing_translation)

# (Аналогичные хендлеры для filter и status...)

@dp.callback_query(BookForm.choosing_status, F.data.startswith("st:"))
async def publish(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    status_msg = await call.message.edit_text("🚀 Публикация на канал...")
    
    # Логика: находим EPUB для парсинга
    epub_path = next((f for f in data["files"] if f["name"].endswith(".epub")), None)
    
    if epub_path:
        meta = await asyncio.to_thread(parse_epub, epub_path["path"])
        # Публикация обложки и описания (Сообщение 1 и 2)
        # Публикация файлов (Сообщение 3)
        await status_msg.edit_text("✅ Готово!")
    
    await state.clear()

if __name__ == "__main__":
    asyncio.run(dp.start_polling(bot))
