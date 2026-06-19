import os
import uuid
import zipfile
import re
import random
import asyncio
import tempfile
from ebooklib import epub, ITEM_COVER, ITEM_IMAGE
from bs4 import BeautifulSoup
from html import escape
from dotenv import load_dotenv
from aiohttp import web, ClientTimeout
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, LinkPreviewOptions
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType

load_dotenv()

# ================= CONFIG =================

TOKEN = os.getenv("BOT_TOKEN")
GROUP_USERNAME = os.getenv("GROUP_USERNAME")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Например: https://my-bot.onrender.com
MAX_FILE_SIZE = 20 * 1024 * 1024
TEMP_DIR = tempfile.gettempdir() # Кроссплатформенная временная папка

if not TOKEN:
    raise ValueError("ОШИБКА: BOT_TOKEN не найден в переменных окружения или файле .env")

# ================= MIDDLEWARES (RETRIES) =================

class RetryRequestMiddleware(BaseRequestMiddleware):
    """Мидлварь для автоматического переподключения при сбоях сети (Пункт 3)"""
    def __init__(self, retries: int = 3, backoff: float = 1.0):
        self.retries = retries
        self.backoff = backoff

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType,
        bot: "Bot",
        method: TelegramMethod[TelegramType],
    ):
        for attempt in range(self.retries):
            try:
                return await make_request(bot, method)
            except Exception as e:
                if attempt == self.retries - 1:
                    raise e
                await asyncio.sleep(self.backoff * (attempt + 1))

session = AiohttpSession(timeout=180.0) 
session.middleware.register(RetryRequestMiddleware(retries=3, backoff=1.0)) # Подключаем ретраи

bot = Bot(token=TOKEN, session=session, default=DefaultBotProperties(parse_mode="HTML"))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

class BookForm(StatesGroup):
    waiting_for_glossary = State()
    waiting_for_translation = State()
    waiting_for_filter = State()

# ================= UTILS =================

def secure_filename(filename: str) -> str:
    """Безопасное имя файла. Сохраняет кириллицу."""
    return re.sub(r'[^\w\-.]', '_', filename)

def clean_hashtag(tag: str) -> str:
    cleaned = re.sub(r'[^\w]+', '_', tag).strip('_')
    return cleaned

# ================= EPUB PARSERS (СИНХРОННЫЕ ФУНКЦИИ) =================

def extract_cover(epub_path):
    try:
        book = epub.read_epub(epub_path)
        for item in book.get_items():
            if item.get_type() in (ITEM_COVER, ITEM_IMAGE):
                path = os.path.join(TEMP_DIR, f"{uuid.uuid4().hex}.jpg")
                with open(path, "wb") as f:
                    f.write(item.get_content())
                return path
    except Exception:
        pass
    return None

def extract_annotation(epub_path):
    try:
        with zipfile.ZipFile(epub_path, 'r') as epub_zip:
            for name in epub_zip.namelist():
                if name.endswith('.opf'):
                    with epub_zip.open(name) as f:
                        content = f.read().decode('utf-8', errors='ignore')
                        soup = BeautifulSoup(content, 'xml')
                        description = soup.find('dc:description')
                        if description and description.text:
                            inner_soup = BeautifulSoup(description.text, 'html.parser')
                            paragraphs = inner_soup.find_all('p')
                            if paragraphs:
                                texts = [p.get_text().strip() for p in paragraphs if p.get_text().strip()]
                                return '\n'.join(texts)[:2000]
                            return description.text.strip()[:2000]
        return "Описание отсутствует"
    except Exception as e:
        print(f"Ошибка парсинга аннотации: {e}")
        return "Описание отсутствует"

