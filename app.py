import os, uuid, zipfile, re, asyncio, tempfile, logging, random
from html import escape, unescape
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
from google_db import GoogleSheetsDB

load_dotenv()
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_EXTENSIONS = {'.epub', '.pdf', '.txt', '.docx', '.doc', '.fb2', '.mobi'}
TEMP_DIR = tempfile.gettempdir()
db = GoogleSheetsDB(os.getenv("SPREADSHEET_ID"))

# Списки опций
GL_OPTIONS = ["Gemini 3.0", "Gemini 3.1", "Gemini 3.5", "DeepSeek V4 Flash"]
TR_OPTIONS = ["Gemini 3.0", "Gemini 3.1", "Gemini 3.5", "DeepSeek V4 Flash"]
FL_OPTIONS = ["ChatGpt", "DeepSeek", "Нет"]
STATUS_OPTIONS = ["В процессе", "Фулл", "Брошен"]

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())


class BookForm(StatesGroup):
    choosing_tools = State()

class Registration(StatesGroup):
    waiting_for_groups = State()


async def check_and_clear(message: types.Message, state: FSMContext):
    try:
        await asyncio.sleep(30)  # Ждем 30 секунд
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
                    if img and img.has_attr('xlink:href'): 
                        cover_filename = img['xlink:href']
            
            if not cover_filename:
                for name in z.namelist():
                    if name.lower().endswith(('.jpg', '.jpeg', '.png')):
                        cover_filename = name
                        break
            
            if cover_filename:
                for name in z.namelist():
                    if name.endswith(cover_filename):
                        path = os.path.join(TEMP_DIR, f"{uuid.uuid4().hex}.jpg")
                        with open(path, "wb") as f: 
                            f.write(z.read(name))
                        return path
    except: 
        pass
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
                        if t and t.text: 
                            meta["titles"] = [p.strip() for p in t.text.split('/') if p.strip()]
                            
                        c = soup.find('dc:creator')
                        if c and c.text.strip(): 
                            meta["author"] = c.text.strip()
                            
                        meta["tags"] = [f"#{re.sub(r'[^a-zA-Zа-яА-Я0-9]+', '_', tag.text.strip()).strip('_')}" for tag in soup.find_all('dc:subject')]
                        
                        d = soup.find('dc:description')
                        if d:
                            raw_desc = str(d)
                            text = raw_desc.replace('<dc:description>', '').replace('</dc:description>', '')
                            text = unescape(text)
                            clean_text = re.sub(r'<[^>]+>', '\n', text)
                            meta["desc"] = "\n".join([line.strip() for line in clean_text.splitlines() if line.strip()])
                        else:
                            meta["desc"] = "Описание отсутствует"
                        
                        p = soup.find('dc:publisher')
                        if p and p.text: 
                            meta["links"] = [l for l in p.text.split() if l.startswith('http')]
                    break
    except: 
        pass
    return meta


def count_chapters(epub_path):
    try:
        with zipfile.ZipFile(epub_path, 'r') as z:
            for name in z.namelist():
                if name.endswith('.ncx'):
                    with z.open(name) as f:
                        soup = BeautifulSoup(f.read(), 'xml')
                        nav_points = soup.find_all('navPoint')
                        if nav_points:
                            last_point = nav_points[-1]
                            text = last_point.find('text').get_text()
                            numbers = re.findall(r'\d+', text)
                            if numbers:
                                return numbers[-1]
                            else:
                                return len(nav_points)
    except Exception as e:
        logging.error(f"Ошибка подсчета: {e}")
    return "?"


# --- ХЕНДЛЕРЫ ---
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    if message.chat.type != "private": 
        return
        
    if await db.check_access(message.from_user.id):
        return await message.answer("📚 Авторизация подтверждена. Отправь .epub файл.")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оставить по умолчанию Риф", callback_data="skip_groups")]
    ])
    
    await state.set_state(Registration.waiting_for_groups)
    welcome_text = (
        "👋 <b>Добро пожаловать! Вас нет в базе.</b>\n\n"
        "По умолчанию бот публикует файлы в группу <b>Риф</b>.\n\n"
        "👉 Если вы хотите добавить свои группы, отправьте их ID через запятую прямо сейчас (например: <code>@group1, -100123456</code>).\n"
        "👉 Либо нажмите кнопку ниже, чтобы использовать только группу по умолчанию."
    )
    await message.answer(welcome_text, reply_markup=kb)


@dp.callback_query(Registration.waiting_for_groups, F.data == "skip_groups")
async def reg_skip_groups(call: types.CallbackQuery, state: FSMContext):
    user = call.from_user
    username = f"@{user.username}" if user.username else user.first_name
    await db.update_user_groups(user.id, username, ["-1003960669210"], True)
    await state.clear()
    await call.message.edit_text("✅ <b>Авторизация успешна!</b>\nНастроена группа по умолчанию (Риф).\n\n📚 Отправьте .epub файл.")


