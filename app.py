import os
import uuid
import zipfile
import re
import random
import asyncio
import tempfile
import logging
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

# ============================================================
# НАСТРОЙКА ЛОГИРОВАНИЯ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

# ============================================================
# БЛОК 1: КОНФИГУРАЦИЯ
# ============================================================

TOKEN = os.getenv("BOT_TOKEN")
GROUP_USERNAME = os.getenv("GROUP_USERNAME")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 МБ
TEMP_DIR = tempfile.gettempdir()

# РАЗРЕШЁННЫЕ РАСШИРЕНИЯ ФАЙЛОВ (только для книг)
ALLOWED_EXTENSIONS = {
    '.epub', '.fb2', '.doc', '.docx', '.txt',
    '.mobi', '.azw', '.azw3', '.pdf', '.djvu'
}

if not TOKEN:
    logger.error("BOT_TOKEN не найден в переменных окружения или файле .env")
    raise ValueError("ОШИБКА: BOT_TOKEN не найден в переменных окружения или файле .env")


# ============================================================
# БЛОК 2: ПЕРЕПОДКЛЮЧЕНИЕ ПРИ СБОЯХ (RETRY)
# ============================================================

class RetryRequestMiddleware(BaseRequestMiddleware):
    """Автоматически переподключается при сбоях сети"""
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
session.middleware.register(RetryRequestMiddleware(retries=3, backoff=1.0))

bot = Bot(token=TOKEN, session=session, default=DefaultBotProperties(parse_mode="HTML"))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# ============================================================
# БЛОК 3: СОСТОЯНИЯ ДЛЯ FSM (пошаговые диалоги)
# ============================================================

class BookForm(StatesGroup):
    waiting_for_glossary = State()
    waiting_for_translation = State()
    waiting_for_filter = State()


# ============================================================
# БЛОК 4: ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def secure_filename(filename: str) -> str:
    """Безопасное имя файла: заменяет всё кроме букв, цифр, точки и дефиса на '_' """
    return re.sub(r'[^\w\-.]', '_', filename)

def clean_hashtag(tag: str) -> str:
    """Очищает теги для хештегов"""
    cleaned = re.sub(r'[^\w]+', '_', tag).strip('_')
    return cleaned


# ============================================================
# БЛОК 5: ПАРСИНГ EPUB (обложка, аннотация, главы, теги, метаданные)
# ============================================================

def extract_cover(epub_path):
    """Извлекает обложку из EPUB"""
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
    """Извлекает аннотацию (описание) из EPUB"""
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
        logger.error(f"Ошибка парсинга аннотации: {e}")
        return "Описание отсутствует"

def count_chapters(epub_path):
    """Пытается подсчитать количество глав в EPUB"""
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
        logger.error(f"Ошибка подсчёта глав: {e}")
        return "?"

def extract_tags_from_opf(epub_path):
    """Извлекает теги (жанры) из EPUB"""
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

