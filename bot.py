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
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Токен вашего бота-маршрутизатора
TOKEN = '8182061892:AAHnfK9k5g4gaaUlEK8plhdFdVHKzHQzYg4'

# Email настройки
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
EMAIL_USER = os.getenv('EMAIL_USER', 'glebsem2005@gmail.com')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD', 'kqbkhnqpskiumddc')

# База данных - можно изменить параметры подключения
DATABASE_URL = "postgresql://bot_admin:sber@172.20.10.13:5432/sber_bot"

# Альтернативные варианты подключения (раскомментируйте нужный):
# DATABASE_URL = "postgresql://bot_admin:sber@localhost:5432/sber_bot"  # если БД локально
# DATABASE_URL = "postgresql://postgres:password@localhost:5432/sber_bot"  # стандартные настройки

# Боты для перенаправления
STRATEGIES = {
    'Анализ инвестиционной привлекательности': 'sber_investment_bot',
    'Скаутинг стартапов': 'sber_startups_bot',
    'Подготовка сделки': 'sber_investment_bot',
}

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Простой кэш авторизованных пользователей
authorized_users_cache = set()
users_email_cache = {}

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

async def get_db_connection():
    """Создает новое подключение к БД с детальной диагностикой."""
    try:
        logger.info(f"Попытка подключения к БД: {DATABASE_URL}")
        
        conn = await asyncio.wait_for(
            asyncpg.connect(DATABASE_URL), 
            timeout=15.0
        )
        
        # Тестируем подключение
        await conn.fetchval("SELECT 1")
        logger.info("✅ Подключение к БД успешно")
        return conn
        
    except asyncio.TimeoutError:
        logger.error("❌ Таймаут подключения к БД (15 сек)")
        return None
    except asyncpg.InvalidCatalogNameError:
        logger.error("❌ База данных 'sber_bot' не существует")
        return None
    except asyncpg.InvalidPasswordError:
        logger.error("❌ Неверный пароль для пользователя БД")
        return None
    except asyncpg.ConnectionDoesNotExistError:
        logger.error("❌ Не удается подключиться к серверу БД")
        return None
    except Exception as e:
        logger.error(f"❌ Неизвестная ошибка подключения к БД: {type(e).__name__}: {e}")
        return None

async def load_authorized_users():
    """Загружает авторизованных пользователей из БД в кэш."""
    logger.info("Попытка загрузки пользователей из БД...")
    
    conn = await get_db_connection()
    if not conn:
        logger.warning("❌ Не удалось подключиться к БД для загрузки пользователей")
        logger.info("ℹ️ Бот будет работать только с локальным кэшем")
        return
    
    try:
        # Сначала проверим, существует ли таблица
        table_exists = await conn.fetchval("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'users'
            )
        """)
        
        if not table_exists:
            logger.warning("⚠️ Таблица 'users' не существует, создаем...")
            await conn.execute("""
                CREATE TABLE users (
                    id SERIAL PRIMARY KEY,
                    users_id BIGINT UNIQUE NOT NULL,
                    email VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            logger.info("✅ Таблица 'users' создана")
            return
        
        # Загружаем всех пользователей
        users = await conn.fetch("SELECT users_id, email FROM users")
        
        # Очищаем и заполняем кэш
        authorized_users_cache.clear()
        users_email_cache.clear()
        
        for user in users:
            user_id = user['users_id']
            email = user['email']
            authorized_users_cache.add(user_id)
            users_email_cache[user_id] = email
        
        logger.info(f"✅ Загружено {len(authorized_users_cache)} пользователей в кэш")
        
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки пользователей: {type(e).__name__}: {e}")
    finally:
        await conn.close()

async def check_user_authorized(user_id: int) -> bool:
    """Проверяет авторизацию пользователя (сначала кэш, потом БД)."""
    # Проверяем кэш
    if user_id in authorized_users_cache:
        return True
    
    # Проверяем БД
    conn = await get_db_connection()
    if not conn:
        logger.warning(f"БД недоступна, пользователь {user_id} не найден в кэше")
        return False
    
    try:
        result = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM users WHERE users_id = $1)",
            user_id
        )
        
        if result:
            # Добавляем в кэш
            authorized_users_cache.add(user_id)
            # Получаем email
            email = await conn.fetchval(
                "SELECT email FROM users WHERE users_id = $1",
                user_id
            )
            if email:
                users_email_cache[user_id] = email
        
        return bool(result)
        
    except Exception as e:
        logger.error(f"Ошибка проверки авторизации: {e}")
        return False
    finally:
        await conn.close()

