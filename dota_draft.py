"""Hero pools for the /bk draft.

The draft has to look thought through, but the bot runs as a stateless
webhook with no model behind it — so the thinking lives in the data. Each
archetype is one coherent game plan, with a separate hero pool per position.
A draft rolls the archetype first and then one hero from each of its five
pools, which makes every result random yet internally consistent: the roles
are always 1-2-3-4-5 exactly once, and the heroes that land together are ones
that actually want to be on the same team.
"""

import random

# (position, label, emoji) — exactly one hero each, so a five-carry draft is
# impossible by construction.
POSITIONS = [
    (1, "Керри", "🗡"),
    (2, "Мид", "⚡"),
    (3, "Оффлейн", "🛡"),
    (4, "Роумер", "🎯"),
    (5, "Саппорт", "💚"),
]

ARCHETYPES = [
    {
        "name": "Вомбо-комбо",
        "plan": (
            "Собрать врага в кучу и разложить одним замесом. Инициатор ловит "
            "пачку, площадные ульты добивают, керри заходит следом под уже "
            "оглушённых."
        ),
        "pools": {
            1: ["Sven", "Luna", "Gyrocopter", "Medusa", "Terrorblade"],
            2: ["Leshrac", "Kunkka", "Zeus", "Void Spirit", "Queen of Pain"],
            3: ["Magnus", "Tidehunter", "Centaur Warrunner", "Enigma", "Dark Seer"],
            4: ["Earthshaker", "Snapfire", "Shadow Demon", "Tusk", "Hoodwink"],
            5: ["Warlock", "Disruptor", "Jakiro", "Crystal Maiden", "Lich"],
        },
    },
    {
        "name": "Осада",
        "plan": (
            "Снести вышки раньше, чем враг соберёт свои предметы. Постоянное "
            "давление по линиям, крипы и саммоны идут вперёд, команда заходит "
            "на хайграунд на 25-й минуте, а не на 45-й."
        ),
        "pools": {
            1: ["Lycan", "Naga Siren", "Drow Ranger", "Luna", "Gyrocopter"],
            2: ["Death Prophet", "Leshrac", "Dragon Knight", "Templar Assassin", "Razor"],
            3: ["Beastmaster", "Underlord", "Dragon Knight", "Venomancer", "Timbersaw"],
            4: ["Nature's Prophet", "Venomancer", "Enchantress", "Visage", "Snapfire"],
            5: ["Shadow Shaman", "Chen", "Warlock", "Keeper of the Light", "Dazzle"],
        },
    },
    {
        "name": "Пикофф",
        "plan": (
            "Не давать врагу собраться впятером. Ловим по одному на линиях и "
            "в лесу, размениваем карту, забираем Рошана на численном "
            "преимуществе. Замесов пять на пять избегаем."
        ),
        "pools": {
            1: ["Ursa", "Slark", "Juggernaut", "Phantom Assassin", "Bloodseeker"],
            2: ["Storm Spirit", "Queen of Pain", "Puck", "Ember Spirit", "Lina"],
            3: ["Slardar", "Night Stalker", "Mars", "Legion Commander", "Beastmaster"],
            4: ["Pudge", "Clockwerk", "Nyx Assassin", "Spirit Breaker", "Tusk"],
            5: ["Lion", "Shadow Shaman", "Bane", "Grimstroke", "Vengeful Spirit"],
        },
    },
    {
        "name": "Долгая игра",
        "plan": (
            "Пережить ранний прессинг и задавить в лейте. Оффлейнер и "
            "саппорты тянут время и выкупают керри из любой беды, всё "
            "упирается в один фарм и один спасающий ульт."
        ),
        "pools": {
            1: ["Medusa", "Spectre", "Anti-Mage", "Terrorblade", "Faceless Void"],
            2: ["Invoker", "Outworld Destroyer", "Tinker", "Storm Spirit", "Void Spirit"],
            3: ["Axe", "Centaur Warrunner", "Underlord", "Bristleback", "Timbersaw"],
            4: ["Shadow Demon", "Dark Willow", "Hoodwink", "Snapfire", "Phoenix"],
            5: ["Oracle", "Dazzle", "Winter Wyvern", "Abaddon", "Omniknight"],
        },
    },
    {
        "name": "Ранний прессинг",
        "plan": (
            "Давить с первой минуты и не дать врагу выйти на свои тайминги. "
            "Агрессивные линии, ранние ганги, темп важнее фарма — если игра "
            "затянется, станет тяжело, поэтому не затягиваем."
        ),
        "pools": {
            1: ["Ursa", "Bloodseeker", "Lifestealer", "Juggernaut", "Monkey King"],
            2: ["Viper", "Razor", "Tiny", "Batrider", "Lina"],
            3: ["Legion Commander", "Bristleback", "Night Stalker", "Mars", "Pangolier"],
            4: ["Earth Spirit", "Spirit Breaker", "Tusk", "Marci", "Clockwerk"],
            5: ["Undying", "Ogre Magi", "Crystal Maiden", "Jakiro", "Vengeful Spirit"],
        },
    },
]


def roll_draft() -> tuple[str, str, list[str]]:
    """Roll one archetype and a hero for each position within it.

    Returns (archetype_name, plan, heroes) with heroes ordered by position.
    The whole draft is rolled at once, before the first reveal, so the
    line-up cannot drift between button presses.
    """
    archetype = random.choice(ARCHETYPES)

    heroes: list[str] = []
    for position, _, _ in POSITIONS:
        # A few heroes sit in two pools of the same archetype (Dragon Knight
        # plays mid and offlane, say) — never draft the same one twice.
        pool = [hero for hero in archetype["pools"][position] if hero not in heroes]
        heroes.append(random.choice(pool))

    return archetype["name"], archetype["plan"], heroes