def extract_metadata_from_epub(epub_path):
    """
    ИЗВЛЕКАЕТ ИЗ EPUB:
    - Название (разбивает по / на русский, английский, оригинал)
    - Автора
    - Описание (аннотацию)
    - Теги (жанры)
    - Ссылки из dc:publisher
    """
    logger.info(f"📖 Начинаем парсинг EPUB: {epub_path}")
    
    result = {
        "title_full": "",
        "title_ru": "",
        "title_en": "",
        "title_original": "",
        "author": "",
        "annotation": "Описание отсутствует",
        "tags": [],
        "publisher_links": []
    }
    
    try:
        with zipfile.ZipFile(epub_path, 'r') as z:
            for name in z.namelist():
                if name.endswith('.opf'):
                    logger.info(f"📖 Найден OPF-файл: {name}")
                    with z.open(name) as f:
                        content = f.read().decode('utf-8', errors='ignore')
                        soup = BeautifulSoup(content, 'xml')
                        
                        # Название (dc:title) — разделяем по /
                        title_tag = soup.find('dc:title')
                        if title_tag and title_tag.text:
                            title_full = title_tag.text.strip()
                            result["title_full"] = title_full
                            parts = [p.strip() for p in title_full.split('/')]
                            if len(parts) >= 1:
                                result["title_ru"] = parts[0]
                            if len(parts) >= 2:
                                result["title_en"] = parts[1]
                            if len(parts) >= 3:
                                result["title_original"] = parts[2]
                            logger.info(f"📖 Название (ru): {result['title_ru']}")
                            if result['title_en']:
                                logger.info(f"📖 Название (en): {result['title_en']}")
                            if result['title_original']:
                                logger.info(f"📖 Название (original): {result['title_original']}")
                        else:
                            logger.warning("⚠️ Тег dc:title не найден")
                        
                        # Автор
                        creator = soup.find('dc:creator')
                        if creator and creator.text:
                            result["author"] = creator.text.strip()
                            logger.info(f"✍ Автор: {result['author']}")
                        else:
                            logger.warning("⚠️ Тег dc:creator не найден")
                        
                        # Описание (аннотация)
                        description = soup.find('dc:description')
                        if description and description.text:
                            inner_soup = BeautifulSoup(description.text, 'html.parser')
                            paragraphs = inner_soup.find_all('p')
                            if paragraphs:
                                texts = [p.get_text().strip() for p in paragraphs if p.get_text().strip()]
                                result["annotation"] = '\n'.join(texts)[:2000]
                            else:
                                result["annotation"] = description.text.strip()[:2000]
                            logger.info(f"📖 Описание: {result['annotation'][:100]}...")
                        else:
                            logger.warning("⚠️ Тег dc:description не найден")
                        
                        # Теги (жанры)
                        for subject in soup.find_all('dc:subject'):
                            if subject.text:
                                result["tags"].append(subject.text.strip())
                        if result["tags"]:
                            logger.info(f"🏷️ Теги: {result['tags']}")
                        else:
                            logger.warning("⚠️ Теги dc:subject не найдены")
                        
                        # --- ССЫЛКИ ИЗ dc:publisher (с проверкой) ---
                        publisher = soup.find('dc:publisher')
                        if publisher and publisher.text:
                            logger.info(f"🔍 Найден тег publisher: {publisher.text}")
                            links = publisher.text.strip().split()
                            logger.info(f"🔍 Ссылки после разбивки: {links}")
                            for link in links:
                                if link.startswith('http'):
                                    result["publisher_links"].append(link)
                            logger.info(f"🔍 Сохранено ссылок: {len(result['publisher_links'])}")
                            if result["publisher_links"]:
                                logger.info(f"🔍 Ссылки: {result['publisher_links']}")
                        else:
                            logger.warning("⚠️ Тег dc:publisher не найден или пуст")
                        
                    break
    except Exception as e:
        logger.error(f"Ошибка парсинга метаданных EPUB: {e}")
    
    logger.info(f"📊 Итоговые метаданные: {result}")
    return result


# ============================================================
# БЛОК 6: КЛАВИАТУРЫ (кнопки для пользователя)
# ============================================================

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


# ============================================================
# БЛОК 7: ФОРМАТИРОВАНИЕ ТЕКСТА ДЛЯ ПУБЛИКАЦИИ
# ============================================================

def format_text(metadata, chapters, status):
    """Формирует пост с описанием книги (название, автор, главы, статус, теги, описание, ссылки)"""
    title_ru = escape(metadata.get('title_ru') or 'Без названия')
    title_en = escape(metadata.get('title_en', ''))
    title_original = escape(metadata.get('title_original', ''))
    author = escape(metadata.get('author', ''))
    annotation = escape(metadata.get('annotation', 'Описание отсутствует'))
    tags = metadata.get('tags', [])
    links = metadata.get('publisher_links', [])
    
    safe_status = escape(status)
    safe_chapters = escape(str(chapters))

    text = f"🏴‍☠ {title_ru}\n"
    if title_en:
        text += f"🇬🇧 {title_en}\n"
    if title_original:
        text += f"🌐 {title_original}\n"

    text += "\n"
    text += f"✍ Автор: {author}\n"
    text += "\n"
    text += f"📊 Глав: {safe_chapters}\n"
    text += "\n"
    text += f"📌 Статус: {safe_status}\n"

    if tags:
        clean_tags = [
            f"#{escape(clean_hashtag(tag))}"
            for tag in tags
            if clean_hashtag(tag)
        ]
        if clean_tags:
            text += f"🏷️ Теги: {', '.join(clean_tags)}\n"

    text += "\n"
    text += "📖 Описание:\n"
    text += f"<blockquote expandable>{annotation}</blockquote>\n"

    for link in links:
        text += f"\n🔗 {escape(link)}"

    logger.info(f"📝 Сформирован пост. Количество ссылок: {len(links)}")
    return text

def format_files(glossary, translation, filter_choice):
    """Формирует подпись к файлам (какие модели использовались)"""
    text = f"🤖 Глоссарий: {escape(glossary)}\n🤖 Перевод: {escape(translation)}"
    if filter_choice and filter_choice != "none":
        text += f"\n🧹 Фильтр: {escape(filter_choice)}"
    return text


