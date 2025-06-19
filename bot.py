import logging
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Токен вашего бота-маршрутизатора
TOKEN = '8182061892:AAHnfK9k5g4gaaUlEK8plhdFdVHKzHQzYg4'

STRATEGIES = {
    'Анализ инвестиционной привлекательности': {'username': 'api_sand_bot'},
    'Скаутинг стартапов': {'username': 'Bot2Username'},
    'Подготовка сделки' : {'username': 'Bot2Username'},
}

bot = Bot(token=TOKEN)
dp = Dispatcher(bot)

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    # Создаем inline-кнопки
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    
    for name, username in STRATEGIES.items():
        # Формируем ссылку для перехода
        bot_url = f"https://t.me/{username}?start=ref_{message.from_user.id}"
        # Добавляем кнопку
        keyboard.add(types.InlineKeyboardButton(name, url=bot_url))
    
    await message.answer(
        "Выберите стратегию:", 
        reply_markup=keyboard
    )

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)