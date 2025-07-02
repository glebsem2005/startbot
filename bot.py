import logging
import random
import smtplib
import re
import asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
import asyncpg
import os

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TOKEN = '8182061892:AAHnfK9k5g4gaaUlEK8plhdFdVHKzHQzYg4'

# Email настройки
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
EMAIL_USER = os.getenv('EMAIL_USER', 'glebsem2005@gmail.com')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD', 'kqbkhnqpskiumddc')

# База данных
DATABASE_URL = "postgresql://postgres:QynuLMnRNPWupmbDGndBwkgrUAdXZxcG@shortline.proxy.rlwy.net:54776/railway"

# Боты для перенаправления
STRATEGIES = {
    'Анализ инвестиционной привлекательности': 'sber_CPNB_investment_bot',
    'Скаутинг стартапов': 'sber_startups_bot',
    'Анализ рынка' : 'sber_CPNB_market_bot'
}

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Простой кэш авторизованных пользователей
authorized_users_cache = set()
users_email_cache = {}

# Пул соединений для оптимизации
db_pool = None

class UserStates(StatesGroup):
    WAITING_EMAIL = State()
    WAITING_CODE = State()

class EmailSender:
    """Класс для отправки email с кодом авторизации."""
    
    def __init__(self):
        self.smtp_server = SMTP_SERVER
        self.smtp_port = SMTP_PORT
        self.email_user = EMAIL_USER
        self.email_password = EMAIL_PASSWORD
        
        if not self.email_user or not self.email_password:
            logger.warning("Email credentials not configured!")
    
    def generate_code(self) -> str:
        """Генерирует 6-значный код."""
        return str(random.randint(100000, 999999))
    
    async def send_verification_code(self, recipient_email: str, code: str) -> bool:
        """Отправляет код верификации на email."""
        if not self.email_user or not self.email_password:
            logger.error("Email credentials not configured")
            return False
        
        try:
            # Создаем сообщение
            msg = MIMEMultipart()
            msg['From'] = f"Сбер CPNB Bot <{self.email_user}>"
            msg['To'] = recipient_email
            msg['Subject'] = "Код авторизации для Сбер CPNB Bot"
            
            # Текст письма
            body = f"""Здравствуйте!

Ваш код для авторизации в боте Сбер CPNB:

{code}

Код действителен в течение 10 минут.

Если вы не запрашивали этот код, проигнорируйте это письмо.

С уважением,
Команда Сбер CPNB"""
            
            msg.attach(MIMEText(body, 'plain', 'utf-8'))
            
            # Отправляем email
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.email_user, self.email_password)
            text = msg.as_string()
            server.sendmail(self.email_user, recipient_email, text)
            server.quit()
            
            logger.info(f"Verification code sent to {recipient_email}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email to {recipient_email}: {e}")
            return False

email_sender = EmailSender()

async def init_db_pool():
    """Инициализирует пул соединений с БД."""
    global db_pool
    try:
        logger.info(f"Создание пула соединений к БД...")
        
        # Настройки для Railway PostgreSQL
        db_pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=5,
            command_timeout=30,
            server_settings={
                'application_name': 'sber_cpnb_bot',
            }
        )
        
        # Тестируем пул
        async with db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
            
        logger.info("✅ Пул соединений создан успешно")
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка создания пула соединений: {type(e).__name__}: {e}")
        db_pool = None
        return False

async def get_db_connection():
    """Получает соединение из пула."""
    global db_pool
    
    if not db_pool:
        logger.error("Пул соединений не инициализирован")
        return None
    
    try:
        conn = await db_pool.acquire()
        return conn
    except Exception as e:
        logger.error(f"Ошибка получения соединения: {e}")
        return None

def release_db_connection(conn):
    """Возвращает соединение в пул."""
    global db_pool
    if db_pool and conn:
        try:
            db_pool.release(conn)
        except Exception as e:
            logger.error(f"Ошибка возврата соединения в пул: {e}")

