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

# База данных с улучшенными параметрами
DATABASE_URL = "postgresql://bot_admin:sber@172.20.10.13:5432/sber_bot"

# Боты для перенаправления
STRATEGIES = {
    'Анализ инвестиционной привлекательности': 'api_sand_bot',
    'Скаутинг стартапов': 'startupsberaibot',
    'Подготовка сделки': 'api_sand_bot',
}

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Глобальный пул подключений к БД
db_pool = None

# Локальное хранение авторизованных пользователей (резерв на случай недоступности БД)
authorized_users_cache = set()  # Множество telegram_id авторизованных пользователей
users_email_cache = {}  # Словарь {telegram_id: email} для хранения email пользователей

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

async def test_database_connection() -> bool:
    """Тестирует подключение к базе данных с таймаутом."""
    try:
        print("🔍 Тестируем подключение к БД...")
        
        # Пробуем подключиться с коротким таймаутом
        conn = await asyncio.wait_for(
            asyncpg.connect(DATABASE_URL), 
            timeout=10.0  # 10 секунд таймаут
        )
        
        # Тестируем запрос
        result = await asyncio.wait_for(
            conn.fetchval("SELECT 1"), 
            timeout=5.0
        )
        
        await conn.close()
        print("✅ Тестовое подключение успешно")
        return True
        
    except asyncio.TimeoutError:
        print("❌ Таймаут подключения к БД")
        return False
    except Exception as e:
        print(f"❌ Ошибка тестового подключения: {e}")
        return False

