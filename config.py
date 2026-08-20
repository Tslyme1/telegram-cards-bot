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

# Each red card earned the same day mutes for longer than the previous one;
# once the ladder runs out, the last step repeats.
MUTE_LADDER_SECONDS = [60, 5 * 60, 15 * 60, 30 * 60, 60 * 60]

# The ladder starts over at midnight in this timezone (UTC+3 — Москва).
DAY_RESET_UTC_OFFSET_HOURS = 3
