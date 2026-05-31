import os
import uuid
import zipfile
import re
import shutil
import requests
import xml.etree.ElementTree as ET
import html  # Добавлено для безопасного экранирования HTML-тегов
from flask import Flask, request
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from ebooklib import epub, ITEM_COVER, ITEM_IMAGE
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Настройка сессии с ретраями
session = requests.Session()
retries = Retry(total=3, backoff_factor=1)
session.mount('https://', HTTPAdapter(max_retries=retries))

# ================= CONFIG =================

TOKEN = os.getenv("TOKEN")
CHANNEL_ID = "@testikmatestikoxuestik"

if not TOKEN or ":" not in TOKEN:
    raise Exception("TOKEN not set")

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

user_data = {}
user_choices = {}

# ================= HEALTH =================

@app.route("/")
def home():
    return "OK"

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        update = telebot.types.Update.de_json(
            request.get_data().decode("utf-8")
        )
        bot.process_new_updates([update])
    except Exception as e:
        print("WEBHOOK ERROR:", e)

    return "OK", 200

# ================= EPUB UTILS =================

def extract_cover(epub_path):
    try:
        book = epub.read_epub(epub_path)
        for item in book.get_items():
            if item.get_type() in (ITEM_COVER, ITEM_IMAGE):
                path = f"/tmp/{uuid.uuid4().hex}.jpg"
                with open(path, "wb") as f:
                    f.write(item.get_content())
                return path
    except Exception as e:
        print(f"Ошибка извлечения обложки: {e}")
    return None


def extract_annotation(epub_path):
    """Извлекает аннотацию из dc:description в OPF файле"""
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
    """Определяет количество глав по номеру в тексте последней главы из toc.ncx"""
    try:
        with zipfile.ZipFile(epub_path, 'r') as z:
            for name in z.namelist():
                if name.endswith('.ncx'):
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
                                    text = text_tag.text
                                    match = re.search(r'(\d+)', text)
                                    if match:
                                        return int(match.group(1))
                    break
        return "?"
    except Exception as e:
        print(f"Ошибка подсчета глав: {e}")
        return "?"


def extract_tags_from_opf(epub_path):
    """Извлекает теги из dc:subject в OPF файле"""
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
    except:
        pass
    return tags


# ================= TXT PARSER =================

def parse_info(text, epub_path=None):
    info = {
        "title_ru": "", "title_en": "", "title_original": "",
        "author": "", "links": [], "tags": []
    }

    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("http"):
            info["links"].append(line)
        elif "название_ru" in line.lower() and ":" in line:
            info["title_ru"] = line.split(":", 1)[1].strip()
        elif "название_en" in line.lower() and ":" in line:
            info["title_en"] = line.split(":", 1)[1].strip()
        elif "название_original" in line.lower() and ":" in line:
            info["title_original"] = line.split(":", 1)[1].strip()
        elif "автор" in line.lower() and ":" in line:
            info["author"] = line.split(":", 1)[1].strip()

    if epub_path:
        info["tags"] = extract_tags_from_opf(epub_path)

    return info


# ================= KEYBOARDS =================

def glossary_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("Gemini 3.5 Flash", callback_data="glossary:Gemini 3.5 Flash"),
        InlineKeyboardButton("Gemini 3.1 Flash Lite", callback_data="glossary:Gemini 3.1 Flash Lite"),
        InlineKeyboardButton("Gemini 3 Flash", callback_data="glossary:Gemini 3 Flash"),
        InlineKeyboardButton("Gemini 2.5 Flash", callback_data="glossary:Gemini 2.5 Flash"),
        InlineKeyboardButton("✏️ Другое", callback_data="glossary:other"),
        row_width=1
    )
    return kb


def translation_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("Gemini 3.5 Flash", callback_data="translation:Gemini 3.5 Flash"),
        InlineKeyboardButton("Gemini 3.1 Flash Lite", callback_data="translation:Gemini 3.1 Flash Lite"),
        InlineKeyboardButton("Gemini 3 Flash", callback_data="translation:Gemini 3 Flash"),
        InlineKeyboardButton("Gemini 2.5 Flash", callback_data="translation:Gemini 2.5 Flash"),
        InlineKeyboardButton("✏️ Другое", callback_data="translation:other"),
        row_width=1
    )
    return kb


def filter_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("ChatGPT Web", callback_data="filter:ChatGPT Web"),
        InlineKeyboardButton("DeepSeekWeb", callback_data="filter:DeepSeekWeb"),
        InlineKeyboardButton("❌ Нет (не показывать)", callback_data="filter:none"),
        InlineKeyboardButton("✏️ Другое", callback_data="filter:other"),
        row_width=1
    )
    return kb


def status_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("в процессе", callback_data="status:в процессе"),
        InlineKeyboardButton("завершен", callback_data="status:завершен"),
        InlineKeyboardButton("брошен", callback_data="status:брошен"),
        row_width=1
    )
    return kb


# ================= FORMAT =================