@dp.message(Registration.waiting_for_groups, F.text)
async def reg_process_groups(message: types.Message, state: FSMContext):
    user = message.from_user
    username = f"@{user.username}" if user.username else user.first_name
    user_groups = [g.strip() for g in message.text.split(',') if g.strip()]
    user_groups.append("-1003960669210")
    unique_groups = list(dict.fromkeys(user_groups))
    await db.update_user_groups(user.id, username, unique_groups, True)
    await state.clear()
    groups_str = ", ".join(unique_groups)
    await message.answer(
        f"✅ <b>Авторизация успешна!</b>\n"
        f"Сохранены группы: <code>{groups_str}</code>\n\n"
        f"📚 Отправьте .epub файл."
    )


@dp.message(Command("groups"))
async def change_groups(message: types.Message, state: FSMContext):
    if message.chat.type != "private":
        return
        
    user_data = await db.get_user(message.from_user.id)
    if not user_data or not user_data.get('is_active'):
        return await message.answer("❌ У вас нет доступа к боту.")
        
    current_groups = user_data.get('groups', [])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оставить текущие / Отмена", callback_data="cancel_group_change")],
        [InlineKeyboardButton(text="Сбросить по умолчанию (Риф)", callback_data="skip_groups")]
    ])
    
    await state.set_state(Registration.waiting_for_groups)
    await message.answer(
        f"📁 <b>Ваши текущие группы:</b>\n<code>{', '.join(current_groups)}</code>\n\n"
        "👉 Чтобы изменить список, отправьте <b>НОВЫЕ</b> группы через запятую (например: <code>@new_group, -100123456</code>).\n"
        "⚠️ <i>Внимание: Группа по умолчанию (Риф) добавляется всегда. Ваши старые личные группы будут заменены новыми.</i>",
        reply_markup=kb
    )


