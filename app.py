from flask import Flask, request
import telebot
import os

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

# ================= TELEGRAM WEBHOOK =================

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        json_str = request.get_data().decode("utf-8")

        update = telebot.types.Update.de_json(json_str)

        # 🔥 ВАЖНО — ЭТО ПРАВИЛЬНЫЙ СПОСОБ
        bot.process_new_updates([update])

    except Exception as e:
        print("WEBHOOK ERROR:", e)

    return "OK", 200


# ================= SIMPLE TEST HANDLERS =================

@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(message.chat.id, "бот жив")

@bot.message_handler(content_types=["document"])
def doc(message):
    bot.send_message(message.chat.id, "файл получен")

# ================= RUN =================

if __name__ == "__main__":
    app.run()