async def ensure_table_exists():
    """Создает таблицу users если она не существует."""
    conn = await get_db_connection()
    if not conn:
        return False
    
    try:
        # Проверяем существование таблицы
        table_exists = await conn.fetchval("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'users'
            )
        """)
        
        if not table_exists:
            # Создаем таблицу с TEXT для users_id (как в оригинале)
            await conn.execute("""
                CREATE TABLE users (
                    id SERIAL PRIMARY KEY,
                    users_id TEXT UNIQUE NOT NULL,
                    email VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            logger.info("✅ Таблица users создана")
        else:
            # Проверяем и добавляем ограничение уникальности если его нет
            constraint_exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.table_constraints 
                    WHERE table_name = 'users' 
                    AND constraint_type = 'UNIQUE'
                    AND constraint_name LIKE '%users_id%'
                )
            """)
            
            if not constraint_exists:
                logger.info("Добавляем ограничение уникальности для users_id...")
                try:
                    await conn.execute("""
                        ALTER TABLE users 
                        ADD CONSTRAINT users_users_id_unique UNIQUE (users_id)
                    """)
                    logger.info("✅ Ограничение уникальности добавлено")
                except Exception as e:
                    if "already exists" not in str(e).lower():
                        logger.error(f"Ошибка добавления ограничения: {e}")
        
        # Создаем индекс для быстрого поиска (если еще не существует)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_users_users_id ON users(users_id)
        """)
        
        logger.info("✅ Таблица users готова")
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка создания/проверки таблицы: {e}")
        return False
    finally:
        release_db_connection(conn)

async def load_authorized_users():
    """Загружает авторизованных пользователей из БД в кэш."""
    logger.info("Загрузка пользователей из БД...")
    
    conn = await get_db_connection()
    if not conn:
        logger.warning("❌ Не удалось подключиться к БД для загрузки пользователей")
        return False
    
    try:
        # Загружаем всех пользователей
        users = await conn.fetch("SELECT users_id, email FROM users")
        
        # Очищаем и заполняем кэш
        authorized_users_cache.clear()
        users_email_cache.clear()
        
        for user in users:
            user_id_str = user['users_id']  # Это строка из БД
            user_id_int = int(user_id_str)   # Преобразуем в int для кэша
            email = user['email']
            authorized_users_cache.add(user_id_int)
            users_email_cache[user_id_int] = email
        
        logger.info(f"✅ Загружено {len(authorized_users_cache)} пользователей в кэш")
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки пользователей: {e}")
        return False
    finally:
        release_db_connection(conn)

async def check_user_authorized(user_id: int) -> bool:
    """Проверяет авторизацию пользователя (сначала кэш, потом БД)."""
    # Проверяем кэш
    if user_id in authorized_users_cache:
        logger.debug(f"Пользователь {user_id} найден в кэше")
        return True
    
    # Проверяем БД
    conn = await get_db_connection()
    if not conn:
        logger.warning(f"БД недоступна, пользователь {user_id} не найден в кэше")
        return False
    
    try:
        # Преобразуем user_id в строку для запроса к БД
        result = await conn.fetchrow(
            "SELECT users_id, email FROM users WHERE users_id = $1",
            str(user_id)  # Преобразуем в строку
        )
        
        if result:
            # Добавляем в кэш
            authorized_users_cache.add(user_id)
            users_email_cache[user_id] = result['email']
            logger.info(f"Пользователь {user_id} найден в БД и добавлен в кэш")
            return True
        else:
            logger.debug(f"Пользователь {user_id} не найден в БД")
            return False
        
    except Exception as e:
        logger.error(f"Ошибка проверки авторизации: {e}")
        return False
    finally:
        release_db_connection(conn)

