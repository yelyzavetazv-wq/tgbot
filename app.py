from flask import Flask, request
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import os
import uuid
import zipfile
import re
import xml.etree.ElementTree as ET
from ebooklib import epub, ITEM_COVER, ITEM_IMAGE
from bs4 import BeautifulSoup

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
    try:
        with zipfile.ZipFile(epub_path, 'r') as z:
            for name in z.namelist():
                if name.endswith((".xhtml", ".html")):
                    with z.open(name) as f:
                        soup = BeautifulSoup(f.read(), "html.parser")
                        text = soup.get_text()
                        if len(text) > 100:
                            return text[:2000]
    except:
        pass
    return "Описание отсутствует"


def count_chapters(epub_path):
    try:
        book = epub.read_epub(epub_path)
        return len(list(book.get_items()))
    except:
        return "?"


def extract_tags_from_opf(epub_path):
    """Извлекает теги (dc:subject) из content.opf"""
    tags = []
    try:
        with zipfile.ZipFile(epub_path, 'r') as z:
            for name in z.namelist():
                if name.endswith('.opf'):
                    with z.open(name) as f:
                        content = f.read().decode('utf-8', errors='ignore')
                        root = ET.fromstring(content)
                        ns = {'dc': 'http://purl.org/dc/elements/1.1/'}
                        for subject in root.findall('.//dc:subject', ns):
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

    # Читаем из TXT
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

    # Парсим теги из EPUB (если есть)
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

✍️ {info.get('author', '')}
📊 Глав: {chapters}
📌 Статус: {status}
"""
    if info.get('tags'):
        text += f"🏷️ Теги: {', '.join(info['tags'])}\n"
    
    text += f"""
📖 Описание:
<blockquote>{annotation}</blockquote>
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
    bot.send_message(message.chat.id, "📚 Бот жив! Отправьте ZIP-архив с книгой (cover, .epub, .fb2, description.txt)")


@bot.message_handler(content_types=["document"])
def handle_docs(message):
    file_info = bot.get_file(message.document.file_id)
    file = bot.download_file(file_info.file_path)

    name = message.document.file_name
    path = f"/tmp/{uuid.uuid4().hex}_{name}"

    with open(path, "wb") as f:
        f.write(file)

    data = user_data.setdefault(message.chat.id, {
        "epub": None,
        "cover": None,
        "fb2": None,
        "txt": ""
    })

    if name.endswith(".epub"):
        data["epub"] = path
        data["cover"] = extract_cover(path)
        bot.send_message(message.chat.id, "📚 EPUB получен")
        # Проверяем, есть ли уже TXT
        if data["txt"]:
            bot.send_message(message.chat.id, "✅ Все файлы получены. Выберите Глоссарий:", reply_markup=glossary_keyboard())
        else:
            bot.send_message(message.chat.id, "⏳ Ожидаю description.txt...")

    elif name.endswith(".fb2"):
        data["fb2"] = path
        bot.send_message(message.chat.id, "📖 FB2 получен")

    elif name.endswith(".txt"):
        with open(path, "r", encoding="utf-8") as f:
            data["txt"] = f.read()
        bot.send_message(message.chat.id, "📝 description.txt получен")
        if data["epub"]:
            bot.send_message(message.chat.id, "✅ Все файлы получены. Выберите Глоссарий:", reply_markup=glossary_keyboard())
        else:
            bot.send_message(message.chat.id, "⏳ Ожидаю EPUB...")

    elif name.endswith(".zip"):
        # Если пришёл ZIP, распаковываем
        bot.send_message(message.chat.id, "📦 Распаковываю ZIP...")
        extract_path = f"/tmp/{uuid.uuid4().hex}"
        with zipfile.ZipFile(path, 'r') as z:
            z.extractall(extract_path)
        
        for root, dirs, files in os.walk(extract_path):
            for f in files:
                full_path = os.path.join(root, f)
                if f.lower().endswith('.epub'):
                    data["epub"] = full_path
                    data["cover"] = extract_cover(full_path)
                elif f.lower().endswith('.fb2'):
                    data["fb2"] = full_path
                elif f.lower().endswith('.txt'):
                    with open(full_path, 'r', encoding='utf-8') as txt_file:
                        data["txt"] = txt_file.read()
        
        bot.send_message(message.chat.id, "✅ ZIP распакован. Выберите Глоссарий:", reply_markup=glossary_keyboard())


# ================= CALLBACK =================

@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    chat_id = call.message.chat.id
    user_choices.setdefault(chat_id, {})

    data_parts = call.data.split(":", 1)
    cat = data_parts[0]
    val = data_parts[1] if len(data_parts) > 1 else ""

    bot.answer_callback_query(call.id)

    # Удаляем сообщение с кнопками
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


def publish_to_channel(chat_id):
    data = user_data.get(chat_id)
    choices = user_choices.get(chat_id)

    if not data or not choices:
        bot.send_message(chat_id, "❌ Ошибка: данные не найдены")
        return

    epub_path = data.get("epub")
    fb2_path = data.get("fb2")
    txt = data.get("txt", "")
    cover = data.get("cover")

    # Парсим информацию (с тегами из EPUB)
    info = parse_info(txt, epub_path)
    
    # Получаем главы и аннотацию
    chapters = count_chapters(epub_path) if epub_path else "?"
    annotation = extract_annotation(epub_path) if epub_path else "Описание отсутствует"

    # Значения из кнопок
    glossary = choices.get("glossary", "?")
    translation = choices.get("translation", "?")
    filter_choice = choices.get("filter", "none")
    status = choices.get("status", "?")

    # Форматируем посты
    post2 = format_text(info, chapters, status, annotation)
    post3 = format_files(glossary, translation, filter_choice)

    # ПОСТ 1: обложка
    if cover:
        with open(cover, "rb") as img:
            bot.send_photo(CHANNEL_ID, img)

    # ПОСТ 2: текст
    bot.send_message(CHANNEL_ID, post2, parse_mode="HTML")

    # ПОСТ 3: файлы + подпись с параметрами
    if epub_path:
        with open(epub_path, "rb") as f:
            bot.send_document(CHANNEL_ID, f, caption=post3)
    if fb2_path:
        with open(fb2_path, "rb") as f:
            bot.send_document(CHANNEL_ID, f)

    # Подтверждение пользователю
    bot.send_message(chat_id, f"✅ Книга '{info.get('title_ru', 'Без названия')}' опубликована в канале!")

    # Очистка
    user_data.pop(chat_id, None)
    user_choices.pop(chat_id, None)


# ================= START =================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