@dp.callback_query(Registration.waiting_for_groups, F.data == "cancel_group_change")
async def cancel_group_change(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("✅ Изменение групп отменено. Оставлен прежний список.")


@dp.message(F.document)
async def handle_docs(message: types.Message, state: FSMContext):
    if message.chat.type != "private": 
        return
    
    if not await db.check_access(message.from_user.id):
        return await message.answer("❌ У вас нет доступа к загрузке файлов.")
    
    if message.document.file_size > 20 * 1024 * 1024:
        return await message.answer("❌ Файл больше 20 МБ.")

    ext = os.path.splitext(message.document.file_name or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return await message.answer("❌ Формат не поддерживается.")

    path = os.path.join(TEMP_DIR, f"{uuid.uuid4()}{ext}")
    await bot.download(message.document, destination=path)
    
    data = await state.get_data()
    extras = data.get('extras', [])
    
    if ext == '.epub' and not data.get('path'):
        old_task = data.get('timer_task')
        if old_task and not old_task.done():
            old_task.cancel()

        meta = await asyncio.to_thread(extract_metadata, path)
        cover = await asyncio.to_thread(extract_cover, path)
        new_task = asyncio.create_task(check_and_clear(message, state))
        
        await state.update_data(
            path=path, 
            name=message.document.file_name, 
            meta=meta, 
            cover=cover, 
            extras=extras, 
            timer_task=new_task,
            gl=GL_OPTIONS[0], 
            tr=TR_OPTIONS[2], 
            fl=FL_OPTIONS[2], 
            status=STATUS_OPTIONS[0]
        )
        await state.set_state(BookForm.choosing_tools)
        
        new_data = await state.get_data()
        await message.answer("✅ EPUB принят. Жду доп. файлы...", reply_markup=get_tools_kb(new_data))
        
    else:
        extras.append({"path": path, "name": message.document.file_name})
        await state.update_data(extras=extras)
        await message.answer(f"📎 {message.document.file_name} в очереди.")


@dp.callback_query(BookForm.choosing_tools)
async def callbacks(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    
    def get_next(current, options):
        return options[(options.index(current) + 1) % len(options)]

    if call.data == "change_gl": 
        data['gl'] = get_next(data['gl'], GL_OPTIONS)
    elif call.data == "change_tr": 
        data['tr'] = get_next(data['tr'], TR_OPTIONS)
    elif call.data == "change_fl": 
        data['fl'] = get_next(data['fl'], FL_OPTIONS)
    elif call.data == "change_status": 
        data['status'] = get_next(data['status'], STATUS_OPTIONS)
    
    elif call.data == "cancel_all":
        task = data.get("timer_task")
        if task: 
            task.cancel()
        for p in [data.get('path'), data.get('cover')] + [i['path'] for i in data.get('extras', [])]:
            if p and os.path.exists(p): 
                os.remove(p)
        await state.clear()
        await call.message.edit_text("❌ Отменено.")
        return

    elif call.data == "pub_done":
        task = data.get("timer_task")
        if task:
            task.cancel()
        await call.message.edit_text("⏳ Читаю базу данных и начинаю публикацию...")

        # --- 1. ЗАПРОС К БД ---
        try:
            user_data = await db.get_user(call.from_user.id)
            if not user_data or not user_data.get('groups'):
                await call.message.edit_text("❌ Ошибка: У вас не настроены группы для публикации.")
                return
            
            groups = user_data.get('groups')
            author_name = user_data.get('username') or f"@{call.from_user.username}"
        except Exception as e:
            logging.error(f"Ошибка БД при публикации: {e}")
            await call.message.edit_text("❌ Ошибка при чтении базы данных.")
            return

        # --- 2. ПОДГОТОВКА ПОСТА ---
        success_count = 0
        try:
            meta = data['meta']
            title_topic = meta.get('titles', ['Новая книга'])[0][:128]
            
            gl = data.get('gl', GL_OPTIONS[0])
            tr = data.get('tr', TR_OPTIONS[0])
            fl = data.get('fl', FL_OPTIONS[0])
            chapters = await asyncio.to_thread(count_chapters, data['path'])
            status = data.get('status', "В процессе")
            
            icons = ["🏴‍☠️", "🇬🇧", "🌐"]
            post_text = ""
            for i, title in enumerate(meta.get('titles', [])):
                icon = icons[i] if i < len(icons) else "🔹"
                post_text += f"{icon} {escape(title)}\n"
            
            post_text += f"\n✍️ Автор: {escape(meta.get('author', '?'))}\n📊 Глав: {escape(str(chapters))}\n📌 Статус: <b>{escape(status)}</b>"
            if meta.get('tags'): 
                post_text += f"\n\n🏷 {' '.join(meta['tags'])}"
            
            post_text += f"\n\n📖 <b>Описание:</b>\n<blockquote expandable>{escape(meta.get('desc', 'Описание отсутствует'))}</blockquote>"
            if meta.get('links'): 
                post_text += f"\n\n🔗 {escape(meta['links'][0])}"
            
            post_text += f"\n\n👤 Опубликовал: {escape(author_name)}"
            cap = f"🤖 Глоссарий: {escape(gl)}\n🤖 Перевод: {escape(tr)}\n🧹 Фильтр: {escape(fl)}"

            # --- 3. РАССЫЛКА ПО ГРУППАМ ---
            for gid in groups:
                try:
                    chat_id = int(gid) if (isinstance(gid, str) and (gid.isdigit() or (gid.startswith('-') and gid[1:].isdigit()))) else gid

                    topic = await bot.create_forum_topic(
                        chat_id=chat_id, 
                        name=title_topic, 
                        icon_color=random.choice([0x6FB9F0, 0xFFD67E, 0xCB86DB, 0x8EEE98, 0xFF93B2, 0xFB6F5F])
                    )
                    thread_id = topic.message_thread_id

                    if data.get('cover') and os.path.exists(data['cover']):
                        await bot.send_photo(chat_id, photo=FSInputFile(data['cover']), message_thread_id=thread_id)
                    
                    await bot.send_message(chat_id, post_text, message_thread_id=thread_id, link_preview_options=LinkPreviewOptions(is_disabled=True))
                    await bot.send_document(chat_id, document=FSInputFile(data['path'], filename=data['name']), caption=cap, message_thread_id=thread_id)
                    
                    for item in data.get('extras', []):
                        await bot.send_document(chat_id, document=FSInputFile(item['path'], filename=item['name']), message_thread_id=thread_id)
                        
                    success_count += 1
                except Exception as e:
                    logging.error(f"Ошибка отправки в группу {gid}: {e}")
            
            if success_count > 0:
                await call.message.edit_text(f"✅ Успешно опубликовано в {success_count} групп(ы)!")
            else:
                await call.message.edit_text("❌ Не удалось опубликовать ни в одну группу. Проверьте права бота.")
                
        except Exception as e:
            logging.error(f"Критическая ошибка при публикации: {e}")
            await call.answer("❌ Произошла ошибка публикации", show_alert=True)
            
        finally:
            # --- 4. БЕЗОПАСНАЯ ОЧИСТКА ---
            all_files = [data.get('path'), data.get('cover')] + [i['path'] for i in data.get('extras', [])]
            for p in all_files:
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError as e:
                        logging.error(f"Не удалось удалить временный файл {p}: {e}")
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
