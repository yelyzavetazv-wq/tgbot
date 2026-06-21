import os, uuid, zipfile, re, asyncio, tempfile, logging, random
from html import escape
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, LinkPreviewOptions

load_dotenv()
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
GROUP_USERNAME = os.getenv("GROUP_USERNAME")
ALLOWED_EXTENSIONS = {'.epub', '.pdf', '.txt', '.docx', '.doc', '.fb2', '.mobi'}
TEMP_DIR = tempfile.gettempdir()
GL_OPTIONS = ["Gemini 3.0", "Gemini 3.1", "Gemini 3.5"]
TR_OPTIONS = ["Gemini 3.0", "Gemini 3.1", "Gemini 3.5"]
FL_OPTIONS = ["ChatGpt", "DeepSeek", "Нет"]

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

class BookForm(StatesGroup):
    choosing_tools = State()

async def check_and_clear(message: types.Message, state: FSMContext):
    try:
        await asyncio.sleep(30)
    except asyncio.CancelledError:
        return # Если мы выключили будильник сами, просто уходим и молчим
    data = await state.get_data()
    # Если за 30 сек так и не пришел EPUB (нет ключа 'path')
    if not data.get('path'):
        files_to_remove = [i['path'] for i in data.get('extras', [])]
        for p in files_to_remove:
            if p and os.path.exists(p): os.remove(p)
        await state.clear()
        await message.answer("❌ Время вышло, EPUB не получен. Все файлы удалены.")

# --- ИНСТРУМЕНТЫ И КНОПКИ ---
def get_tools_kb(gl, tr, fl):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📖 Глоссарий: {gl}", callback_data="change_gl")],
        [InlineKeyboardButton(text=f"🌐 Перевод: {tr}", callback_data="change_tr")],
        [InlineKeyboardButton(text=f"🧹 Фильтр: {fl}", callback_data="change_fl")],
        [InlineKeyboardButton(text="✅ ПУБЛИКАЦИЯ", callback_data="pub_done")],
        [InlineKeyboardButton(text="❌ ОТМЕНА", callback_data="cancel_all")]
    ])

# --- ПАРСИНГ ---
def extract_cover(epub_path):
    try:
        with zipfile.ZipFile(epub_path, 'r') as z:
            cover_filename = None
            if 'titlepage.xhtml' in z.namelist():
                with z.open('titlepage.xhtml') as f:
                    soup = BeautifulSoup(f.read(), 'xml')
                    img = soup.find('image')
                    if img and img.has_attr('xlink:href'): cover_filename = img['xlink:href']
            
            if not cover_filename:
                for name in z.namelist():
                    if name.lower().endswith(('.jpg', '.jpeg', '.png')):
                        cover_filename = name; break
            
            if cover_filename:
                for name in z.namelist():
                    if name.endswith(cover_filename):
                        path = os.path.join(TEMP_DIR, f"{uuid.uuid4().hex}.jpg")
                        with open(path, "wb") as f: f.write(z.read(name))
                        return path
    except: pass
    return None

def extract_metadata(epub_path):
    meta = {"titles": [], "author": "?", "tags": [], "links": [], "desc": "Описание отсутствует"}
    try:
        with zipfile.ZipFile(epub_path, 'r') as z:
            for name in z.namelist():
                if name.endswith('.opf'):
                    with z.open(name) as f:
                        soup = BeautifulSoup(f.read(), 'xml')
                        t = soup.find('dc:title')
                        if t and t.text: meta["titles"] = [p.strip() for p in t.text.split('/') if p.strip()]
                        c = soup.find('dc:creator')
                        if c and c.text.strip(): meta["author"] = c.text.strip()
                        meta["tags"] = [f"#{re.sub(r'[^a-zA-Zа-яА-Я0-9]', '', tag.text.strip())}" for tag in soup.find_all('dc:subject')]
                        d = soup.find('dc:description')
                        if d and d.text:
                            inner = BeautifulSoup(d.text, 'html.parser')
                            meta["desc"] = "\n".join([p.get_text(strip=True) for p in inner.find_all('p') if p.get_text(strip=True)])
                        p = soup.find('dc:publisher')
                        if p and p.text: meta["links"] = [l for l in p.text.split() if l.startswith('http')]
                    break
    except: pass
    return meta

def count_chapters(epub_path):
    try:
        with zipfile.ZipFile(epub_path, 'r') as z:
            for name in z.namelist():
                if name.endswith('.ncx'):
                    with z.open(name) as f:
                        soup = BeautifulSoup(f.read(), 'xml')
                        nav_points = soup.find_all('navPoint')
                        return len(nav_points) if nav_points else "?"
    except: pass
    return "?"

# --- ХЕНДЛЕРЫ ---
@dp.message(Command("start"))
async def start(message: types.Message):
    if message.chat.type == "private": await message.answer("📚 Отправь .epub файл.")

