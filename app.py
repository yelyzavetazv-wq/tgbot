import os, uuid, zipfile, re, asyncio, tempfile
from ebooklib import epub, ITEM_COVER, ITEM_IMAGE
from bs4 import BeautifulSoup
from html import escape
from dotenv import load_dotenv
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
GROUP_USERNAME = os.getenv("GROUP_USERNAME")
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

# --- УТИЛИТЫ ---
def secure_filename(name): return re.sub(r'[^\w\-.]', '_', name)
def clean_hashtag(tag): return re.sub(r'[^\w]+', '_', tag).strip('_')

def extract_metadata_from_epub(epub_path):
    result = {"title_ru": "", "title_en": "", "author": "", "annotation": "Описание отсутствует", "tags": []}
    try:
        with zipfile.ZipFile(epub_path, 'r') as z:
            for name in z.namelist():
                if name.endswith('.opf'):
                    with z.open(name) as f:
                        soup = BeautifulSoup(f.read(), 'xml')
                        t = soup.find('dc:title')
                        if t and t.text:
                            parts = [p.strip() for p in t.text.split('/')]
                            result["title_ru"] = parts[0]
                            if len(parts) > 1: result["title_en"] = parts[1]
                        c = soup.find('dc:creator')
                        if c: result["author"] = c.text.strip()
                        desc = soup.find('dc:description')
                        if desc and desc.text:
                            inner = BeautifulSoup(desc.text, 'html.parser')
                            result["annotation"] = inner.get_text(separator="\n", strip=True)[:2000]
                        for s in soup.find_all('dc:subject'):
                            if s.text: result["tags"].append(s.text.strip())
    except: pass
    return result

# --- ФОРМАТИРОВАНИЕ ---
def format_text(metadata, status):
    text = f"🏴‍☠️ <b>{escape(metadata.get('title_ru') or 'Без названия')}</b>\n"
    if metadata.get('title_en'): text += f"🇬🇧 {escape(metadata['title_en'])}\n"
    text += f"\n✍️ Автор: {escape(metadata.get('author') or 'Неизвестен')}\n"
    text += f"📌 Статус: {escape(status)}\n"
    if metadata['tags']:
        text += f"🏷️ Теги: {', '.join([f'#{clean_hashtag(t)}' for t in metadata['tags']])}\n"
    text += f"\n📖 <b>Описание:</b>\n<blockquote expandable>{escape(metadata['annotation'])}</blockquote>"
    return text

# --- ПУБЛИКАЦИЯ ---
async def publish_to_forum(chat_id: int, state: FSMContext):
    data = await state.get_data()
    meta = data['metadata']
    
    # 1. Текст описания
    await bot.send_message(GROUP_USERNAME, format_text(meta, data['status']))
    
    # 2. Файл с инструментами в подписи
    caption = f"🤖 Глоссарий: {escape(data['glossary'])}\n🤖 Перевод: {escape(data['translation'])}\n🧹 Фильтр: {escape(data['filter'])}"
    await bot.send_document(
        GROUP_USERNAME, 
        FSInputFile(data['epub'], filename=secure_filename(data['name'])),
        caption=caption
    )
    
    await state.clear()
    if os.path.exists(data['epub']): os.remove(data['epub'])

# --- ХЕНДЛЕРЫ ---
@dp.message(F.document)
async def handle_docs(message: types.Message, state: FSMContext):
    if not message.document.file_name.endswith(".epub"): return
    path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}.epub")
    await bot.download(message.document, destination=path)
    meta = await asyncio.to_thread(extract_metadata_from_epub, path)
    await state.update_data(epub=path, name=message.document.file_name, metadata=meta)
    await message.answer("Файл принят. Выберите Глоссарий:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Gemini 3.5", callback_data="gl:Gemini 3.5")],
        [InlineKeyboardButton(text="Gemini 3", callback_data="gl:Gemini 3")]
    ]))

@dp.callback_query(F.data.startswith("gl:"))
async def set_gl(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(glossary=call.data.split(":")[1])
    await call.message.edit_text("Выберите Перевод:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Gemini 3.5", callback_data="tr:Gemini 3.5")],
        [InlineKeyboardButton(text="Gemini 3", callback_data="tr:Gemini 3")]
    ]))

@dp.callback_query(F.data.startswith("tr:"))
async def set_tr(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(translation=call.data.split(":")[1])
    await call.message.edit_text("Выберите Фильтр:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ChatGPT", callback_data="fl:ChatGPT")],
        [InlineKeyboardButton(text="Нет", callback_data="fl:none")]
    ]))

@dp.callback_query(F.data.startswith("fl:"))
async def set_fl(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(filter=call.data.split(":")[1], status="в процессе")
    await publish_to_forum(call.message.chat.id, state)
    await call.message.edit_text("✅ Опубликовано!")

if __name__ == "__main__":
    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    web.run_app(app, port=int(os.environ.get("PORT", 8080)))
