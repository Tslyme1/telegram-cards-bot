"""Hero pools for the /bk draft.

The draft has to look thought through, but the bot runs as a stateless
webhook with no model behind it — so the thinking lives in the data. Each
archetype is one coherent game plan, with a separate hero pool per position.
A draft rolls the archetype first and then one hero from each of its five
pools, which makes every result random yet internally consistent: the roles
are always 1-2-3-4-5 exactly once, and the heroes that land together are ones
that actually want to be on the same team.
"""

import itertools
import math
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
        # Matched against a plan written by the player.
        "keywords": ["замес", "тимфайт", "файт", "ульт", "площад", "кучу", "куч", "инициац", "комбо", "вомбо", "разлож", "оглуш", "стан", "магическ"],
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
        # Matched against a plan written by the player.
        "keywords": ["вышк", "пуш", "осад", "здани", "трон", "крип", "саммон", "тавер", "ломаем", "сносим", "хайграунд", "башн", "строени"],
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
        # Matched against a plan written by the player.
        "keywords": ["пикоф", "ганг", "ловим", "по одному", "роум", "убива", "инвиз", "засад", "охот", "размен", "рошан", "поодиночке"],
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
        # Matched against a plan written by the player.
        "keywords": ["лейт", "фарм", "долг", "поздн", "тянем", "скейл", "выжив", "пережи", "защища", "затян"],
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
        # Matched against a plan written by the player.
        "keywords": ["ранн", "агресс", "прессинг", "темп", "давим", "снежн", "наглы", "первой минут", "быстр", "начал"],
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


