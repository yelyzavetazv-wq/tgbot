from flask import Flask, request
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import zipfile
import os
import shutil
import re
import time
from ebooklib import epub
from bs4 import BeautifulSoup
from telebot.types import InputFile
import io


# ========== ВСТАВЬТЕ СЮДА ==========
def fix_filename(filename):
    """Исправляет кракозябры в именах файлов"""
    try:
        return filename.encode('latin1').decode('utf-8')
    except:
        try:
            return filename.encode('cp1252').decode('utf-8')
        except:
            return filename
# ==================================

TOKEN = "8653759634:AAGxGfkJvj3pEZ_kvry7FRkqzYhnxeJNZlU"
CHANNEL_ID = "@my_ff_translate"

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)
# ... остальной код

TOKEN = "8653759634:AAGxGfkJvj3pEZ_kvry7FRkqzYhnxeJNZlU"
CHANNEL_ID = "@my_ff_translate"

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

user_choices = {}
user_waiting = {}
user_data = {}

def glossary_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(InlineKeyboardButton("Gemini 3.5 Flash", callback_data="glossary:Gemini 3.5 Flash"))
    keyboard.add(InlineKeyboardButton("Gemini 3.1 Flash Lite", callback_data="glossary:Gemini 3.1 Flash Lite"))
    keyboard.add(InlineKeyboardButton("Gemini 3 Flash", callback_data="glossary:Gemini 3 Flash"))
    keyboard.add(InlineKeyboardButton("Gemini 2.5 Flash", callback_data="glossary:Gemini 2.5 Flash"))
    keyboard.add(InlineKeyboardButton("✏️ Другое", callback_data="glossary:other"))
    return keyboard

def translation_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(InlineKeyboardButton("Gemini 3.5 Flash", callback_data="translation:Gemini 3.5 Flash"))
    keyboard.add(InlineKeyboardButton("Gemini 3.1 Flash Lite", callback_data="translation:Gemini 3.1 Flash Lite"))
    keyboard.add(InlineKeyboardButton("Gemini 3 Flash", callback_data="translation:Gemini 3 Flash"))
    keyboard.add(InlineKeyboardButton("Gemini 2.5 Flash", callback_data="translation:Gemini 2.5 Flash"))
    keyboard.add(InlineKeyboardButton("✏️ Другое", callback_data="translation:other"))
    return keyboard

def filter_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(InlineKeyboardButton("ChatGPT Web", callback_data="filter:ChatGPT Web"))
    keyboard.add(InlineKeyboardButton("DeepSeekWeb", callback_data="filter:DeepSeekWeb"))
    keyboard.add(InlineKeyboardButton("❌ Нет (не показывать)", callback_data="filter:none"))
    keyboard.add(InlineKeyboardButton("✏️ Другое", callback_data="filter:other"))
    return keyboard

def status_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(InlineKeyboardButton("в процессе", callback_data="status:в процессе"))
    keyboard.add(InlineKeyboardButton("завершен", callback_data="status:завершен"))
    keyboard.add(InlineKeyboardButton("брошен", callback_data="status:брошен"))
    return keyboard

def detect_language(text):
    if re.search(r'[\u4e00-\u9fff]', text):
        return '🇨🇳'
    elif re.search(r'[\u3040-\u309f\u30a0-\u30ff]', text):
        return '🇯🇵'
    elif re.search(r'[\uac00-\ud7af]', text):
        return '🇰🇷'
    else:
        return '🌐'

def count_chapters_from_epub(epub_path):
    try:
        book = epub.read_epub(epub_path)
        chapters = 0
        for item in book.get_items():
            if item.get_type() == 9:
                soup = BeautifulSoup(item.get_content(), 'html.parser')
                nav_links = soup.find_all('a')
                if nav_links:
                    chapters = max(chapters, len(nav_links))
            elif 'toc' in item.get_name().lower() or 'ncx' in item.get_name().lower():
                content = item.get_content().decode('utf-8', errors='ignore')
                chapters = max(chapters, content.count('<navPoint'))
        if chapters == 0:
            for item in book.get_items():
                if item.get_type() == 8:
                    chapters += 1
        return chapters if chapters > 0 else "Неизвестно"
    except Exception as e:
        return "Неизвестно"