async def add_authorized_user(user_id: int, email: str) -> bool:
    """Добавляет пользователя в БД и кэш."""
    logger.info(f"Добавление пользователя {user_id} с email {email}")
    
    # Сначала пытаемся добавить в БД
    conn = await get_db_connection()
    if not conn:
        logger.warning(f"БД недоступна, пользователь {user_id} добавлен только в кэш")
        # Добавляем в кэш даже если БД недоступна
        authorized_users_cache.add(user_id)
        users_email_cache[user_id] = email
        return True
    
    try:
        # Преобразуем user_id в строку для БД
        user_id_str = str(user_id)
        
        # Сначала проверяем, есть ли уже такой пользователь
        existing_user = await conn.fetchrow(
            "SELECT users_id, email FROM users WHERE users_id = $1",
            user_id_str
        )
        
        if existing_user:
            # Пользователь уже существует, обновляем email если изменился
            if existing_user['email'] != email:
                await conn.execute(
                    "UPDATE users SET email = $1, created_at = CURRENT_TIMESTAMP WHERE users_id = $2",
                    email, user_id_str
                )
                logger.info(f"✅ Email пользователя {user_id} обновлен")
            else:
                logger.info(f"✅ Пользователь {user_id} уже существует в БД")
        else:
            # Добавляем нового пользователя
            await conn.execute(
                "INSERT INTO users (users_id, email) VALUES ($1, $2)",
                user_id_str, email
            )
            logger.info(f"✅ Пользователь {user_id} добавлен в БД")
        
        # Добавляем в кэш после успешной записи в БД
        authorized_users_cache.add(user_id)
        users_email_cache[user_id] = email
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка добавления пользователя в БД: {e}")
        # Добавляем в кэш, даже если БД не работает
        authorized_users_cache.add(user_id)
        users_email_cache[user_id] = email
        return True
    finally:
        release_db_connection(conn)

async def remove_authorized_user(user_id: int) -> bool:
    """Удаляет пользователя из БД и кэша."""
    logger.info(f"Удаление пользователя {user_id}")
    
    # Удаляем из кэша
    authorized_users_cache.discard(user_id)
    users_email_cache.pop(user_id, None)
    
    # Пытаемся удалить из БД
    conn = await get_db_connection()
    if not conn:
        logger.warning(f"БД недоступна, пользователь {user_id} удален только из кэша")
        return True
    
    try:
        # Преобразуем user_id в строку для БД
        result = await conn.execute('DELETE FROM users WHERE users_id = $1', str(user_id))
        logger.info(f"✅ Пользователь {user_id} удален из БД (затронуто строк: {result})")
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка удаления пользователя: {e}")
        return True  # Из кэша удален в любом случае
    finally:
        release_db_connection(conn)

