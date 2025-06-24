import logging
import random
import smtplib
import re
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

# База данных
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

async def init_database():
    """Инициализация базы данных."""
    global db_pool
    
    try:
        print("🔗 Пытаемся подключиться к БД...")
        print(f"📡 Строка подключения: {DATABASE_URL}")
        
        db_pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=5,
            command_timeout=30
        )
        print("✅ Пул подключений создан")
        
        # Тестируем подключение и проверяем таблицу
        async with db_pool.acquire() as connection:
            print("📋 Тестируем подключение...")
            result = await connection.fetchval("SELECT current_database()")
            print(f"✅ Подключение OK, база: {result}")
            
            # Проверяем существование таблицы users
            table_exists = await connection.fetchval("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'users'
                )
            """)
            
            if table_exists:
                print("✅ Таблица 'users' найдена")
                
                # Проверяем структуру таблицы
                columns = await connection.fetch("""
                    SELECT column_name, data_type 
                    FROM information_schema.columns 
                    WHERE table_name = 'users'
                    ORDER BY ordinal_position
                """)
                
                print("📋 Структура таблицы 'users':")
                for col in columns:
                    print(f"   - {col['column_name']}: {col['data_type']}")
            else:
                print("❌ Таблица 'users' НЕ найдена!")
                raise Exception("Таблица 'users' не существует")
        
        logger.info("✅ База данных инициализирована")
        
    except Exception as e:
        print(f"❌ ДЕТАЛЬНАЯ ОШИБКА: {e}")
        print(f"❌ Тип ошибки: {type(e)}")
        import traceback
        traceback.print_exc()
        logger.error(f"❌ Ошибка инициализации базы данных: {e}")
        raise

async def check_user_authorized(user_id: int) -> bool:
    """Проверяет, авторизован ли пользователь в таблице users."""
    if not db_pool:
        print(f"⚠️ БД недоступна, пропускаем пользователя {user_id}")
        return True  # Пропускаем всех если БД недоступна
        
    try:
        async with db_pool.acquire() as connection:
            # Проверяем в таблице users по столбцу user_id
            result = await connection.fetchval(
                "SELECT EXISTS(SELECT 1 FROM users WHERE user_id = $1)",
                user_id
            )
            print(f"🔍 Проверка пользователя {user_id}: {'✅ найден' if result else '❌ не найден'}")
            return bool(result)
    except Exception as e:
        print(f"❌ Ошибка проверки авторизации: {e}")
        logger.error(f"Ошибка проверки авторизации: {e}")
        return True  # В случае ошибки пропускаем пользователя

async def add_authorized_user(user_id: int, email: str) -> bool:
    """Добавляет пользователя в таблицу users (id, user_id, email)."""
    if not db_pool:
        print(f"⚠️ БД недоступна, не можем добавить пользователя {user_id}")
        return False
        
    try:
        async with db_pool.acquire() as connection:
            # Вставляем в таблицу users только user_id и email
            await connection.execute('''
                INSERT INTO users (user_id, email)
                VALUES ($1, $2)
                ON CONFLICT (user_id) DO UPDATE SET
                    email = EXCLUDED.email
            ''', user_id, email)
        
        print(f"✅ Пользователь {user_id} добавлен с email {email}")
        logger.info(f"Пользователь {user_id} авторизован с email {email}")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка добавления пользователя: {e}")
        logger.error(f"Ошибка добавления пользователя: {e}")
        return False

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

@dp.message_handler(commands=['reset'])
async def reset_auth(message: types.Message, state: FSMContext):
    """Сброс авторизации (для тестирования)."""
    await state.finish()
    await message.answer("🔄 Состояние сброшено. Отправьте /start для авторизации.")

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
        await init_database()
        print("🚀 Бот-маршрутизатор запущен")
        logger.info("🚀 Бот-маршрутизатор запущен")
    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА ЗАПУСКА: {e}")
        import traceback
        traceback.print_exc()
        logger.error(f"❌ Ошибка запуска: {e}")
        # НЕ ПОДНИМАЕМ ИСКЛЮЧЕНИЕ - пусть бот работает без БД
        print("⚠️ Бот запущен БЕЗ базы данных")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)