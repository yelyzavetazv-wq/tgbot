from flask import Flask, request
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import os
import uuid
import zipfile
import re
import xml.etree.ElementTree as ET
import requests
import shutil
from werkzeug.utils import secure_filename
from ebooklib import epub, ITEM_COVER, ITEM_IMAGE
from bs4 import BeautifulSoup
from html import escape
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Настройка сессии с ретраями
session = requests.Session()
retries = Retry(total=3, backoff_factor=1)
session.mount('https://', HTTPAdapter(max_retries=retries))

# ================= CONFIG =================

TOKEN = os.getenv("TOKEN")
CHANNEL_ID = "@my_ff_translate"

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

# ================= EPUB =================

def extract_cover(epub_path):
    try:
        book = epub.read_epub(epub_path)

        for item in book.get_items():
            if item.get_type() in (ITEM_COVER, ITEM_IMAGE):
                path = f"/tmp/{uuid.uuid4().hex}.jpg"
                with open(path, "wb") as f:
                    f.write(item.get_content())
                return path
    except:
        pass
    return None


def extract_annotation(epub_path):
    """Извлекает аннотацию из dc:description в OPF файле"""
    try:
        import zipfile
        from bs4 import BeautifulSoup
        
        with zipfile.ZipFile(epub_path, 'r') as epub_zip:
            for name in epub_zip.namelist():
                if name.endswith('.opf'):
                    with epub_zip.open(name) as f:
                        content = f.read().decode('utf-8', errors='ignore')
                        soup = BeautifulSoup(content, 'xml')
                        
                        # Ищем dc:description
                        description = soup.find('dc:description')
                        if description and description.text:
                            # Парсим HTML внутри описания
                            inner_soup = BeautifulSoup(description.text, 'html.parser')
                            # Берём текст из всех тегов p
                            paragraphs = inner_soup.find_all('p')
                            if paragraphs:
                                texts = [p.get_text().strip() for p in paragraphs if p.get_text().strip()]
                                return '\n'.join(texts)[:2000]
                            # Если нет p, берём весь текст
                            return description.text.strip()[:2000]
        return "Описание отсутствует"
    except Exception as e:
        print(f"Ошибка парсинга аннотации: {e}")
        return "Описание отсутствует"


def count_chapters(epub_path):
    """Определяет количество глав по номеру в тексте последней главы из toc.ncx"""
    try:
        import zipfile
        import re
        from bs4 import BeautifulSoup
        
        with zipfile.ZipFile(epub_path, 'r') as z:
            for name in z.namelist():
                if name.endswith('.ncx'):
                    with z.open(name) as f:
                        content = f.read().decode('utf-8', errors='ignore')
                        soup = BeautifulSoup(content, 'xml')
                        
                        # Находим все navPoint
                        nav_points = soup.find_all('navPoint')
                        if nav_points:
                            # Берём последний navPoint
                            last_nav = nav_points[-1]
                            # Ищем текст внутри navLabel
                            nav_label = last_nav.find('navLabel')
                            if nav_label:
                                text_tag = nav_label.find('text')
                                if text_tag and text_tag.text:
                                    text = text_tag.text
                                    # Ищем число в тексте (например "Глава 376")
                                    match = re.search(r'(\d+)', text)
                                    if match:
                                        return int(match.group(1))
                    break
        return "?"
    except Exception as e:
        print(f"Ошибка подсчёта глав: {e}")
        return "?"


def extract_tags_from_opf(epub_path):
    """Извлекает теги из dc:subject в OPF файле"""
    tags = []
    try:
        import zipfile
        from bs4 import BeautifulSoup
        
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
        "title_ru": "",
        "title_en": "",
        "title_original": "",
        "author": "",
        "links": [],
        "tags": []
    }

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
    text = f"""
🏴‍☠️ {info.get('title_ru', 'Без названия')}
🇬🇧 {info.get('title_en', '')}
🌐 {info.get('title_original', '')}

✍️ Автор: {info.get('author', '')}

📊 Глав: {chapters}

📌 Статус: {status}

"""
    
    if info.get('tags'):
        tags_with_hash = ", ".join([f"#{tag}" for tag in info['tags']])
        text += f"🏷️ Теги: {tags_with_hash}\n"
    
    text += f"""
    
📖 Описание:
<blockquote>{escape(annotation)}</blockquote>
"""
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
    bot.send_message(message.chat.id, "📚 Отправьте файлы книги: .epub (обязательно), .fb2, .doc, и файл описания .txt  ")

    
