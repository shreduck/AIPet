"""Live preference holders shared by the UI and background workers, with the ranges that keep them valid.

Each holder is a one-key dict mutated in place (holder["v"] = ...), so every module that imported it sees the
change. Import the holder, never its value, and never rebind it.
"""

DONE_TIMEOUT = {"v": 3}  # minutes until a finished session's pet is cleared (0 = never)
SCALE_UNIT = 2.0  # the absolute drawing scale that counts as 100% (it was the old 200%, the size people settled on)
SCALE = {"v": SCALE_UNIT}  # absolute drawing scale: 92x122 px per pet at 1.0; the slider shows SCALE / SCALE_UNIT
STYLE = {"v": "robot"}  # "robot", "mole" or "cat"
ANSWER_WAIT = {"v": 180}  # seconds; mirrored into <pet dir>/answer-wait for the hook (0 = wait until answered)
MAX_WAIT = 1800  # every timer: 0 (no limit / never) to 30 minutes
HEALTH = {"v": 15}  # seconds between checks that a working session's process still runs (0 = off)
MAX_HEALTH = 300
CARD_SCALE = {"v": 1.0}  # the permission / question card's size (its slider, Ctrl + plus / minus / 0, Ctrl + wheel)
CARD_SCALE_MIN, CARD_SCALE_MAX, CARD_SCALE_STEP = 0.5, 1.5, 0.1


def clamp_health(v):
    try:
        return int(max(0, min(MAX_HEALTH, round(float(v)))))
    except (TypeError, ValueError):
        return 15


def clamp_wait(v):
    try:
        return int(max(0, min(MAX_WAIT, round(float(v)))))
    except (TypeError, ValueError):
        return 180


def clamp_done(v):
    try:
        return int(max(0, min(MAX_WAIT // 60, round(float(v)))))
    except (TypeError, ValueError):
        return 3


def clamp_scale(v):
    try:
        return max(0.6, min(6.0, float(v)))  # 30% - 300% of SCALE_UNIT
    except (TypeError, ValueError):
        return 1.0


def clamp_card_scale(value):
    try:
        return round(min(CARD_SCALE_MAX, max(CARD_SCALE_MIN, float(value))), 2)
    except (TypeError, ValueError):
        return 1.0
