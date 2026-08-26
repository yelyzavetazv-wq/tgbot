import asyncio
import logging
import os
import random
import re
import tempfile
import uuid
import zipfile
from html import escape, unescape

from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions
from aiogram.utils.keyboard import InlineKeyboardBuilder
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from google_db import GoogleSheetsDB

load_dotenv()
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_EXTENSIONS = {'.epub', '.pdf', '.txt', '.docx', '.doc', '.fb2', '.mobi'}
TEMP_DIR = tempfile.gettempdir()
db = GoogleSheetsDB(os.getenv("SPREADSHEET_ID"))

# ==========================================
# 1. КЛАССЫ СОСТОЯНИЙ И ФИЛЬТРЫ
# ==========================================
class Registration(StatesGroup):
    waiting_for_groups = State()
    waiting_for_role = State()
    waiting_for_presets = State()
    waiting_for_custom = State()

class BookForm(StatesGroup):
    choosing_tools = State()

class IsAdmin(Filter):
    """Кастомный фильтр для проверки прав администратора через БД."""
    async def __call__(self, message: types.Message) -> bool:
        user_data = await db.get_user(message.from_user.id)
        return bool(user_data and user_data.get('is_admin'))

# ==========================================
# 2. ИНИЦИАЛИЗАЦИЯ И КОНСТАНТЫ
# ==========================================
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

STATUS_OPTIONS = ["В процессе", "Фулл", "Брошен"]

# Базовые пресеты для регистрации переводчиков
DEFAULT_PRESETS = {
    "gl": ["Gemini 3.0", "Gemini 3.1", "Gemini 3.5", "Gemini 3.6", "Gemini 3.7", "DeepSeek"],
    "tr": ["Gemini 3.0", "Gemini 3.1", "Gemini 3.5", "Gemini 3.6", "Gemini 3.7", "DeepSeek"],
    "fl": ["ChatGpt", "DeepSeek", "Нет"]
}

# ==========================================
# 3. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ UI
# ==========================================
def get_presets_keyboard(user_prefs: dict) -> InlineKeyboardMarkup:
    """Генерирует компактную клавиатуру с сеткой по 2 кнопки в ряд для категорий настроек."""
    builder = InlineKeyboardBuilder()
    
    categories = {
        "gl": "📚 Глоссарий",
        "tr": "🌐 Перевод",
        "fl": "🧹 Фильтр"
    }
    
    for cat_key, cat_name in categories.items():
        # Заголовок категории на всю ширину
        builder.row(InlineKeyboardButton(text=f"--- {cat_name} ---", callback_data="ignore"))
        
        # Объединяем базовые пресеты с кастомными (без дубликатов)
        user_custom_items = user_prefs.get(cat_key, [])
        all_options = list(dict.fromkeys(DEFAULT_PRESETS[cat_key] + user_custom_items))
        
        # Временный массив для кнопок текущей категории, чтобы выстроить их по 2 в ряд
        category_buttons = []
        for item in all_options:
            mark = "✅" if item in user_prefs.get(cat_key, []) else "❌"
            category_buttons.append(InlineKeyboardButton(text=f"{mark} {item}", callback_data=f"toggle_{cat_key}_{item}"))
        
        # Добавляем кнопки опций парами (по 2 в ряд)
        builder.row(*category_buttons, width=2)
            
        # Кнопка добавления своего идет на всю ширину
        builder.row(InlineKeyboardButton(text="➕ Добавить свое", callback_data=f"add_custom_{cat_key}"))

    # Управляющие кнопки внизу
    builder.row(InlineKeyboardButton(text="💾 СОХРАНИТЬ И ЗАВЕРШИТЬ", callback_data="finish_presets"))
    builder.row(InlineKeyboardButton(text="👤 Стать обычным пользователем", callback_data="demote_to_publisher"))
    return builder.as_markup()