def count_chapters(epub_path):
    try:
        with zipfile.ZipFile(epub_path, 'r') as z:
            for name in z.namelist():
                if name.endswith('.ncx') or name.endswith('nav.xhtml') or name.endswith('toc.xhtml'):
                    with z.open(name) as f:
                        content = f.read().decode('utf-8', errors='ignore')
                        soup = BeautifulSoup(content, 'xml')
                        nav_points = soup.find_all('navPoint')
                        if nav_points:
                            last_nav = nav_points[-1]
                            nav_label = last_nav.find('navLabel')
                            if nav_label:
                                text_tag = nav_label.find('text')
                                if text_tag and text_tag.text:
                                    match = re.search(r'(\d+)', text_tag.text)
                                    if match:
                                        return int(match.group(1))
                    break
        return "?"
    except Exception as e:
        print(f"Ошибка подсчёта глав: {e}")
        return "?"

def extract_tags_from_opf(epub_path):
    tags = []
    try:
        with zipfile.ZipFile(epub_path, 'r') as z:
            for name in z.namelist():
                if name.endswith('.opf'):
                    with z.open(name) as f:
                        content = f.read().decode('utf-8', errors='ignore')
                        soup = BeautifulSoup(content, 'xml')
                        for subject in soup.find_all('dc:subject'):
                            if subject.text:
                                tags.append(subject.text.strip())
                    break
    except Exception:
        pass
    return tags

def parse_info(text, epub_path=None):
    info = {"title_ru": "", "title_en": "", "title_original": "", "author": "", "links": [], "tags": []}
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("http"):
            info["links"].append(line)
        elif "название_ru" in line.lower():
            info["title_ru"] = line.split(":", 1)[1].strip()
        elif "название_en" in line.lower():
            info["title_en"] = line.split(":", 1)[1].strip()
        elif "название_original" in line.lower():
            info["title_original"] = line.split(":", 1)[1].strip()
        elif "автор" in line.lower():
            info["author"] = line.split(":", 1)[1].strip()

    if epub_path:
        info["tags"] = extract_tags_from_opf(epub_path)
    return info

# ================= KEYBOARDS =================

def glossary_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Gemini 3.5 Flash", callback_data="glossary:Gemini 3.5 Flash")],
        [InlineKeyboardButton(text="Gemini 3.1 Flash Lite", callback_data="glossary:Gemini 3.1 Flash Lite")],
        [InlineKeyboardButton(text="Gemini 3 Flash", callback_data="glossary:Gemini 3 Flash")],
        [InlineKeyboardButton(text="Gemini 2.5 Flash", callback_data="glossary:Gemini 2.5 Flash")],
        [InlineKeyboardButton(text="✏️ Другое", callback_data="glossary:other")]
    ])

def translation_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Gemini 3.5 Flash", callback_data="translation:Gemini 3.5 Flash")],
        [InlineKeyboardButton(text="Gemini 3.1 Flash Lite", callback_data="translation:Gemini 3.1 Flash Lite")],
        [InlineKeyboardButton(text="Gemini 3 Flash", callback_data="translation:Gemini 3 Flash")],
        [InlineKeyboardButton(text="Gemini 2.5 Flash", callback_data="translation:Gemini 2.5 Flash")],
        [InlineKeyboardButton(text="✏️ Другое", callback_data="translation:other")]
    ])

def filter_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ChatGPT Web", callback_data="filter:ChatGPT Web")],
        [InlineKeyboardButton(text="DeepSeekWeb", callback_data="filter:DeepSeekWeb")],
        [InlineKeyboardButton(text="❌ Нет (не показывать)", callback_data="filter:none")],
        [InlineKeyboardButton(text="✏️ Другое", callback_data="filter:other")]
    ])

def status_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="в процессе", callback_data="status:в процессе")],
        [InlineKeyboardButton(text="завершен", callback_data="status:завершен")],
        [InlineKeyboardButton(text="брошен", callback_data="status:брошен")]
    ])

# ================= FORMAT =================