async def add_authorized_user(user_id: int, email: str) -> bool:
    """Добавляет пользователя в БД и кэш."""
    # Добавляем в кэш сразу
    authorized_users_cache.add(user_id)
    users_email_cache[user_id] = email
    
    # Пытаемся добавить в БД
    conn = await get_db_connection()
    if not conn:
        logger.warning(f"БД недоступна, пользователь {user_id} добавлен только в кэш")
        return True  # Считаем успехом, так как в кэше есть
    
    try:
        await conn.execute(
            "INSERT INTO users (users_id, email) VALUES ($1, $2) ON CONFLICT (users_id) DO UPDATE SET email = $2",
            user_id, email
        )
        logger.info(f"Пользователь {user_id} добавлен в БД")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка добавления пользователя в БД: {e}")
        return True  # Пользователь все равно в кэше
    finally:
        await conn.close()

async def remove_authorized_user(user_id: int) -> bool:
    """Удаляет пользователя из БД и кэша."""
    # Удаляем из кэша
    authorized_users_cache.discard(user_id)
    users_email_cache.pop(user_id, None)
    
    # Пытаемся удалить из БД
    conn = await get_db_connection()
    if not conn:
        logger.warning(f"БД недоступна, пользователь {user_id} удален только из кэша")
        return True
    
    try:
        await conn.execute('DELETE FROM users WHERE users_id = $1', user_id)
        logger.info(f"Пользователь {user_id} удален из БД")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка удаления пользователя: {e}")
        return True  # Из кэша удален
    finally:
        await conn.close()

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
            "👋 Добро пожаловать в Сбер CPNB Bot!\n\n"
            "Для доступа к боту необходима авторизация.\n"
            "📧 Введите ваш email для получения кода подтверждения:"
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
        f"✅ Код отправлен на {email}\n\n"
        "🔢 Введите 6-значный код из письма:"
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
        await message.answer("✅ Авторизация успешна!")
        await state.finish()
        await show_strategies(message)
    else:
        await message.answer(
            "❌ Ошибка при авторизации.\n"
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
            "🚪 Вы успешно вышли из аккаунта!\n\n"
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
        types.InlineKeyboardButton("✅ Да", callback_data="confirm_logout"),
        types.InlineKeyboardButton("❌ Нет", callback_data="cancel_logout")
    )
    
    await message.answer(
        "🚪 Вы уверены, что хотите выйти из аккаунта?\n\n"
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
            "✅ Вы успешно вышли из аккаунта!\n\n"
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
    """Тестирование подключения к БД (только для отладки)."""
    await message.answer("🔍 Тестирую подключение к БД...")
    
    conn = await get_db_connection()
    if conn:
        try:
            # Тестируем различные запросы
            version = await conn.fetchval("SELECT version()")
            current_db = await conn.fetchval("SELECT current_database()")
            user_count = await conn.fetchval("SELECT COUNT(*) FROM users")
            
            result = f"""✅ Подключение к БД успешно!
            
📊 Информация о БД:
• База данных: {current_db}
• Пользователей в БД: {user_count}
• Пользователей в кэше: {len(authorized_users_cache)}

🔧 Версия PostgreSQL:  
{version[:100]}..."""
            
        except Exception as e:
            result = f"❌ Ошибка при тестировании: {type(e).__name__}: {e}"
        finally:
            await conn.close()
    else:
        result = """❌ Не удалось подключиться к БД
        
🔍 Возможные причины:
• Сервер PostgreSQL не запущен
• Неверная строка подключения  
• База данных не существует
• Неверный логин/пароль
• Проблемы с сетью"""
    
    await message.answer(result)

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
            await conn.close()
    else:
        db_status = "❌ БД: недоступна"
    
    status_text = f"""📊 Статус системы:
📦 Кэш: {cache_count} пользователей
{db_status}
👤 Ваш статус: {'✅ авторизован' if in_cache else '❌ не авторизован'}"""
    
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
    
    # Выводим информацию о подключении
    logger.info(f"📡 Строка подключения: {DATABASE_URL}")
    
    # Пробуем подключиться и создать таблицу
    conn = await get_db_connection()
    if conn:
        try:
            logger.info("✅ Подключение к БД установлено")
            
            # Проверяем и создаем таблицу
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    users_id BIGINT UNIQUE NOT NULL,
                    email VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            logger.info("✅ Таблица users готова")
            
        except Exception as e:
            logger.error(f"❌ Ошибка при создании таблицы: {type(e).__name__}: {e}")
        finally:
            await conn.close()
        
        # Загружаем пользователей в кэш
        await load_authorized_users()
        
    else:
        logger.warning("⚠️ БД недоступна при запуске - работаем только с кэшем")
        logger.info("ℹ️ Проверьте:")
        logger.info("   1. Запущен ли сервер PostgreSQL")
        logger.info("   2. Правильность строки подключения")
        logger.info("   3. Существует ли база данных 'sber_bot'")
        logger.info("   4. Правильность логина/пароля")
    
    logger.info("✅ Бот готов к работе")

if __name__ == '__main__':
    executor.start_polling(
        dp, 
        skip_updates=True, 
        on_startup=on_startup
    )