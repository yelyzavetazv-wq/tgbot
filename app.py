import os, uuid, zipfile, re, asyncio, tempfile, logging
from html import escape
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile

load_dotenv()
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
GROUP_USERNAME = os.getenv("GROUP_USERNAME")
ALLOWED_EXTENSIONS = {'.epub'}

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class BookForm(StatesGroup):
    choosing_tools = State()

def get_tools_kb(gl, tr, fl):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📖 Глоссарий: {gl}", callback_data="change_gl")],
        [InlineKeyboardButton(text=f"🌐 Перевод: {tr}", callback_data="change_tr")],
        [InlineKeyboardButton(text=f"🧹 Фильтр: {fl}", callback_data="change_fl")],
        [InlineKeyboardButton(text="✅ ПУБЛИКАЦИЯ", callback_data="pub_done")]
    ])

def extract_metadata(epub_path):
    meta = {"title_ru": "Без названия", "title_en": "", "author": "", "tags": [], "links": [], "desc": "Описание отсутствует"}
    try:
        with zipfile.ZipFile(epub_path, 'r') as z:
            for name in z.namelist():
                if name.endswith('.opf'):
                    with z.open(name) as f:
                        soup = BeautifulSoup(f.read(), 'xml')
                        t = soup.find('dc:title')
                        if t and t.text:
                            parts = [p.strip() for p in t.text.split('/')]
                            meta["title_ru"] = parts[0]
                            if len(parts) > 1: meta["title_en"] = parts[1]
                        c = soup.find('dc:creator')
                        if c: meta["author"] = c.text.strip()
                        meta["tags"] = [f"#{re.sub(r'[^a-zA-Zа-яА-Я0-9]', '', tag.text.strip())}" for tag in soup.find_all('dc:subject')]
                        d = soup.find('dc:description')
                        if d and d.text:
                            inner = BeautifulSoup(d.text, 'html.parser')
                            meta["desc"] = inner.get_text(separator="\n", strip=True)
                        p = soup.find('dc:publisher')
                        if p and p.text:
                            meta["links"] = [l for l in p.text.split() if l.startswith('http')]
                    break
    except: pass
    return meta

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("📚 Отправь .epub файл.")

@dp.message(F.document)
async def handle_docs(message: types.Message, state: FSMContext):
    ext = os.path.splitext(message.document.file_name or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return await message.answer("❌ Только .epub")
    
    path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}.epub")
    await bot.download(message.document, destination=path)
    meta = await asyncio.to_thread(extract_metadata, path)
    
    await state.update_data(path=path, name=message.document.file_name, meta=meta, gl="Gemini 3", tr="Gemini 3.5", fl="Нет")
    await message.answer(f"✅ {message.document.file_name}\nНастрой инструменты:", reply_markup=get_tools_kb("Gemini 3", "Gemini 3.5", "Нет"))
    await state.set_state(BookForm.choosing_tools)

@dp.callback_query(BookForm.choosing_tools)
async def callbacks(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if call.data == "change_gl": data['gl'] = "GPT-4o" if data['gl'] == "Gemini 3" else "Gemini 3"
    elif call.data == "change_tr": data['tr'] = "DeepL" if data['tr'] == "Gemini 3.5" else "Gemini 3.5"
    elif call.data == "change_fl": data['fl'] = "DeepSeek" if data['fl'] == "Нет" else "Нет"
    elif call.data == "pub_done":
        meta = data['meta']
        # Формирование текста поста
        post_text = f"🏴‍☠️ <b>{escape(meta['title_ru'])}</b>\n"
        if meta['title_en']: post_text += f"🇬🇧 {escape(meta['title_en'])}\n"
        post_text += f"\n✍️ Автор: {escape(meta['author'])}\n\n🏷 {' '.join(meta['tags'])}"
        post_text += f"\n\n📖 <b>Описание:</b>\n<blockquote expandable>{escape(meta['desc'])}</blockquote>"
        if meta['links']: post_text += f"\n\n🔗 {escape(meta['links'][0])}"
        
        # Инструменты в подпись
        cap = f"🤖 Глоссарий: {data['gl']}\n🤖 Перевод: {data['tr']}\n🧹 Фильтр: {data['fl']}"
        
        await bot.send_message(GROUP_USERNAME, post_text, parse_mode="HTML")
        await bot.send_document(GROUP_USERNAME, FSInputFile(data['path'], filename=data['name']), caption=cap, parse_mode="HTML")
        await call.message.edit_text("✅ Опубликовано!")
        if os.path.exists(data['path']): os.remove(data['path'])
        return await state.clear()
        
    await state.update_data(data)
    await call.message.edit_reply_markup(reply_markup=get_tools_kb(data['gl'], data['tr'], data['fl']))

if __name__ == "__main__":
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
    app = web.Application()
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    web.run_app(app, port=int(os.environ.get("PORT", 8080)))
