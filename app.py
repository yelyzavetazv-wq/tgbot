import os
import uuid
import zipfile
import asyncio
import tempfile
import logging
import re
from html import escape
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import FSInputFile, LinkPreviewOptions
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from bs4 import BeautifulSoup
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
GROUP_USERNAME = os.getenv("GROUP_USERNAME")
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

def clean_filename(name: str) -> str:
    base, ext = os.path.splitext(name)
    return re.sub(r'\s+', '_', base) + ext.lower()

def parse_metadata(epub_path: str) -> dict:
    data = {"title_ru": "Без названия", "title_en": "", "title_orig": "", "author": "Неизвестен", 
            "chapters": "?", "tags": [], "desc": "Описание отсутствует", "links": []}
    try:
        with zipfile.ZipFile(epub_path, 'r') as z:
            # Парсинг OPF
            for name in z.namelist():
                if name.endswith('.opf'):
                    with z.open(name) as f:
                        s = BeautifulSoup(f.read(), 'xml')
                        t = s.find('dc:title')
                        if t and t.text:
                            parts = [p.strip() for p in t.text.split('/')]
                            data["title_ru"] = parts[0]
                            if len(parts) > 1: data["title_en"] = parts[1]
                            if len(parts) > 2: data["title_orig"] = parts[2]
                        c = s.find('dc:creator')
                        if c: data["author"] = c.text.strip()
                        data["tags"] = [f"#{tag.text.strip().replace(' ', '_')}" for tag in s.find_all('dc:subject')]
                        d = s.find('dc:description')
                        if d: data["desc"] = d.get_text(strip=True)[:2000]
                        pub = s.find('dc:publisher')
                        if pub and pub.text:
                            data["links"] = [l for l in pub.text.split() if l.startswith('http')]
                    break
            # Парсинг NCX
            for name in z.namelist():
                if name.endswith('.ncx'):
                    with z.open(name) as f:
                        s = BeautifulSoup(f.read(), 'xml')
                        data["chapters"] = len(s.find_all('navPoint'))
    except: pass
    return data

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Я готов! Присылай файл для публикации.")

@dp.message(F.document)
async def handle_docs(message: types.Message):
    status = await message.answer("⏳ Загрузка файла...")
    tmp_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}{os.path.splitext(message.document.file_name)[1]}")
    await bot.download(message.document, destination=tmp_path)
    
    await status.edit_text("⏳ Парсинг метаданных...")
    meta = await asyncio.to_thread(parse_metadata, tmp_path)
    
    await status.edit_text("🚀 Публикация...")
    
    # Публикация обложки
    try:
        with zipfile.ZipFile(tmp_path) as z:
            opf_data = next((z.read(n) for n in z.namelist() if n.endswith('.opf')), b"")
            s = BeautifulSoup(opf_data, 'xml')
            cover_meta = s.find("meta", {"name": "cover"})
            if cover_meta:
                item = s.find("item", {"id": cover_meta["content"]})
                cover_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}.jpg")
                with z.open(os.path.basename(item["href"])) as f_in, open(cover_path, "wb") as f_out:
                    f_out.write(f_in.read())
                await bot.send_photo(GROUP_USERNAME, FSInputFile(cover_path))
    except: pass
    
    # Описание
    text = [f"🏴‍☠️ <b>{escape(meta['title_ru'])}</b>"]
    if meta['title_en']: text.append(f"🇬🇧 {escape(meta['title_en'])}")
    text.append(f"\n✍️ Автор: {escape(meta['author'])}\n📊 Глав: {meta['chapters']}")
    if meta['tags']: text.append(f"🏷 {' '.join(meta['tags'])}")
    text.append(f"\n📖 <i>{escape(meta['desc'][:500])}...</i>")
    
    await bot.send_message(GROUP_USERNAME, "\n".join(text), parse_mode="HTML")
    await bot.send_document(GROUP_USERNAME, FSInputFile(tmp_path, filename=clean_filename(message.document.file_name)), 
                            caption="🤖 Инструменты: Gemini 3.5")
    
    await status.edit_text("✅ Опубликовано!")
    if os.path.exists(tmp_path): os.remove(tmp_path)

async def on_startup(bot: Bot):
    await bot.set_webhook(f"{os.getenv('WEBHOOK_URL')}/webhook")

if __name__ == "__main__":
    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path="/webhook")
    dp.startup.register(on_startup)
    setup_application(app, dp, bot=bot)
    web.run_app(app, port=int(os.environ.get("PORT", 8080)))