async def load_authorized_users_to_cache():
    global authorized_users_cache, users_email_cache, db_pool  # ✅ Все переменные объявлены
    
    if not db_pool:  # ✅ Теперь можно использовать
        
    try:
        async with db_pool.acquire() as connection:
            # Загружаем всех авторизованных пользователей
            users = await asyncio.wait_for(
                connection.fetch("SELECT users_id, email FROM users"),
                timeout=15.0
            )
            
            # Очищаем старый кэш
            authorized_users_cache.clear()
            users_email_cache.clear()
            
            # Заполняем кэш
            for user in users:
                user_id = user['users_id']  # Используем правильное имя столбца
                email = user['email']
                authorized_users_cache.add(user_id)
                users_email_cache[user_id] = email
            
            print(f"✅ Загружено {len(authorized_users_cache)} пользователей в кэш")
            logger.info(f"Загружено {len(authorized_users_cache)} пользователей в кэш")
            return True
            
    except asyncio.TimeoutError:
        print("❌ Таймаут загрузки пользователей в кэш")
        logger.error("Таймаут загрузки пользователей в кэш")
        return False
    except Exception as e:
        print(f"❌ Ошибка загрузки кэша: {e}")
        logger.error(f"Ошибка загрузки кэша: {e}")
        return False
    """Инициализация базы данных с улучшенной обработкой ошибок."""
    global db_pool
    
    try:
        print("🔗 Начинаем инициализацию БД...")
        print(f"📡 Строка подключения: {DATABASE_URL}")
        
        # Сначала тестируем подключение
        if not await test_database_connection():
            raise Exception("Не удалось установить тестовое подключение")
        
        print("🏊 Создаем пул подключений...")
        
        # Создаем пул с улучшенными параметрами
        db_pool = await asyncio.wait_for(
            asyncpg.create_pool(
                DATABASE_URL,
                min_size=1,
                max_size=3,  # Уменьшаем размер пула
                command_timeout=20,  # Таймаут команд
                server_settings={
                    'jit': 'off',  # Отключаем JIT для стабильности
                    'application_name': 'telegram_bot'
                }
            ),
            timeout=30.0  # Общий таймаут создания пула
        )
        
        print("✅ Пул подключений создан")
        
        # Тестируем пул
        async with db_pool.acquire() as connection:
            print("📋 Тестируем пул подключений...")
            result = await asyncio.wait_for(
                connection.fetchval("SELECT current_database()"), 
                timeout=10.0
            )
            print(f"✅ Подключение OK, база: {result}")
            
            # Проверяем существование таблицы users
            table_exists = await asyncio.wait_for(
                connection.fetchval("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = 'users'
                    )
                """),
                timeout=10.0
            )
            
            if table_exists:
                print("✅ Таблица 'users' найдена")
                
                # Проверяем структуру таблицы
                columns = await asyncio.wait_for(
                    connection.fetch("""
                        SELECT column_name, data_type 
                        FROM information_schema.columns 
                        WHERE table_name = 'users'
                        ORDER BY ordinal_position
                    """),
                    timeout=10.0
                )
                
                print("📋 Структура таблицы 'users':")
                for col in columns:
                    print(f"   - {col['column_name']}: {col['data_type']}")
                    
                # Проверяем, какой именно столбец для user_id используется
                user_id_column = None
                for col in columns:
                    if col['column_name'] in ['user_id', 'users_id', 'telegram_id']:
                        user_id_column = col['column_name']
                        break
                
                if user_id_column:
                    print(f"✅ Найден столбец для Telegram ID: {user_id_column}")
                else:
                    print("❌ Не найден столбец для Telegram ID!")
            else:
                print("❌ Таблица 'users' НЕ найдена!")
                # Пытаемся создать таблицу
                print("🔨 Создаем таблицу 'users'...")
                await connection.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        users_id BIGINT UNIQUE NOT NULL,
                        email VARCHAR(255) NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                print("✅ Таблица 'users' создана")
        
        logger.info("✅ База данных инициализирована успешно")
        
        # Загружаем пользователей в кэш
        await load_authorized_users_to_cache()
        
        return True
        
    except asyncio.TimeoutError:
        print("❌ ТАЙМАУТ при инициализации БД")
        logger.error("❌ Таймаут инициализации базы данных")
        return False
    except Exception as e:
        print(f"❌ ДЕТАЛЬНАЯ ОШИБКА: {e}")
        print(f"❌ Тип ошибки: {type(e)}")
        import traceback
        traceback.print_exc()
        logger.error(f"❌ Ошибка инициализации базы данных: {e}")
        return False

async def check_user_authorized(user_id: int) -> bool:
    """Проверяет, авторизован ли пользователь (сначала в кэше, затем в БД)."""
    
    # Сначала проверяем в локальном кэше
    if user_id in authorized_users_cache:
        print(f"✅ Пользователь {user_id} найден в кэше")
        return True
    
    # Если БД недоступна, полагаемся только на кэш
    if not db_pool:
        print(f"❌ БД недоступна, пользователь {user_id} НЕ найден в кэше - отказываем в доступе")
        return False
        
    try:
        async with db_pool.acquire() as connection:
            # Проверяем в БД
            result = await asyncio.wait_for(
                connection.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM users WHERE users_id = $1)",
                    user_id
                ),
                timeout=10.0
            )
            
            # Если пользователь найден в БД, добавляем в кэш
            if result:
                authorized_users_cache.add(user_id)
                # Получаем email для кэша
                email = await asyncio.wait_for(
                    connection.fetchval(
                        "SELECT email FROM users WHERE users_id = $1",
                        user_id
                    ),
                    timeout=5.0
                )
                if email:
                    users_email_cache[user_id] = email
                    
            print(f"🔍 Проверка пользователя {user_id} в БД: {'✅ найден' if result else '❌ не найден'}")
            return bool(result)
            
    except asyncio.TimeoutError:
        print(f"❌ Таймаут проверки пользователя {user_id} - полагаемся на кэш")
        logger.error(f"Таймаут проверки авторизации пользователя {user_id}")
        return user_id in authorized_users_cache
    except Exception as e:
        print(f"❌ Ошибка проверки авторизации: {e} - полагаемся на кэш")
        logger.error(f"Ошибка проверки авторизации: {e}")
        return user_id in authorized_users_cache

async def remove_authorized_user(user_id: int) -> bool:
    """Удаляет пользователя из БД и кэша (выход из аккаунта)."""
    
    # Сначала удаляем из кэша
    authorized_users_cache.discard(user_id)  # discard не вызывает ошибку если элемента нет
    users_email_cache.pop(user_id, None)  # pop с default не вызывает ошибку
    
    if not db_pool:
        print(f"⚠️ БД недоступна, пользователь {user_id} удален только из кэша")
        logger.warning(f"Пользователь {user_id} удален только из локального кэша")
        return True  # Считаем успешным, так как удален из кэша
        
    try:
        async with db_pool.acquire() as connection:
            # Удаляем из БД
            result = await asyncio.wait_for(
                connection.execute(
                    'DELETE FROM users WHERE users_id = $1', 
                    user_id
                ),
                timeout=10.0
            )
        
        print(f"✅ Пользователь {user_id} удален из БД и кэша")
        logger.info(f"Пользователь {user_id} вышел из аккаунта")
        return True
        
    except asyncio.TimeoutError:
        print(f"❌ Таймаут удаления пользователя {user_id} из БД, но удален из кэша")
        logger.error(f"Таймаут удаления пользователя {user_id} из БД")
        return True  # Удален из кэша
    except Exception as e:
        print(f"❌ Ошибка удаления пользователя из БД: {e}, но удален из кэша")
        logger.error(f"Ошибка удаления пользователя из БД: {e}")
        return True  # Удален из кэша

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
    
    # Показываем, что обрабатываем запрос
    await callback_query.answer("Выходим из аккаунта...")
    
    # Удаляем пользователя из БД
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

@dp.message_handler(commands=['dbstatus'])
async def db_status(message: types.Message):
    """Проверка статуса БД и кэша."""
    status_lines = []
    
    # Статус кэша
    cache_count = len(authorized_users_cache)
    status_lines.append(f"📦 Кэш: {cache_count} пользователей")
    
    # Статус БД
    if not db_pool:
        status_lines.append("❌ БД: не инициализирована")
    else:
        try:
            async with db_pool.acquire() as connection:
                result = await asyncio.wait_for(
                    connection.fetchval("SELECT COUNT(*) FROM users"),
                    timeout=5.0
                )
                status_lines.append(f"✅ БД: {result} пользователей")
        except Exception as e:
            status_lines.append(f"❌ БД: ошибка - {e}")
    
    # Проверка пользователя
    user_id = message.from_user.id
    in_cache = user_id in authorized_users_cache
    status_lines.append(f"👤 Вы в кэше: {'✅' if in_cache else '❌'}")
    
    await message.answer("\n".join(status_lines))

@dp.message_handler()
async def handle_other_messages(message: types.Message):
    """Обработка всех остальных сообщений."""
    await message.answer(
        "Используйте команду /start для начала работы с ботом."
    )

async def on_startup(dp):
    """Инициализация при запуске бота."""
    try:
        print("🚀 Запускаем инициализацию...")
        db_success = await init_database()
        
        if db_success:
            print("🚀 Бот-маршрутизатор запущен с БД")
            logger.info("🚀 Бот-маршрутизатор запущен с БД")
        else:
            print("⚠️ Бот запущен БЕЗ базы данных")
            logger.warning("⚠️ Бот запущен БЕЗ базы данных")
            
    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА ЗАПУСКА: {e}")
        import traceback
        traceback.print_exc()
        logger.error(f"❌ Ошибка запуска: {e}")
        print("⚠️ Бот запущен БЕЗ базы данных")

async def on_shutdown(dp):
    """Очистка ресурсов при остановке."""
    global db_pool
    if db_pool:
        print("🔌 Закрываем пул подключений...")
        await db_pool.close()
        print("✅ Пул подключений закрыт")

if __name__ == '__main__':
    executor.start_polling(
        dp, 
        skip_updates=True, 
        on_startup=on_startup,
        on_shutdown=on_shutdown
    )