def format_text(info, chapters, status, annotation):

    title_ru = escape(info.get('title_ru') or info.get('title_en') or info.get('title_original') or 'Без названия')
    title_en = escape(info.get('title_en', ''))
    title_original = escape(info.get('title_original', ''))
    author = escape(info.get('author', ''))
    safe_status = escape(status)
    safe_chapters = escape(str(chapters))

    # Заголовки
    text = f"🏴‍☠ {title_ru}\n"
    if title_en:
        text += f"🇬🇧 {title_en}\n"
    if title_original:
        text += f"🌐 {title_original}\n"

    # Блок с автором, главами и статусом
    text += "\n"
    text += f"✍ Автор: {author}\n"
    text += "\n"
    text += f"📊 Глав: {safe_chapters}\n"
    text += "\n"
    text += f"📌 Статус: {safe_status}\n"

    # Теги (только если есть непустые)
    if info.get('tags'):
        clean_tags = [
            f"#{escape(clean_hashtag(tag))}"
            for tag in info['tags']
            if clean_hashtag(tag)
        ]
        if clean_tags:
            text += f"🏷️ Теги: {', '.join(clean_tags)}\n"

    # Описание
    text += "\n"
    text += "📖 Описание:\n"
    text += f"<blockquote expandable>{escape(annotation)}</blockquote>\n"

    # Ссылки
    for link in info.get('links', []):
        text += f"\n🔗 {escape(link)}"

    return text

def format_files(glossary, translation, filter_choice):
    text = f"🤖 Глоссарий: {escape(glossary)}\n🤖 Перевод: {escape(translation)}"
    if filter_choice and filter_choice != "none":
        text += f"\n🧹 Фильтр: {escape(filter_choice)}"
    return text

# ============================================
# ПРИЁМ ФАЙЛОВ (ТОЛЬКО ИЗ ЛИЧКИ)
# ============================================

# ================= HANDLERS =================

@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("📚 Отправьте файлы книги: .epub, .fb2, .doc/.docx и файл описания .txt")

@dp.message(F.document)
async def handle_docs(message: types.Message, state: FSMContext):
    if message.chat.type != "private":
        # Если сообщение не из лички — просто игнорируем
        return