def is_valid_email(email: str) -> bool:
    """Проверяет валидность email."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    user_id = message.from_user.id
    user_name = f'{message.from_user.first_name} {message.from_user.last_name}'
    
    logger.info(f'Команда /start от пользователя {user_id} ({user_name})')
    
    # Проверяем авторизацию
    is_authorized = await check_user_authorized(user_id)
    
    if is_authorized:
        logger.info(f'✅ Пользователь {user_id} уже авторизован')
        await show_strategies(message)
    else:
        logger.info(f'❌ Пользователь {user_id} не авторизован, запрашиваем email')
        await message.answer(
            "Добро пожаловать в Сбер CPNB Bot!\n\n"
            "Для доступа к боту необходима авторизация.\n"
            "Введите ваш email для получения кода подтверждения:"
        )
        await UserStates.WAITING_EMAIL.set()

async def show_strategies(message: types.Message):
    """Показывает стратегии пользователю."""
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    
    for name, username in STRATEGIES.items():
        # Формируем ссылку для перехода
        bot_url = f"https://t.me/{username}?start=ref_{message.from_user.id}"
        # Добавляем кнопку
        keyboard.add(types.InlineKeyboardButton(name, url=bot_url))
    
    # Добавляем кнопку выхода из аккаунта
    keyboard.add(types.InlineKeyboardButton("🚪 Выйти из аккаунта", callback_data="logout"))
    
    await message.answer(
        "✅ Вы авторизованы!\n\n"
        "Выберите нужную стратегию:", 
        reply_markup=keyboard
    )

@dp.message_handler(state=UserStates.WAITING_EMAIL)
async def process_email(message: types.Message, state: FSMContext):
    email = message.text.strip().lower()
    
    if not is_valid_email(email):
        await message.answer("❌ Некорректный email. Введите правильный email:")
        return
    
    # Генерируем и отправляем код
    code = email_sender.generate_code()
    
    await message.answer("📧 Отправляю код на ваш email...")
    
    success = await email_sender.send_verification_code(email, code)
    
    if not success:
        await message.answer(
            "❌ Не удалось отправить код на email.\n"
            "Проверьте правильность адреса или попробуйте позже.\n\n"
            "Введите email еще раз:"
        )
        return
    
    # Сохраняем данные в состоянии
    await state.update_data(
        email=email,
        verification_code=code,
        user_id=message.from_user.id
    )
    
    await message.answer(
        f"Код отправлен на {email}\n\n"
        "Введите 6-значный код из письма:"
    )
    await UserStates.WAITING_CODE.set()

@dp.message_handler(state=UserStates.WAITING_CODE)
async def process_verification_code(message: types.Message, state: FSMContext):
    entered_code = message.text.strip()
    user_data = await state.get_data()
    
    correct_code = user_data.get('verification_code')
    
    if entered_code != correct_code:
        await message.answer(
            "❌ Неверный код!\n\n"
            "Введите код еще раз или отправьте /start для нового кода:"
        )
        return
    
    # Код правильный - авторизуем пользователя
    user_id = user_data.get('user_id')
    email = user_data.get('email')
    
    success = await add_authorized_user(user_id, email)
    
    if success:
        await message.answer("Авторизация успешна!")
        await state.finish()
        await show_strategies(message)
    else:
        await message.answer(
            "Ошибка при авторизации.\n"
            "Попробуйте еще раз позже или обратитесь к администратору."
        )
        await state.finish()

@dp.callback_query_handler(lambda c: c.data == 'logout')
async def process_logout(callback_query: types.CallbackQuery):
    """Обработка выхода из аккаунта."""
    user_id = callback_query.from_user.id
    
    await callback_query.answer("Выходим из аккаунта...")
    
    success = await remove_authorized_user(user_id)
    
    if success:
        await callback_query.message.edit_text(
            "Вы успешно вышли из аккаунта!\n\n"
            "Для повторного входа отправьте команду /start"
        )
        logger.info(f"Пользователь {user_id} вышел из аккаунта")
    else:
        await callback_query.message.edit_text(
            "❌ Ошибка при выходе из аккаунта.\n"
            "Попробуйте позже или обратитесь к администратору.\n\n"
            "Команды: /start - авторизация"
        )

@dp.message_handler(commands=['logout'])
async def logout_command(message: types.Message):
    """Команда для выхода из аккаунта."""
    user_id = message.from_user.id
    
    # Проверяем, авторизован ли пользователь
    is_authorized = await check_user_authorized(user_id)
    
    if not is_authorized:
        await message.answer(
            "❌ Вы не авторизованы.\n"
            "Используйте /start для входа в систему."
        )
        return
    
    # Создаем клавиатуру подтверждения
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("Да", callback_data="confirm_logout"),
        types.InlineKeyboardButton("Нет", callback_data="cancel_logout")
    )
    
    await message.answer(
        "Вы уверены, что хотите выйти из аккаунта?\n\n"
        "После выхода потребуется повторная авторизация по email.",
        reply_markup=keyboard
    )

@dp.callback_query_handler(lambda c: c.data == 'confirm_logout')
async def confirm_logout(callback_query: types.CallbackQuery):
    """Подтверждение выхода из аккаунта."""
    user_id = callback_query.from_user.id
    
    await callback_query.answer("Выходим из аккаунта...")
    
    success = await remove_authorized_user(user_id)
    
    if success:
        await callback_query.message.edit_text(
            "Вы успешно вышли из аккаунта!\n\n"
            "Для повторного входа отправьте команду /start"
        )
    else:
        await callback_query.message.edit_text(
            "❌ Ошибка при выходе из аккаунта.\n"
            "Попробуйте позже."
        )

@dp.callback_query_handler(lambda c: c.data == 'cancel_logout')
async def cancel_logout(callback_query: types.CallbackQuery):
    """Отмена выхода из аккаунта."""
    await callback_query.answer("Отменено")
    await callback_query.message.edit_text(
        "❌ Выход из аккаунта отменен.\n\n"
        "Вы остались авторизованы. Используйте /start для доступа к стратегиям."
    )

@dp.message_handler(commands=['dbtest'])
async def db_test_command(message: types.Message):
    """Тестирование подключения к БД."""
    await message.answer("🔍 Тестирую подключение к БД...")
    
    conn = await get_db_connection()
    if conn:
        try:
            # Тестируем различные запросы
            version = await conn.fetchval("SELECT version()")
            current_db = await conn.fetchval("SELECT current_database()")
            
            # Проверяем таблицу users
            table_exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'users'
                )
            """)
            
            # Проверяем ограничения
            constraints = await conn.fetch("""
                SELECT constraint_name, constraint_type 
                FROM information_schema.table_constraints 
                WHERE table_name = 'users'
            """)
            
            constraint_info = "\n".join([
                f"• {row['constraint_name']}: {row['constraint_type']}"
                for row in constraints
            ]) if constraints else "• Нет ограничений"
            
            if table_exists:
                user_count = await conn.fetchval("SELECT COUNT(*) FROM users")
                sample_users = await conn.fetch("SELECT users_id, email, created_at FROM users LIMIT 3")
                
                sample_text = "\n".join([
                    f"• ID: {row['users_id']}, Email: {row['email'][:20]}..."
                    for row in sample_users
                ]) if sample_users else "• Нет пользователей"
            else:
                user_count = "Таблица не существует"
                sample_text = "Таблица users не найдена"
            
            result = f"""✅ Подключение к БД успешно!
            
📊 Информация о БД:
• База данных: {current_db}
• Таблица users: {'существует' if table_exists else 'не существует'}
• Пользователей в БД: {user_count}
• Пользователей в кэше: {len(authorized_users_cache)}

🔒 Ограничения таблицы:
{constraint_info}

👥 Примеры пользователей:
{sample_text}

🔧 PostgreSQL версия:  
{version[:100]}..."""
            
        except Exception as e:
            result = f"❌ Ошибка при тестировании: {type(e).__name__}: {e}"
        finally:
            release_db_connection(conn)
    else:
        result = """❌ Не удалось подключиться к БД
        
🔍 Возможные причины:
• Сервер PostgreSQL недоступен
• Неверная строка подключения  
• Проблемы с сетью или аутентификацией"""
    
    await message.answer(result)

