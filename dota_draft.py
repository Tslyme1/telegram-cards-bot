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
import re
from typing import NamedTuple

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


# Heroes outside the archetype pools, so that locking in any real hero still
# lands on a sane position. Values are the positions each one commonly plays.
EXTRA_HEROES = {
    "Alchemist": [1, 3],
    "Ancient Apparition": [5],
    "Arc Warden": [1, 2],
    "Bounty Hunter": [4],
    "Brewmaster": [3],
    "Broodmother": [2, 3],
    "Chaos Knight": [1],
    "Clinkz": [1, 2],
    "Dawnbreaker": [3, 4],
    "Doom": [3],
    "Elder Titan": [3, 4],
    "Huskar": [1, 2, 3],
    "Io": [4, 5],
    "Kez": [1, 2],
    "Lone Druid": [1, 3],
    "Meepo": [1, 2],
    "Mirana": [1, 4],
    "Morphling": [1, 2],
    "Muerta": [1, 2],
    "Necrophos": [2, 3],
    "Phantom Lancer": [1],
    "Primal Beast": [3],
    "Pugna": [2, 4, 5],
    "Riki": [1, 4],
    "Ringmaster": [4, 5],
    "Rubick": [5],
    "Sand King": [3, 4],
    "Shadow Fiend": [2],
    "Silencer": [4, 5],
    "Skywrath Mage": [5],
    "Sniper": [1, 2],
    "Techies": [4, 5],
    "Treant Protector": [5],
    "Troll Warlord": [1],
    "Weaver": [1, 4],
    "Windranger": [2, 4],
    "Witch Doctor": [4, 5],
    "Wraith King": [1, 3],
}

# What people actually type instead of the full name.
ALIASES = {
    "pa": "Phantom Assassin",
    "am": "Anti-Mage",
    "qop": "Queen of Pain",
    "sf": "Shadow Fiend",
    "ta": "Templar Assassin",
    "cm": "Crystal Maiden",
    "wk": "Wraith King",
    "wd": "Witch Doctor",
    "ww": "Winter Wyvern",
    "es": "Earthshaker",
    "sb": "Spirit Breaker",
    "np": "Nature's Prophet",
    "od": "Outworld Destroyer",
    "lc": "Legion Commander",
    "dp": "Death Prophet",
    "dk": "Dragon Knight",
    "tb": "Terrorblade",
    "sk": "Sand King",
    "void": "Faceless Void",
    "magnus": "Magnus",
    "aa": "Ancient Apparition",
    "bh": "Bounty Hunter",
    "ns": "Night Stalker",
    "pl": "Phantom Lancer",
    "ck": "Chaos Knight",
    "mk": "Monkey King",
    "sd": "Shadow Demon",
    "ss": "Shadow Shaman",
    "kotl": "Keeper of the Light",
    "veno": "Venomancer",
    "invo": "Invoker",
    "пудж": "Pudge",
    "свен": "Sven",
    "инвокер": "Invoker",
    "зевс": "Zeus",
    "лина": "Lina",
    "лион": "Lion",
    "слардар": "Slardar",
    "урса": "Ursa",
    "тини": "Tiny",
    "медуза": "Medusa",
    "спектра": "Spectre",
    "аксе": "Axe",
    "акс": "Axe",
    "джаг": "Juggernaut",
    "джагернаут": "Juggernaut",
}

POOL_HEROES = {hero for a in ARCHETYPES for pool in a["pools"].values() for hero in pool}
ALL_HEROES = POOL_HEROES | set(EXTRA_HEROES)


class Draft(NamedTuple):
    archetype: str
    plan: str
    heroes: list[str]
    # Index into POSITIONS of the hero the player asked for, or None.
    locked_index: int | None
    # False when the locked hero is not one we know — the role was then a guess.
    known: bool


def _norm(name: str) -> str:
    """Fold a typed name so spelling and punctuation stop mattering."""
    return re.sub(r"[^a-zа-яё0-9]", "", name.lower())


_BY_NORM = {_norm(hero): hero for hero in ALL_HEROES}
_BY_ALIAS = {_norm(alias): hero for alias, hero in ALIASES.items()}


def resolve_hero(name: str) -> tuple[str, bool]:
    """Match typed text to a known hero. Returns (name_to_use, recognised)."""
    key = _norm(name)
    hero = _BY_NORM.get(key) or _BY_ALIAS.get(key)
    if hero:
        return hero, True

    # A unique prefix is enough — "terror" and "shadow sh" resolve fine.
    matches = {full for norm, full in _BY_NORM.items() if key and norm.startswith(key)}
    if len(matches) == 1:
        return matches.pop(), True

    # Anything else is taken at face value: the hero is the player's to choose.
    return " ".join(name.split())[:32], False


def _placements(hero: str, known: bool) -> list[tuple[int, int]]:
    """Every (archetype, position index) the hero could legitimately fill."""
    spots = [
        (index, position - 1)
        for index, archetype in enumerate(ARCHETYPES)
        for position, pool in archetype["pools"].items()
        if hero in pool
    ]
    if spots:
        return spots

    positions = EXTRA_HEROES.get(hero) if known else None
    if not positions:
        # Nothing to go on, so any slot is as good as another.
        positions = [position for position, _, _ in POSITIONS]
    return [
        (index, position - 1)
        for index in range(len(ARCHETYPES))
        for position in positions
    ]


def roll_draft(locked_hero: str | None = None) -> Draft:
    """Roll a full line-up, optionally built around a hero the player named.

    The whole draft is rolled at once, before the first reveal, so the line-up
    cannot drift between button presses. A locked hero fixes both the position
    it occupies and the plan the rest of the team is drawn from, so the other
    four are picked to go with it rather than around it.
    """
    heroes: list[str | None] = [None] * len(POSITIONS)
    locked_index = None
    known = True

    if locked_hero:
        hero, known = resolve_hero(locked_hero)
        archetype_index, locked_index = random.choice(_placements(hero, known))
        archetype = ARCHETYPES[archetype_index]
        heroes[locked_index] = hero
    else:
        archetype = random.choice(ARCHETYPES)

    for index, (position, _, _) in enumerate(POSITIONS):
        if heroes[index] is not None:
            continue
        # A few heroes sit in two pools of the same archetype (Dragon Knight
        # plays mid and offlane, say) — never draft the same one twice.
        pool = [hero for hero in archetype["pools"][position] if hero not in heroes]
        heroes[index] = random.choice(pool)

    return Draft(archetype["name"], archetype["plan"], heroes, locked_index, known)