@dp.message(F.document)
async def handle_docs(message: types.Message, state: FSMContext):
    if message.document.file_size > MAX_FILE_SIZE:
        await message.answer("❌ Файл слишком большой. Максимальный размер для скачивания ботом: 20 МБ.")
        return

    file_info = await bot.get_file(message.document.file_id)
    name = secure_filename(message.document.file_name)
    path = os.path.join(TEMP_DIR, f"{uuid.uuid4().hex}_{name}")

    await bot.download(file_info, destination=path)

    current_data = await state.get_data()
    epub_path = current_data.get("epub")
    cover_path = current_data.get("cover")
    fb2_path = current_data.get("fb2")
    doc_path = current_data.get("doc")
    txt_content = current_data.get("txt", "")
    keyboard_sent = current_data.get("keyboard_sent", False)

    if name.lower().endswith(".epub"):

        if epub_path and os.path.exists(epub_path):
            try: os.remove(epub_path)
            except Exception: pass
        if cover_path and os.path.exists(cover_path):
            try: os.remove(cover_path)
            except Exception: pass

        epub_path = path
        cover_path = await asyncio.to_thread(extract_cover, path)
        await message.answer(f"✅ Получен EPUB: {name}")

    elif name.lower().endswith(".fb2"):

        if fb2_path and os.path.exists(fb2_path):
            try: os.remove(fb2_path)
            except Exception: pass

        fb2_path = path
        await message.answer(f"✅ Получен FB2: {name}")

    elif name.lower().endswith(".doc") or name.lower().endswith(".docx"):
        
        if doc_path and os.path.exists(doc_path):
            try: os.remove(doc_path)
            except Exception: pass

        doc_path = path
        await message.answer(f"✅ Получен DOC/DOCX: {name}")

    elif name.lower().endswith(".txt"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                txt_content = f.read()
        except UnicodeDecodeError:
            try:
                with open(path, "r", encoding="cp1251") as f:
                    txt_content = f.read()
            except Exception:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    txt_content = f.read()
        
        await message.answer(f"✅ Получен description.txt: {name}")
        if os.path.exists(path):
            os.remove(path)

    await state.update_data(
        epub=epub_path,
        cover=cover_path,
        fb2=fb2_path,
        doc=doc_path,
        txt=txt_content
    )

    # Пункт 4: Кнопки появляются, если есть ХОТЯ БЫ один файл книги + TXT файл
    if (epub_path or fb2_path or doc_path) and txt_content and not keyboard_sent:
        await state.update_data(keyboard_sent=True)
        await message.answer("📚 Все необходимые файлы получены! Выберите Глоссарий:", reply_markup=glossary_keyboard())
    elif txt_content and not (epub_path or fb2_path or doc_path):
        await message.answer("💡 Файл описания принят. Теперь отправьте файл книги (.epub, .fb2, .doc, .docx)")

# ================= CALLBACKS & FSM =================

@dp.callback_query()
async def callbacks(call: types.CallbackQuery, state: FSMContext):
    data_parts = call.data.split(":", 1)
    cat = data_parts[0]
    val = data_parts[1] if len(data_parts) > 1 else ""

    await call.answer()

    try:
        await call.message.delete()
    except Exception as e:
        print(f"Ошибка удаления сообщения: {e}")

    if cat == "glossary":
        if val == "other":
            await call.message.answer("✏️ Введите название модели для Глоссария:")
            await state.set_state(BookForm.waiting_for_glossary)
        else:
            await state.update_data(glossary=val)
            await call.message.answer("🤖 Выберите модель для Перевода:", reply_markup=translation_keyboard())

    elif cat == "translation":
        if val == "other":
            await call.message.answer("✏️ Введите название модели для Перевода:")
            await state.set_state(BookForm.waiting_for_translation)
        else:
            await state.update_data(translation=val)
            await call.message.answer("🧹 Выберите Фильтр:", reply_markup=filter_keyboard())

    elif cat == "filter":
        if val == "other":
            await call.message.answer("✏️ Введите название Фильтра:")
            await state.set_state(BookForm.waiting_for_filter)
        else:
            await state.update_data(filter=val)
            await call.message.answer("📌 Выберите Статус:", reply_markup=status_keyboard())

    elif cat == "status":
        await state.update_data(status=val)
        await publish_to_forum(call.message.chat.id, state)

@dp.message(BookForm.waiting_for_glossary)
async def set_glossary(message: types.Message, state: FSMContext):
    await state.update_data(glossary=message.text)
    await state.set_state(None) 
    await message.answer("🤖 Выберите модель для Перевода:", reply_markup=translation_keyboard())

@dp.message(BookForm.waiting_for_translation)
async def set_translation(message: types.Message, state: FSMContext):
    await state.update_data(translation=message.text)
    await state.set_state(None)
    await message.answer("🧹 Выберите Фильтр:", reply_markup=filter_keyboard())

@dp.message(BookForm.waiting_for_filter)
async def set_filter(message: types.Message, state: FSMContext):
    await state.update_data(filter=message.text)
    await state.set_state(None)
    await message.answer("📌 Выберите Статус:", reply_markup=status_keyboard())

# ================= CLEANUP & PUBLISH =================

def cleanup_files(data: dict):
    for key in ["epub", "fb2", "doc", "cover"]:
        file_path = data.get(key)
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                print(f"Ошибка удаления {file_path}: {e}")

async def publish_to_forum(chat_id: int, state: FSMContext):
    data = await state.get_data()

    try:
        epub_path = data.get("epub")
        fb2_path = data.get("fb2")
        doc_path = data.get("doc")
        txt = data.get("txt", "")
        cover = data.get("cover")

        # Пункт 4: Мягкая проверка — нужен хотя бы один формат книги и txt
        if not (epub_path or fb2_path or doc_path) or not txt:
            await bot.send_message(chat_id, "❌ Ошибка: Необходимые файлы (Книга/TXT) отсутствуют.")
            return

        info = await asyncio.to_thread(parse_info, txt, epub_path)
        chapters = await asyncio.to_thread(count_chapters, epub_path) if epub_path else "?"
        annotation = await asyncio.to_thread(extract_annotation, epub_path) if epub_path else "Описание отсутствует"

        glossary = data.get("glossary", "?")
        translation = data.get("translation", "?")
        filter_choice = data.get("filter", "none")
        status = data.get("status", "?")

        post2 = format_text(info, chapters, status, annotation)
        post3 = format_files(glossary, translation, filter_choice)

        title_topic = info.get('title_ru') or info.get('title_en') or info.get('title_original') or 'Без названия'
        safe_title = re.sub(r'[\\/*?:"<>|]', "", title_topic).strip()
        if not safe_title:
            safe_title = "book"

        # 1. Создаем тему
        icon_color = random.choice([0x6FB9F0, 0xFFD67E, 0xCB86DB, 0x8EEE98, 0xFF93B2, 0xFB6F5F])
        forum_topic = await bot.create_forum_topic(
            chat_id=GROUP_USERNAME,
            name=title_topic[:128], 
            icon_color=icon_color
        )
        topic_id = forum_topic.message_thread_id

        # 2. Отправляем обложку
        if cover and os.path.exists(cover):
            await bot.send_photo(
                chat_id=GROUP_USERNAME,
                photo=FSInputFile(cover),
                message_thread_id=topic_id
            )

        # 3. Отправляем описание
        await bot.send_message(
            chat_id=GROUP_USERNAME,
            text=post2,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            message_thread_id=topic_id
        )

        # 4. Отправляем документы
        caption_sent = False

        if epub_path and os.path.exists(epub_path):
            await bot.send_document(
                chat_id=GROUP_USERNAME,
                document=FSInputFile(epub_path, filename=f"{safe_title}.epub"),
                caption=post3,
                message_thread_id=topic_id
            )
            caption_sent = True

        if fb2_path and os.path.exists(fb2_path):
            await bot.send_document(
                chat_id=GROUP_USERNAME,
                document=FSInputFile(fb2_path, filename=f"{safe_title}.fb2"),
                caption=None if caption_sent else post3,
                message_thread_id=topic_id
            )
            caption_sent = True

        if doc_path and os.path.exists(doc_path):
            ext = "docx" if doc_path.lower().endswith("docx") else "doc"
            await bot.send_document(
                chat_id=GROUP_USERNAME,
                document=FSInputFile(doc_path, filename=f"{safe_title}.{ext}"),
                caption=None if caption_sent else post3,
                message_thread_id=topic_id
            )

        await bot.send_message(chat_id=chat_id, text=f"✅ Тема '{escape(title_topic)}' успешно создана!")
        
        await state.clear() 
        cleanup_files(data)

    except Exception as e:
        print(f"Ошибка публикации: {e}")
        await bot.send_message(chat_id=chat_id, text=f"❌ Ошибка публикации:\n{escape(str(e))}")

# ================= СМАРТ-ЗАПУСК =================

async def on_startup(bot: Bot):
    if WEBHOOK_URL:
        await bot.set_webhook(f"{WEBHOOK_URL}/webhook", drop_pending_updates=True)
        print(f"Webhook установлен на {WEBHOOK_URL}/webhook")

if __name__ == "__main__":
    PORT = os.environ.get("PORT")
    if PORT:
        # ---- РЕЖИМ СЕРВЕРА ----
        port_num = int(PORT)
        print(f"Запуск в режиме Webhook через aiohttp на порту {port_num}...")
        
        app = web.Application()
        webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
        webhook_requests_handler.register(app, path="/webhook")
        
        dp.startup.register(on_startup)
        setup_application(app, dp, bot=bot)
        
        web.run_app(app, host="0.0.0.0", port=port_num)
    else:
        # ---- РЕЖИМ ЛОКАЛЬНОГО ПК (POLLING) ----
        print("PORT не найден. Запуск в режиме Polling...")
        async def main():
            await bot.delete_webhook(drop_pending_updates=True)
            print("Бот запущен!")
            await dp.start_polling(bot)

        asyncio.run(main())