def extract_annotation_from_epub(epub_path):
    try:
        import zipfile
        from bs4 import BeautifulSoup
        
        with zipfile.ZipFile(epub_path, 'r') as epub_zip:
            for name in epub_zip.namelist():
                if name.endswith('.xhtml') or name.endswith('.html'):
                    with epub_zip.open(name) as f:
                        content = f.read().decode('utf-8', errors='ignore')
                        soup = BeautifulSoup(content, 'html.parser')
                        
                        h1 = None
                        for tag in soup.find_all('h1'):
                            text = tag.get_text().strip()
                            if text == 'Annotation' or text == 'Аннотация':
                                h1 = tag
                                break
                        
                        if not h1:
                            h1 = soup.find('h1', id='calibre_toc_1')
                        
                        if h1:
                            texts = []
                            for sibling in h1.find_next_siblings():
                                if sibling.name == 'div' and 'paragraph' in sibling.get('class', []):
                                    text = sibling.get_text().strip()
                                    if text:
                                        texts.append(text)
                                if sibling.name == 'hr':
                                    break
                            
                            if texts:
                                result = '\n'.join(texts)[:2000]
                                return result
        
        return "Описание отсутствует"
    except Exception as e:
        return "Описание отсутствует"

def parse_info(content):
    info = {
        'title_ru': '',
        'title_en': '',
        'title_original': '',
        'author': '',
        'links': []
    }
    lines = content.split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue
        line_lower = line.lower()
        if line.startswith('http'):
            info['links'].append(line)
        elif 'название_ru' in line_lower and ':' in line:
            info['title_ru'] = line.split(':', 1)[1].strip()
        elif 'название_en' in line_lower and ':' in line:
            info['title_en'] = line.split(':', 1)[1].strip()
        elif 'название_original' in line_lower and ':' in line:
            info['title_original'] = line.split(':', 1)[1].strip()
        elif 'автор' in line_lower and ':' in line:
            info['author'] = line.split(':', 1)[1].strip()
    return info

def format_text_post(info, chapters, status, annotation):
    lines = []
    lines.append(f"🏴‍☠️ {info.get('title_ru', 'Без названия')}")
    if info.get('title_en'):
        lines.append(f"🇬🇧 {info['title_en']}")
    if info.get('title_original'):
        lines.append(f"{detect_language(info['title_original'])} {info['title_original']}")
    lines.append("")
    if info.get('author'):
        lines.append(f"✍️ Автор: {info['author']}")
    lines.append(f"📊 Глав: {chapters}")
    lines.append(f"📌 Статус: {status}")
    lines.append("")
    quoted = f'<blockquote>{annotation}</blockquote>'
    lines.append(f"📖 Описание:\n{quoted}")
    if info.get('links') and len(info['links']) > 0:
        lines.append("")
        lines.append("🔗 Ссылки:")
        for link in info['links']:
            lines.append(link)
    return '\n'.join(lines)

