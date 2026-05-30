from flask import Flask, request
import telebot
import os
import uuid
from ebooklib import epub, ITEM_COVER, ITEM_IMAGE
from bs4 import BeautifulSoup

TOKEN = os.getenv("TOKEN")
CHANNEL_ID = "@testikmatestikoxuestik"

if not TOKEN:
    raise Exception("TOKEN not set")

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

# ================= HEALTH =================

@app.route("/")
def home():
    return "OK"

# ================= WEBHOOK =================

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

# ================= EPUB HELPERS =================

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


def extract_text(epub_path):
    try:
        book = epub.read_epub(epub_path)
        text = ""

        for item in book.get_items():
            if item.get_type() == 9:
                soup = BeautifulSoup(item.get_content(), "html.parser")
                text += soup.get_text() + "\n"

        return text[:2000]
    except:
        return "no text"

# ================= COMMANDS =================

@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(message.chat.id, "бот жив")

@bot.message_handler(commands=["publish"])
def publish_test(message):
    bot.send_message(message.chat.id, "проверка публикации в канал")

    bot.send_message(CHANNEL_ID, "📢 тест пост из бота")

# ================= FILE HANDLER =================

user_data = {}

@bot.message_handler(content_types=["document"])
def handle_docs(message):
    file_info = bot.get_file(message.document.file_id)
    file = bot.download_file(file_info.file_path)

    name = message.document.file_name
    path = f"/tmp/{uuid.uuid4().hex}_{name}"

    with open(path, "wb") as f:
        f.write(file)

    user_data[message.chat.id] = user_data.get(message.chat.id, {})
    data = user_data[message.chat.id]

    # ================= EPUB =================
    if name.endswith(".epub"):
        data["epub"] = path
        data["cover"] = extract_cover(path)

        bot.send_message(message.chat.id, "📚 EPUB получен")

        # 🔥 ПУБЛИКАЦИЯ В КАНАЛ
        if data.get("cover"):
            bot.send_photo(CHANNEL_ID, open(data["cover"], "rb"))

        bot.send_document(CHANNEL_ID, open(path, "rb"), caption="📚 новая книга EPUB")

    # ================= TXT =================
    elif name.endswith(".txt"):
        text = open(path, "r", encoding="utf-8").read()

        bot.send_message(message.chat.id, "📝 TXT получен")

        # 🔥 ПУБЛИКАЦИЯ В КАНАЛ
        bot.send_message(CHANNEL_ID, f"📖 Описание:\n\n{text[:3000]}")

# ================= RUN =================

if __name__ == "__main__":
    app.run()
