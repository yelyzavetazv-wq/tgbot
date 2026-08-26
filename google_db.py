import os
import json
import time
import logging
import gspread_asyncio
from google.oauth2.service_account import Credentials

# Настройки кэширования (в секундах). 300 = 5 минут.
CACHE_TTL = 300 

def get_creds():
    """Чтение и парсинг JSON ключа из переменных окружения"""
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise ValueError("CRITICAL: GOOGLE_CREDENTIALS_JSON не задан")
    
    try:
        creds_dict = json.loads(creds_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"CRITICAL: Ошибка парсинга JSON ключа: {e}")

    creds = Credentials.from_service_account_info(creds_dict)
    scoped = creds.with_scopes([
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ])
    return scoped

# Инициализация асинхронного менеджера
agcm = gspread_asyncio.AsyncioGspreadClientManager(get_creds)

class GoogleSheetsDB:
    def __init__(self, spreadsheet_id: str):
        self.spreadsheet_id = spreadsheet_id
        self.client = None
        self.sheet = None
        self._cache = {}
        self._cache_time = 0

    async def _init_sheet(self):
        """Ленивая инициализация подключения к таблице"""
        if not self.client:
            self.client = await agcm.authorize()
        if not self.sheet:
            ss = await self.client.open_by_key(self.spreadsheet_id)
            self.sheet = await ss.get_worksheet(0) # Используем Лист 1 (индекс 0)

    async def _fetch_all_users(self):
        """Загружает всех пользователей из таблицы и кэширует их в ОЗУ"""
        await self._init_sheet()
        records = await self.sheet.get_all_records()
        
        new_cache = {}
        for idx, row in enumerate(records):
            try:
                uid = int(row.get('user_id', 0))
                if uid:
                    new_cache[uid] = {
                        'username': str(row.get('username', '')).strip(),
                        # Убираем пробелы, бьем по запятой
                        'groups': [g.strip() for g in str(row.get('groups', '')).split(',') if g.strip()],
                        'is_active': str(row.get('is_active', 'TRUE')).upper() == 'TRUE',
                        'row_index': idx + 2 # +2 (1 - строка заголовков, +1 сдвиг индекса)
                    }
            except Exception as e:
                logging.error(f"Ошибка парсинга строки БД: {row} - {e}")
        
        self._cache = new_cache
        self._cache_time = time.time()

    async def get_user(self, user_id: int) -> dict:
        """Получает данные пользователя (с учетом TTL кэша)"""
        if time.time() - self._cache_time > CACHE_TTL or not self._cache:
            await self._fetch_all_users()
        return self._cache.get(user_id)

    async def check_access(self, user_id: int) -> bool:
        """Проверка активности пользователя"""
        user = await self.get_user(user_id)
        if user and user.get('is_active'):
            return True
        return False

    async def update_user_groups(self, user_id: int, username: str, groups: list, is_active: bool = True):
        """Добавляет нового пользователя или обновляет группы существующего"""
        await self._init_sheet()
        user = await self.get_user(user_id)
        
        groups_str = ", ".join(groups)
        is_active_str = "TRUE" if is_active else "FALSE"
        
        if user:
            row_idx = user['row_index']
            # update() принимает диапазон и матрицу значений
            await self.sheet.update(f"A{row_idx}:D{row_idx}", [[user_id, username, groups_str, is_active_str]])
        else:
            await self.sheet.append_row([user_id, username, groups_str, is_active_str])
        
        # Принудительно сбрасываем кэш
        self._cache_time = 0
