import logging
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor

# Настройка логирования
logging.basicConfig(level=logging.INFO)

TOKEN = '8182061892:AAHnfK9k5g4gaaUlEK8plhdFdVHKzHQzYg4'

STRATEGIES = {
    'анализ инвестиционной привлекательности': 'api_sand_bot',
    'скаутинг стартапов': 'startup_scout_bot',
    'подготовка сделки': 'deal_maker_bot'
}

bot = Bot(token=TOKEN)
dp = Dispatcher(bot)

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    help_text = "Отправьте одну из стратегий:\n" + "\n".join([f"• {key}" for key in STRATEGIES.keys()])
    await message.answer(help_text)

@dp.message_handler(content_types=types.ContentType.TEXT)
async def redirect_by_text(message: types.Message):
    user_text = message.text.lower().strip()  # Приводим текст к нижнему регистру и убираем пробелы
    
    if user_text in STRATEGIES:
        username = STRATEGIES[user_text]
        bot_url = f"https://t.me/{username}"
        
        # Отправляем сообщение с прямой ссылкой
        await message.answer(
            f"Перенаправляю вас к боту по стратегии: {user_text}\n\n"
            f"Переходите: {bot_url}",
            disable_web_page_preview=True
        )
    else:
        await message.answer("Стратегия не найдена. Введите корректное название.")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)