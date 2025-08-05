import asyncpg
from typing import List, Tuple, Optional, Dict, Any
from datetime import datetime
import pytz
import logging
import asyncio
from config import DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT

logger = logging.getLogger(__name__)

class Database:
    _instance = None
    _pool = None
    _moscow_tz = pytz.timezone('Europe/Moscow')

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def _init_connection(self, conn):
        """Инициализация соединения с установкой московского времени"""
        await conn.execute("SET TIME ZONE 'Europe/Moscow'")
        await conn.execute("SET datestyle = 'ISO, DMY'")

    async def create_pool(self):
        """Создание пула соединений с настройками"""
        if not self._pool:
            try:
                self._pool = await asyncpg.create_pool(
                    database=DB_NAME,
                    user=DB_USER,
                    password=DB_PASSWORD,
                    host=DB_HOST,
                    port=DB_PORT,
                    min_size=2,
                    max_size=10,
                    command_timeout=30,
                    init=self._init_connection,
                    timeout=10
                )
                logger.info("Database pool created successfully")
            except Exception as e:
                logger.error(f"Error creating database pool: {e}")
                raise

    async def close(self):
        """Закрытие пула соединений"""
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("Database pool closed")

    async def initialize_database(self):
        """Инициализация структуры БД"""
        await self._ensure_database_exists()
        await self.create_pool()
        
        async with self._pool.acquire() as conn:
            try:
                # Удаляем старые таблицы если они есть
                await conn.execute("DROP TABLE IF EXISTS test_results CASCADE")
                await conn.execute("DROP TABLE IF EXISTS tests CASCADE")
                await conn.execute("DROP TABLE IF EXISTS learning_session CASCADE")
                await conn.execute("DROP TABLE IF EXISTS user_progress CASCADE")
                await conn.execute("DROP TABLE IF EXISTS leaderboard CASCADE")
                await conn.execute("DROP TABLE IF EXISTS user_words CASCADE")
                await conn.execute("DROP TABLE IF EXISTS default_words CASCADE")
                await conn.execute("DROP TABLE IF EXISTS users CASCADE")
                
                # Создаем все таблицы заново
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id BIGINT PRIMARY KEY,
                        username VARCHAR(255),
                        first_name VARCHAR(255),
                        last_name VARCHAR(255),
                        registration_date TIMESTAMPTZ DEFAULT (NOW() AT TIME ZONE 'Europe/Moscow')
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS default_words (
                        word_id SERIAL PRIMARY KEY,
                        english_word VARCHAR(255) UNIQUE NOT NULL,
                        russian_translation VARCHAR(255) NOT NULL
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS user_words (
                        user_word_id SERIAL PRIMARY KEY,
                        user_id BIGINT REFERENCES users(user_id),
                        english_word VARCHAR(255) NOT NULL,
                        russian_translation VARCHAR(255) NOT NULL,
                        added_date TIMESTAMPTZ DEFAULT (NOW() AT TIME ZONE 'Europe/Moscow'),
                        UNIQUE(user_id, english_word)
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS tests (
                        test_id SERIAL PRIMARY KEY,
                        user_id BIGINT REFERENCES users(user_id),
                        start_time TIMESTAMPTZ DEFAULT (NOW() AT TIME ZONE 'Europe/Moscow'),
                        end_time TIMESTAMPTZ,
                        total_questions INT,
                        correct_answers INT,
                        incorrect_answers INT
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS test_results (
                        id SERIAL PRIMARY KEY,
                        test_id INTEGER REFERENCES tests(test_id) ON DELETE CASCADE,
                        word VARCHAR(100) NOT NULL,
                        correct_answer VARCHAR(100) NOT NULL,
                        user_answer VARCHAR(100) NOT NULL,
                        is_correct BOOLEAN NOT NULL,
                        answer_time TIMESTAMPTZ DEFAULT (NOW() AT TIME ZONE 'Europe/Moscow')
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS leaderboard (
                        user_id BIGINT PRIMARY KEY REFERENCES users(user_id),
                        total_tests INT DEFAULT 0,
                        total_correct INT DEFAULT 0,
                        total_incorrect INT DEFAULT 0,
                        last_test TIMESTAMPTZ
                    )
                """)
                
                # Добавляем стандартные слова
                default_words = [
                    ('hello', 'привет'), ('goodbye', 'пока'), ('thank you', 'спасибо'),
                    ('please', 'пожалуйста'), ('yes', 'да'), ('no', 'нет'),
                    ('sorry', 'извините'), ('help', 'помощь'), ('water', 'вода'),
                    ('food', 'еда'), ('time', 'время'), ('day', 'день')
                ]
                for en, ru in default_words:
                    await conn.execute("""
                        INSERT INTO default_words (english_word, russian_translation)
                        VALUES ($1, $2)
                        ON CONFLICT (english_word) DO NOTHING
                    """, en, ru)
                
                logger.info("Database reinitialized successfully")
            except Exception as e:
                logger.error(f"Error initializing database: {e}")
                raise

    async def _ensure_database_exists(self):
        """Проверка существования БД"""
        try:
            sys_conn = await asyncpg.connect(
                user=DB_USER,
                password=DB_PASSWORD,
                host=DB_HOST,
                port=DB_PORT,
                database='postgres'
            )
            db_exists = await sys_conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1", DB_NAME
            )
            if not db_exists:
                await sys_conn.execute(f'CREATE DATABASE "{DB_NAME}"')
            await sys_conn.close()
        except Exception as e:
            logger.error(f"Error creating database: {e}")
            raise

    async def add_user(self, user_id: int, username: Optional[str] = None,
                     first_name: Optional[str] = None, last_name: Optional[str] = None) -> None:
        async with self._pool.acquire() as conn:
            try:
                await conn.execute("""
                    INSERT INTO users (user_id, username, first_name, last_name)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (user_id) DO NOTHING
                """, user_id, username, first_name, last_name)
            except Exception as e:
                logger.error(f"Error adding user: {e}")

    async def add_user_word(self, user_id: int, english_word: str, russian_translation: str) -> bool:
        async with self._pool.acquire() as conn:
            try:
                result = await conn.execute("""
                    INSERT INTO user_words (user_id, english_word, russian_translation)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id, english_word) DO NOTHING
                """, user_id, english_word.lower(), russian_translation.lower())
                return "INSERT 0 1" in result
            except Exception as e:
                logger.error(f"Error adding word: {e}")
                return False

    async def remove_user_word(self, user_id: int, english_word: str) -> bool:
        async with self._pool.acquire() as conn:
            try:
                result = await conn.execute("""
                    DELETE FROM user_words 
                    WHERE user_id = $1 AND english_word = $2
                """, user_id, english_word.lower())
                return "DELETE 1" in result
            except Exception as e:
                logger.error(f"Error removing word: {e}")
                return False

    async def get_user_words(self, user_id: int) -> List[Tuple[str, str]]:
        async with self._pool.acquire() as conn:
            try:
                rows = await conn.fetch("""
                    SELECT english_word, russian_translation 
                    FROM user_words 
                    WHERE user_id = $1 
                    ORDER BY added_date DESC
                """, user_id)
                return [(row['english_word'], row['russian_translation']) for row in rows]
            except Exception as e:
                logger.error(f"Error getting user words: {e}")
                return []

    async def get_default_words(self, limit: int = 12) -> List[Tuple[str, str]]:
        async with self._pool.acquire() as conn:
            try:
                rows = await conn.fetch("""
                    SELECT english_word, russian_translation 
                    FROM default_words 
                    ORDER BY word_id 
                    LIMIT $1
                """, limit)
                return [(row['english_word'], row['russian_translation']) for row in rows]
            except Exception as e:
                logger.error(f"Error getting default words: {e}")
                return []

    async def get_word_translation(self, word: str, word_type: str = 'default') -> Optional[str]:
        async with self._pool.acquire() as conn:
            try:
                if word_type == 'default':
                    row = await conn.fetchrow("""
                        SELECT russian_translation 
                        FROM default_words 
                        WHERE english_word = $1
                    """, word.lower())
                else:
                    row = await conn.fetchrow("""
                        SELECT russian_translation 
                        FROM user_words 
                        WHERE english_word = $1
                    """, word.lower())
                return row['russian_translation'] if row else None
            except Exception as e:
                logger.error(f"Error getting translation: {e}")
                return None

    async def get_possible_translations(self, en_word: str, user_id: int) -> List[str]:
        async with self._pool.acquire() as conn:
            try:
                default_rows = await conn.fetch("""
                    SELECT russian_translation 
                    FROM default_words 
                    WHERE english_word = $1
                """, en_word.lower())
                
                user_rows = await conn.fetch("""
                    SELECT russian_translation 
                    FROM user_words 
                    WHERE user_id = $1 AND english_word = $2
                """, user_id, en_word.lower())
                
                translations = [row['russian_translation'] for row in default_rows + user_rows]
                return list(set(translations))
            except Exception as e:
                logger.error(f"Error getting possible translations: {e}")
                return []

    async def update_user_progress(self, user_id: int, word: str, 
                                 word_type: str, is_correct: bool) -> None:
        async with self._pool.acquire() as conn:
            try:
                if word_type == 'default':
                    word_id = await conn.fetchval("""
                        SELECT word_id FROM default_words 
                        WHERE english_word = $1
                    """, word.lower())
                else:
                    word_id = await conn.fetchval("""
                        SELECT user_word_id FROM user_words 
                        WHERE user_id = $1 AND english_word = $2
                    """, user_id, word.lower())
                
                if word_id:
                    await conn.execute("""
                        INSERT INTO user_progress 
                        (user_id, word_id, word_type, times_shown, times_correct, last_shown)
                        VALUES ($1, $2, $3, 1, $4, NOW())
                        ON CONFLICT (user_id, word_id, word_type) 
                        DO UPDATE SET 
                            times_shown = user_progress.times_shown + 1,
                            times_correct = user_progress.times_correct + EXCLUDED.times_correct,
                            last_shown = NOW()
                    """, user_id, word_id, word_type, 1 if is_correct else 0)
            except Exception as e:
                logger.error(f"Error updating progress: {e}")

    async def update_leaderboard(self, user_id: int, correct: int, incorrect: int):
        async with self._pool.acquire() as conn:
            try:
                await conn.execute("""
                    INSERT INTO leaderboard (user_id, total_tests, total_correct, total_incorrect, last_test)
                    VALUES ($1, 1, $2, $3, NOW())
                    ON CONFLICT (user_id) DO UPDATE SET
                        total_tests = leaderboard.total_tests + 1,
                        total_correct = leaderboard.total_correct + EXCLUDED.total_correct,
                        total_incorrect = leaderboard.total_incorrect + EXCLUDED.total_incorrect,
                        last_test = NOW()
                """, user_id, correct, incorrect)
            except Exception as e:
                logger.error(f"Error updating leaderboard: {e}")

    async def create_test_session(self, user_id: int) -> Optional[int]:
        async with self._pool.acquire() as conn:
            try:
                test_id = await conn.fetchval("""
                    INSERT INTO tests 
                    (user_id, start_time) 
                    VALUES ($1, NOW()) 
                    RETURNING test_id
                """, user_id)
                return test_id
            except Exception as e:
                logger.error(f"Error creating test session: {e}")
                return None

    async def add_test_result(self, test_id: int, word: str, 
                            correct_answer: str, user_answer: str, 
                            is_correct: bool) -> bool:
        async with self._pool.acquire() as conn:
            try:
                await conn.execute("""
                    INSERT INTO test_results 
                    (test_id, word, correct_answer, user_answer, is_correct) 
                    VALUES ($1, $2, $3, $4, $5)
                """, test_id, word, correct_answer, user_answer, is_correct)
                return True
            except Exception as e:
                logger.error(f"Error adding test result: {e}")
                return False

    async def update_test_session(self, test_id: int, 
                                questions_answered: int, 
                                correct_answers: int, 
                                incorrect_answers: int) -> bool:
        async with self._pool.acquire() as conn:
            try:
                await conn.execute("""
                    UPDATE tests 
                    SET end_time = NOW(),
                        total_questions = $2,
                        correct_answers = $3,
                        incorrect_answers = $4
                    WHERE test_id = $1
                """, test_id, questions_answered, correct_answers, incorrect_answers)
                return True
            except Exception as e:
                logger.error(f"Error updating test session: {e}")
                return False

    async def get_test_results(self, test_id: int) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            try:
                return await conn.fetch("""
                    SELECT word, correct_answer, user_answer, is_correct
                    FROM test_results 
                    WHERE test_id = $1
                    ORDER BY id
                """, test_id)
            except Exception as e:
                logger.error(f"Error getting test results: {e}")
                return []

    async def get_user_test_history(self, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            try:
                return await conn.fetch("""
                    SELECT test_id, start_time, end_time, 
                           correct_answers, incorrect_answers, total_questions
                    FROM tests 
                    WHERE user_id = $1 
                    ORDER BY start_time DESC 
                    LIMIT $2
                """, user_id, limit)
            except Exception as e:
                logger.error(f"Error getting test history: {e}")
                return []

    async def get_user_test_stats(self, user_id: int) -> Dict[str, Any]:
        async with self._pool.acquire() as conn:
            try:
                return await conn.fetchrow("""
                    SELECT 
                    COUNT(*) as tests_count,
                    SUM(correct_answers) as total_correct,
                    AVG(correct_answers) as avg_score,
                    MAX(correct_answers) as best_score
                    FROM tests
                    WHERE user_id = $1
                """, user_id)
            except Exception as e:
                logger.error(f"Error getting test stats: {e}")
                return {}

    async def get_user_last_test_results(self, user_id: int) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            try:
                test_id = await conn.fetchval("""
                    SELECT test_id FROM tests 
                    WHERE user_id = $1 
                    ORDER BY start_time DESC 
                    LIMIT 1
                """, user_id)
                
                if test_id:
                    return await self.get_test_results(test_id)
                return []
            except Exception as e:
                logger.error(f"Error getting last test results: {e}")
                return []

# Глобальный экземпляр для использования
db = Database()