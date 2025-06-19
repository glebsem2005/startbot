import logging
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Токен вашего бота-маршрутизатора
ROUTER_BOT_TOKEN = '8182061892:AAHnfK9k5g4gaaUlEK8plhdFdVHKzHQzYg4'

BOTS = {
    'Анализ инвестиционной привлекательности': {'token': 'BOT1_TOKEN', 'username': 'sand_bot'},
    'Скаутинг стартапов': {'token': 'BOT2_TOKEN', 'username': 'Bot2Username'},
    'Подготовка сделки' : {'token': 'BOT2_TOKEN', 'username': 'Bot2Username'},
}

# Инициализация бота и диспетчера
bot = Bot(token=ROUTER_BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Состояния для FSM
class RouterStates(StatesGroup):
    waiting_for_strategy = State()

# Обработчик команды /start
@dp.message_handler(commands=['start'], state='*')
async def cmd_start(message: types.Message):
    # Создаем клавиатуру с кнопками стратегий
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for strategy in BOTS.keys():
        keyboard.add(strategy)
    
    await message.answer(
        "Выберите стратегию для перенаправления:",
        reply_markup=keyboard
    )
    await RouterStates.waiting_for_strategy.set()

# Обработчик выбора стратегии
@dp.message_handler(state=RouterStates.waiting_for_strategy)
async def process_strategy(message: types.Message, state: FSMContext):
    strategy = message.text.lower()
    
    if strategy not in BOTS:
        await message.answer("Пожалуйста, выберите стратегию из предложенных вариантов.")
        return
    
    # Получаем информацию о целевом боте
    target_bot = BOTS[strategy]
    
    # Создаем deep link для перенаправления
    deep_link = f"https://t.me/{target_bot['username']}?start={message.from_user.id}"
    
    # Отправляем пользователю ссылку на выбранного бота
    await message.answer(
        f"Вы выбрали стратегию '{strategy}'. Переходите к боту: {deep_link}",
        reply_markup=types.ReplyKeyboardRemove()
    )
    
    # Можно также отправить пользователя сразу, используя кнопку
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton(
        text="Перейти к боту", 
        url=deep_link
    ))
    
    await message.answer("Нажмите кнопку ниже для перехода:", reply_markup=keyboard)
    
    # Завершаем состояние
    await state.finish()

if __name__ == '__main__':
    from aiogram import executor
    executor.start_polling(dp, skip_updates=True)