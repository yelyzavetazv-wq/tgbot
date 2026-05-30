from flask import Flask, request
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import os
import uuid
import zipfile
import re
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

# ================= TXT PARSER =================

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
        InlineKeyboardButton("Gemini 3.5 Flash", callback_data="glossary:gemini"),
        InlineKeyboardButton("✏️ Другое", callback_data="glossary:other")
    )
    return kb


def translation_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("Gemini 3.5", callback_data="translation:gemini"),
        InlineKeyboardButton("✏️ Другое", callback_data="translation:other")
    )
    return kb


def filter_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("ChatGPT", callback_data="filter:chatgpt"),
        InlineKeyboardButton("DeepSeek", callback_data="filter:deepseek"),
        InlineKeyboardButton("❌ None", callback_data="filter:none")
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

def format_text(info, chapters, status, annotation):
    return f"""
🏴‍☠️ {info.get('title_ru','Без названия')}
🇬🇧 {info.get('title_en','')}
🌐 {info.get('title_original','')}

✍️ {info.get('author','')}
📊 Глав: {chapters}
📌 Статус: {status}

📖 Описание:
<blockquote>{annotation}</blockquote>
"""


def format_files(glossary, translation, filter_choice):
    text = f"🤖 Glossary: {glossary}\n🤖 Translation: {translation}"
    if filter_choice and filter_choice != "none":
        text += f"\n🧹 Filter: {filter_choice}"
    return text

# ================= COMMANDS =================

@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(message.chat.id, "бот жив")

# ================= DOCUMENTS =================

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
        "txt": ""
    })

    if name.endswith(".epub"):
        data["epub"] = path
        data["cover"] = extract_cover(path)

        bot.send_message(message.chat.id, "📚 EPUB получен")
        bot.send_message(message.chat.id, "Выберите Glossary:", reply_markup=glossary_keyboard())

    elif name.endswith(".txt"):
        with open(path, "r", encoding="utf-8") as f:
            data["txt"] = f.read()

        bot.send_message(message.chat.id, "📝 TXT получен")
        bot.send_message(message.chat.id, "Выберите Glossary:", reply_markup=glossary_keyboard())

# ================= CALLBACK =================

@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):

    chat_id = call.message.chat.id
    user_choices.setdefault(chat_id, {})

    cat, val = call.data.split(":")

    bot.answer_callback_query(call.id)

    if cat == "glossary":
        user_choices[chat_id]["glossary"] = val
        bot.send_message(chat_id, "Translation:", reply_markup=translation_keyboard())

    elif cat == "translation":
        user_choices[chat_id]["translation"] = val
        bot.send_message(chat_id, "Filter:", reply_markup=filter_keyboard())

    elif cat == "filter":
        user_choices[chat_id]["filter"] = val
        bot.send_message(chat_id, "Status:", reply_markup=status_keyboard())

    elif cat == "status":

        user_choices[chat_id]["status"] = val

        data = user_data.get(chat_id)
        choices = user_choices.get(chat_id)

        if not data:
            bot.send_message(chat_id, "❌ нет данных")
            return

        epub_path = data.get("epub")
        txt = data.get("txt", "")

        cover = data.get("cover")

        info = parse_info(txt)
        chapters = count_chapters(epub_path) if epub_path else "?"

        annotation = extract_annotation(epub_path) if epub_path else "нет"

        # POST 1 cover
        if cover:
            bot.send_photo(CHANNEL_ID, open(cover, "rb"))

        # POST 2 text
        bot.send_message(
            CHANNEL_ID,
            format_text(info, chapters, val, annotation),
            parse_mode="HTML"
        )

        # POST 3 files + settings
        if epub_path:
            bot.send_document(
                CHANNEL_ID,
                open(epub_path, "rb"),
                caption=format_files(
                    choices.get("glossary"),
                    choices.get("translation"),
                    choices.get("filter")
                )
            )

        bot.send_message(chat_id, "✅ опубликовано")

        user_data.pop(chat_id, None)
        user_choices.pop(chat_id, None)