def get_tools_kb(data: dict, is_translator: bool) -> InlineKeyboardMarkup:
    """Динамическая клавиатура в зависимости от роли и настроек юзера."""
    builder = InlineKeyboardBuilder()
    
    if is_translator:
        builder.row(InlineKeyboardButton(text=f"📖 Глоссарий: {data.get('gl', 'Нет')}", callback_data="change_gl"))
        builder.row(InlineKeyboardButton(text=f"🌐 Перевод: {data.get('tr', 'Нет')}", callback_data="change_tr"))
        builder.row(InlineKeyboardButton(text=f"🧹 Фильтр: {data.get('fl', 'Нет')}", callback_data="change_fl"))
        builder.row(InlineKeyboardButton(text=f"📌 Статус: {data.get('status', 'В процессе')}", callback_data="change_status"))
        
    builder.row(InlineKeyboardButton(text="✅ ПУБЛИКАЦИЯ", callback_data="pub_done"))
    builder.row(InlineKeyboardButton(text="❌ ОТМЕНА", callback_data="cancel_all"))
    return builder.as_markup()

async def check_and_clear(message: types.Message, state: FSMContext):
    try:
        await asyncio.sleep(180)  # Таймаут 3 минуты
    except asyncio.CancelledError:
        return 

    # ПРОВЕРКА: удаляем файлы только если юзер всё ещё находится в процессе публикации
    current_state = await state.get_state()
    if current_state == BookForm.choosing_tools.state:
        data = await state.get_data()
        files_to_remove = [data.get('path'), data.get('cover')] + [i['path'] for i in data.get('extras', [])]
        for p in files_to_remove:
            if p and os.path.exists(p): 
                try:
                    os.remove(p)
                except OSError:
                    pass
                    
        await state.clear()
        await message.answer("❌ Время вышло, публикация отменена. Все временные файлы удалены.")

# ==========================================
# 4. ПАРСЕРЫ (ОСТАВЛЕНЫ БЕЗ ИЗМЕНЕНИЙ)
# ==========================================
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

# ==========================================
# 5. ХЕНДЛЕРЫ
# ==========================================
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    if message.chat.type != "private": 
        return
        
    user_data = await db.get_user(message.from_user.id)
    
    if user_data and user_data.get('is_active'):
        return await message.answer("📚 Авторизация подтверждена. Отправь .epub файл.")
        
    if user_data and not user_data.get('is_active'):
        admins = await db.get_all_admins()
        admins_text = ", ".join(admins) if admins else "администраторам"
        return await message.answer(f"❌ Вы заблокированы. Для разблокировки напишите: {admins_text}")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оставить по умолчанию РиФ", callback_data="skip_groups")]
    ])
    
    await state.set_state(Registration.waiting_for_groups)
    welcome_text = (
        "👋 <b>Добро пожаловать! Вас нет в базе.</b>\n\n"
        "По умолчанию бот публикует файлы в группу <b>РиФ</b>.\n\n"
        "👉 Если вы хотите добавить свои группы, отправьте их ID через запятую прямо сейчас (например: <code>@group1, -100123456</code>) (не забудьте пригласить бота в группу и дать ему права Администратора).\n"
        "👉 Либо нажмите кнопку ниже, чтобы использовать только группу по умолчанию."
    )
    await message.answer(welcome_text, reply_markup=kb)

async def ask_for_role(message, state: FSMContext):
    """Вызов меню выбора роли после сохранения групп."""
    await state.set_state(Registration.waiting_for_role)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Я переводчик (настроить меню)", callback_data="role_translator")],
        [InlineKeyboardButton(text="📖 Просто публикую (без настроек)", callback_data="role_publisher")]
    ])
    await message.answer("Отлично! Теперь укажите вашу роль в проекте:", reply_markup=kb)