# ============================================================
# БЛОК 8: ТАЙМЕР ОЖИДАНИЯ ДОПОЛНИТЕЛЬНЫХ ФАЙЛОВ
# ============================================================

async def wait_and_publish(chat_id: int, state: FSMContext, wait_time: int = 60):
    """
    Ждёт 60 секунд после получения EPUB.
    Если за это время пользователь ничего не выбрал — публикует с настройками по умолчанию.
    """
    logger.info(f"⏱️ Запущен таймер на {wait_time} секунд для пользователя {chat_id}")
    await asyncio.sleep(wait_time)
    
    data = await state.get_data()
    epub_path = data.get("epub")
    
    if epub_path:
        status = data.get("status")
        if not status:
            logger.info(f"⏰ Время ожидания истекло для пользователя {chat_id}. Публикую с настройками по умолчанию.")
            await bot.send_message(chat_id, "⏰ Время ожидания истекло. Публикую с настройками по умолчанию.")
            await state.update_data(status="?")
            await publish_to_forum(chat_id, state)
    else:
        logger.warning(f"⏰ Время ожидания истекло для пользователя {chat_id}, но EPUB не найден.")


# ============================================================
# БЛОК 9: ОСНОВНЫЕ ХЕНДЛЕРЫ (команды и приём файлов)
# ============================================================

@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    """Команда /start — приветствие и инструкция"""
    logger.info(f"💬 Команда /start от пользователя {message.chat.id}")
    await state.clear()
    await message.answer(
        "📚 Отправьте файл книги в формате .epub\n"
        "➕ Дополнительно можно отправить: .fb2, .doc, .docx, .txt, .mobi, .pdf\n"
        "⏱️ Бот подождёт 60 секунд, чтобы вы могли добавить файлы."
    )

@dp.message(F.document)
async def handle_docs(message: types.Message, state: FSMContext):
    """
    Принимает файлы ТОЛЬКО из личных сообщений.
    - .epub → первый становится основным (парсится), остальные — дополнительные
    - .fb2, .doc, .docx, .txt, .mobi, .pdf → дополнительные файлы
    """
    logger.info(f"📎 Получен файл от пользователя {message.chat.id}: {message.document.file_name}")
    
    # Проверка: только из лички
    if message.chat.type != "private":
        logger.info(f"⛔ Файл из группы {message.chat.id} игнорируется")
        return

    if message.document.file_size > MAX_FILE_SIZE:
        logger.warning(f"⚠️ Файл слишком большой: {message.document.file_size} байт")
        await message.answer("❌ Файл слишком большой. Максимальный размер: 20 МБ.")
        return

    file_info = await bot.get_file(message.document.file_id)
    name = secure_filename(message.document.file_name)
    ext = os.path.splitext(name)[1].lower()
    
    # --- ПРОВЕРКА НА РАЗРЕШЁННЫЕ РАСШИРЕНИЯ ---
    if ext not in ALLOWED_EXTENSIONS:
        logger.warning(f"⚠️ Неподдерживаемый формат: {ext} от пользователя {message.chat.id}")
        await message.answer(f"❌ Формат {ext} не поддерживается. Разрешены: {', '.join(ALLOWED_EXTENSIONS)}")
        return

    path = os.path.join(TEMP_DIR, f"{uuid.uuid4().hex}_{name}")
    await bot.download(file_info, destination=path)
    logger.info(f"💾 Файл сохранён: {path}")

    current_data = await state.get_data()

    # --- ОСНОВНОЙ ФАЙЛ: ПЕРВЫЙ EPUB ---
    if ext == ".epub" and not current_data.get("epub"):
        logger.info(f"📚 Это первый EPUB от пользователя {message.chat.id} → делаем основным")
        epub_path = path
        cover_path = await asyncio.to_thread(extract_cover, path)
        metadata = await asyncio.to_thread(extract_metadata_from_epub, path)
        
        await state.update_data(
            epub=epub_path,
            cover=cover_path,
            metadata=metadata,
            epub_original_name=name  # сохраняем оригинальное имя
        )

        await message.answer(f"✅ Получен основной EPUB: {name}")

        # Запускаем таймер, если ещё не запущен
        if not current_data.get("timer_started"):
            await state.update_data(timer_started=True)
            asyncio.create_task(wait_and_publish(message.chat.id, state))
            logger.info(f"⏱️ Таймер запущен для пользователя {message.chat.id}")

        # Показываем кнопки, если ещё не показывали
        if not current_data.get("keyboard_sent"):
            await state.update_data(keyboard_sent=True)
            await message.answer("📚 Книга загружена! Выберите Глоссарий:", reply_markup=glossary_keyboard())

    # --- ДОПОЛНИТЕЛЬНЫЕ ФАЙЛЫ (включая второй и последующие EPUB) ---
    else:
        logger.info(f"➕ Дополнительный файл от пользователя {message.chat.id}: {name}")
        additional_files = current_data.get("additional_files", [])
        additional_files.append({
            "path": path,
            "name": name,
            "ext": ext
        })
        await state.update_data(additional_files=additional_files)
        
        if ext == ".epub":
            await message.answer(f"✅ Получен дополнительный EPUB: {name}")
        else:
            await message.answer(f"✅ Получен дополнительный файл: {name}")


