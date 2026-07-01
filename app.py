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
# Списки опций
GL_OPTIONS = ["Gemini 3.0", "Gemini 3.1", "Gemini 3.5", "DeepSeek V4 Flash"]
TR_OPTIONS = ["Gemini 3.0", "Gemini 3.1", "Gemini 3.5", "DeepSeek V4 Flash"]
FL_OPTIONS = ["ChatGpt", "DeepSeek", "Нет"]
STATUS_OPTIONS = ["В процессе", "Фулл", "Брошен"]

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

class BookForm(StatesGroup):
    choosing_tools = State()

async def check_and_clear(message: types.Message, state: FSMContext):
    try:
        await asyncio.sleep(30) # Ждем 30 секунд
    except asyncio.CancelledError:
        # Задача была отменена (пользователь нажал "ПУБЛИКАЦИЯ" или "ОТМЕНА")
        return 

    # Если дошли сюда, значит время вышло
    data = await state.get_data()
    # Удаляем файлы
    files_to_remove = [data.get('path'), data.get('cover')] + [i['path'] for i in data.get('extras', [])]
    for p in files_to_remove:
        if p and os.path.exists(p): 
            try:
                os.remove(p)
            except Exception as e:
                logging.error(f"Ошибка удаления файла {p}: {e}")
                
    await state.clear()
    await message.answer("❌ Время вышло, EPUB не получен. Все файлы удалены.")

# --- ИНСТРУМЕНТЫ И КНОПКИ ---
def get_tools_kb(data):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📖 Глоссарий: {data['gl']}", callback_data="change_gl")],
        [InlineKeyboardButton(text=f"🌐 Перевод: {data['tr']}", callback_data="change_tr")],
        [InlineKeyboardButton(text=f"🧹 Фильтр: {data['fl']}", callback_data="change_fl")],
        [InlineKeyboardButton(text=f"📌 Статус: {data['status']}", callback_data="change_status")],
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
                        meta["tags"] = [f"#{re.sub(r'[^a-zA-Zа-яА-Я0-9]+', '_', tag.text.strip()).strip('_')}" for tag in soup.find_all('dc:subject')]
                        
                        d = soup.find('dc:description')
                        if d:
                            # 1. Берем содержимое тега как строку
                            raw_html = str(d)
                            # 2. Создаем ВТОРОЙ, временный суп, который парсит именно HTML
                            html_soup = BeautifulSoup(raw_html, 'html.parser')
                            # 3. Вытаскиваем текст из него
                            meta["desc"] = html_soup.get_text(separator='\n', strip=True)
                        else:
                            meta["desc"] = "Описание отсутствует"


                        #=========================================
                        #d = soup.find('dc:description')
                        #if d and d.text:
                            #inner = BeautifulSoup(d.text, 'html.parser')
                            #meta["desc"] = "\n".join([p.get_text(strip=True) for p in inner.find_all('p') if p.get_text(strip=True)])
                       #============================================ 
                        
                        
                        
                        p = soup.find('dc:publisher')
                        if p and p.text: meta["links"] = [l for l in p.text.split() if l.startswith('http')]
                    break
    except: pass
    return meta

def count_chapters(epub_path):
    try:
        with zipfile.ZipFile(epub_path, 'r') as z:
            # Ищем файл .ncx
            for name in z.namelist():
                if name.endswith('.ncx'):
                    with z.open(name) as f:
                        soup = BeautifulSoup(f.read(), 'xml')
                        # Ищем все точки навигации
                        nav_points = soup.find_all('navPoint')
                        if nav_points:
                            # Берем самый последний navPoint
                            last_point = nav_points[-1]
                            # Пытаемся найти текст внутри него (название главы)
                            text = last_point.find('text').get_text()
                            
                            # Пытаемся вытащить число из названия (например, "Глава 161")
                            numbers = re.findall(r'\d+', text)
                            if numbers:
                                return numbers[-1] # Возвращаем последнее найденное число
                            else:
                                return len(nav_points) # Если чисел нет, вернем просто количество
    except Exception as e:
        logging.error(f"Ошибка подсчета: {e}")
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
    
    # ЕСЛИ EPUB (начало процесса)
    if ext == '.epub' and not data.get('path'):
        # 1. Отменяем старый таймер, если он был
        old_task = data.get('timer_task')
        if old_task and not old_task.done():
            old_task.cancel()

        # 2. Парсим
        meta = await asyncio.to_thread(extract_metadata, path)
        cover = await asyncio.to_thread(extract_cover, path)
        
        # 3. Создаем новый таймер
        new_task = asyncio.create_task(check_and_clear(message, state))
        
        # 4. Сохраняем всё ОДНИМ РАЗОМ
        await state.update_data(
            path=path, 
            name=message.document.file_name, 
            meta=meta, 
            cover=cover, 
            extras=extras, 
            timer_task=new_task, # Сохраняем ID задачи
            gl=GL_OPTIONS[0], tr=TR_OPTIONS[2], fl=FL_OPTIONS[2], status=STATUS_OPTIONS[0]
        )
        await state.set_state(BookForm.choosing_tools)
        
        new_data = await state.get_data()
        await message.answer("✅ EPUB принят. Жду доп. файлы...", reply_markup=get_tools_kb(new_data))
        
    else:
        # Если это просто доп. файл
        extras.append({"path": path, "name": message.document.file_name})
        await state.update_data(extras=extras)
        await message.answer(f"📎 {message.document.file_name} в очереди.")