@dp.callback_query(Registration.waiting_for_groups, F.data == "skip_groups")
async def reg_skip_groups(call: types.CallbackQuery, state: FSMContext):
    user = call.from_user
    username = f"@{user.username}" if user.username else user.first_name
    await db.update_user_groups(user.id, username, ["-1003960669210"], True)
    
    data = await state.get_data()
    if data.get("is_edit_mode"):
        await state.clear()
        await call.message.edit_text("✅ Группы по умолчанию успешно установлены!")
    else:
        await state.update_data(groups=["-1003960669210"])
        await ask_for_role(call.message, state)
        
    await call.answer()

@dp.message(Registration.waiting_for_groups, F.text)
async def reg_process_groups(message: types.Message, state: FSMContext):
    user = message.from_user
    username = f"@{user.username}" if user.username else user.first_name
    
    # Разбиваем введенный текст по запятой и очищаем от пробелов
    raw_groups = [g.strip() for g in message.text.split(',') if g.strip()]
    
    valid_groups = []
    for g in raw_groups:
        # Проверяем формат: либо начинается с @, либо это числовой ID (может начинаться с минуса)
        if g.startswith('@') or (g.lstrip('-').isdigit()):
            valid_groups.append(g)
        else:
            # Если пользователь ввел команду (например, /ban) или некорректный текст
            return await message.answer(
                f"❌ Некорректный формат группы: <code>{escape(g)}</code>.\n"
                "Группа должна начинаться с символа <code>@</code> или быть числовым ID (например: <code>-100123456</code>).\n"
                "Пожалуйста, отправьте список групп заново."
            )
            
    if not valid_groups:
        return await message.answer("❌ Список групп пуст. Пожалуйста, отправьте корректные данные.")

    valid_groups.append("-1003960669210")
    unique_groups = list(dict.fromkeys(valid_groups))
    await db.update_user_groups(user.id, username, unique_groups, True)
    
    data = await state.get_data()
    if data.get("is_edit_mode"):
        await state.clear()
        await message.answer("✅ Новые группы для рассылки успешно сохранены!")
    else:
        await state.update_data(groups=unique_groups)
        await ask_for_role(message, state)

@dp.callback_query(Registration.waiting_for_role, F.data == "role_publisher")
async def process_role_publisher(call: types.CallbackQuery, state: FSMContext):
    """Ветка обычного пользователя: сохраняем профиль и завершаем."""
    user_id = call.from_user.id
    
    await db.update_user_profile(user_id, {"is_translator": False})
    
    await state.clear()
    await call.message.edit_text("✅ Регистрация завершена! Просто отправьте мне .epub файл для публикации.")

@dp.callback_query(Registration.waiting_for_presets, F.data == "demote_to_publisher")
async def process_demote_to_publisher(call: types.CallbackQuery, state: FSMContext):
    """Сброс роли переводчика до обычного пользователя из настроек."""
    user_id = call.from_user.id
    
    await db.update_user_profile(user_id, {"is_translator": False})
    
    await state.clear()
    await call.message.edit_text("✅ Роль изменена. Теперь вы обычный пользователь. Просто отправьте мне .epub файл для публикации.")


@dp.callback_query(Registration.waiting_for_role, F.data == "role_translator")
async def process_role_translator(call: types.CallbackQuery, state: FSMContext):
    """Ветка переводчика: инициализируем профиль и показываем меню."""
    initial_prefs = {"is_translator": True, "gl": [], "tr": [], "fl": [], "status": STATUS_OPTIONS}
    await state.update_data(profile=initial_prefs)
    
    await state.set_state(Registration.waiting_for_presets)
    await call.message.edit_text(
        "⚙️ <b>Настройка личного меню</b>\n\n"
        "Выберите инструменты, которые вы используете. Они будут закреплены за вашими кнопками при публикации:",
        reply_markup=get_presets_keyboard(initial_prefs)
    )

@dp.callback_query(Registration.waiting_for_presets, F.data.startswith("toggle_"))
async def toggle_preset(call: types.CallbackQuery, state: FSMContext):
    """Ставит/снимает галочку для любой категории."""
    _, category, item = call.data.split("_", 2)
    data = await state.get_data()
    profile = data.get("profile", {})
    
    if item in profile.get(category, []):
        profile[category].remove(item)
    else:
        profile.setdefault(category, []).append(item)
        
    await state.update_data(profile=profile)
    await call.message.edit_reply_markup(reply_markup=get_presets_keyboard(profile))