@bot.message_handler(content_types=["document"])
def handle_docs(message):
    MAX_FILE_SIZE = 100 * 1024 * 1024

    if message.document.file_size > MAX_FILE_SIZE:
        bot.send_message(
            message.chat.id,
            "❌ Файл слишком большой. Максимальный размер: 100 МБ."
        )
        return
    file_info = bot.get_file(message.document.file_id)
    file = bot.download_file(file_info.file_path)

    name = secure_filename(message.document.file_name)
    path = f"/tmp/{uuid.uuid4().hex}_{name}"

    with open(path, "wb") as f:
        f.write(file)

    data = user_data.setdefault(message.chat.id, {
        "epub": None,
        "cover": None,
        "fb2": None,
        "doc": None,
        "txt": ""
    })

    if name.endswith(".epub"):
        data["epub"] = path
        data["epub_name"] = message.document.file_name
        data["cover"] = extract_cover(path)
    
        bot.send_message(
            message.chat.id,
            f"✅ Получен EPUB: {message.document.file_name}"
        )
        
        # Проверяем: нужен только EPUB + TXT
        if data.get("epub") and data.get("txt"):
            bot.send_message(message.chat.id, "📚 Все файлы получены! Выберите Глоссарий:", reply_markup=glossary_keyboard())
        elif data.get("epub") and not data.get("txt"):
            # Молчим, ждём TXT
            pass

    elif name.endswith(".fb2"):
        data["fb2"] = path
        bot.send_message(message.chat.id, f"✅ Получен FB2: {name}")
        
        # FB2 не обязателен, но если уже есть EPUB и TXT — можно начинать
        if data.get("epub") and data.get("txt"):
            bot.send_message(message.chat.id, "📚 Все необходимые файлы получены! Выберите Глоссарий:", reply_markup=glossary_keyboard())

    elif name.endswith(".doc") or name.endswith(".docx"):
        data["doc"] = path
        bot.send_message(message.chat.id, f"✅ Получен DOC: {name}")
        
        if data.get("epub") and data.get("txt"):
            bot.send_message(message.chat.id, "📚 Все необходимые файлы получены! Выберите Глоссарий:", reply_markup=glossary_keyboard())

    elif name.endswith(".txt"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data["txt"] = f.read()
        except UnicodeDecodeError:
            try:
                with open(path, "r", encoding="cp1251") as f:
                    data["txt"] = f.read()
            except Exception:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    data["txt"] = f.read()
        
        bot.send_message(message.chat.id, f"✅ Получен description.txt: {name}")
        
        if data.get("epub"):
            bot.send_message(message.chat.id, "📚 Все файлы получены! Выберите Глоссарий:", reply_markup=glossary_keyboard())
        else:
            bot.send_message(message.chat.id, "❌ Нет EPUB файла! Пожалуйста, отправьте EPUB файл.")

# ================= CALLBACK =================

@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    chat_id = call.message.chat.id
    user_choices.setdefault(chat_id, {})

    data_parts = call.data.split(":", 1)
    cat = data_parts[0]
    val = data_parts[1] if len(data_parts) > 1 else ""

    bot.answer_callback_query(call.id)

    try:
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

def cleanup_user(chat_id):
    data = user_data.get(chat_id, {})

    # Удаляем файлы пользователя
    for key in ["epub", "fb2", "doc", "cover"]:
        file_path = data.get(key)
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                print(f"Ошибка удаления {file_path}: {e}")

    # Удаляем временную папку
    extract_path = data.get("extract_path")
    if extract_path and os.path.exists(extract_path):
        try:
            shutil.rmtree(extract_path)
        except Exception as e:
            print(f"Ошибка удаления папки {extract_path}: {e}")

    user_data.pop(chat_id, None)
    user_choices.pop(chat_id, None)

def publish_to_channel(chat_id):
    try:
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

        if cover:
            with open(cover, "rb") as img:
                bot.send_photo(CHANNEL_ID, img, timeout=60)

        bot.send_message(
            CHANNEL_ID,
            post2,
            parse_mode="HTML",
            disable_web_page_preview=True
        )

        if epub_path:
            original_name = data.get("epub_name", "book.epub")
            temp_epub = f"/tmp/{original_name}"
        
            shutil.copy2(epub_path, temp_epub)
        
            with open(temp_epub, "rb") as f:
                bot.send_document(
                    CHANNEL_ID,
                    f,
                    caption=post3,
                    timeout=180
                )
        
            if os.path.exists(temp_epub):
                os.remove(temp_epub)

        elif fb2_path:
            clean_name = f"{info.get('title_ru', 'book')}.fb2"
            temp_fb2 = f"/tmp/{clean_name}"

            shutil.copy2(fb2_path, temp_fb2)

            with open(temp_fb2, "rb") as f:
                bot.send_document(
                    CHANNEL_ID,
                    f,
                    caption=post3,
                    timeout=180
                )

            if os.path.exists(temp_fb2):
                os.remove(temp_fb2)

        else:
            bot.send_message(CHANNEL_ID, post3)

        if fb2_path and epub_path:
            clean_name = f"{info.get('title_ru', 'book')}.fb2"
            temp_fb2 = f"/tmp/{clean_name}"

            shutil.copy2(fb2_path, temp_fb2)

            with open(temp_fb2, "rb") as f:
                bot.send_document(
                    CHANNEL_ID,
                    f,
                    timeout=180
                )

            if os.path.exists(temp_fb2):
                os.remove(temp_fb2)

        if doc_path:
            clean_name = f"{info.get('title_ru', 'document')}.docx"
            temp_doc = f"/tmp/{clean_name}"

            shutil.copy2(doc_path, temp_doc)

            with open(temp_doc, "rb") as f:
                bot.send_document(
                    CHANNEL_ID,
                    f,
                    timeout=180
                )

            if os.path.exists(temp_doc):
                os.remove(temp_doc)

        bot.send_message(
            chat_id,
            f"✅ Книга '{info.get('title_ru', 'Без названия')}' опубликована в канале!"
        )

    except Exception as e:
        print(f"Ошибка публикации: {e}")

        bot.send_message(
            chat_id,
            f"❌ Ошибка публикации:\n{e}"
        )

    finally:
        cleanup_user(chat_id)


# ================= START =================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
