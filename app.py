from flask import Flask, request
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import os
import re
import uuid
import time
from ebooklib import epub, ITEM_COVER, ITEM_IMAGE
from bs4 import BeautifulSoup



# ================= CONFIG =================

TOKEN = os.getenv("TOKEN")

if not TOKEN or ":" not in TOKEN:
    raise ValueError("TOKEN не загружен или неправильный (проверь Render ENV)")

CHANNEL_ID = "@testikmatestikoxuestik"

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)
@app.route("/")
def index():
    return "Bot is running!"
user_choices = {}
user_data = {}

# ================= EPUB HELPERS =================

def extract_cover(epub_path):
    try:
        book = epub.read_epub(epub_path)

        for item in book.get_items():
            if item.get_type() == ITEM_COVER:
                path = f"/tmp/cover_{uuid.uuid4().hex}.jpg"
                with open(path, "wb") as f:
                    f.write(item.get_content())
                return path

        for item in book.get_items():
            if item.get_type() == ITEM_IMAGE:
                path = f"/tmp/cover_{uuid.uuid4().hex}.jpg"
                with open(path, "wb") as f:
                    f.write(item.get_content())
                return path

        return None
    except:
        return None


def count_chapters_from_epub(epub_path):
    try:
        book = epub.read_epub(epub_path)
        return len(list(book.get_items()))
    except:
        return "Неизвестно"


def extract_annotation_from_epub(epub_path):
    try:
        import zipfile
        with zipfile.ZipFile(epub_path, 'r') as z:
            for name in z.namelist():
                if name.endswith((".xhtml", ".html")):
                    with z.open(name) as f:
                        soup = BeautifulSoup(f.read(), "html.parser")
                        text = soup.get_text()
                        if len(text) > 100:
                            return text[:2000]
        return "Описание отсутствует"
    except:
        return "Описание отсутствует"


# ================= PARSER =================

def parse_info(text):
    info = {
        "title_ru": "",
        "title_en": "",
        "title_original": "",
        "author": "",
        "links": []
    }

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

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

    return info


# ================= KEYBOARDS =================

def glossary_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("Gemini 3.5 Flash", callback_data="glossary:Gemini 3.5"),
        InlineKeyboardButton("✏️ Другое", callback_data="glossary:other")
    )
    return kb


def translation_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("Gemini 3.5 Flash", callback_data="translation:Gemini 3.5"),
        InlineKeyboardButton("✏️ Другое", callback_data="translation:other")
    )
    return kb


def filter_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("ChatGPT Web", callback_data="filter:chatgpt"),
        InlineKeyboardButton("DeepSeek", callback_data="filter:deepseek"),
        InlineKeyboardButton("❌ Нет", callback_data="filter:none")
    )
    return kb


def status_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("в процессе", callback_data="status:process"),
        InlineKeyboardButton("завершен", callback_data="status:done"),
        InlineKeyboardButton("брошен", callback_data="status:dropped")
    )
    return kb


# ================= FORMAT =================

def detect_language(text):
    if re.search(r'[\u4e00-\u9fff]', text):
        return "🇨🇳"
    elif re.search(r'[\u3040-\u309f\u30a0-\u30ff]', text):
        return "🇯🇵"
    elif re.search(r'[\uac00-\ud7af]', text):
        return "🇰🇷"
    return "🌐"


def format_text_post(info, chapters, status, annotation):
    return f"""
🏴‍☠️ {info.get('title_ru','Без названия')}
🇬🇧 {info.get('title_en','')}
{detect_language(info.get('title_original',''))} {info.get('title_original','')}

✍️ Автор: {info.get('author','')}
📊 Глав: {chapters}
📌 Статус: {status}

📖 Описание:
<blockquote>{annotation}</blockquote>
"""


def format_files_post(glossary, translation, filter_choice):
    text = f"🤖 Глоссарий: {glossary}\n🤖 Перевод: {translation}"
    if filter_choice and filter_choice != "none":
        text += f"\n🧹 Фильтр: {filter_choice}"
    return text


# ================= WEBHOOK =================

@app.route("/webhook", methods=["POST"])
def webhook():
    update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))

    # ================= FILES =================
    if update.message and update.message.document:

        chat_id = update.message.chat.id

        file_info = bot.get_file(update.message.document.file_id)
        file_content = bot.download_file(file_info.file_path)

        original_name = update.message.document.file_name
        ext = os.path.splitext(original_name)[1]
        path = f"/tmp/{uuid.uuid4().hex}{ext}"

        with open(path, "wb") as f:
            f.write(file_content)

        user_data.setdefault(chat_id, {
            "epub": None,
            "cover": None,
            "description": ""
        })

        d = user_data[chat_id]

        if original_name.lower().endswith(".epub"):
            d["epub"] = path
            d["cover"] = extract_cover(path)
            bot.send_message(chat_id, "📚 EPUB принят + обложка извлечена")

        elif original_name.lower().endswith(".txt"):
            with open(path, "r", encoding="utf-8") as f:
                d["description"] = f.read()
            bot.send_message(chat_id, "📝 описание сохранено")

        return "OK", 200


    # ================= CALLBACK =================
    if update.callback_query:

        cb = update.callback_query
        chat_id = cb.message.chat.id
        category, value = cb.data.split(":")

        bot.answer_callback_query(cb.id)

        user_choices.setdefault(chat_id, {})

        if category == "glossary":
            user_choices[chat_id]["glossary"] = value
            bot.send_message(chat_id, "Выбор перевода:", reply_markup=translation_keyboard())

        elif category == "translation":
            user_choices[chat_id]["translation"] = value
            bot.send_message(chat_id, "Фильтр:", reply_markup=filter_keyboard())

        elif category == "filter":
            user_choices[chat_id]["filter"] = value
            bot.send_message(chat_id, "Статус:", reply_markup=status_keyboard())

        elif category == "status":

            user_choices[chat_id]["status"] = value

            choices = user_choices[chat_id]
            data = user_data.get(chat_id)

            if not data or not data.get("epub"):
                bot.send_message(chat_id, "❌ EPUB не найден")
                return "OK", 200

            epub_path = data["epub"]
            cover = data["cover"]

            info = parse_info(data["description"]) if data["description"] else {}

            chapters = count_chapters_from_epub(epub_path)
            annotation = extract_annotation_from_epub(epub_path)

            # POST 1 cover
            if cover:
                with open(cover, "rb") as f:
                    bot.send_photo(CHANNEL_ID, f)

            # POST 2 text
            bot.send_message(
                CHANNEL_ID,
                format_text_post(info, chapters, value, annotation),
                parse_mode="HTML"
            )

            # POST 3 files
            bot.send_document(
                CHANNEL_ID,
                open(epub_path, "rb"),
                caption=format_files_post(
                    choices["glossary"],
                    choices["translation"],
                    choices.get("filter")
                )
            )

            try:
                os.remove(epub_path)
                if cover:
                    os.remove(cover)
            except:
                pass

            user_data.pop(chat_id, None)
            user_choices.pop(chat_id, None)

            bot.send_message(chat_id, "✅ опубликовано")

    return "OK", 200
