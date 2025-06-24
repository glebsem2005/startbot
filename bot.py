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

# Email настройки (добавьте в переменные окружения или замените на свои)
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
EMAIL_USER = os.getenv('EMAIL_USER', 'glebsem2005@gmail.com')  # Замените на свой email
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD', 'kqbkhnqpskiumddc')  # Замените на app password

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
        db_pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=5,
            command_timeout=30
        )
        
        # Создаем таблицу если не существует
        async with db_pool.acquire() as connection:
            await connection.execute('''
                CREATE TABLE IF NOT EXISTS authorized_users (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT UNIQUE NOT NULL,
                    email VARCHAR(255) NOT NULL,
                    username VARCHAR(255),
                    first_name VARCHAR(255),
                    last_name VARCHAR(255),
                    authorized_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        
        logger.info("✅ База данных инициализирована")
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации базы данных: {e}")
        raise

async def check_user_authorized(user_id: int) -> bool:
    """Проверяет, авторизован ли пользователь."""
    try:
        async with db_pool.acquire() as connection:
            result = await connection.fetchval(
                "SELECT EXISTS(SELECT 1 FROM authorized_users WHERE user_id = $1)",
                user_id
            )
            return bool(result)
    except Exception as e:
        logger.error(f"Ошибка проверки авторизации: {e}")
        return False

async def add_authorized_user(user_id: int, email: str, username: str = None, 
                            first_name: str = None, last_name: str = None) -> bool:
    """Добавляет пользователя в список авторизованных."""
    try:
        async with db_pool.acquire() as connection:
            await connection.execute('''
                INSERT INTO authorized_users (user_id, email, username, first_name, last_name)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (user_id) DO UPDATE SET
                    email = EXCLUDED.email,
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name,
                    last_name = EXCLUDED.last_name,
                    authorized_at = CURRENT_TIMESTAMP
            ''', user_id, email, username, first_name, last_name)
        
        logger.info(f"Пользователь {user_id} авторизован с email {email}")
        return True
        
    except Exception as e:
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
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name
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
    username = user_data.get('username')
    first_name = user_data.get('first_name')
    last_name = user_data.get('last_name')
    
    success = await add_authorized_user(user_id, email, username, first_name, last_name)
    
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
        await init_database()
        logger.info("🚀 Бот-маршрутизатор запущен")
    except Exception as e:
        logger.error(f"❌ Ошибка запуска: {e}")
        raise

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)