@dp.message(F.document)
async def handle_docs(message: types.Message, state: FSMContext):
    if message.chat.type != "private": return
    
    if message.document.file_size > 20 * 1024 * 1024:
        return await message.answer("❌ Файл больше 20 МБ.")

    ext = os.path.splitext(message.document.file_name or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return await message.answer("❌ Формат не поддерживается.")

    path = os.path.join(TEMP_DIR, f"{uuid.uuid4()}{ext}")
    await bot.download(message.document, destination=path)
    
    data = await state.get_data()
    extras = data.get('extras', [])
    
    # ЕСЛИ EPUB - сохраняем метаданные и запускаем таймер
    if ext == '.epub' and not data.get('path'):
        meta = await asyncio.to_thread(extract_metadata, path)
        cover = await asyncio.to_thread(extract_cover, path)
        
        # Сохраняем всё ОДНИМ РАЗОМ
        await state.update_data(
            path=path, 
            name=message.document.file_name, 
            meta=meta, 
            cover=cover, 
            extras=extras, 
            gl=GL_OPTIONS[0], tr=TR_OPTIONS[0], fl=FL_OPTIONS[0]
        )
        await state.set_state(BookForm.choosing_tools)
        await message.answer("✅ EPUB принят. Жду 30 сек для доп. файлов...", reply_markup=get_tools_kb(GL_OPTIONS[0], TR_OPTIONS[0], FL_OPTIONS[0]))
        task = asyncio.create_task(check_and_clear(message, state))
        await state.update_data(timer_task=task) # Кладем ID будильника в «карман» (состояние)    
    else:
        # Если это просто файл или уже есть EPUB - кидаем в extras
        extras.append({"path": path, "name": message.document.file_name})
        await state.update_data(extras=extras)
        await message.answer(f"📎 {message.document.file_name} в очереди.")

@dp.callback_query(BookForm.choosing_tools)
async def callbacks(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    
    def get_next(current, options):
        # Если текущего нет в списке (например, при первом запуске), берем 0
        if current not in options: return options[0]
        return options[(options.index(current) + 1) % len(options)]

    if call.data == "change_gl": 
        data['gl'] = get_next(data['gl'], GL_OPTIONS)
    elif call.data == "change_tr": 
        data['tr'] = get_next(data['tr'], TR_OPTIONS)
    elif call.data == "change_fl": 
        data['fl'] = get_next(data['fl'], FL_OPTIONS)
    elif call.data == "cancel_all":
        # Останавливаем таймер, если он есть
        task = data.get("timer_task")
        if task:
            task.cancel()
            
        # Удаляем все файлы
        all_files = [data.get('path'), data.get('cover')] + [i['path'] for i in data.get('extras', [])]
        for p in all_files:
            if p and os.path.exists(p): os.remove(p)
            
        await state.clear()
        await call.message.edit_text("❌ Операция отменена. Файлы удалены.")
        return
elif call.data == "pub_done":
        # ВЫКЛЮЧАЕМ БУДИЛЬНИК
        task = data.get("timer_task")
        if task: task.cancel()
        
        try:
            # 1. СОЗДАЕМ ТЕМУ
            meta = data['meta']
            title_topic = meta.get('titles', ['Новая книга'])[0][:128]
            
            forum_topic = await bot.create_forum_topic(
                chat_id=GROUP_USERNAME,
                name=title_topic,
                icon_color=random.choice([0x6FB9F0, 0xFFD67E, 0xCB86DB, 0x8EEE98, 0xFF93B2, 0xFB6F5F])
            )
            thread_id = forum_topic.message_thread_id

            # 2. ПУБЛИКАЦИЯ В ТЕМУ
            gl = data.get('gl', GL_OPTIONS[0])
            tr = data.get('tr', TR_OPTIONS[0])
            fl = data.get('fl', FL_OPTIONS[0])
            
            # (Твой код формирования post_text остается прежним)
            icons = ["🏴‍☠️", "🇬🇧", "🌐"]
            post_text = ""
            for i, title in enumerate(meta.get('titles', [])):
                icon = icons[i] if i < len(icons) else "🔹"
                post_text += f"{icon} {escape(title)}\n"
            
            chapters = await asyncio.to_thread(count_chapters, data['path'])
            post_text += f"\n✍️ Автор: {escape(meta.get('author', '?'))}\n📊 Глав: {escape(str(chapters))}"
            if meta.get('tags'): post_text += f"\n\n🏷 {' '.join(meta['tags'])}"
            post_text += f"\n\n📖 <b>Описание:</b>\n<blockquote expandable>{escape(meta.get('desc', 'Описание отсутствует'))}</blockquote>"
            if meta.get('links'): post_text += f"\n\n🔗 {escape(meta['links'][0])}"
            
            # Отправка фото
            if data.get('cover') and os.path.exists(data['cover']):
                await bot.send_photo(GROUP_USERNAME, photo=FSInputFile(data['cover']), message_thread_id=thread_id)
            
            # Отправка текста
            await bot.send_message(GROUP_USERNAME, post_text, message_thread_id=thread_id, link_preview_options=LinkPreviewOptions(is_disabled=True))
            
            # Отправка EPUB
            cap = f"🤖 Глоссарий: {escape(gl)}\n🤖 Перевод: {escape(tr)}\n🧹 Фильтр: {escape(fl)}"
            await bot.send_document(GROUP_USERNAME, document=FSInputFile(data['path'], filename=data['name']), caption=cap, message_thread_id=thread_id)
            
            # Отправка доп. файлов
            for item in data.get('extras', []):
                await bot.send_document(GROUP_USERNAME, document=FSInputFile(item['path'], filename=item['name']), message_thread_id=thread_id)
            
            await call.message.edit_text("✅ Опубликовано в тему!")
            return

    # ОБНОВЛЕНИЕ ДАННЫХ (если нажали кнопку смены инструмента)
    await state.update_data(data)
    await call.message.edit_reply_markup(reply_markup=get_tools_kb(data['gl'], data['tr'], data['fl']))
    await call.answer()

if __name__ == "__main__":
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
    app = web.Application()
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    web.run_app(app, port=int(os.environ.get("PORT", 8080)))