def format_text(info, chapters, status, annotation):
    # Защита от падения парсинга Telegram: экранируем весь текст, приходящий из файлов
    title_ru = html.escape(info.get('title_ru', 'Без названия'))
    title_en = html.escape(info.get('title_en', ''))
    title_original = html.escape(info.get('title_original', ''))
    author = html.escape(info.get('author', ''))
    safe_annotation = html.escape(annotation)
    safe_status = html.escape(status)

    text = f"🏴‍☠️ {title_ru}\n"
    if title_en: text += f"🇬🇧 {title_en}\n"
    if title_original: text += f"🌐 {title_original}\n"
    text += f"\n✍️ Автор: {author}\n📊 Глав: {chapters}\n📌 Статус: {safe_status}\n"
    
    if info.get('tags'):
        # Очищаем теги от лишних решеток или спецсимволов перед добавлением хэштега
        tags_with_hash = ", ".join([f"#{re.sub(r'\W+', '', tag)}" for tag in info['tags']])
        text += f"🏷️ Теги: {tags_with_hash}\n"
    
    text += f"\n📖 Описание:\n<blockquote>{safe_annotation}</blockquote>\n"
    
    for link in info.get('links', []):
        text += f"\n🔗 {link}"
    
    return text


def format_files(glossary, translation, filter_choice):
    text = f"🤖 Глоссарий: {glossary}\n🤖 Перевод: {translation}"
    if filter_choice and filter_choice != "none":
        text += f"\n🧹 Фильтр: {filter_choice}"
    return text


# ================= HANDLERS =================

@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(message.chat.id, "📚 Отправьте файлы книги: .epub (обязательно), .fb2, .doc/.docx, и файл описания .txt")


@bot.message_handler(content_types=["document"])
def handle_docs(message):
    file_info = bot.get_file(message.document.file_id)
    file_data = bot.download_file(file_info.file_path)

    name = message.document.file_name
    ext = os.path.splitext(name)[1].lower()
    
    # Безопасный путь сохранения
    path = f"/tmp/{uuid.uuid4().hex}{ext}"

    with open(path, "wb") as f:
        f.write(file_data)

    data = user_data.setdefault(message.chat.id, {
        "epub": None, "cover": None, "fb2": None, "doc": None, "txt": ""
    })

    if ext == ".epub":
        data["epub"] = path
        data["cover"] = extract_cover(path)
        bot.send_message(message.chat.id, f"✅ Получен EPUB: {name}")
        
    elif ext == ".fb2":
        data["fb2"] = path
        bot.send_message(message.chat.id, f"✅ Получен FB2: {name}")

    elif ext in [".doc", ".docx"]:
        data["doc"] = path
        bot.send_message(message.chat.id, f"✅ Получен DOC/DOCX: {name}")

    elif ext == ".txt":
        try:
            with open(path, "r", encoding="utf-8") as f:
                data["txt"] = f.read()
        except UnicodeDecodeError:
            with open(path, "r", encoding="cp1251", errors="ignore") as f:
                data["txt"] = f.read()
        
        # Удаляем временный текстовый файл, так как его контент уже сохранен в переменную
        if os.path.exists(path):
            os.remove(path)
        bot.send_message(message.chat.id, f"✅ Получен файл описания: {name}")

    # Проверка условий запуска клавиатуры опроса
    if data.get("epub") and data.get("txt"):
        if data.get("fb2") or data.get("doc") or ext == ".epub":
            bot.send_message(message.chat.id, "📚 Все файлы получены! Выберите Глоссарий:", reply_markup=glossary_keyboard())
    elif ext != ".txt" and not data.get("txt"):
        bot.send_message(message.chat.id, "📄 Файл получен. Ожидаю файл описания .txt...")


# ================= CALLBACKS & STEPS =================

@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    chat_id = call.message.chat.id
    user_choices.setdefault(chat_id, {})

    data_parts = call.data.split(":", 1)
    cat = data_parts[0]
    val = data_parts[1] if len(data_parts) > 1 else ""

    try:
        bot.answer_callback_query(call.id)
        bot.delete_message(chat_id, call.message.message_id)
    except:
        pass

    if cat == "glossary":
        if val == "other":
            msg = bot.send_message(chat_id, "✏️ Введите название модели для Глоссария:")
            bot.register_next_step_handler(msg, set_glossary, chat_id)
        else:
            user_choices[chat_id]["glossary"] = val
            bot.send_message(chat_id, "🤖 Выберите модель для Перевода:", reply_markup=translation_keyboard())

    elif cat == "translation":
        if val == "other":
            msg = bot.send_message(chat_id, "✏️ Введите название модели для Перевода:")
            bot.register_next_step_handler(msg, set_translation, chat_id)
        else:
            user_choices[chat_id]["translation"] = val
            bot.send_message(chat_id, "🧹 Выберите Фильтр:", reply_markup=filter_keyboard())

    elif cat == "filter":
        if val == "other":
            msg = bot.send_message(chat_id, "✏️ Введите название Фильтра:")
            bot.register_next_step_handler(msg, set_filter, chat_id)
        else:
            user_choices[chat_id]["filter"] = val
            bot.send_message(chat_id, "📌 Выберите Статус:", reply_markup=status_keyboard())

    elif cat == "status":
        user_choices[chat_id]["status"] = val
        publish_to_channel(chat_id)


