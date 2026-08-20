YELLOW_THRESHOLD = 3
GIVE_COOLDOWN_SECONDS = 15
GREEN_COOLDOWN_SECONDS = 60

# How many unused green cards one person can hold at once.
GREEN_IMMUNITY_LIMIT = 3

# /casino hands out one card of a random colour, at most once an hour.
CASINO_COOLDOWN_SECONDS = 30 * 60
CASINO_OUTCOMES = ["green", "yellow", "red"]

# Chance that a spin backfires and the red card lands on whoever spun.
# Checked before the colour roll, so it replaces the spin's outcome entirely.
CASINO_BACKFIRE_CHANCE = 0.1

# Each red card earned the same day mutes for longer than the previous one;
# once the ladder runs out, the last step repeats.
MUTE_LADDER_SECONDS = [60, 5 * 60, 15 * 60, 30 * 60, 60 * 60]

# The ladder starts over at midnight in this timezone (UTC+3 — Москва).
DAY_RESET_UTC_OFFSET_HOURS = 3
