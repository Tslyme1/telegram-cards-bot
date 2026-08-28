YELLOW_THRESHOLD = 3
GIVE_COOLDOWN_SECONDS = 15
GREEN_COOLDOWN_SECONDS = 60

# How many unused green cards one person can hold at once.
GREEN_IMMUNITY_LIMIT = 3

# /casino hands out one card to the target, with red kept rare.
CASINO_COOLDOWN_SECONDS = 10
CASINO_OUTCOME_WEIGHTS = {"green": 45, "yellow": 40, "red": 15}

# Before the colour is rolled, the spinner assembles a combination on a slot
# machine. Hitting one of the losing combinations backfires: the red card
# lands on whoever spun, and the target gets nothing.
CASINO_SYMBOLS = ["🍒", "🍋", "🎰", "🔔"]
# Each spin draws this many symbols out of the set above to be its reels, so
# 3 of 4 symbols in 3 slots gives 3^3 = 27 possible combinations.
CASINO_REEL_SYMBOLS = 3
CASINO_SLOTS = 3
# Drawn fresh for every spin, so nobody can learn which ones to avoid.
# 3 of the 27 combinations is a backfire chance of about 11%.
CASINO_LOSING_COMBOS = 3

# Kabankoins are a daily budget, not a wallet: everyone starts each day with
# this many, spending them refills nothing until the next day (the balance is
# stored per-day, like the mute ladder, so it resets on its own).
KABANKOIN_DAILY_AMOUNT = 5

# The card-casino spin (unchanged mechanic above) costs a flat coin.
CASINO_CARDS_SPIN_COST = 1

# The coin-casino spin's cost *is* the bet: betting more both costs more and
# skews the payout odds toward the higher tiers below.
CASINO_COINS_BET_MIN = 1
CASINO_COINS_BET_MAX = 5

# Pay table for the coin casino: (payout multiplier, weight at minimum bet,
# weight at maximum bet). The payout is bet * multiplier, which is what keeps
# the house edge honest — an earlier version paid a flat 1..100 coins no
# matter the stake, so a one-coin spin returned ~8 on average and any balance
# grew on its own.
#
# Weights are interpolated by bet size and fed to random.choices, so they need
# not be normalised, but each column sums to 100 to be readable as percentages.
# Expected return is ~0.91 of the stake at the minimum bet and ~0.92 at the
# maximum: every bet size loses money over time, so no stake and no bankroll
# can be ground upwards, while a single session still swings either way often
# enough to be worth playing. A bigger bet buys fewer but heavier wins and a
# jackpot twice as likely — at the maximum bet the 20x jackpot pays 100.
KABANKOIN_PAYOUT_TIERS = [
    (0, 58, 64),  # the stake is lost
    (1, 22, 16),  # the stake comes back
    (2, 13.5, 12),
    (5, 5, 6.4),
    (10, 1.3, 1.2),
    (20, 0.2, 0.4),  # the jackpot
]

# Each red card earned the same day mutes for longer than the previous one;
# once the ladder runs out, the last step repeats.
MUTE_LADDER_SECONDS = [60, 5 * 60, 15 * 60, 30 * 60, 60 * 60]

# The ladder starts over at midnight in this timezone (UTC+3 — Москва).
DAY_RESET_UTC_OFFSET_HOURS = 3

# Buying a temporary tag (setChatMemberTag, Bot API 9.5+) for someone else —
# a label next to their name that needs no admin promotion at all. Each tier
# is (duration_seconds, price_in_kabankoins).
TAG_PRICE_TIERS = [
    (3600, 50),
    (2 * 3600, 100),
    (4 * 3600, 200),
    (24 * 3600, 500),
    (7 * 24 * 3600, 2000),
]

# Telegram's own limits on setChatMemberTag: 16 characters, no emoji.
TAG_MAX_LENGTH = 16

# One rolled Dota line-up for a ranked match, revealed a hero at a time.
DOTA_DRAFT_PRICE = 50

# Swapping one hero the bot picked for another from the same plan. Only the
# bot's own picks can be rerolled — the player's own choices stay put.
DOTA_REROLL_PRICE = 10

# Writing the plan yourself and letting the bot fill in the heroes. Cheaper
# than a rolled draft because the player supplies the idea.
DOTA_CUSTOM_PLAN_PRICE = 25

# /food: gift someone a joke item, announced in the chat. No real delivery —
# it is the announcement itself that is the point. Each tier is (price, item).
FOOD_ITEMS = [
    (50, "двойную вялую"),
    (100, "3 литра «Медведя»"),
    (150, "шёлковый"),
    (200, "Нутришес"),
]

# Casino bets no longer get refused for lack of funds — they can go into debt
# instead, down to KABANKOIN_DEBT_BAN_LEVEL. Crossing each level below
# punishes the spinner in the group chat; once debt reaches the ban level,
# further casino spins are refused until the balance recovers (a win, a
# /send, or the next day's reset). Spaced 5 apart on purpose: the biggest
# single bet (CASINO_COINS_BET_MAX) can never jump past a level unnoticed.
KABANKOIN_DEBT_YELLOW_LEVEL = -5
KABANKOIN_DEBT_RED_LEVEL = -10
KABANKOIN_DEBT_BAN_LEVEL = -15
KABANKOIN_DEBT_BAN_SECONDS = 24 * 60 * 60

# Hitting the ban level offers a way out: "serve time" cuts the mute down to
# this, and forgives the debt back to the daily default balance.
KABANKOIN_JAIL_MUTE_SECONDS = 60 * 60
