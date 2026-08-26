import os
import json
import time
import logging
import gspread_asyncio
from google.oauth2.service_account import Credentials

# Настройки кэширования (в секундах). 300 = 5 минут.
CACHE_TTL = 300 

def get_creds():
    """Чтение и парсинг JSON ключа из переменных окружения."""
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
        """Ленивая инициализация подключения к таблице."""
        if not self.client:
            self.client = await agcm.authorize()
        if not self.sheet:
            ss = await self.client.open_by_key(self.spreadsheet_id)
            self.sheet = await ss.get_worksheet(0)  # Используем Лист 1 (индекс 0)

    async def _ensure_cache(self):
        """Проверяет актуальность кэша и обновляет его при необходимости."""
        if time.time() - self._cache_time > CACHE_TTL or not self._cache:
            await self._fetch_all_users()

    async def _fetch_all_users(self):
        """Считывает всех пользователей из таблицы в локальный кэш (A:E)."""
        await self._init_sheet()
        
        # Прямой асинхронный вызов gspread_asyncio (без run_in_executor)
        try:
            records = await self.sheet.get_all_values()
        except Exception as e:
            logging.error(f"Ошибка чтения данных из таблицы: {e}")
            return

        new_cache = {}
        
        # Пропускаем первую строку (заголовки)
        for row in records[1:]:
            if not row or not row[0].strip():
                continue
                
            try:
                user_id = int(row[0].strip())
                username = row[1].strip() if len(row) > 1 else ""
                groups_str = row[2].strip() if len(row) > 2 else ""
                groups = [g.strip() for g in groups_str.split(',') if g.strip()]
                is_active = str(row[3]).strip().upper() == 'TRUE' if len(row) > 3 else False
                is_admin = str(row[4]).strip().upper() == 'TRUE' if len(row) > 4 else False
                
                new_cache[user_id] = {
                    'username': username,
                    'groups': groups,
                    'is_active': is_active,
                    'is_admin': is_admin
                }
            except ValueError:
                continue
                
        self._cache = new_cache
        self._cache_time = time.time()

    async def get_user(self, user_id: int) -> dict:
        """Получает данные пользователя (с учетом TTL кэша)."""
        await self._ensure_cache()
        return self._cache.get(user_id)

    async def check_access(self, user_id: int) -> bool:
        """Проверка активности пользователя."""
        user = await self.get_user(user_id)
        return bool(user and user.get('is_active'))

    async def update_user_groups(self, user_id: int, username: str, groups: list, is_active: bool = True):
        """Добавляет или обновляет пользователя, сохраняя его права администратора."""
        await self._ensure_cache()
        groups_str = ", ".join(groups)
        
        # Сохраняем статус админа, если юзер уже есть в базе
        user = self._cache.get(user_id)
        is_admin = user['is_admin'] if user else False
        
        row_data = [str(user_id), username, groups_str, str(is_active).upper(), str(is_admin).upper()]
        
        try:
            # Нативный асинхронный поиск
            cell = await self.sheet.find(str(user_id))
            if cell:
                # Обновляем диапазон A:E
                range_name = f"A{cell.row}:E{cell.row}"
                await self.sheet.update(range_name, [row_data])
            else:
                await self.sheet.append_row(row_data)
                
            # Обновляем локальный кэш "на лету" без полного перезапроса таблицы
            self._cache[user_id] = {
                'username': username,
                'groups': groups,
                'is_active': is_active,
                'is_admin': is_admin
            }
        except Exception as e:
            logging.error(f"Ошибка записи пользователя {user_id} в БД: {e}")

    async def find_user_by_identifier(self, identifier: str):
        """Ищет пользователя по числовому ID или @username (Сложность O(N) по кэшу)."""
        await self._ensure_cache()
        identifier = str(identifier).strip().lower()
        
        for uid, data in self._cache.items():
            if str(uid) == identifier:
                return uid, data
            
            db_username = data.get('username', '').lower()
            if db_username == identifier or db_username.lstrip('@') == identifier.lstrip('@'):
                return uid, data
                
        return None, None

    async def set_user_active(self, user_id: int, is_active: bool) -> bool:
        """Изменяет статус активности пользователя (бан/разбан)."""
        await self._ensure_cache()
        if user_id not in self._cache:
            return False
            
        try:
            cell = await self.sheet.find(str(user_id))
            if cell:
                # Колонка D (4-я по счету) отвечает за is_active
                await self.sheet.update_cell(cell.row, 4, str(is_active).upper())
                self._cache[user_id]['is_active'] = is_active
                return True
        except Exception as e:
            logging.error(f"Ошибка при бане пользователя {user_id}: {e}")
            
        return False