def format_files_post(glossary, translation, filter_choice):
    lines = []
    lines.append("🤖 Глоссарий: " + glossary)
    lines.append("🤖 Перевод: " + translation)
    if filter_choice and filter_choice != "none":
        lines.append("🧹 Фильтр: " + filter_choice)
    return '\n'.join(lines)

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
            text = update.message.text

            if chat_id in user_waiting:
                category = user_waiting[chat_id]
                user_choices[chat_id][category] = text
                del user_waiting[chat_id]
                if category == 'glossary':
                    bot.send_message(chat_id, "Выберите модель для Перевода:", reply_markup=translation_keyboard())
                elif category == 'translation':
                    bot.send_message(chat_id, "Выберите Фильтр:", reply_markup=filter_keyboard())
                elif category == 'filter':
                    bot.send_message(chat_id, "Выберите Статус:", reply_markup=status_keyboard())
                return 'OK', 200

            if text == '/start':
                bot.send_message(chat_id, "📚 Отправьте ZIP-архив с книгой (cover.png, description.txt, .epub, .fb2)")
                return 'OK', 200

            if update.message.document:
                # ЕДИНЫЙ ПРОГРЕСС-БАР
                progress_msg = bot.send_message(chat_id, "⏳ [░░░░░░░░░░] 0% - Начинаю...")
                
                bot.edit_message_text("⏳ [▓▓░░░░░░░░] 20% - Скачиваю...", chat_id, progress_msg.message_id)
                file_info = bot.get_file(update.message.document.file_id)
                file_content = bot.download_file(file_info.file_path)
                
                bot.edit_message_text("⏳ [▓▓▓▓░░░░░░] 40% - Сохраняю...", chat_id, progress_msg.message_id)
                zip_path = f"/tmp/{update.message.document.file_name}"
                with open(zip_path, 'wb') as f:
                    f.write(file_content)
                
                bot.edit_message_text("⏳ [▓▓▓▓▓▓░░░░] 60% - Распаковываю...", chat_id, progress_msg.message_id)
                extract_path = f"/tmp/extract_{update.message.message_id}"
                with zipfile.ZipFile(zip_path, 'r') as z:
                    z.extractall(extract_path)
                
                bot.edit_message_text("⏳ [▓▓▓▓▓▓▓▓░░] 80% - Ищу файлы...", chat_id, progress_msg.message_id)
                
                cover = None
                description_text = ""
                epub_files = []
                fb2_files = []

                for root, dirs, files in os.walk(extract_path):
                    for f in files:
                        full_path = os.path.join(root, f)
                        name_lower = f.lower()
                        if name_lower.endswith(('.png', '.jpg', '.jpeg')):
                            if cover is None:
                                cover = full_path
                        elif name_lower.endswith('.txt'):
                            with open(full_path, 'r', encoding='utf-8') as txt:
                                description_text = txt.read()
                        elif name_lower.endswith('.epub'):
                            epub_files.append(full_path)
                        elif name_lower.endswith('.fb2'):
                            fb2_files.append(full_path)

                bot.edit_message_text("⏳ [▓▓▓▓▓▓▓▓▓░] 90% - Анализирую EPUB...", chat_id, progress_msg.message_id)
                
                if not cover and epub_files:
                    try:
                        with zipfile.ZipFile(epub_files[0], 'r') as epub_zip:
                            for name in epub_zip.namelist():
                                if name.lower().endswith(('.png', '.jpg', '.jpeg')):
                                    cover_data = epub_zip.read(name)
                                    cover_path = os.path.join(extract_path, f"cover_from_epub_{os.path.basename(name)}")
                                    with open(cover_path, 'wb') as cf:
                                        cf.write(cover_data)
                                    cover = cover_path
                                    break
                    except:
                        pass
                
                if not cover:
                    bot.edit_message_text("❌ Не найдена обложка", chat_id, progress_msg.message_id)
                    time.sleep(1)
                    bot.delete_message(chat_id, progress_msg.message_id)
                    shutil.rmtree(extract_path)
                    os.remove(zip_path)
                    return 'OK', 200

                if not epub_files and not fb2_files:
                    bot.edit_message_text("❌ Нет EPUB или FB2 файлов", chat_id, progress_msg.message_id)
                    time.sleep(1)
                    bot.delete_message(chat_id, progress_msg.message_id)
                    shutil.rmtree(extract_path)
                    os.remove(zip_path)
                    return 'OK', 200

                info = parse_info(description_text) if description_text else {'title_ru': '', 'title_en': '', 'title_original': '', 'author': '', 'links': []}
                epub_path = epub_files[0] if epub_files else None
                chapters = count_chapters_from_epub(epub_path) if epub_path else "Неизвестно"
                annotation = extract_annotation_from_epub(epub_path) if epub_path else "Описание отсутствует"

                user_data[chat_id] = {
                    'info': info,
                    'chapters': chapters,
                    'annotation': annotation,
                    'cover_path': cover,
                    'epub_files': epub_files,
                    'fb2_files': fb2_files,
                    'extract_path': extract_path,
                    'zip_path': zip_path,
                    'progress_msg_id': progress_msg.message_id
                }
                bot.send_message(chat_id, "✅ Архив обработан. Теперь выберите параметры публикации.")
                bot.send_message(chat_id, "Выберите модель для Глоссария:", reply_markup=glossary_keyboard())
                return 'OK', 200

        if update and update.callback_query:
            callback = update.callback_query
            chat_id = callback.message.chat.id
            data_parts = callback.data.split(':', 1)
            category = data_parts[0]
            value = data_parts[1] if len(data_parts) > 1 else ''
            bot.answer_callback_query(callback.id)
            
            # Удаляем сообщение с кнопками
            try:
                bot.delete_message(chat_id, callback.message.message_id)
            except:
                pass

            if category == 'glossary':
                if value == 'other':
                    user_waiting[chat_id] = 'glossary'
                    bot.send_message(chat_id, "✏️ Введите название модели для Глоссария:")
                else:
                    user_choices[chat_id] = {'glossary': value}
                    bot.send_message(chat_id, "Выберите модель для Перевода:", reply_markup=translation_keyboard())
            
            elif category == 'translation':
                if value == 'other':
                    user_waiting[chat_id] = 'translation'
                    bot.send_message(chat_id, "✏️ Введите название модели для Перевода:")
                else:
                    user_choices[chat_id]['translation'] = value
                    bot.send_message(chat_id, "Выберите Фильтр:", reply_markup=filter_keyboard())
            
            elif category == 'filter':
                if value == 'other':
                    user_waiting[chat_id] = 'filter'
                    bot.send_message(chat_id, "✏️ Введите название Фильтра:")
                else:
                    user_choices[chat_id]['filter'] = value
                    bot.send_message(chat_id, "Выберите Статус:", reply_markup=status_keyboard())
            
            elif category == 'status':
                user_choices[chat_id]['status'] = value
                choices = user_choices[chat_id]
                data = user_data.get(chat_id)
                if not data:
                    bot.send_message(chat_id, "❌ Ошибка: данные не найдены")
                    return 'OK', 200

                progress_msg_id = data.get('progress_msg_id')
                
                try:
                    if progress_msg_id:
                        bot.edit_message_text("⏳ [▓▓▓▓▓▓▓▓▓▓] 100% - Публикую...", chat_id, progress_msg_id)
                    
                    # Пост 1: обложка
                    with open(data['cover_path'], 'rb') as img:
                        bot.send_photo(CHANNEL_ID, img)
                    
                    # Пост 2: текст
                    post2 = format_text_post(data['info'], data['chapters'], choices['status'], data['annotation'])
                    bot.send_message(CHANNEL_ID, post2, parse_mode="HTML", disable_web_page_preview=True)
                    
                    # Пост 3: параметры + файлы в одном сообщении
                    post3 = format_files_post(choices['glossary'], choices['translation'], choices.get('filter', 'none'))
                    
                    # Отправляем файлы с подписью (параметры под последним файлом)
                    all_files = data['epub_files'] + data['fb2_files']
                    for i, file_path in enumerate(all_files):
                        clean_name = fix_filename(os.path.basename(file_path))
                        
                        # Читаем файл в память
                        with open(file_path, 'rb') as f:
                            file_data = f.read()
                        
                        # Создаём BytesIO объект с правильным именем
                        file_io = io.BytesIO(file_data)
                        file_io.name = clean_name
                        
                        if i == len(all_files) - 1:
                            bot.send_document(CHANNEL_ID, file_io, caption=post3)
                        else:
                            bot.send_document(CHANNEL_ID, file_io)
                    
                    if progress_msg_id:
                        bot.edit_message_text("✅ [▓▓▓▓▓▓▓▓▓▓] 100% - Книга опубликована!", chat_id, progress_msg_id)
                        time.sleep(1)
                        bot.delete_message(chat_id, progress_msg_id)
                    
                    bot.send_message(chat_id, f"✅ Книга '{data['info'].get('title_ru', 'Без названия')}' опубликована в канале!")
                    
                except Exception as e:
                    if progress_msg_id:
                        bot.edit_message_text(f"❌ Ошибка: {str(e)[:50]}", chat_id, progress_msg_id)
                        time.sleep(2)
                        bot.delete_message(chat_id, progress_msg_id)
                    bot.send_message(chat_id, f"❌ Ошибка публикации: {str(e)}")
                
                shutil.rmtree(data['extract_path'])
                os.remove(data['zip_path'])
                del user_choices[chat_id]
                del user_data[chat_id]

        return 'OK', 200
    except Exception as e:
        print(f"Error: {e}")
        return 'OK', 200