@dp.callback_query(BookForm.choosing_tools)
async def callbacks(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    
    def get_next(current, options):
        return options[(options.index(current) + 1) % len(options)]

    if call.data == "change_gl": data['gl'] = get_next(data['gl'], GL_OPTIONS)
    elif call.data == "change_tr": data['tr'] = get_next(data['tr'], TR_OPTIONS)
    elif call.data == "change_fl": data['fl'] = get_next(data['fl'], FL_OPTIONS)
    elif call.data == "change_status": data['status'] = get_next(data['status'], STATUS_OPTIONS)
    
    elif call.data == "cancel_all":
        task = data.get("timer_task")
        if task: task.cancel()
        for p in [data.get('path'), data.get('cover')] + [i['path'] for i in data.get('extras', [])]:
            if p and os.path.exists(p): os.remove(p)
        await state.clear()
        await call.message.edit_text("❌ Отменено.")
        return

    elif call.data == "pub_done":
        # ВЫКЛЮЧАЕМ БУДИЛЬНИК
        task = data.get("timer_task")
        if task:
            task.cancel()
        await call.message.edit_text("⏳ Публикация началась... подожди немного.")
        
        try:
            # 1. СОЗДАЕМ ТЕМЫ
            meta = data['meta']
            title_topic = meta.get('titles', ['Новая книга'])[0][:128]
            
            for gid in [GROUP_USERNAME, os.getenv("GROUP_USERNAME_2")]:
                if not gid: continue
                topic = await bot.create_forum_topic(chat_id=gid, name=title_topic, icon_color=random.choice([0x6FB9F0, 0xFFD67E, 0xCB86DB, 0x8EEE98, 0xFF93B2, 0xFB6F5F]))
                thread_id = topic.message_thread_id

                # 2. ПУБЛИКАЦИЯ В ТЕМУ
                gl = data.get('gl', GL_OPTIONS[0])
                tr = data.get('tr', TR_OPTIONS[0])
                fl = data.get('fl', FL_OPTIONS[0])
                
                icons = ["🏴‍☠️", "🇬🇧", "🌐"]
                post_text = ""
                for i, title in enumerate(meta.get('titles', [])):
                    icon = icons[i] if i < len(icons) else "🔹"
                    post_text += f"{icon} {escape(title)}\n"
                
                chapters = await asyncio.to_thread(count_chapters, data['path'])
                status = data.get('status', "В процессе")
               
                post_text += f"\n✍️ Автор: {escape(meta.get('author', '?'))}\n📊 Глав: {escape(str(chapters))}\n📌 Статус: <b>{escape(status)}</b>"
                if meta.get('tags'): post_text += f"\n\n🏷 {' '.join(meta['tags'])}"
             
                post_text += f"\n\n📖 <b>Описание:</b>\n<blockquote expandable>{escape(meta.get('desc', 'Описание отсутствует'))}</blockquote>"
                if meta.get('links'): post_text += f"\n\n🔗 {escape(meta['links'][0])}"
                
                if data.get('cover') and os.path.exists(data['cover']):
                    await bot.send_photo(gid, photo=FSInputFile(data['cover']), message_thread_id=thread_id)
                
                await bot.send_message(gid, post_text, message_thread_id=thread_id, link_preview_options=LinkPreviewOptions(is_disabled=True))
                
                cap = f"🤖 Глоссарий: {escape(gl)}\n🤖 Перевод: {escape(tr)}\n🧹 Фильтр: {escape(fl)}"
                
                await bot.send_document(gid, document=FSInputFile(data['path'], filename=data['name']), caption=cap, message_thread_id=thread_id)
                
                for item in data.get('extras', []):
                    await bot.send_document(gid, document=FSInputFile(item['path'], filename=item['name']), message_thread_id=thread_id)
                
            await call.message.edit_text("✅ Опубликовано в обе темы!")
            
        except Exception as e:
            logging.error(f"Ошибка при публикации: {e}")
            await call.answer("❌ Ошибка публикации", show_alert=True)
            return
        finally:
            all_files = [data.get('path'), data.get('cover')] + [i['path'] for i in data.get('extras', [])]
            for p in all_files:
                if p and os.path.exists(p): os.remove(p)
            await state.clear()
            return

    # ОБНОВЛЕНИЕ ДАННЫХ (если нажали кнопку смены инструмента)
    await state.update_data(data)
    await call.message.edit_reply_markup(reply_markup=get_tools_kb(data))
    await call.answer()

if __name__ == "__main__":
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
    app = web.Application()
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    web.run_app(app, port=int(os.environ.get("PORT", 8080)))