@dp.callback_query(F.data == "ignore")
async def ignore_callback(call: types.CallbackQuery):
    """Глушит индикатор загрузки при нажатии на информационные кнопки."""
    await call.answer()

@dp.callback_query(Registration.waiting_for_presets, F.data.startswith("add_custom_"))
async def add_custom_item(call: types.CallbackQuery, state: FSMContext):
    """Режим ожидания текста для новой кастомной настройки."""
    category = call.data.split("_")[2]  # Получаем gl, tr или fl
    await state.update_data(custom_category=category)
    await state.set_state(Registration.waiting_for_custom)
    
    cat_names = {"gl": "глоссария", "tr": "перевода", "fl": "фильтра"}
    await call.message.edit_text(
        f"✍️ <b>Добавление своего {cat_names.get(category, 'инструмента')}</b>\n\n"
        "Напишите название в одном сообщении:"
    )

@dp.message(Registration.waiting_for_custom, F.text)
async def process_custom_item(message: types.Message, state: FSMContext):
    data = await state.get_data()
    profile = data.get("profile", {})
    category = data.get("custom_category", "gl")
    
    raw_text = message.text.strip()
    # Универсальная обрезка для любого инструмента (защита от лимита 64 байт в callback_data)
    custom_item = raw_text if len(raw_text) <= 30 else raw_text[:30] + "..."
    
    if category not in profile:
        profile[category] = []
    
    if custom_item not in profile[category]:
        profile[category].append(custom_item)
        
    await state.update_data(profile=profile)
    await state.set_state(Registration.waiting_for_presets)
    await message.answer(
        "✅ Успешно добавлено! Выберите инструменты:",
        reply_markup=get_presets_keyboard(profile)
    )

@dp.callback_query(Registration.waiting_for_presets, F.data == "finish_presets")
async def finish_presets(call: types.CallbackQuery, state: FSMContext):
    """Финальное сохранение профиля переводчика в БД."""
    data = await state.get_data()
    profile = data.get("profile", {})
    
    await db.update_user_profile(call.from_user.id, profile)
    await state.clear()
    await call.message.edit_text(
        "✅ <b>Настройки успешно сохранены!</b>\n\n"
        "Теперь просто отправьте мне <code>.epub</code> файл для начала работы."
    )

