from flask import Flask, request
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import os
import re
import uuid
from ebooklib import epub, ITEM_COVER, ITEM_IMAGE
from bs4 import BeautifulSoup

# ================= CONFIG =================

TOKEN = os.getenv("TOKEN")

if not TOKEN or ":" not in TOKEN:
    raise ValueError("TOKEN не найден в ENV")

CHANNEL_ID = "@testikmatestikoxuestik"

bot = telebot.TeleBot(TOKEN, threaded=False)

app = Flask(__name__)

user_choices = {}
user_data = {}

# ================= HEALTH =================

@app.route("/")
def index():
    return "Bot is running!"

# ================= EPUB =================

def extract_cover(epub_path):
    try:
        book = epub.read_epub(epub_path)

        for item in book.get_items():
            if item.get_type() == ITEM_COVER:
                path = f"/tmp/{uuid.uuid4().hex}.jpg"
                with open(path, "wb") as f:
                    f.write(item.get_content())
                return path

        for item in book.get_items():
            if item.get_type() == ITEM_IMAGE:
                path = f"/tmp/{uuid.uuid4().hex}.jpg"
                with open(path, "wb") as f:
                    f.write(item.get_content())
                return path

    except:
        pass

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
                        txt = soup.get_text()
                        if len(txt) > 100:
                            return txt[:2000]
    except:
        pass

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

# ================= CALLBACK / LOGIC =================

def handle_update(update):
    try:

        # ========== MESSAGE ==========
        if update.message:

            chat_id = update.message.chat.id

            if update.message.document:

                file_info = bot.get_file(update.message.document.file_id)
                file_content = bot.download_file(file_info.file_path)

                name = update.message.document.file_name
                path = f"/tmp/{uuid.uuid4().hex}{os.path.splitext(name)[1]}"

                with open(path, "wb") as f:
                    f.write(file_content)

                user_data.setdefault(chat_id, {
                    "epub": None,
                    "cover": None,
                    "description": ""
                })

                d = user_data[chat_id]

                if name.endswith(".epub"):
                    d["epub"] = path
                    d["cover"] = extract_cover(path)
                    bot.send_message(chat_id, "📚 EPUB принят")

                elif name.endswith(".txt"):
                    with open(path, "r", encoding="utf-8") as f:
                        d["description"] = f.read()
                    bot.send_message(chat_id, "📝 TXT принят")

        # ========== CALLBACK ==========
        if update.callback_query:

            cb = update.callback_query
            chat_id = cb.message.chat.id
            category, value = cb.data.split(":")

            bot.answer_callback_query(cb.id)

            user_choices.setdefault(chat_id, {})

            if category == "glossary":
                user_choices[chat_id]["glossary"] = value
                bot.send_message(chat_id, "Перевод:", reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("Gemini", callback_data="translation:gemini")
                ))

            elif category == "translation":
                user_choices[chat_id]["translation"] = value
                bot.send_message(chat_id, "Фильтр:", reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("ChatGPT", callback_data="filter:chatgpt")
                ))

            elif category == "filter":
                user_choices[chat_id]["filter"] = value
                bot.send_message(chat_id, "Статус:", reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("done", callback_data="status:done")
                ))

            elif category == "status":

                user_choices[chat_id]["status"] = value

                data = user_data.get(chat_id)
                choices = user_choices.get(chat_id)

                if not data or not data.get("epub"):
                    bot.send_message(chat_id, "❌ EPUB нет")
                    return

                epub_path = data["epub"]
                cover = data["cover"]

                info = parse_info(data["description"])
                chapters = count_chapters_from_epub(epub_path)
                annotation = extract_annotation_from_epub(epub_path)

                if cover:
                    bot.send_photo(CHANNEL_ID, open(cover, "rb"))

                bot.send_message(
                    CHANNEL_ID,
                    format_text_post(info, chapters, value, annotation),
                    parse_mode="HTML"
                )

                bot.send_document(
                    CHANNEL_ID,
                    open(epub_path, "rb"),
                    caption=format_files_post(
                        choices.get("glossary"),
                        choices.get("translation"),
                        choices.get("filter")
                    )
                )

                user_data.pop(chat_id, None)
                user_choices.pop(chat_id, None)

                bot.send_message(chat_id, "✅ опубликовано")

    except Exception as e:
        print("ERROR:", e)

# ================= WEBHOOK =================

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        json_str = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_str)

        handle_update(update)

    except Exception as e:
        print("WEBHOOK ERROR:", e)

    return "OK", 200
