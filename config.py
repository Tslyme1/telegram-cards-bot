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

# Payout tiers for the coin casino: (low, high, weight at minimum bet, weight
# at maximum bet). Weights are interpolated by bet size and used with
# random.choices, so they don't need to be normalised, but each column here
# sums to 100 to make the percentages easy to read directly. Modelled on a
# real slot machine's pay table: the bet can come back as nothing at all,
# small wins are the next most common outcome, the jackpot (100) is rare, and
# a bigger bet buys both a lower bust chance and better odds at the top
# tiers, without touching the payout amounts themselves.
KABANKOIN_PAYOUT_TIERS = [
    (0, 0, 30, 10),  # the bet is lost outright
    (1, 10, 48, 40),
    (11, 25, 17, 30),
    (26, 50, 4.5, 15),
    (51, 80, 0.4, 4),
    (81, 99, 0.08, 0.9),
    (100, 100, 0.02, 0.1),  # the jackpot
]

# Each red card earned the same day mutes for longer than the previous one;
# once the ladder runs out, the last step repeats.
MUTE_LADDER_SECONDS = [60, 5 * 60, 15 * 60, 30 * 60, 60 * 60]

# The ladder starts over at midnight in this timezone (UTC+3 — Москва).
DAY_RESET_UTC_OFFSET_HOURS = 3

# Buying a temporary admin badge (custom title) for someone else. Each tier is
# (duration_seconds, price_in_kabankoins).
NICKNAME_PRICE_TIERS = [
    (3600, 50),
    (2 * 3600, 100),
    (4 * 3600, 200),
    (24 * 3600, 500),
    (7 * 24 * 3600, 2000),
]

# Telegram's own limit on setChatAdministratorCustomTitle.
NICKNAME_MAX_LENGTH = 16
