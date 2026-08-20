YELLOW_THRESHOLD = 3
GIVE_COOLDOWN_SECONDS = 15
GREEN_COOLDOWN_SECONDS = 60

# How many unused green cards one person can hold at once.
GREEN_IMMUNITY_LIMIT = 3

# /casino hands out one card of a random colour.
CASINO_COOLDOWN_SECONDS = 10
CASINO_OUTCOMES = ["green", "yellow", "red"]

# Before the colour is rolled, the spinner picks a combination on a slot
# machine. Hitting one of the losing combinations backfires: the red card
# lands on whoever spun, and the target gets nothing.
CASINO_SYMBOLS = ["🍒", "🍋", "🎰", "🔔"]
CASINO_SLOTS = 3
# Drawn fresh for every spin, so nobody can learn which ones to avoid.
# With 4 symbols in 3 slots there are 64 combinations, so 3 of them is a
# backfire chance of about 4.7%.
CASINO_LOSING_COMBOS = 3

# Each red card earned the same day mutes for longer than the previous one;
# once the ladder runs out, the last step repeats.
MUTE_LADDER_SECONDS = [60, 5 * 60, 15 * 60, 30 * 60, 60 * 60]

# The ladder starts over at midnight in this timezone (UTC+3 — Москва).
DAY_RESET_UTC_OFFSET_HOURS = 3
