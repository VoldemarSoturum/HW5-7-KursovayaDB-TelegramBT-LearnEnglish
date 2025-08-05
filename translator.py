# translator.py

import xml.etree.ElementTree as ET
import os

# Убедитесь, что путь к файлу верный
XML_FILE = os.path.join(os.path.dirname(__file__), 'dictionary.xml')


def load_dictionary():
    tree = ET.parse(XML_FILE)
    root = tree.getroot()
    dictionary = {}
    for entry in root.findall('entry'):
        en = entry.find('en').text.strip().lower()
        ru = entry.find('ru').text.strip().lower()
        dictionary[ru] = en
        dictionary[en] = ru
    return dictionary


# Кэш словарь, чтобы не загружать каждый раз
_translation_dict = load_dictionary()


def translate_word(word: str) -> str | None:
    word = word.strip().lower()
    return _translation_dict.get(word)
def is_in_dictionary(word: str) -> bool:
    word = word.strip().lower()
    return word in _translation_dict