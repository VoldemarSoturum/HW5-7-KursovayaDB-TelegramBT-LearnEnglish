import random
import logging
from datetime import datetime
from typing import Tuple, Optional, List, Dict, Union
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
from database import db
from translator import translate_word, is_in_dictionary
import xml.etree.ElementTree as ET
import os

logger = logging.getLogger(__name__)

# Допустимые опечатки
ALLOWED_TYPOS = {
    'прстите': 'простите',
    'извните': 'извините',
    'сори': 'sorry',
    'вада': 'вода',
    'ватер': 'water',
    'йес': 'yes',
    'ноу': 'no',
    'хелло': 'hello',
    'санкс': 'thanks',
    'тхак ю': 'thank you'
}

async def check_db_connection():
    """Проверяет соединение с базой данных"""
    try:
        await db.pool.fetch("SELECT 1")
        return True
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        return False

async def load_words_from_xml() -> List[Tuple[str, str]]:
    """Загружает слова из XML-файла dictionary.xml"""
    try:
        dir_path = os.path.dirname(os.path.realpath(__file__))
        xml_path = os.path.join(dir_path, 'dictionary.xml')
        
        if not os.path.exists(xml_path):
            logger.warning("XML dictionary file not found")
            return []
            
        tree = ET.parse(xml_path)
        root = tree.getroot()
        
        words = []
        for entry in root.findall('entry'):
            en_word = entry.find('en').text.strip().lower()
            ru_word = entry.find('ru').text.strip().lower()
            if en_word and ru_word:
                words.append((en_word, ru_word))
                
        return words
    except Exception as e:
        logger.error(f"Error loading XML dictionary: {e}")
        return []

async def start_learning_test(user_id: int, state: FSMContext) -> Tuple[Optional[str], Optional[str], Optional[InlineKeyboardMarkup]]:
    """Генерирует вопрос для теста"""
    try:
        data = await state.get_data()
        
        # Если тест только начинается
        if not data.get('test_in_progress'):
            test_id = await db.create_test_session(user_id)
            if not test_id:
                logger.error("Failed to create test session")
                return None, None, None
            
            await state.update_data(
                test_id=test_id,
                test_in_progress=True,
                test_start_time=datetime.now(),
                questions_answered=0,
                correct_answers=0,
                incorrect_answers=0,
                test_questions=[]
            )
            data = await state.get_data()

        # Получаем слова для теста
        user_words = await db.get_user_words(user_id) or []
        default_words = await db.get_default_words() or []
        xml_words = await load_words_from_xml() or []
        
        # Объединяем все слова, исключая дубликаты
        all_words = list({word[0]: word for word in user_words + default_words + xml_words}.values())

        if not all_words:
            logger.warning(f"No words available for user {user_id}")
            return None, None, None

        # Выбираем случайное слово
        en_word, ru_word = random.choice(all_words)
        
        # Определяем тип слова
        word_type = 'user' if (en_word, ru_word) in user_words else \
                   'default' if (en_word, ru_word) in default_words else 'xml'

        # Выбираем тип вопроса
        question_type = random.choice(['en_to_ru', 'ru_to_en'])
        
        if question_type == 'en_to_ru':
            question = f"Переведите слово: {en_word}"
            correct_answer = ru_word
        else:
            question = f"Как будет '{ru_word}' по-английски?"
            correct_answer = en_word

        # Сохраняем данные вопроса
        await state.update_data({
            'current_question': question,
            'test_correct_answer': correct_answer,
            'current_word': en_word,
            'word_type': word_type,
            'question_type': question_type,
            'original_ru_word': ru_word
        })

        # Клавиатура с кнопкой завершения теста
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏁 Завершить тест", callback_data="end_test")]
        ])

        return question, correct_answer, keyboard

    except Exception as e:
        logger.error(f"Error in start_learning_test: {e}", exc_info=True)
        return None, None, None

