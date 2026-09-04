"""Known short-section titles for Tarasov's Технология жизни.

The four named 'главы' are parts. Daily units are the titled essays inside them.
Titles come from the publisher table of contents (Добрая книга). Editions differ
slightly; missing titles are skipped at ingest time rather than forcing a split.
"""

from __future__ import annotations

from dataclasses import dataclass

BOOK_HINTS = (
    "технология жизни",
    "книга для героев",
    "tarasov",
    "тарасов",
    "tehnologiya_zhi",
    "tehnologiya-zhi",
)

PARTS = {
    "preface": "Предисловие",
    "path": "Путь",
    "manager": "Великий менеджер",
    "army": "Человек-армия",
    "death": "Приготовление к смерти",
}


@dataclass(frozen=True)
class KnownSection:
    part_id: str
    title: str


SECTIONS: tuple[KnownSection, ...] = (
    KnownSection("preface", "Предисловие к третьему изданию"),
    KnownSection("preface", "Предисловие"),
    KnownSection("path", "Смысл всякой деятельности лежит вне ее пределов"),
    KnownSection("path", "Выходи за пределы жизни"),
    KnownSection("path", "Человек, который кормил обезьян"),
    KnownSection("path", "Большая цель достижима"),
    KnownSection("path", "Наметь дату своей смерти"),
    KnownSection("path", "Мы не знаем, кто стар и кто молод"),
    KnownSection("path", "Радость неудачи"),
    KnownSection("path", "Крестьянин, у которого была лошадь"),
    KnownSection("path", "Словно шарики в ящике, который трясут"),
    KnownSection("path", "Восемь ног паука"),
    KnownSection("path", "Учитель боевых петухов"),
    KnownSection("path", "Горизонтальная карьера"),
    KnownSection("path", "Управляй из любой точки"),
    KnownSection("path", "Без жалоб и просьб"),
    KnownSection("path", "Сила и слабость"),
    KnownSection("path", "Пути не пересекаются"),
    KnownSection("path", "Взгляд из будущего всегда спокоен"),
    KnownSection("path", "Путь сильнее человека"),
    KnownSection("manager", "Он придет и скажет"),
    KnownSection("manager", "Идеальный руководитель"),
    KnownSection("manager", "Яблоко в траве"),
    KnownSection("manager", "Твердое и пустое"),
    KnownSection("manager", "Обучение гарема"),
    KnownSection("manager", "Страх и бесстрашие"),
    KnownSection("manager", "Последний приказ полководца"),
    KnownSection("manager", "Полководец медлит"),
    KnownSection("manager", "Искушение алиби"),
    KnownSection("manager", "Приблизься к оленю и не ошибешься"),
    KnownSection("manager", "Выбирая путь, думай, кто по нему пойдет"),
    KnownSection("manager", "Управляй многими"),
    KnownSection("manager", "Храбрый не наступает один"),
    KnownSection("manager", "С первым ударом барабана"),
    KnownSection("manager", "Запрещай говорить правду"),
    KnownSection("manager", "Сначала говорят, а потом и делают"),
    KnownSection("manager", "Два друга работали в саду"),
    KnownSection("manager", "Говори о том, что есть"),
    KnownSection("manager", "Не все приказы императора надо выполнять"),
    KnownSection("manager", "Упаковка поступка"),
    KnownSection("manager", "Казнь ребенка"),
    KnownSection("manager", "Наказание невиновного"),
    KnownSection("manager", "Не завоевав сердце, нельзя наказывать"),
    KnownSection("manager", "Награждая и наказывая, не обманывай"),
    KnownSection("manager", "Поднебесная не стоит того"),
    KnownSection("manager", "Дели отряды для грабежа"),
    KnownSection("manager", "Прощание с человеком"),
    KnownSection("army", "Война — путь обмана"),
    KnownSection("army", "Обман — путь войны"),
    KnownSection("army", "Технология обмана"),
    KnownSection("army", "История первого обмана"),
    KnownSection("army", "Военное искусство"),
    KnownSection("army", "Падающий мельничный жернов"),
    KnownSection("army", "Покажи врагу дорогу к жизни"),
    KnownSection("army", "Борьба за прошлое, настоящее и будущее"),
    KnownSection("army", "Получить выгоду — удача"),
    KnownSection("army", "Располагайся на выгодной местности"),
    KnownSection("army", "Кто не использует проводников"),
    KnownSection("army", "Удерживай вредом"),
    KnownSection("army", "Будь хозяином, а не гостем"),
    KnownSection("army", "Выходи вторым, приходи первым"),
    KnownSection("army", "Не ищи выгоды за сто ли"),
    KnownSection("army", "Помести своих солдат в местность смерти"),
    KnownSection("army", "Стрела, не имеющая силы"),
    KnownSection("army", "Находясь в бедствии, думай о выгоде"),
    KnownSection("army", "Управляй гражданскими методами"),
    KnownSection("army", "Как отличить ложь милую от лжи злой"),
    KnownSection("army", "Что может один"),
    KnownSection("army", "Есть армии, с которыми не сражаются"),
    KnownSection("death", "Коридоры, двери и комнаты"),
    KnownSection("death", "Короля играют придворные"),
    KnownSection("death", "Главные часы человека"),
    KnownSection("death", "Зачем человеку другие часы"),
    KnownSection("death", "Жизнь без часов"),
    KnownSection("death", "Боязнь жизни"),
    KnownSection("death", "Рождение человека"),
    KnownSection("death", "Мы и другие"),
    KnownSection("death", "За пределами жизни"),
)

SKIP_AFTER_TITLES = (
    "Об авторе",
    "Ценности Владимира Тарасова",
    "Academy of Management Art",
    "Крауд-тренинги Владимира Тарасова",
    "Управленческие поединки",
    "Книги Владимира Тарасова",
)


def looks_like_tarasov(filename: str, text: str) -> bool:
    blob = f"{filename}\n{text[:8000]}".lower()
    return any(hint in blob for hint in BOOK_HINTS)
