from flask import Flask, request
import telebot
import os

TOKEN = os.environ.get("TELEGRAM_TOKEN", "TOKEN = "8653759634:AAFl0owDYVGcOecOz06u2cQW81PBUlaF0EU"")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@my_ff_translate")

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is running!"

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(data)
        if update and update.message:
            chat_id = update.message.chat.id
            if update.message.text == '/start':
                bot.send_message(chat_id, "✅ Бот работает на Render!")
        return 'OK', 200
    except Exception as e:
        return 'OK', 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