# Which positions each hero actually plays — the whole roster, not just the
# heroes used by the plans above.
#
# This is deliberately kept apart from the pools. A pool says "this plan wants
# this hero in this slot"; that is a much narrower statement than "this is the
# only slot the hero can play". Deriving roles from pool membership pinned
# every hero to a single position forever — Pudge sat in the pickoff plan's
# position 4, so a player asking for Pudge always got a roamer and never a
# midlaner.
HERO_POSITIONS = {
    "Abaddon": [3, 4, 5],
    "Alchemist": [1, 3],
    "Ancient Apparition": [4, 5],
    "Anti-Mage": [1],
    "Arc Warden": [1, 2],
    "Axe": [3],
    "Bane": [4, 5],
    "Batrider": [2, 3, 4],
    "Beastmaster": [3, 4],
    "Bloodseeker": [1, 3],
    "Bounty Hunter": [4],
    "Brewmaster": [3],
    "Bristleback": [3],
    "Broodmother": [2, 3],
    "Centaur Warrunner": [3],
    "Chaos Knight": [1],
    "Chen": [4, 5],
    "Clinkz": [1, 2],
    "Clockwerk": [3, 4],
    "Crystal Maiden": [5],
    "Dark Seer": [3],
    "Dark Willow": [4, 5],
    "Dawnbreaker": [3, 4],
    "Dazzle": [4, 5],
    "Death Prophet": [2, 3],
    "Disruptor": [5],
    "Doom": [3],
    "Dragon Knight": [2, 3],
    "Drow Ranger": [1],
    "Earth Spirit": [4],
    "Earthshaker": [3, 4, 5],
    "Elder Titan": [3, 4],
    "Ember Spirit": [2],
    "Enchantress": [4, 5],
    "Enigma": [3, 4],
    "Faceless Void": [1],
    "Grimstroke": [4, 5],
    "Gyrocopter": [1, 4],
    "Hoodwink": [4],
    "Huskar": [1, 2, 3],
    "Invoker": [2],
    "Io": [4, 5],
    "Jakiro": [4, 5],
    "Juggernaut": [1],
    "Keeper of the Light": [4, 5],
    "Kez": [1, 2],
    "Kunkka": [2, 3],
    "Legion Commander": [3, 4],
    "Leshrac": [2, 3],
    "Lich": [5],
    "Lifestealer": [1],
    "Lina": [2, 4],
    "Lion": [4, 5],
    "Lone Druid": [1, 3],
    "Luna": [1],
    "Lycan": [1, 3],
    "Magnus": [3, 4],
    "Marci": [1, 3, 4],
    "Mars": [3],
    "Medusa": [1],
    "Meepo": [1, 2],
    "Mirana": [1, 4],
    "Monkey King": [1, 3],
    "Morphling": [1, 2],
    "Muerta": [1, 2],
    "Naga Siren": [1, 5],
    "Nature's Prophet": [1, 3, 4],
    "Necrophos": [2, 3],
    "Night Stalker": [3, 4],
    "Nyx Assassin": [4],
    "Ogre Magi": [4, 5],
    "Omniknight": [3, 5],
    "Oracle": [4, 5],
    "Outworld Destroyer": [2],
    "Pangolier": [2, 3],
    "Phantom Assassin": [1],
    "Phantom Lancer": [1],
    "Phoenix": [3, 4],
    "Primal Beast": [3],
    "Puck": [2],
    "Pudge": [2, 3, 4],
    "Pugna": [2, 4, 5],
    "Queen of Pain": [2],
    "Razor": [2, 3],
    "Riki": [1, 4],
    "Ringmaster": [4, 5],
    "Rubick": [4, 5],
    "Sand King": [3, 4],
    "Shadow Demon": [4, 5],
    "Shadow Fiend": [2],
    "Shadow Shaman": [4, 5],
    "Silencer": [4, 5],
    "Skywrath Mage": [5],
    "Slardar": [3],
    "Slark": [1],
    "Snapfire": [4, 5],
    "Sniper": [1, 2],
    "Spectre": [1],
    "Spirit Breaker": [4],
    "Storm Spirit": [2],
    "Sven": [1, 3],
    "Techies": [4, 5],
    "Templar Assassin": [1, 2],
    "Terrorblade": [1],
    "Tidehunter": [3],
    "Timbersaw": [3],
    "Tinker": [2],
    "Tiny": [2, 3, 4],
    "Treant Protector": [5],
    "Troll Warlord": [1],
    "Tusk": [4],
    "Underlord": [3],
    "Undying": [4, 5],
    "Ursa": [1],
    "Vengeful Spirit": [4, 5],
    "Venomancer": [3, 4],
    "Viper": [2, 3],
    "Visage": [3, 4],
    "Void Spirit": [2],
    "Warlock": [5],
    "Weaver": [1, 4],
    "Windranger": [2, 4],
    "Winter Wyvern": [5],
    "Witch Doctor": [4, 5],
    "Wraith King": [1, 3],
    "Zeus": [2],
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
ALL_HEROES = POOL_HEROES | set(HERO_POSITIONS)

# How strongly a seating that a plan already lists is preferred over one that
# merely matches the hero's role. Finite on purpose: a hero with several roles
# should turn up in all of them, not only in the one some plan happens to list.
# Playing out of role scores zero, so it only happens when the heroes the
# player named cannot all be seated properly (three carries, say).
FIT_WEIGHTS = {2: 8, 1: 1, 0: 0}
FIT_WEIGHTS_FORCED = {2: 8, 1: 1, 0: 0.05}


class Draft(NamedTuple):
    archetype: str
    plan: str
    heroes: list[str]
    # Indices into POSITIONS of the heroes the player asked for.
    locked: list[int]
    # Names we did not recognise — their roles were a guess.
    unknown: list[str]
    # The plan whose pools the heroes came from. Differs from `archetype` when
    # the player wrote their own, and is what reroll_hero looks up.
    pool_name: str = ""
    # With a written plan: whether any keyword actually matched.
    matched: bool = True


def match_plan(text: str) -> tuple[int, bool]:
    """Find the known plan closest to one a player wrote.

    Keyword counting, not comprehension: there is no model behind the bot, so
    prose can only be mapped onto the nearest plan it already knows how to
    draft for. Returns (archetype index, whether anything matched) so the
    caller can be honest when it is a guess.
    """
    haystack = re.sub(r"[^a-zа-яё0-9 ]+", " ", (text or "").lower())
    scores = [
        sum(1 for word in archetype["keywords"] if word in haystack)
        for archetype in ARCHETYPES
    ]
    best = max(scores)
    if not best:
        return random.randrange(len(ARCHETYPES)), False
    return random.choice([i for i, score in enumerate(scores) if score == best]), True


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


def parse_heroes(text: str, limit: int = len(POSITIONS)) -> list[str]:
    """Split a typed line into hero names — commas or newlines separate them."""
    names = [part.strip() for part in re.split(r"[,\n;]+", text or "")]
    return [name for name in names if name][:limit]


def natural_positions(hero: str, known: bool = True) -> list[int]:
    """Positions this hero plausibly plays, as 1-based positions.

    Comes from HERO_POSITIONS rather than from pool membership, so a hero
    keeps every role they actually play even when the plans only ever ask
    them to fill one of them.
    """
    if hero in HERO_POSITIONS:
        return HERO_POSITIONS[hero]
    if known:
        spots = sorted(
            {
                position
                for archetype in ARCHETYPES
                for position, pool in archetype["pools"].items()
                if hero in pool
            }
        )
        if spots:
            return spots
    # Nothing to go on, so any slot is as good as another.
    return [position for position, _, _ in POSITIONS]


def _fit(archetype: dict, hero: str, known: bool, index: int) -> int:
    """How well a hero sits at one position of one plan. Higher is better."""
    position = index + 1
    if hero in archetype["pools"][position]:
        return 2  # right role and part of this plan already
    if position in natural_positions(hero, known):
        return 1  # right role, borrowed into this plan
    return 0  # forced


def roll_draft(
    locked_heroes: list[str] | str | None = None,
    plan_name: str | None = None,
    plan_text: str | None = None,
) -> Draft:
    """Roll a full line-up, optionally built around heroes the player named.

    The whole draft is rolled at once, before the first reveal, so the line-up
    cannot drift between button presses.

    With heroes locked in, the plan is not chosen at random: every plan is
    scored on how well it can seat all of them at once — each on a position it
    actually plays, no two on the same one — and the best-fitting plan wins.
    Their team-mates are then drawn from that plan, so the rest of the draft is
    picked to go with them rather than merely around them.

    A plan written by the player replaces the displayed name and text, and its
    wording picks which known plan the heroes are drafted from.
    """
    if isinstance(locked_heroes, str):
        locked_heroes = [locked_heroes]

    resolved: list[tuple[str, bool]] = []
    for name in locked_heroes or []:
        hero, known = resolve_hero(name)
        if all(hero != already for already, _ in resolved):
            resolved.append((hero, known))
    resolved = resolved[: len(POSITIONS)]

    heroes: list[str | None] = [None] * len(POSITIONS)
    locked: list[int] = []

    written = bool(plan_name or plan_text)
    matched = True
    if written:
        chosen_index, matched = match_plan(f"{plan_name or ''} {plan_text or ''}")
        candidates = [chosen_index]
    else:
        candidates = list(range(len(ARCHETYPES)))

    if not resolved:
        archetype = ARCHETYPES[random.choice(candidates)]
    else:
        seatings = [
            (archetype_index, indices)
            for archetype_index in candidates
            for indices in itertools.permutations(range(len(POSITIONS)), len(resolved))
        ]
        fits = [
            [
                _fit(ARCHETYPES[archetype_index], hero, known, index)
                for (hero, known), index in zip(resolved, indices)
            ]
            for archetype_index, indices in seatings
        ]

        def weigh(table):
            return [
                math.prod(table[fit] for fit in seating_fits) for seating_fits in fits
            ]

        weights = weigh(FIT_WEIGHTS)
        if not any(weights):
            # No way to seat everyone in their own role — the names collide.
            weights = weigh(FIT_WEIGHTS_FORCED)

        # Weighted rather than best-only: the strongest seating is favoured but
        # not guaranteed, so a hero with several roles is not nailed to one of
        # them every single time. Multiplying the per-hero weights keeps the
        # preference sharp when several heroes agree on the same plan.
        archetype_index, indices = random.choices(seatings, weights=weights)[0]
        archetype = ARCHETYPES[archetype_index]
        for (hero, _), index in zip(resolved, indices):
            heroes[index] = hero
            locked.append(index)

    for index, (position, _, _) in enumerate(POSITIONS):
        if heroes[index] is not None:
            continue
        # A few heroes sit in two pools of the same archetype (Dragon Knight
        # plays mid and offlane, say) — never draft the same one twice.
        pool = [hero for hero in archetype["pools"][position] if hero not in heroes]
        heroes[index] = random.choice(pool)

    return Draft(
        (plan_name or archetype["name"]).strip() if written else archetype["name"],
        (plan_text or "").strip() if written else archetype["plan"],
        heroes,
        sorted(locked),
        [hero for hero, known in resolved if not known],
        archetype["name"],
        matched,
    )


def reroll_hero(pool_name: str, index: int, taken: list[str]) -> str | None:
    """Swap one position for a different hero from the same plan."""
    archetype = next((a for a in ARCHETYPES if a["name"] == pool_name), None)
    if archetype is None:
        return None
    pool = [hero for hero in archetype["pools"][index + 1] if hero not in taken]
    return random.choice(pool) if pool else None