# --- НОВАЯ КОМАНДА: НАСТРОЙКИ ---
@dp.message(Command("settings"))
async def settings_command(message: types.Message, state: FSMContext):
    if message.chat.type != "private":
        return
        
    user_data = await db.get_user(message.from_user.id)
    if not user_data or not user_data.get('is_active'):
        return await message.answer("❌ У вас нет доступа к боту или вы не зарегистрированы. Введите /start.")
        
    profile = user_data.get('profile', {})
    is_translator = profile.get('is_translator', False)
    
    if not is_translator:
        # Если это паблишер, предлагаем стать переводчиком
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Стать переводчиком", callback_data="role_translator")]
        ])
        
        # Устанавливаем нужное состояние перед показом кнопки
        await state.set_state(Registration.waiting_for_role)
        
        return await message.answer(
            "Вы зарегистрированы как обычный пользователь.\nХотите стать переводчиком и настроить инструменты?", 
            reply_markup=kb
        )
        
    # Если переводчик, запускаем FSM меню пресетов
    await state.update_data(profile=profile)
    await state.set_state(Registration.waiting_for_presets)
    await message.answer(
        "⚙️ <b>Настройка личного меню</b>\n\n"
        "Добавьте или уберите инструменты для будущих публикаций:",
        reply_markup=get_presets_keyboard(profile)
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
    # СЕКРЕТНЫЙ ФЛАГ: указываем, что это режим редактирования, а не регистрация
    await state.update_data(is_edit_mode=True) 
    
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

@dp.message(Command("ban"), IsAdmin())
async def ban_user(message: types.Message):
    """Блокировка пользователя по ID или Username."""
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.answer("❌ Укажите ID или Username.\n👉 Пример: <code>/ban @username</code>")
        
    target = args[1]
    target_id, target_data = await db.find_user_by_identifier(target)
    
    if not target_id:
        return await message.answer(f"❌ Пользователь <b>{escape(target)}</b> не найден в базе.")
        
    if target_id == message.from_user.id:
        return await message.answer("❌ Вы не можете забанить самого себя.")
        
    if target_data.get('is_admin'):
        return await message.answer("❌ Вы не можете забанить другого администратора.")
        
    if not target_data.get('is_active'):
        return await message.answer(f"⚠️ Пользователь <b>{escape(target_data.get('username', target))}</b> уже находится в бане.")
        
    success = await db.set_user_active(target_id, False)
    if success:
        await message.answer(f"✅ Пользователь <b>{escape(target_data.get('username', str(target_id)))}</b> (ID: <code>{target_id}</code>) успешно забанен.")
    else:
        await message.answer("❌ Произошла ошибка при обращении к базе данных.")

@dp.message(Command("unban"), IsAdmin())
async def unban_user(message: types.Message):
    """Разблокировка пользователя по ID или Username."""
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.answer("❌ Укажите ID или Username.\n👉 Пример: <code>/unban @username</code>")
        
    target = args[1]
    target_id, target_data = await db.find_user_by_identifier(target)
    
    if not target_id:
        return await message.answer(f"❌ Пользователь <b>{escape(target)}</b> не найден в базе.")
        
    if target_data.get('is_active'):
        return await message.answer(f"⚠️ Пользователь <b>{escape(target_data.get('username', target))}</b> не забанен (уже активен).")
        
    success = await db.set_user_active(target_id, True)
    if success:
        await message.answer(f"✅ Пользователь <b>{escape(target_data.get('username', str(target_id)))}</b> (ID: <code>{target_id}</code>) успешно разбанен.")
    else:
        await message.answer("❌ Произошла ошибка при обращении к базе данных.")

@dp.message(Command("help"), IsAdmin())
async def help_admin(message: types.Message):
    """Расширенная справка для администраторов."""
    help_text = (
        "🛠 <b>Справка для Администратора</b>\n\n"
        "<b>1. Загрузка и публикация книг</b>\n"
        "• Отправьте боту файл <code>.epub</code> (макс. 20 МБ).\n"
        "• Бот извлечет метаданные, обложку и предложит выбрать параметры с помощью кнопок.\n"
        "• По готовности нажмите «✅ ПУБЛИКАЦИЯ». Пост будет отправлен во все настроенные группы.\n\n"
        "<b>2. Настройка групп (/groups)</b>\n"
        "• Используйте команду /groups для изменения списка целевых чатов.\n"
        "• Формат ввода: <code>@group_name, -100123456789</code> (через запятую).\n"
        "• <i>Важно:</i> Базовая группа «Риф» добавляется в рассылку автоматически.\n\n"
        "<b>3. Управление доступом</b>\n"
        "• <code>/ban @username</code> (или ID) — заблокировать нарушителя (запрещает загрузку книг и использование кнопок).\n"
        "• <code>/unban @username</code> (или ID) — снять блокировку."
    )
    await message.answer(help_text)

@dp.message(Command("help"))
async def help_user(message: types.Message):
    """Базовая справка для обычных пользователей."""
    if not await db.check_access(message.from_user.id):
        return
        
    help_text = (
        "📖 <b>Справка по использованию бота</b>\n\n"
        "<b>1. Загрузка и публикация книг</b>\n"
        "• Отправьте боту файл (поддерживаются форматы: <code>.epub</code>, <code>.pdf</code>, <code>.txt</code>, <code>.docx</code>, <code>.doc</code>, <code>.fb2</code>, <code>.mobi</code>, макс. 20 МБ).\n"        "• Бот извлечет метаданные, обложку и предложит выбрать параметры с помощью кнопок.\n"
        "• Бот извлечет метаданные и обложку. <b>Для переводчиков бот предложит выбрать индивидуальные параметры и инструменты</b> с помощью кнопок.\n"
        "• По готовности нажмите «✅ ПУБЛИКАЦИЯ». Пост будет отправлен во все ваши настроенные группы.\n\n"
        "<b>2. Настройка групп (/groups)</b>\n"
        "• Используйте команду /groups для изменения списка целевых чатов.\n"
        "• Формат ввода: <code>@group_name, -100123456789</code> (через запятую).\n"
        "• <i>Важно:</i> Базовая группа «РиФ» добавляется в рассылку автоматически."
    )
    await message.answer(help_text)

async def process_batch(message: types.Message, state: FSMContext, user_data: dict):
    """Фоновая задача для обработки накопленного пакета файлов."""
    try:
        await asyncio.sleep(3)  # Окно ожидания для пакетной загрузки
    except asyncio.CancelledError:
        # Если прилетел еще один файл, таймер отменяется и запускается заново
        return

    data = await state.get_data()
    batch_files = data.get('batch_files', [])

    if not batch_files:
        return

    await message.answer(f"✅ Принято файлов: {len(batch_files)}. Начинаю обработку...")

    # Ищем EPUB и отделяем дополнительные файлы
    epub_file = None
    extras = []
    
    for f in batch_files:
        if f['ext'] == '.epub' and not epub_file:
            epub_file = f  # Берем первый найденный EPUB как главный
        else:
            extras.append(f)

    # Проверка на наличие EPUB
    if not epub_file:
        for f in batch_files:
            if os.path.exists(f['path']):
                try:
                    os.remove(f['path'])
                except OSError:
                    pass
        await state.update_data(batch_files=[])
        return await message.answer("❌ В пачке файлов нет .epub! Операция отменена. Пожалуйста, отправьте файлы заново.")

    # Если EPUB найден, проводим тяжелый парсинг
    epub_path = epub_file['path']
    meta = await asyncio.to_thread(extract_metadata, epub_path)
    cover = await asyncio.to_thread(extract_cover, epub_path)

    # Запускаем основной 3-минутный таймер сессии
    new_task = asyncio.create_task(check_and_clear(message, state))

    profile = user_data.get('profile', {})
    is_translator = profile.get('is_translator', False)

    user_gl_opts = profile.get("gl", ["Нет"]) if profile.get("gl") else ["Нет"]
    user_tr_opts = profile.get("tr", ["Нет"]) if profile.get("tr") else ["Нет"]
    user_fl_opts = profile.get("fl", ["Нет"]) if profile.get("fl") else ["Нет"]
    user_status_opts = profile.get("status", STATUS_OPTIONS) if profile.get("status") else STATUS_OPTIONS

    await state.update_data(
        path=epub_path, 
        name=epub_file['name'], 
        meta=meta, 
        cover=cover, 
        extras=extras, 
        timer_task=new_task,
        is_translator=is_translator,
        profile=profile,
        gl=user_gl_opts[0], 
        tr=user_tr_opts[0], 
        fl=user_fl_opts[0], 
        status=user_status_opts[0],
        batch_files=[]  # Очищаем очередь пакета
    )
    
    await state.set_state(BookForm.choosing_tools)
    new_data = await state.get_data()
    await message.answer("✅ EPUB успешно обработан. Выберите инструменты:", reply_markup=get_tools_kb(new_data, is_translator))


@dp.message(F.document)
async def handle_docs(message: types.Message, state: FSMContext):
    if message.chat.type != "private": 
        return
    
    user_data = await db.get_user(message.from_user.id)
    if not user_data or not user_data.get('is_active'):
        return await message.answer("❌ У вас нет доступа к загрузке файлов.")
    
    if message.document.file_size > 20 * 1024 * 1024:
        return await message.answer(f"❌ Файл {message.document.file_name} больше 20 МБ и был пропущен.")

    ext = os.path.splitext(message.document.file_name or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return await message.answer(f"❌ Формат {ext} не поддерживается и был пропущен.")

    # Сохраняем файл
    path = os.path.join(TEMP_DIR, f"{uuid.uuid4()}{ext}")
    await bot.download(message.document, destination=path)
    
    data = await state.get_data()
    
    # Защита от наслоения сессий: если юзер кидает файлы, будучи уже в меню настройки
    current_state = await state.get_state()
    if current_state == BookForm.choosing_tools.state:
        # Отменяем старый таймер сессии и удаляем старые файлы
        old_timer = data.get('timer_task')
        if old_timer and not old_timer.done():
            old_timer.cancel()
        files_to_remove = [data.get('path'), data.get('cover')] + [i['path'] for i in data.get('extras', [])]
        for p in files_to_remove:
            if p and os.path.exists(p):
                try: os.remove(p)
                except OSError: pass
        await state.clear()
        data = {}

    batch_files = data.get('batch_files', [])
    batch_files.append({"path": path, "name": message.document.file_name, "ext": ext})
    
    # Механизм антидребезга (Debounce)
    debounce_task = data.get('debounce_task')
    if debounce_task and not debounce_task.done():
        debounce_task.cancel()  # Отменяем предыдущее ожидание
        
    # Запускаем новое ожидание (3 секунды)
    new_debounce_task = asyncio.create_task(process_batch(message, state, user_data))
    
    await state.update_data(batch_files=batch_files, debounce_task=new_debounce_task)

@dp.callback_query(BookForm.choosing_tools)
async def callbacks(call: types.CallbackQuery, state: FSMContext):
    if not await db.check_access(call.from_user.id):
        return await call.answer("❌ Вы заблокированы. Действие отменено.", show_alert=True)
    
    data = await state.get_data()
    
    # Щит от критической ошибки (если юзер нажал кнопку после таймаута)
    if not data:
        return await call.message.edit_text("❌ Время сессии истекло. Пожалуйста, отправьте файл заново.")
        
    profile = data.get('profile', {})
    is_translator = data.get('is_translator', False)
    
    def get_next(current, options):
        if not options: return "Нет"
        try: return options[(options.index(current) + 1) % len(options)]
        except ValueError: return options[0]

    if call.data == "change_gl": 
        opts = profile.get("gl", ["Нет"]) if profile.get("gl") else ["Нет"]
        data['gl'] = get_next(data.get('gl'), opts)
    elif call.data == "change_tr": 
        opts = profile.get("tr", ["Нет"]) if profile.get("tr") else ["Нет"]
        data['tr'] = get_next(data.get('tr'), opts)
    elif call.data == "change_fl": 
        opts = profile.get("fl", ["Нет"]) if profile.get("fl") else ["Нет"]
        data['fl'] = get_next(data.get('fl'), opts)
    elif call.data == "change_status": 
        opts = profile.get("status", STATUS_OPTIONS) if profile.get("status") else STATUS_OPTIONS
        data['status'] = get_next(data.get('status'), opts)
    
    elif call.data == "cancel_all":
        task = data.get("timer_task")
        if task: task.cancel()
        for p in [data.get('path'), data.get('cover')] + [i['path'] for i in data.get('extras', [])]:
            if p and os.path.exists(p): os.remove(p)
        await state.clear()
        return await call.message.edit_text("❌ Отменено.")

    elif call.data == "pub_done":
        task = data.get("timer_task")
        if task: task.cancel()
        await call.message.edit_text("⏳ Читаю базу данных и начинаю публикацию...")

        try:
            user_data = await db.get_user(call.from_user.id)
            groups = user_data.get('groups')
            if not groups: return await call.message.edit_text("❌ Ошибка: У вас не настроены группы.")
            author_name = user_data.get('username') or f"@{call.from_user.username}"
        except Exception as e:
            logging.error(f"Ошибка БД при публикации: {e}")
            return await call.message.edit_text("❌ Ошибка при чтении базы данных.")

        success_count = 0
        try:
            meta = data['meta']
            title_topic = meta.get('titles', ['Новая книга'])[0][:128]
            chapters = await asyncio.to_thread(count_chapters, data['path'])
            status = data.get('status', "В процессе")
            
            icons = ["🏴‍☠️", "🇬🇧", "🌐"]
            post_text = ""
            for i, title in enumerate(meta.get('titles', [])):
                icon = icons[i] if i < len(icons) else "🔹"
                post_text += f"{icon} {escape(title)}\n"
            
            post_text += f"\n✍️ Автор: {escape(meta.get('author', '?'))}\n📊 Глав: {escape(str(chapters))}"
            if is_translator: 
                post_text += f"\n📌 Статус: <b>{escape(status)}</b>"
                
            if meta.get('tags'): post_text += f"\n\n🏷 {' '.join(meta['tags'])}"
            post_text += f"\n\n📖 <b>Описание:</b>\n<blockquote expandable>{escape(meta.get('desc', 'Описание отсутствует'))}</blockquote>"
            if meta.get('links'): post_text += f"\n\n🔗 {escape(meta['links'][0])}"
            post_text += f"\n\n👤 Опубликовал: {escape(author_name)}"
            
            # Формируем подпись к файлу только для переводчиков
            cap = ""
            if is_translator:
                cap = f"🤖 Глоссарий: {escape(data.get('gl', 'Нет'))}\n🤖 Перевод: {escape(data.get('tr', 'Нет'))}\n🧹 Фильтр: {escape(data.get('fl', 'Нет'))}"

            for gid in groups:
                try:
                    chat_id = int(gid) if isinstance(gid, str) and (gid.isdigit() or (gid.startswith('-') and gid[1:].isdigit())) else gid
                    topic = await bot.create_forum_topic(chat_id=chat_id, name=title_topic, icon_color=random.choice([0x6FB9F0, 0xFFD67E, 0xCB86DB, 0x8EEE98, 0xFF93B2, 0xFB6F5F]))
                    thread_id = topic.message_thread_id

                    if data.get('cover') and os.path.exists(data['cover']):
                        await bot.send_photo(chat_id, photo=FSInputFile(data['cover']), message_thread_id=thread_id)
                    
                    await bot.send_message(chat_id, post_text, message_thread_id=thread_id, link_preview_options=LinkPreviewOptions(is_disabled=True))
                    
                    # Отправляем файл. Если cap пустой, caption не добавится.
                    await bot.send_document(chat_id, document=FSInputFile(data['path'], filename=data['name']), caption=cap if cap else None, message_thread_id=thread_id)
                    
                    for item in data.get('extras', []):
                        await bot.send_document(chat_id, document=FSInputFile(item['path'], filename=item['name']), message_thread_id=thread_id)
                        
                    success_count += 1
                except Exception as e:
                    logging.error(f"Ошибка отправки в группу {gid}: {e}")
            
            if success_count > 0: await call.message.edit_text(f"✅ Успешно опубликовано в {success_count} групп(ы)!")
            else: await call.message.edit_text("❌ Не удалось опубликовать ни в одну группу.")
                
        except Exception as e:
            logging.error(f"Критическая ошибка при публикации: {e}")
            await call.answer("❌ Произошла ошибка публикации", show_alert=True)
            
        finally:
            all_files = [data.get('path'), data.get('cover')] + [i['path'] for i in data.get('extras', [])]
            for p in all_files:
                if p and os.path.exists(p):
                    try: os.remove(p)
                    except OSError: pass
            await state.clear()
            return

    await state.update_data(data)
    await call.message.edit_reply_markup(reply_markup=get_tools_kb(data, is_translator))
    await call.answer()

if __name__ == "__main__":
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
    app = web.Application()
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    web.run_app(app, port=int(os.environ.get("PORT", 8080)))