@dp.message_handler(commands=['fixdb'])
async def fix_db_command(message: types.Message):
    """Команда для починки структуры БД."""
    await message.answer("🔧 Проверяю и исправляю структуру БД...")
    
    success = await ensure_table_exists()
    
    if success:
        await message.answer("✅ Структура БД проверена и исправлена!\n\nВыполните /dbtest для проверки.")
    else:
        await message.answer("❌ Не удалось исправить структуру БД. Проверьте логи.")

@dp.message_handler(commands=['status'])
async def status_command(message: types.Message):
    """Проверка статуса кэша и БД."""
    cache_count = len(authorized_users_cache)
    user_id = message.from_user.id
    in_cache = user_id in authorized_users_cache
    
    # Проверяем БД
    conn = await get_db_connection()
    if conn:
        try:
            db_count = await conn.fetchval("SELECT COUNT(*) FROM users")
            db_status = f"✅ БД: {db_count} пользователей"
        except Exception as e:
            db_status = f"❌ БД: ошибка - {e}"
        finally:
            release_db_connection(conn)
    else:
        db_status = "❌ БД: недоступна"
    
    status_text = f"""📊 Статус системы:
📦 Кэш: {cache_count} пользователей
{db_status}
👤 Ваш статус: {'✅ авторизован' if in_cache else '❌ не авторизован'}
🆔 Ваш ID: {user_id}"""
    
    await message.answer(status_text)

@dp.message_handler()
async def handle_other_messages(message: types.Message):
    """Обработка всех остальных сообщений."""
    await message.answer(
        "Используйте команду /start для начала работы с ботом."
    )

async def on_startup(dp):
    """Инициализация при запуске бота."""
    logger.info("🚀 Запуск бота...")
    
    # Инициализируем пул соединений
    pool_created = await init_db_pool()
    
    if pool_created:
        # Создаем таблицу если не существует
        table_created = await ensure_table_exists()
        
        if table_created:
            # Загружаем пользователей в кэш
            await load_authorized_users()
        else:
            logger.error("❌ Не удалось создать/проверить таблицу users")
    else:
        logger.warning("⚠️ БД недоступна при запуске - работаем только с кэшем")
    
    logger.info("✅ Бот готов к работе")

async def on_shutdown(dp):
    """Закрытие соединений при остановке бота."""
    global db_pool
    if db_pool:
        await db_pool.close()
        logger.info("✅ Пул соединений закрыт")

if __name__ == '__main__':
    executor.start_polling(
        dp, 
        skip_updates=True, 
        on_startup=on_startup,
        on_shutdown=on_shutdown
    )
