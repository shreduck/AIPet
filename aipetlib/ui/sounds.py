"""AIPet's sounds: synthesized xylophone chimes, or the host system's own sounds.

AIPet's own sounds are two xylophone notes each: "ting" rises when a session is done, "knock" falls when one needs
you, and "thud" signals an error. Written once to ~/.aipet/sounds as WAV files, they are the default on Windows and
macOS; users can choose their host OS sounds instead (config "sound_style": "chimes" or "system").
No Tk here: the caller passes its bell for the platforms that have nothing else.
"""
import math
import os
import subprocess
import sys

from ..runtime import paths

try:
    import winsound
except ImportError:  # not Windows
    winsound = None

IS_MAC = sys.platform == "darwin"

CHIME_VERSION = 4  # bump when the sounds change, so the new WAV files get written
CHIME_RATE = 44100


# Two xylophone notes each: (frequency, start in seconds, loudness, ring time)
CHIMES = {
    # Mid register and mellow on purpose: noticeable without being shrill while you're concentrating
    "ting": ((440.0, 0.0, 0.9, 0.17), (587.3, 0.22, 1.0, 0.22)),  # done: A4 then D5, rising
    "knock": ((392.0, 0.0, 1.0, 0.14), (329.6, 0.24, 0.95, 0.17)),  # needs you: G4 then E4, like a doorbell
    # error: a low, falling tritone (F4 then B3) - clearly "something's off", still soft, never a buzzer
    "thud": ((349.2, 0.0, 1.0, 0.13), (246.9, 0.24, 1.0, 0.2)),
}


def xylophone_note(out, f0, start, amp, ring):
    """Add one soft-mallet xylophone note: the bar's fundamental, its tuned 3rd harmonic and a faint high overtone
    that die away quickly, with a tiny downward bend at the strike."""
    rate, phase = CHIME_RATE, 0.0
    first, n = int(start * rate), int(ring * 6 * rate)
    attack, release = int(0.008 * rate), int(n * 0.3)
    for i in range(n):
        t = i / rate
        phase += 2 * math.pi * f0 * (1 + 0.02 * math.exp(-t / 0.006)) / rate
        v = (math.exp(-t / ring) * math.sin(phase)
             + 0.12 * math.exp(-t / (ring * 0.3)) * math.sin(3 * phase)
             + 0.02 * math.exp(-t / (ring * 0.12)) * math.sin(6.27 * phase))
        # Smooth (raised-cosine) 8 ms attack and a release that lands exactly on zero: any corner in the envelope,
        # at the start or where a note stops, is heard as a tick
        env = 0.5 - 0.5 * math.cos(math.pi * i / attack) if i < attack else 1.0
        if i > n - release:
            env *= 0.5 - 0.5 * math.cos(math.pi * (n - i) / release)
        if first + i < len(out):
            out[first + i] += amp * v * env


CHIME_PAD = (0.06, 0.2)  # seconds of silence before / after, so opening and closing the audio device is not heard


def chime_samples(kind):
    """The chime as floats in -1..1, padded with silence."""
    notes = CHIMES.get(kind, CHIMES["ting"])
    out = [0.0] * int(CHIME_RATE * max(start + ring * 6 for _, start, _, ring in notes))
    for f0, start, amp, ring in notes:
        xylophone_note(out, f0, start, amp, ring)
    peak = max(abs(v) for v in out) or 1.0
    lead, tail = (int(CHIME_RATE * s) for s in CHIME_PAD)
    return [0.0] * lead + [v / peak * 0.5 for v in out] + [0.0] * tail


def chime_path(kind):
    """A WAV file for the chime, created on first use."""
    import struct
    import wave
    folder = os.path.join(paths.HOME_DIR, "sounds")
    path = os.path.join(folder, f"{kind}-v{CHIME_VERSION}.wav")
    if not os.path.exists(path):
        os.makedirs(folder, exist_ok=True)
        data = b"".join(struct.pack("<h", int(v * 32767)) for v in chime_samples(kind))
        with wave.open(path + ".tmp", "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(CHIME_RATE)
            f.writeframes(data)
        os.replace(path + ".tmp", path)
    return path


def play(urgent, error=False, style="chimes", bell=None):
    """Play the sound for: error, else urgent (a session needs you), else done. style: "chimes" or "system";
    bell: called where there is no sound to play (and on macOS when even the system sound fails)."""
    if winsound:
        if style == "chimes":
            try:
                winsound.PlaySound(chime_path("thud" if error else "knock" if urgent else "ting"),
                                   winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                return
            except (RuntimeError, OSError):
                pass  # fall back to the system sound
        try:
            winsound.MessageBeep(winsound.MB_ICONHAND if error else
                                 winsound.MB_ICONEXCLAMATION if urgent else winsound.MB_ICONASTERISK)
        except RuntimeError:
            pass
    elif IS_MAC:
        if style == "chimes":
            try:
                subprocess.Popen(["afplay", chime_path("thud" if error else "knock" if urgent else "ting")])
                return
            except OSError:
                pass  # fall back to the system sound if the chime cannot be created or played
        try:
            sound = "Basso" if error else "Funk" if urgent else "Glass"
            subprocess.Popen(["afplay", f"/System/Library/Sounds/{sound}.aiff"])
        except OSError:
            if bell:
                bell()
    elif bell:
        bell()