async def handle_test_response(user_id: int, user_answer: str, 
                             correct_answer: str, state: FSMContext) -> Tuple[bool, str, InlineKeyboardMarkup]:
    """Обрабатывает ответ пользователя"""
    try:
        data = await state.get_data()
        test_id = data.get('test_id')
        if not test_id:
            raise ValueError("Test session ID not found")
            
        word_type = data.get('word_type', 'default')
        question_type = data.get('question_type', 'en_to_ru')
        current_word = data.get('current_word', '')
        original_ru_word = data.get('original_ru_word', '')
        
        # Нормализация ответа
        user_answer = user_answer.strip().lower()
        correct_answer_lower = correct_answer.lower()
        user_answer = ALLOWED_TYPOS.get(user_answer, user_answer)
        
        # Проверка правильности ответа
        is_correct, possible_translations = await _check_answer(
            user_answer, correct_answer_lower, 
            question_type, current_word, user_id
        )

        # Сохраняем результат
        await _save_test_results(
            test_id, user_id, current_word, word_type,
            correct_answer, user_answer, is_correct, state
        )

        # Формируем ответ пользователю
        feedback = _generate_feedback(
            is_correct, user_answer, correct_answer,
            question_type, possible_translations, original_ru_word
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏁 Завершить тест", callback_data="end_test")]
        ])

        return is_correct, feedback, keyboard
            
    except Exception as e:
        logger.error(f"Error in handle_test_response: {e}", exc_info=True)
        return False, f"❌ Ошибка при проверке. Правильный ответ: '{correct_answer}'", None

async def _check_answer(user_answer: str, correct_answer: str, 
                       question_type: str, current_word: str, 
                       user_id: int) -> Tuple[bool, List[str]]:
    """Проверяет правильность ответа"""
    is_correct = False
    possible_translations = []

    if question_type == 'ru_to_en':
        is_correct = (user_answer == correct_answer)
        possible_translations = [correct_answer]
    else:
        possible_translations = await _get_possible_translations(current_word, user_id)
        is_correct = (user_answer in possible_translations)

    return is_correct, possible_translations

async def _get_possible_translations(word: str, user_id: int) -> List[str]:
    """Получает все возможные варианты перевода слова"""
    translations = set()

    # Переводы из словаря
    if is_in_dictionary(word):
        translated = translate_word(word)
        if translated:
            translations.update(t.strip().lower() for t in translated.split(',') if t.strip())

    # Переводы из базы данных
    db_translations = await db.get_possible_translations(word, user_id)
    if db_translations:
        translations.update(t.lower() for t in db_translations if t.strip())

    return list(translations)

async def _save_test_results(test_id: int, user_id: int, word: str, 
                            word_type: str, correct_answer: str, 
                            user_answer: str, is_correct: bool, 
                            state: FSMContext):
    """Сохраняет результаты теста"""
    # Обновляем счетчики
    data = await state.get_data()
    questions_answered = data.get('questions_answered', 0) + 1
    correct_answers = data.get('correct_answers', 0) + (1 if is_correct else 0)
    incorrect_answers = data.get('incorrect_answers', 0) + (0 if is_correct else 1)

    # Сохраняем результат вопроса
    await db.add_test_result(
        test_id,
        word,
        correct_answer,
        user_answer,
        is_correct
    )

    # Обновляем прогресс пользователя
    await db.update_user_progress(user_id, word, word_type, is_correct)

    # Сохраняем вопрос в историю теста
    test_questions = data.get('test_questions', [])
    test_questions.append({
        'question': data.get('current_question', ''),
        'correct_answer': correct_answer,
        'user_answer': user_answer,
        'is_correct': is_correct
    })

    # Обновляем состояние
    await state.update_data(
        questions_answered=questions_answered,
        correct_answers=correct_answers,
        incorrect_answers=incorrect_answers,
        test_questions=test_questions
    )