# ============================================================
# БЛОК 10: ОБРАБОТКА КНОПОК (callback'и)
# ============================================================

@dp.callback_query()
async def callbacks(call: types.CallbackQuery, state: FSMContext):
    """Обрабатывает нажатия на кнопки"""
    logger.info(f"🔘 Нажата кнопка: {call.data} от пользователя {call.from_user.id}")
    
    data_parts = call.data.split(":", 1)
    cat = data_parts[0]
    val = data_parts[1] if len(data_parts) > 1 else ""

    await call.answer()

    try:
        await call.message.delete()
    except Exception as e:
        logger.error(f"Ошибка удаления сообщения: {e}")

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
        logger.info(f"📌 Выбран статус: {val} для пользователя {call.from_user.id}")
        await publish_to_forum(call.message.chat.id, state)


# ============================================================
# БЛОК 11: ОБРАБОТКА РУЧНОГО ВВОДА (когда выбрали "Другое")
# ============================================================

@dp.message(BookForm.waiting_for_glossary)
async def set_glossary(message: types.Message, state: FSMContext):
    logger.info(f"✏️ Пользователь {message.chat.id} ввёл глоссарий: {message.text}")
    await state.update_data(glossary=message.text)
    await state.set_state(None) 
    await message.answer("🤖 Выберите модель для Перевода:", reply_markup=translation_keyboard())

@dp.message(BookForm.waiting_for_translation)
async def set_translation(message: types.Message, state: FSMContext):
    logger.info(f"✏️ Пользователь {message.chat.id} ввёл перевод: {message.text}")
    await state.update_data(translation=message.text)
    await state.set_state(None)
    await message.answer("🧹 Выберите Фильтр:", reply_markup=filter_keyboard())

@dp.message(BookForm.waiting_for_filter)
async def set_filter(message: types.Message, state: FSMContext):
    logger.info(f"✏️ Пользователь {message.chat.id} ввёл фильтр: {message.text}")
    await state.update_data(filter=message.text)
    await state.set_state(None)
    await message.answer("📌 Выберите Статус:", reply_markup=status_keyboard())


# ============================================================
# БЛОК 12: ОЧИСТКА ВРЕМЕННЫХ ФАЙЛОВ
# ============================================================

def cleanup_files(data: dict):
    """Удаляет все временные файлы (и основные, и дополнительные)"""
    logger.info("🧹 Начинаем очистку временных файлов")
    
    # Основные
    for key in ["epub", "cover"]:
        file_path = data.get(key)
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"🗑️ Удалён файл: {file_path}")
            except Exception as e:
                logger.error(f"Ошибка удаления {file_path}: {e}")
    
    # Дополнительные
    additional_files = data.get("additional_files", [])
    for file_info in additional_files:
        file_path = file_info.get("path")
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"🗑️ Удалён дополнительный файл: {file_path}")
            except Exception as e:
                logger.error(f"Ошибка удаления {file_path}: {e}")


# ============================================================
# БЛОК 13: ПУБЛИКАЦИЯ В КАНАЛ
# ============================================================

