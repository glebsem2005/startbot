import asyncio
import asyncpg

async def test_database_connection():
    """Тестирует все возможные варианты подключения к базе."""
    
    # Возможные названия баз данных
    database_names = [
        "сбербот",
        "sber_bot", 
        "sberbot",
        "postgres",
        "сбер",
        "cpnb_bot"
    ]
    
    # Возможные пользователи
    users = [
        ("bot_admin", "sber"),
        ("bot_reader", "sber"),
        ("postgres", ""),  # без пароля
        ("postgres", "sber"),
    ]
    
    host = "172.20.10.13"
    port = 5432
    
    print("🔍 Тестируем подключения к базе данных...")
    
    for db_name in database_names:
        for user, password in users:
            try:
                if password:
                    connection_string = f"postgresql://{user}:{password}@{host}:{port}/{db_name}"
                else:
                    connection_string = f"postgresql://{user}@{host}:{port}/{db_name}"
                
                print(f"\n📡 Пробуем: {connection_string}")
                
                conn = await asyncpg.connect(connection_string)
                result = await conn.fetchval("SELECT current_database()")
                
                # Проверяем есть ли таблица authorized_users
                table_exists = await conn.fetchval("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = 'authorized_users'
                    )
                """)
                
                await conn.close()
                
                print(f"✅ УСПЕХ! База: {result}")
                print(f"📋 Таблица authorized_users: {'✅ есть' if table_exists else '❌ нет'}")
                print(f"🔗 Рабочая строка: {connection_string}")
                return connection_string, table_exists
                
            except Exception as e:
                print(f"❌ Ошибка: {str(e)[:100]}...")
    
    print("\n❌ Ни одно подключение не сработало!")
    return None, False

if __name__ == "__main__":
    asyncio.run(test_database_connection())