def _generate_feedback(is_correct: bool, user_answer: str, 
                      correct_answer: str, question_type: str, 
                      possible_translations: List[str], 
                      original_ru_word: str) -> str:
    """Генерирует обратную связь для пользователя"""
    if is_correct:
        return "✅ Верно! Отличная работа!"
    
    feedback = (
        f"❌ Неверно. Ваш ответ: '{user_answer}'\n"
        f"Правильный ответ: '{correct_answer}'"
    )
    
    if question_type == 'en_to_ru' and len(possible_translations) > 1:
        other_options = [t for t in possible_translations if t != correct_answer]
        if other_options:
            feedback += f"\nДругие варианты: {', '.join(other_options)}"
    elif question_type == 'ru_to_en':
        feedback += f"\nПеревод: '{original_ru_word}'"
    
    return feedback

async def end_test_and_show_results(user_id: int, state: FSMContext) -> Tuple[str, InlineKeyboardMarkup]:
    """Завершает тест и возвращает результаты"""
    try:
        data = await state.get_data()
        test_id = data.get('test_id')
        if not test_id:
            raise ValueError("Test session ID not found")

        # Статистика теста
        questions_answered = data.get('questions_answered', 0)
        correct_answers = data.get('correct_answers', 0)
        incorrect_answers = data.get('incorrect_answers', 0)

        # Обновляем сессию теста
        await db.update_test_session(
            test_id,
            questions_answered,
            correct_answers,
            incorrect_answers
        )

        # Получаем результаты
        test_results = await db.get_test_results(test_id)
        stats = await db.get_user_test_stats(user_id)

        # Формируем сообщение с результатами
        result_message = _format_results_message(
            questions_answered, correct_answers, 
            incorrect_answers, test_results, stats
        )

        # Обновляем таблицу лидеров
        await db.update_leaderboard(user_id, correct_answers, incorrect_answers)

        # Клавиатура с действиями
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 История тестов", callback_data="test_history")],
            [InlineKeyboardButton(text="🔁 Новый тест", callback_data="new_test")]
        ])

        await state.clear()
        return result_message, keyboard
        
    except Exception as e:
        logger.error(f"Error in end_test_and_show_results: {e}", exc_info=True)
        return "Произошла ошибка при завершении теста. Результаты не сохранены.", None

def _format_results_message(questions_answered: int, correct_answers: int,
                          incorrect_answers: int, test_results: List[Dict],
                          stats: Dict) -> str:
    """Форматирует сообщение с результатами теста"""
    percentage = round((correct_answers / questions_answered) * 100) if questions_answered > 0 else 0
    
    message = (
        f"📊 <b>Результаты теста</b>\n\n"
        f"🔹 Всего вопросов: {questions_answered}\n"
        f"✅ Правильных ответов: {correct_answers}\n"
        f"❌ Неправильных ответов: {incorrect_answers}\n"
        f"📈 Процент правильных: {percentage}%\n\n"
        f"<b>Общая статистика:</b>\n"
        f"🔥 Средний балл: {stats.get('avg_score', 0):.1f}\n"
        f"🏆 Лучший результат: {stats.get('best_score', 0)}\n"
        f"📋 Всего тестов: {stats.get('tests_count', 0)}\n\n"
    )
    
    if not test_results:
        message += "ℹ️ Детальная информация о вопросах недоступна\n"
    else:
        message += "<b>Детализация последних вопросов:</b>\n\n"
        for i, result in enumerate(test_results[-3:], 1):
            status = "✅" if result['is_correct'] else "❌"
            message += (
                f"{i}. {status} Слово: <b>{result['word']}</b>\n"
                f"   Ваш ответ: {result['user_answer']}\n"
                f"   Правильно: {result['correct_answer']}\n\n"
            )
    
    return message

async def format_duration(start_time: datetime, end_time: datetime = None) -> str:
    """Форматирует длительность теста"""
    if not end_time:
        return "не завершен"
    duration = end_time - start_time
    minutes = duration.total_seconds() // 60
    seconds = duration.total_seconds() % 60
    return f"{int(minutes)} мин {int(seconds)} сек"