async def publish_to_forum(chat_id: int, state: FSMContext):
    """
    Публикует книгу в канал:
    - Обложка
    - Описание (из EPUB)
    - EPUB файл (с оригинальным именем)
    - Все дополнительные файлы (с оригинальными именами)
    """
    logger.info(f"📤 Начинаем публикацию для пользователя {chat_id}")
    data = await state.get_data()

    try:
        epub_path = data.get("epub")
        cover = data.get("cover")
        metadata = data.get("metadata", {})
        
        if not epub_path:
            logger.error(f"❌ Ошибка: Файл EPUB отсутствует для пользователя {chat_id}")
            await bot.send_message(chat_id, "❌ Ошибка: Файл EPUB отсутствует.")
            return

        # --- ПРИНУДИТЕЛЬНО ПАРСИМ ЗАНОВО (для проверки) ---
        logger.info("🔄 Принудительный парсинг метаданных перед публикацией...")
        metadata = await asyncio.to_thread(extract_metadata_from_epub, epub_path)
        logger.info(f"📊 Метаданные после парсинга: {metadata}")

        chapters = await asyncio.to_thread(count_chapters, epub_path) if epub_path else "?"
        glossary = data.get("glossary", "?")
        translation = data.get("translation", "?")
        filter_choice = data.get("filter", "none")
        status = data.get("status", "?")

        post_text = format_text(metadata, chapters, status)
        post_files = format_files(glossary, translation, filter_choice)

        title_topic = metadata.get('title_ru') or metadata.get('title_en') or metadata.get('title_original') or 'Без названия'
        safe_title = re.sub(r'[\\/*?:"<>|]', "", title_topic).strip()
        if not safe_title:
            safe_title = "book"

        logger.info(f"📝 Название для публикации: {title_topic}")

        # --- 1. Отправляем обложку ---
        if cover and os.path.exists(cover):
            logger.info("🖼️ Отправляем обложку")
            await bot.send_photo(
                chat_id=GROUP_USERNAME,
                photo=FSInputFile(cover),
            )
        else:
            logger.warning("⚠️ Обложка не найдена")

        # --- 2. Отправляем описание ---
        logger.info("📝 Отправляем описание")
        await bot.send_message(
            chat_id=GROUP_USERNAME,
            text=post_text,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

        # --- 3. Отправляем основной EPUB (с ОРИГИНАЛЬНЫМ именем) ---
        if epub_path and os.path.exists(epub_path):
            epub_filename = data.get("epub_original_name")
            if not epub_filename:
                epub_filename = f"{safe_title}.epub"
            logger.info(f"📄 Отправляем основной EPUB: {epub_filename}")
            await bot.send_document(
                chat_id=GROUP_USERNAME,
                document=FSInputFile(epub_path, filename=epub_filename),
                caption=post_files,
            )

        # --- 4. Отправляем дополнительные файлы (с ОРИГИНАЛЬНЫМИ именами) ---
        additional_files = data.get("additional_files", [])
        if additional_files:
            logger.info(f"📎 Отправляем {len(additional_files)} дополнительных файлов")
            for file_info in additional_files:
                if os.path.exists(file_info["path"]):
                    logger.info(f"📎 Отправляем: {file_info['name']}")
                    await bot.send_document(
                        chat_id=GROUP_USERNAME,
                        document=FSInputFile(file_info["path"], filename=file_info["name"]),
                    )

        await bot.send_message(chat_id=chat_id, text=f"✅ Книга '{escape(title_topic)}' успешно опубликована!")
        logger.info(f"✅ Публикация завершена для пользователя {chat_id}")
        
        await state.clear() 
        cleanup_files(data)

    except Exception as e:
        logger.error(f"❌ Ошибка публикации для пользователя {chat_id}: {e}")
        await bot.send_message(chat_id=chat_id, text=f"❌ Ошибка публикации:\n{escape(str(e))}")


# ============================================================
# БЛОК 14: ЗАПУСК БОТА (Webhook / Polling)
# ============================================================

async def on_startup(bot: Bot):
    if WEBHOOK_URL:
        await bot.set_webhook(f"{WEBHOOK_URL}/webhook", drop_pending_updates=True)
        logger.info(f"Webhook установлен на {WEBHOOK_URL}/webhook")

if __name__ == "__main__":
    PORT = os.environ.get("PORT")
    if PORT:
        # --- РЕЖИМ СЕРВЕРА (Render / Heroku) ---
        port_num = int(PORT)
        logger.info(f"Запуск в режиме Webhook через aiohttp на порту {port_num}...")
        
        app = web.Application()
        
        # Корневой маршрут для проверки, что сервер жив
        async def health_check(request):
            return web.Response(text="Bot is running")
        app.router.add_get("/", health_check)
        
        # Регистрируем вебхук
        webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
        webhook_requests_handler.register(app, path="/webhook")
        
        dp.startup.register(on_startup)
        setup_application(app, dp, bot=bot)
        
        web.run_app(app, host="0.0.0.0", port=port_num)
    else:
        # --- РЕЖИМ ЛОКАЛЬНОГО ПК (Polling) ---
        logger.info("PORT не найден. Запуск в режиме Polling...")
        async def main():
            await bot.delete_webhook(drop_pending_updates=True)
            logger.info("Бот запущен!")
            await dp.start_polling(bot)

        asyncio.run(main())