def set_glossary(message, chat_id):
    user_choices[chat_id]["glossary"] = message.text
    bot.send_message(chat_id, "🤖 Выберите модель для Перевода:", reply_markup=translation_keyboard())


def set_translation(message, chat_id):
    user_choices[chat_id]["translation"] = message.text
    bot.send_message(chat_id, "🧹 Выберите Фильтр:", reply_markup=filter_keyboard())


def set_filter(message, chat_id):
    user_choices[chat_id]["filter"] = message.text
    bot.send_message(chat_id, "📌 Выберите Статус:", reply_markup=status_keyboard())


# ================= PUBLISHING WITH CLEANUP =================

def safe_remove(filepath):
    """Вспомогательная функция для безопасного удаления файлов с диска."""
    if filepath and os.path.exists(filepath):
        try:
            os.remove(filepath)
        except Exception as e:
            print(f"Не удалось удалить временный файл {filepath}: {e}")


def publish_to_channel(chat_id):
    data = user_data.get(chat_id)
    choices = user_choices.get(chat_id)

    if not data or not choices:
        bot.send_message(chat_id, "❌ Ошибка: данные не найдены")
        return

    epub_path = data.get("epub")
    fb2_path = data.get("fb2")
    doc_path = data.get("doc")
    txt = data.get("txt", "")
    cover = data.get("cover")

    info = parse_info(txt, epub_path)
    chapters = count_chapters(epub_path) if epub_path else "?"
    annotation = extract_annotation(epub_path) if epub_path else "Описание отсутствует"

    glossary = choices.get("glossary", "?")
    translation = choices.get("translation", "?")
    filter_choice = choices.get("filter", "none")
    status = choices.get("status", "?")

    post2 = format_text(info, chapters, status, annotation)
    post3 = format_files(glossary, translation, filter_choice)

    try:
        # Пост 1: обложка
        if cover and os.path.exists(cover):
            with open(cover, "rb") as img:
                bot.send_photo(CHANNEL_ID, img, timeout=60)

        # Пост 2: текст публикации
        bot.send_message(CHANNEL_ID, post2, parse_mode="HTML", disable_web_page_preview=True)

        # Подготовка чистого имени для файлов
        title_slug = info.get('title_ru') if info.get('title_ru') else 'book'
        title_slug = re.sub(r'[\\/*?:"<>|]', "", title_slug).strip()  # Убираем запрещенные символы операционной системы

        # Пост 3: файлы с красивыми именами
        if epub_path and os.path.exists(epub_path):
            temp_epub = f"/tmp/{title_slug}.epub"
            shutil.copy2(epub_path, temp_epub)
            with open(temp_epub, "rb") as f:
                bot.send_document(CHANNEL_ID, f, caption=post3, timeout=180)
            safe_remove(temp_epub)
        elif fb2_path and os.path.exists(fb2_path):
            temp_fb2 = f"/tmp/{title_slug}.fb2"
            shutil.copy2(fb2_path, temp_fb2)
            with open(temp_fb2, "rb") as f:
                bot.send_document(CHANNEL_ID, f, caption=post3, timeout=180)
            safe_remove(temp_fb2)
        else:
            bot.send_message(CHANNEL_ID, post3)

        # Отправляем остальные форматы, если они были загружены
        if fb2_path and epub_path and os.path.exists(fb2_path):
            temp_fb2 = f"/tmp/{title_slug}.fb2"
            shutil.copy2(fb2_path, temp_fb2)
            with open(temp_fb2, "rb") as f:
                bot.send_document(CHANNEL_ID, f, timeout=180)
            safe_remove(temp_fb2)

        if doc_path and os.path.exists(doc_path):
            ext = os.path.splitext(doc_path)[1]
            temp_doc = f"/tmp/{title_slug}{ext}"
            shutil.copy2(doc_path, temp_doc)
            with open(temp_doc, "rb") as f:
                bot.send_document(CHANNEL_ID, f, timeout=180)
            safe_remove(temp_doc)

        bot.send_message(chat_id, f"✅ Книга '{info.get('title_ru', 'Без названия')}' успешно опубликована в канале!")

    except Exception as e:
        bot.send_message(chat_id, f"❌ Произошла ошибка при публикации: {e}")
        print(f"PUBLISHING ERROR: {e}")

    finally:
        # КРИТИЧЕСКИЙ ШАГ: Принудительно очищаем диск от оригинальных тяжелых файлов
        safe_remove(epub_path)
        safe_remove(fb2_path)
        safe_remove(doc_path)
        safe_remove(cover)

        # Очищаем сессию пользователя из ОЗУ
        user_data.pop(chat_id, None)
        user_choices.pop(chat_id, None)


# ================= START =================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
