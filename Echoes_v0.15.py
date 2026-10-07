#!/usr/bin/env python3
"""
ECHOES OF THE ASYLUM
Nested building · Contextual scares · Patient 47 folder · Procedural audio
"""

import os
import sys
import time
import random
import threading
import queue
import wave
import struct
import tempfile
import math
import json
import array

try:
    import winsound
    HAS_WINSOUND = True
except ImportError:
    HAS_WINSOUND = False

FOLDER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "patient_47_folder.json")

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "asylum_settings.json")
SAVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saves")
AUTOSAVE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saves", "autosave.json")
_ACTIVE_STATE = None  # current run — every choice can autosave against this
_ACTIVE_SLOT = None   # "1" | "2" | "3" — autosave writes to this slot
CASE_EXPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "patient_47_case_file.txt")
RED, BOLD, DIM, RESET = "\033[91m", "\033[1m", "\033[2m", "\033[0m"
SETTINGS = {
    "skip_typewriter": False,
    "qte_time_scale": 1.0,
    "reduce_motion": False,
    "director_commentary": False,
    "director_unlocked": False,
    "difficulty": "standard",  # story | standard | hard
    "qte_key": "f",
    "director_cut_unlocked": False,
    "hold_qte": False,
    "debug_mode": False,
}
_ambient_stop = threading.Event()
_ambient_sanity = 100
_ambient_scene = "cell"
_ambient_volume = 0.55
_sound_lock = threading.Lock()
_typewriter_busy = False
_ambient_hold = False  # True until room intro text finishes
_BED_CACHE = {}  # (scene, sanity_bucket) -> samples
_BED_CACHE_MAX = 48

def load_settings():
    global SETTINGS
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            SETTINGS.update(json.load(f))
    except Exception:
        pass

def save_settings():
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(SETTINGS, f, indent=2)
    except Exception:
        pass

def colorize(text, code):
    if not SETTINGS.get("reduce_motion"):
        return f"{code}{text}{RESET}"
    return text

def commentary(line):
    if SETTINGS.get("director_commentary") and SETTINGS.get("director_unlocked"):
        print(colorize(f"  [DIR] {line}", DIM))
        time.sleep(0.25)

def qte_scale(base):
    scale = float(SETTINGS.get("qte_time_scale", 1.0))
    diff = SETTINGS.get("difficulty", "standard")
    if diff == "story":
        scale *= 1.55
    elif diff == "hard":
        scale *= 0.72
    return base * scale

def qte_primary_key():
    k = str(SETTINGS.get("qte_key", "f") or "f").lower()[:1]
    return k if k and k.isalpha() else "f"


def flush_input_buffer():
    """Discard any buffered keypresses so prior QTE spam cannot spill into the next."""
    try:
        if os.name == "nt":
            import msvcrt
            while msvcrt.kbhit():
                msvcrt.getch()
        else:
            import select
            while select.select([sys.stdin], [], [], 0)[0]:
                try:
                    sys.stdin.read(1)
                except Exception:
                    break
    except Exception:
        pass



def confirm_run_end(warning_lines, confirm_phrase, deny_hint=None):
    """Flavored last-chance confirmation before a non-suicide run end.
    Player must type confirm_phrase exactly (case-insensitive) to proceed.
    Returns True if they commit, False if they refuse / mistype as refusal path.
    """
    print()
    for line in warning_lines:
        typewriter(line)
        pause(0.35)
    print()
    typewriter(f'To continue, type exactly: "{confirm_phrase}"')
    if deny_hint:
        typewriter(deny_hint)
    else:
        typewriter('Anything else — or Enter alone — and you step back from the edge.')
    flush_input_buffer()
    try:
        typed = input("  > ").strip()
    except Exception:
        typed = ""
    if typed.lower() == confirm_phrase.lower():
        typewriter("So be it.")
        pause(0.4)
        return True
    typewriter("You draw back. The night continues.")
    pause(0.5)
    return False


def countdown_before_qte(seconds=3.0, label="READY"):
    """Live 3s (default) countdown between prompt and action; continuously flushes input."""
    seconds = float(seconds)
    if seconds <= 0:
        flush_input_buffer()
        return
    print()
    end = time.time() + seconds
    last_shown = None
    while True:
        flush_input_buffer()
        left = end - time.time()
        if left <= 0:
            break
        n = int(left) + 1  # 3,2,1
        if n != last_shown:
            last_shown = n
            sys.stdout.write(f"\r  {label}... {n}   ")
            sys.stdout.flush()
        time.sleep(0.04)
    flush_input_buffer()
    sys.stdout.write(f"\r  {label}... GO          \n")
    sys.stdout.flush()
    flush_input_buffer()



def difficulty_nurse_aggression():
    """Higher = more likely to hunt."""
    d = SETTINGS.get("difficulty", "standard")
    return {"story": 0.25, "standard": 0.55, "hard": 0.8}.get(d, 0.55)


def screen_flicker(frames=3):
    if SETTINGS.get("reduce_motion"):
        return
    for _ in range(frames):
        clear()
        time.sleep(0.04)
        print("\n" * 2 + colorize("          # # # # # # # #", RED))
        time.sleep(0.05)
    clear()

def _ambient_loop():
    """Continuous loop: keep room/menu bed playing for the whole stay.
    Stays silent while the typewriter is printing so music does not interject."""
    global _ambient_sanity
    while not _ambient_stop.is_set():
        if False:  # reserved
            _ambient_stop.wait(0.15)
            continue
        scene = _ambient_scene or "cell"
        try:
            bed = _render_room_bed(scene, _ambient_sanity)
            if False:
                continue
            play_samples(bed, wait=False)
            dur = max(2.0, len(bed) / 22050.0 - 0.4)
            # poll in small slices so we can yield to typewriter quickly
            elapsed = 0.0
            while elapsed < dur and not _ambient_stop.is_set():
                if _typewriter_busy:
                    break
                step = min(0.2, dur - elapsed)
                _ambient_stop.wait(step)
                elapsed += step
        except Exception:
            _ambient_stop.wait(3.0)



def start_ambient():
    stop_ambient()
    _ambient_stop.clear()
    threading.Thread(target=_ambient_loop, daemon=True).start()

def suppress_ambient():
    """No-op: ambient is allowed under typewriter."""
    pass


def allow_ambient():
    """No-op: ambient already loops under typewriter."""
    pass



def stop_ambient():
    _ambient_stop.set()


# ============================================================
# Procedural Audio
# ============================================================

def generate_tone(frequency, duration, volume=0.55, sample_rate=22050, wave_type="sine"):
    """Fast PCM tone generator. Returns list of int16-range samples."""
    n_samples = int(sample_rate * duration)
    if n_samples <= 0:
        return []
    amp = volume * 32767.0
    samples = [0] * n_samples
    if wave_type == "noise" or frequency == 0 and wave_type != "sine":
        # pure noise / silence-ish noise path
        if frequency == 0 and wave_type == "sine":
            return samples  # silence
        ru = random.uniform
        for i in range(n_samples):
            samples[i] = int(ru(-1, 1) * amp)
        return samples
    two_pi_f = 2.0 * math.pi * frequency
    inv_sr = 1.0 / sample_rate
    if wave_type == "sine":
        sin = math.sin
        for i in range(n_samples):
            samples[i] = int(sin(two_pi_f * i * inv_sr) * amp)
    elif wave_type == "square":
        sin = math.sin
        for i in range(n_samples):
            samples[i] = int(amp if sin(two_pi_f * i * inv_sr) > 0 else -amp)
    elif wave_type == "saw":
        floor = math.floor
        for i in range(n_samples):
            t = i * inv_sr
            samples[i] = int((2.0 * (t * frequency - floor(0.5 + t * frequency))) * amp)
    else:
        ru = random.uniform
        for i in range(n_samples):
            samples[i] = int(ru(-1, 1) * amp)
    return samples


def generate_drone(base_freq, duration, sanity=100, intensity=0.3, layers=3):
    sample_rate = 22050
    n = int(sample_rate * duration)
    if n <= 0:
        return []
    detune = max(0.0, (100 - sanity) / 100.0) * 12.0
    inv_sr = 1.0 / sample_rate
    scale = intensity * 32767.0
    sin = math.sin
    two_pi = 2.0 * math.pi
    f1 = two_pi * base_freq
    f2 = two_pi * (base_freq + detune)
    f3 = two_pi * (base_freq * 1.5)
    f4 = two_pi * (base_freq * 0.5) if layers >= 4 else 0.0
    use_noise = sanity < 60
    noise_amp = (1.0 - sanity / 100.0) if use_noise else 0.0
    ru = random.uniform
    samples = [0] * n
    for i in range(n):
        t = i * inv_sr
        env = min(1.0, t * 1.5) * min(1.0, (duration - t) * 1.2)
        val = (
            sin(f1 * t) * 0.45
            + sin(f2 * t) * 0.28
            + sin(f3 * t) * 0.18
        )
        if f4:
            val += sin(f4 * t) * 0.22
        if use_noise:
            val += ru(-0.18, 0.18) * noise_amp
        samples[i] = int(val * scale * env)
    return samples


def generate_melody_bed(base_freq, duration, sanity=100, intensity=0.22):
    sample_rate = 22050
    n = int(sample_rate * duration)
    if n <= 0:
        return []
    samples = [0] * n
    notes = (
        (0.0, base_freq),
        (duration * 0.28, base_freq * 1.189),
        (duration * 0.55, base_freq * 0.943),
        (duration * 0.78, base_freq * 1.335),
    )
    note_len = duration * 0.22
    fade = sample_rate * 0.15
    for start, freq in notes:
        note = generate_tone(freq, note_len, intensity * 0.7, sample_rate, "sine")
        base_idx = int(start * sample_rate)
        for i, s in enumerate(note):
            idx = base_idx + i
            if idx >= n:
                break
            env = i / fade if i < fade else 1.0
            samples[idx] = max(-32767, min(32767, samples[idx] + int(s * env * 0.6)))
    pad = generate_drone(base_freq * 0.5, duration, sanity, intensity * 0.6, layers=3)
    lim = min(n, len(pad))
    for i in range(lim):
        samples[i] = max(-32767, min(32767, samples[i] + (pad[i] // 2)))
    return samples


def generate_menu_music(duration=12.0):
    sample_rate = 22050
    n = int(sample_rate * duration)
    samples = [0] * n
    pad = generate_drone(42, duration, 90, 0.16, layers=4)
    for i in range(n):
        samples[i] = pad[i] if i < len(pad) else 0
    arp_notes = [84, 100, 126, 100, 84, 75, 100, 126]
    note_dur = duration / len(arp_notes)
    for idx, freq in enumerate(arp_notes):
        start = idx * note_dur
        note = generate_tone(freq, note_dur * 0.7, 0.09, sample_rate, "sine")
        for i, s in enumerate(note):
            env = min(1.0, i / (sample_rate * 0.2)) * min(1.0, (len(note) - i) / (sample_rate * 0.3))
            pos = int(start * sample_rate) + i
            if pos < n:
                samples[pos] = max(-32767, min(32767, samples[pos] + int(s * env)))
    return samples


def generate_heartbeat(bpm=48, beats=4, volume=0.4):
    sample_rate = 22050
    samples = []
    beat_dur = 60 / bpm
    for _ in range(beats):
        thump = generate_tone(52, 0.11, volume, sample_rate, "sine")
        for i in range(len(thump)):
            thump[i] = int(thump[i] * (1 - i / len(thump) * 0.45))
        samples.extend(thump)
        samples.extend([0] * int(sample_rate * (beat_dur - 0.11)))
    return samples


def generate_type_click():
    return generate_tone(random.randint(1800, 2600), 0.018, 0.09, wave_type="square")



def generate_suicide_soundscape(kind="rope"):
    """Long, bassy, dissonant bed — played after ending text, not under it."""
    samples = []
    sample_rate = 22050
    for base in (28, 31, 36, 41):
        layer = generate_drone(base, 2.2, sanity=20, intensity=0.85, layers=4)
        for i, s in enumerate(layer):
            if i >= len(samples):
                samples.append(s)
            else:
                samples[i] = max(-32767, min(32767, samples[i] + s // 2))
    pulse = []
    n = int(sample_rate * 3.5)
    for i in range(n):
        t = i / sample_rate
        val = (
            0.45 * math.sin(2 * math.pi * 32 * t)
            + 0.40 * math.sin(2 * math.pi * 34.5 * t)
            + 0.30 * math.sin(2 * math.pi * 48 * t)
            + 0.25 * math.sin(2 * math.pi * 51 * t)
            + 0.15 * math.sin(2 * math.pi * 64 * t)
        )
        env = 0.4 + 0.6 * abs(math.sin(2 * math.pi * 0.15 * t))
        pulse.append(int(val * env * 0.9 * 32767))
    for i, s in enumerate(pulse):
        if i >= len(samples):
            samples.append(s)
        else:
            samples[i] = max(-32767, min(32767, samples[i] + s // 2))
    samples.extend(generate_tone(0, 0.8, 0.55, wave_type="noise"))
    if kind == "rope":
        for f in (55, 48, 40, 32, 24, 18):
            samples.extend(generate_tone(f, 0.35, 0.9, wave_type="sine"))
        samples.extend(generate_tone(22, 1.2, 0.95, wave_type="sine"))
        samples.extend(generate_tone(0, 0.5, 0.7, wave_type="noise"))
    else:
        for _ in range(5):
            samples.extend(generate_tone(30, 0.25, 0.95, wave_type="sine"))
            samples.extend(generate_tone(45, 0.25, 0.7, wave_type="sine"))
            samples.extend(generate_tone(0, 0.12, 0.5, wave_type="noise"))
        samples.extend(generate_tone(27, 1.0, 0.9, wave_type="sine"))
        samples.extend(generate_tone(0, 0.6, 0.65, wave_type="noise"))
    tail = generate_tone(33, 1.5, 0.6, wave_type="sine")
    detune = generate_tone(36.5, 1.5, 0.5, wave_type="sine")
    for i in range(len(tail)):
        v = tail[i] + (detune[i] if i < len(detune) else 0)
        samples.append(max(-32767, min(32767, v // 2)))
    return samples



def play_suicide_audio(kind="rope"):
    """Full soundscape; blocks until finished so it is not under the typewriter."""
    stop_ambient()
    play_samples(generate_suicide_soundscape(kind), wait=True)


def generate_stinger_classic(intensity=1.0):
    samples = []
    for freq in range(600, 2800, 40):
        samples.extend(generate_tone(freq, 0.03, intensity * 0.7, wave_type="square"))
    samples.extend(generate_tone(1800, 0.08, intensity * 0.85, wave_type="saw"))
    samples.extend(generate_tone(0, 0.22, intensity, wave_type="noise"))
    return samples


def generate_stinger_bass_drop(intensity=0.9):
    samples = []
    samples.extend(generate_tone(35, 0.25, intensity * 0.9, wave_type="sine"))
    for freq in range(600, 2200, 50):
        samples.extend(generate_tone(freq, 0.02, intensity * 0.7, wave_type="saw"))
    samples.extend(generate_tone(0, 0.12, intensity * 0.8, wave_type="noise"))
    return samples


def generate_stinger_metal_scrape(intensity=0.8):
    samples = []
    for i in range(18):
        freq = 180 + i * 40 + random.randint(-30, 30)
        samples.extend(generate_tone(freq, 0.04, intensity * 0.5, wave_type="noise"))
        samples.extend(generate_tone(freq * 1.7, 0.03, intensity * 0.35, wave_type="square"))
    samples.extend(generate_tone(50, 0.2, intensity, wave_type="sine"))
    return samples


def generate_stinger_whisper_scream(intensity=0.85):
    samples = []
    samples.extend(generate_tone(120, 0.35, intensity * 0.15, wave_type="sine"))
    for f in [280, 450, 720, 1100, 1600]:
        samples.extend(generate_tone(f, 0.08, intensity * 0.55, wave_type="square"))
    samples.extend(generate_tone(0, 0.2, intensity * 0.75, wave_type="noise"))
    return samples


def generate_stinger_child_laugh(intensity=0.75):
    samples = []
    for _ in range(5):
        f = random.randint(700, 1400)
        samples.extend(generate_tone(f, 0.06, intensity * 0.5, wave_type="sine"))
        samples.extend([0] * int(22050 * 0.04))
    for freq in range(900, 200, -60):
        samples.extend(generate_tone(freq, 0.025, intensity * 0.45, wave_type="saw"))
    samples.extend(generate_tone(40, 0.3, intensity * 0.7, wave_type="sine"))
    return samples


def generate_stinger_heartstop(intensity=1.0):
    samples = [0] * int(22050 * 0.25)
    low = generate_tone(30, 0.35, intensity, wave_type="sine")
    high = generate_tone(1800, 0.12, intensity * 0.8, wave_type="square")
    for i in range(max(len(low), len(high))):
        v = (low[i] if i < len(low) else 0) + (high[i] // 2 if i < len(high) else 0)
        samples.append(max(-32767, min(32767, v)))
    samples.extend(generate_tone(0, 0.15, intensity * 0.6, wave_type="noise"))
    return samples


def generate_stinger_nurse(intensity=1.0):
    samples = []
    for _ in range(4):
        samples.extend(generate_tone(70, 0.06, intensity * 0.25, wave_type="sine"))
        samples.extend([0] * int(22050 * 0.12))
    for f in [320, 480, 650, 900]:
        samples.extend(generate_tone(f, 0.07, intensity * 0.6, wave_type="square"))
    samples.extend(generate_tone(0, 0.18, intensity * 0.7, wave_type="noise"))
    return samples


def generate_stinger_body(intensity=1.0):
    samples = []
    samples.extend(generate_tone(40, 0.4, intensity * 0.5, wave_type="sine"))
    for freq in range(200, 1100, 45):
        samples.extend(generate_tone(freq, 0.03, intensity * 0.55, wave_type="saw"))
    samples.extend(generate_tone(0, 0.22, intensity, wave_type="noise"))
    return samples


def play_samples(samples, sample_rate=22050, wait=False):
    """Play PCM samples via winsound. wait=True blocks until finished (stingers)."""
    if not samples:
        return
    n = len(samples)
    # boost overall loudness into int16 range via array (much faster than struct loop)
    boosted = array.array('h')
    boosted.extend(max(-32767, min(32767, int(s * 1.35))) for s in samples)
    if not HAS_WINSOUND:
        if wait:
            time.sleep(min(2.5, n / float(sample_rate)))
        return
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            path = f.name
        with wave.open(path, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(boosted.tobytes())
        flags = winsound.SND_FILENAME
        if not wait:
            flags |= winsound.SND_ASYNC
        with _sound_lock:
            winsound.PlaySound(path, flags)

        def cleanup():
            dur = n / float(sample_rate) + 0.5
            time.sleep(dur if not wait else 0.3)
            try:
                os.remove(path)
            except Exception:
                pass

        threading.Thread(target=cleanup, daemon=True).start()
        if wait:
            time.sleep(n / float(sample_rate) + 0.05)
    except Exception:
        pass



def play_ambient(scene_type, sanity=100):
    """Set and start looping ambient bed for a room (plays under typewriter)."""
    global _ambient_scene, _ambient_sanity, _ambient_hold
    _ambient_scene = scene_type
    _ambient_sanity = sanity
    _ambient_hold = False
    if _ambient_stop.is_set():
        start_ambient()
    play_samples(_render_room_bed(scene_type, sanity), wait=False)


def _render_room_bed(scene_type, sanity=100):
    # Bucket sanity so we reuse beds instead of regenerating every loop
    bucket = int(sanity) // 10
    cache_key = (scene_type, bucket)
    cached = _BED_CACHE.get(cache_key)
    if cached is not None:
        return cached
    beds = {
        "cell": (55, 5.0, 0.52, 220),
        "hallway": (70, 4.5, 0.55, 310),
        "morgue": (42, 5.5, 0.62, 180),
        "basement": (38, 5.5, 0.65, 160),
        "chapel": (88, 5.0, 0.55, 440),
        "kitchen": (62, 4.5, 0.5, 280),
        "laundry": (55, 4.5, 0.52, 250),
        "archives": (78, 5.0, 0.55, 360),
        "courtyard": (100, 4.5, 0.48, 520),
        "nurse": (95, 4.5, 0.58, 400),
        "stairs": (58, 4.5, 0.55, 240),
        "tension": (110, 4.0, 0.7, 600),
        "side_cell": (50, 5.0, 0.52, 200),
        "menu": (48, 6.0, 0.5, 200),
        # Dedicated upper-floor / new-room beds
        "upper_landing": (52, 5.0, 0.54, 210),
        "upper_corridor": (66, 4.8, 0.53, 290),
        "gallery": (72, 5.2, 0.5, 380),
        "therapy": (84, 5.3, 0.48, 420),
        "records_annex": (74, 5.0, 0.56, 340),
        "dir_suite": (46, 5.5, 0.5, 190),
        "roof": (108, 4.6, 0.46, 560),
        "staff_wing": (68, 4.7, 0.54, 300),
    }
    base, dur, inten, high = beds.get(scene_type, (60, 4.5, 0.55, 300))
    samples = generate_drone(base, dur, sanity, inten, layers=4)
    # higher-pitched shimmer / dissonance layer
    high_layer = generate_tone(high, dur, inten * 0.35, wave_type="sine")
    high2 = generate_tone(high * 1.5, dur, inten * 0.22, wave_type="square")
    for i in range(min(len(samples), len(high_layer))):
        samples[i] = max(-32767, min(32767, samples[i] + high_layer[i] // 2))
    for i in range(min(len(samples), len(high2))):
        samples[i] = max(-32767, min(32767, samples[i] + high2[i] // 3))
    if scene_type in ("cell", "chapel", "menu", "therapy", "dir_suite", "gallery"):
        mel = generate_melody_bed(base * 2, dur, sanity, 0.22)
        for i in range(min(len(samples), len(mel))):
            samples[i] = max(-32767, min(32767, samples[i] + mel[i] // 2))
    if len(_BED_CACHE) >= _BED_CACHE_MAX:
        # drop an arbitrary oldest-ish entry
        try:
            _BED_CACHE.pop(next(iter(_BED_CACHE)))
        except Exception:
            _BED_CACHE.clear()
    _BED_CACHE[cache_key] = samples
    return samples


def play_background_music(sanity=100):
    play_ambient(_ambient_scene if _ambient_scene else "cell", sanity)


def play_menu_music():
    global _ambient_scene
    _ambient_scene = "menu"
    start_ambient()
    play_samples(_render_room_bed("menu", 90), wait=False)



def play_qte_tick(sanity=100):
    play_samples(generate_tone(780 + (100 - sanity) * 5, 0.07, 0.32))


def play_success():
    play_samples(generate_tone(523, 0.14, 0.38) + generate_tone(659, 0.2, 0.32))


def play_fail():
    play_samples(generate_stinger_classic(0.45), wait=True)


def play_type_click():
    if HAS_WINSOUND and random.random() < 0.5:
        play_samples(generate_type_click())


# ============================================================
# Big ASCII text for QTE instructions
# ============================================================

BIG_CHARS = {
    "A": [" ███ ", "█   █", "█████", "█   █", "█   █"],
    "B": ["████ ", "█   █", "████ ", "█   █", "████ "],
    "C": [" ████", "█    ", "█    ", "█    ", " ████"],
    "D": ["████ ", "█   █", "█   █", "█   █", "████ "],
    "E": ["█████", "█    ", "████ ", "█    ", "█████"],
    "F": ["█████", "█    ", "████ ", "█    ", "█    "],
    "G": [" ████", "█    ", "█  ██", "█   █", " ████"],
    "H": ["█   █", "█   █", "█████", "█   █", "█   █"],
    "I": ["█████", "  █  ", "  █  ", "  █  ", "█████"],
    "J": ["█████", "   █ ", "   █ ", "█  █ ", " ██  "],
    "K": ["█   █", "█  █ ", "███  ", "█  █ ", "█   █"],
    "L": ["█    ", "█    ", "█    ", "█    ", "█████"],
    "M": ["█   █", "██ ██", "█ █ █", "█   █", "█   █"],
    "N": ["█   █", "██  █", "█ █ █", "█  ██", "█   █"],
    "O": [" ███ ", "█   █", "█   █", "█   █", " ███ "],
    "P": ["████ ", "█   █", "████ ", "█    ", "█    "],
    "Q": [" ███ ", "█   █", "█   █", "█  █ ", " ██ █"],
    "R": ["████ ", "█   █", "████ ", "█  █ ", "█   █"],
    "S": [" ████", "█    ", " ███ ", "    █", "████ "],
    "T": ["█████", "  █  ", "  █  ", "  █  ", "  █  "],
    "U": ["█   █", "█   █", "█   █", "█   █", " ███ "],
    "V": ["█   █", "█   █", "█   █", " █ █ ", "  █  "],
    "W": ["█   █", "█   █", "█ █ █", "██ ██", "█   █"],
    "X": ["█   █", " █ █ ", "  █  ", " █ █ ", "█   █"],
    "Y": ["█   █", " █ █ ", "  █  ", "  █  ", "  █  "],
    "Z": ["█████", "   █ ", "  █  ", " █   ", "█████"],
    "0": [" ███ ", "█  ██", "█ █ █", "██  █", " ███ "],
    "1": ["  █  ", " ██  ", "  █  ", "  █  ", "█████"],
    "2": [" ███ ", "█   █", "  ██ ", " █   ", "█████"],
    "3": ["████ ", "    █", " ███ ", "    █", "████ "],
    "4": ["█  █ ", "█  █ ", "█████", "   █ ", "   █ "],
    "5": ["█████", "█    ", "████ ", "    █", "████ "],
    "6": [" ███ ", "█    ", "████ ", "█   █", " ███ "],
    "7": ["█████", "   █ ", "  █  ", " █   ", "█    "],
    "8": [" ███ ", "█   █", " ███ ", "█   █", " ███ "],
    "9": [" ███ ", "█   █", " ████", "    █", " ███ "],
    " ": ["     ", "     ", "     ", "     ", "     "],
    "!": ["  █  ", "  █  ", "  █  ", "     ", "  █  "],
    "?": [" ███ ", "█   █", "  ██ ", "     ", "  █  "],
    "-": ["     ", "     ", "█████", "     ", "     "],
    ".": ["     ", "     ", "     ", "     ", "  █  "],
    "[": [" ██  ", " █   ", " █   ", " █   ", " ██  "],
    "]": ["  ██ ", "   █ ", "   █ ", "   █ ", "  ██ "],
}


def big_text(text, max_width=70):
    text = text.upper()
    lines_out = []
    words = text.split(" ")
    chunks = []
    cur = ""
    for w in words:
        test = (cur + " " + w).strip()
        if len(test) * 6 > max_width and cur:
            chunks.append(cur)
            cur = w
        else:
            cur = test
    if cur:
        chunks.append(cur)
    for chunk in chunks:
        rows = ["", "", "", "", ""]
        for ch in chunk:
            glyph = BIG_CHARS.get(ch, BIG_CHARS["?"])
            for r in range(5):
                rows[r] += glyph[r] + " "
        lines_out.extend(rows)
        lines_out.append("")
    return "\n".join(lines_out)


def print_big(text):
    print()
    print(big_text(text))
    print()


# ============================================================
# Timed input
# ============================================================


def timed_keypress(prompt, valid_keys, timeout=3.5):
    """Timed key read (no on-screen timer bar)."""
    flush_input_buffer()
    timeout = qte_scale(timeout)
    if isinstance(valid_keys, str):
        valid = set(valid_keys.lower())
    else:
        valid = set(k.lower() for k in valid_keys)
    print(prompt, end="", flush=True)
    result_queue = queue.Queue()

    def get_input():
        try:
            if os.name == "nt":
                import msvcrt
                start = time.time()
                while time.time() - start < timeout:
                    if msvcrt.kbhit():
                        key = msvcrt.getch().decode("utf-8", errors="ignore").lower()
                        result_queue.put(key)
                        return
                    time.sleep(0.015)
                result_queue.put(None)
            else:
                import select
                rlist, _, _ = select.select([sys.stdin], [], [], timeout)
                if rlist:
                    result_queue.put(sys.stdin.read(1).lower())
                else:
                    result_queue.put(None)
        except Exception:
            result_queue.put(None)

    th = threading.Thread(target=get_input, daemon=True)
    th.start()
    th.join(timeout + 0.15)
    try:
        key = result_queue.get_nowait()
    except Exception:
        key = None
    if key and key in valid:
        return key
    return None



def hold_keypress(prompt, key, hold_seconds=0.85, timeout=4.0):
    """Hold-to-complete QTE (accessibility alternative to quick taps)."""
    flush_input_buffer()
    key = key.lower()
    timeout = qte_scale(timeout)
    hold_seconds = max(0.4, hold_seconds * (0.85 if SETTINGS.get("difficulty") == "hard" else 1.0))
    print(prompt + f"  (HOLD [{key.upper()}] {hold_seconds:.1f}s)", flush=True)
    start = time.time()
    held = 0.0
    while time.time() - start < timeout:
        try:
            if os.name == "nt":
                import msvcrt
                if msvcrt.kbhit():
                    k = msvcrt.getch().decode("utf-8", errors="ignore").lower()
                    if k == key:
                        held += 0.12
                        sys.stdout.write(".")
                        sys.stdout.flush()
                        if held >= hold_seconds:
                            print()
                            play_success()
                            return True
            else:
                import select
                if select.select([sys.stdin], [], [], 0.05)[0]:
                    k = sys.stdin.read(1).lower()
                    if k == key:
                        held += 0.12
                        sys.stdout.write(".")
                        sys.stdout.flush()
                        if held >= hold_seconds:
                            print()
                            play_success()
                            return True
        except Exception:
            pass
        time.sleep(0.05)
    print()
    play_fail()
    return False


def qte_press_or_hold(prompt, key, timeout=2.5, hold_seconds=0.85):
    """Respect accessibility hold_qte setting."""
    if SETTINGS.get("hold_qte"):
        return hold_keypress(prompt, key, hold_seconds=hold_seconds, timeout=max(timeout, 3.5))
    return timed_keypress(prompt, key, timeout) == key.lower()


def qte_inverted(prompt, shown_key, real_key, timeout=3.2):
    print(f"\n  >>> {prompt}")
    print_big(f"SHOWN [{shown_key.upper()}]")
    print("  The shadows invert your mind. Press the OPPOSITE key.")
    pause(1.2)
    result = timed_keypress("  TRUE key: ", real_key, timeout)
    if result == real_key.lower():
        play_success()
        print("  [OK] You resisted the inversion.")
        return True
    play_fail()
    print("  [X] The shadows took your hands.")
    return False


def qte_echo(key, timeout=4.0):
    print_big(f"ECHO [{key.upper()}]")
    print("  Wait for the echo. Do not press yet.")
    time.sleep(1.5)
    print(f"  >>> Echo returns: [{key.upper()}]  <- PRESS NOW")
    result = timed_keypress("  ", key, timeout)
    if result == key.lower():
        play_success()
        print("  [OK] Echo matched.")
        return True
    play_fail()
    print("  [X] Too early or too late.")
    return False



def qte_debris_clear(key="f", bpm=52, windows=4):
    """Rhythmic force on a sealed door/lock — same style as heartbeat QTE."""
    print_big("FORCE")
    typewriter("Work the seam. Lever on the beat — not before, not after.")
    pause(0.8)
    play_samples(generate_heartbeat(bpm, 1))
    beat_interval = 60 / bpm
    window = qte_scale(beat_interval * 1.15)
    for i in range(windows):
        play_samples(generate_tone(40 + i * 8, 0.12, 0.5))
        print(f"  (lock cycles)  force {i + 1}/{windows}...")
        result = timed_keypress(f"  Press [{key.upper()}]: ", key, window)
        if result == key.lower():
            play_qte_tick()
            print("  [OK] The seam yields.")
            time.sleep(0.25)
        else:
            play_fail()
            print("  [FAIL] The pile settles heavier.")
            return False
        time.sleep(max(0.15, beat_interval - window * 0.3))
    play_success()
    return True



def qte_heartbeat(key="f", bpm=46, windows=4):
    """Press only when the window opens. Early presses fail the whole sequence."""
    flush_input_buffer()
    key = key.lower()
    windows = max(4, min(6, int(windows)))
    print_big(f"HEART [{key.upper()}]")
    print("  Wait for each open window — then press. Early presses fail.")
    pause(0.9)
    play_samples(generate_heartbeat(bpm, 1))
    beat_interval = 60.0 / bpm
    # open window length
    window = qte_scale(beat_interval * 0.95)
    # closed gap before each window where input = fail
    closed = max(0.25, beat_interval * 0.55)
    for i in range(windows):
        flush_input_buffer()
        # CLOSED phase — any key is failure
        print("  ...")
        play_samples(generate_tone(50, 0.1, 0.42))
        closed_end = time.time() + closed
        while time.time() < closed_end:
            flush_input_buffer()  # still discard
            # poll for early press
            early = False
            try:
                if os.name == "nt":
                    import msvcrt
                    if msvcrt.kbhit():
                        msvcrt.getch()
                        early = True
                else:
                    import select
                    if select.select([sys.stdin], [], [], 0)[0]:
                        sys.stdin.read(1)
                        early = True
            except Exception:
                pass
            if early:
                play_fail()
                print("  [X] Too early.")
                return False
            time.sleep(0.02)
        flush_input_buffer()
        print(f"  NOW — press [{key.upper()}]!")
        result = timed_keypress(f"  Press [{key.upper()}]: ", key, window)
        if result == key:
            play_qte_tick()
            print("  [OK] In rhythm.")
            time.sleep(0.2)
        else:
            play_fail()
            print("  [X] Missed the window.")
            return False
        time.sleep(0.12)
    play_success()
    print("  [OK] Heartbeat synchronized.")
    return True



def qte_whisper_filter(target_word="HELP", timeout=10.0):
    target_word = target_word.upper().strip()
    print_big(target_word)
    print(f"  Type ONLY the letters that spell: {target_word}")
    print("  Ignore the noise. Case does not matter. Press Enter when done.")
    pause(1.0)
    noise = list("XYZQJKMVPWR")
    stream = []
    for ch in target_word:
        stream.extend(random.sample(noise, k=2))
        stream.append(ch)
    stream.extend(random.sample(noise, k=3))
    random.shuffle(stream[0:3])
    print("  " + " ".join(stream))

    try:
        typed = input(f"\n  Type ({len(target_word)} letters): ").strip().upper()
    except Exception:
        play_fail()
        return False
    # Keep only letters
    typed = "".join(c for c in typed if c.isalpha())
    if typed == target_word:
        play_success()
        print("  [OK] The whispers part.")
        return True
    play_fail()
    print("  [X] Wrong letters.")
    return False



def timed_line_input(prompt, timeout):
    """Read a full line within timeout seconds. Returns the string, or None on timeout.
    Windows: character-by-character via msvcrt. POSIX: select + readline.
    """
    flush_input_buffer()
    print(prompt, end="", flush=True)
    deadline = time.time() + float(timeout)
    if os.name == "nt":
        try:
            import msvcrt
        except ImportError:
            # fallback — untimed
            try:
                return input().strip()
            except Exception:
                return None
        chars = []
        while time.time() < deadline:
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\r", "\n"):
                    print()
                    return "".join(chars)
                if ch in ("\x08", "\x7f"):  # backspace / delete
                    if chars:
                        chars.pop()
                        sys.stdout.write("\b \b")
                        sys.stdout.flush()
                    continue
                if ch == "\x03":
                    raise KeyboardInterrupt
                # ignore other control chars
                if ord(ch) < 32:
                    continue
                chars.append(ch)
                sys.stdout.write(ch)
                sys.stdout.flush()
            else:
                time.sleep(0.015)
        print()
        return None
    # POSIX: terminal is line-buffered — wait for a full line or timeout
    try:
        import select
    except ImportError:
        try:
            return input().strip()
        except Exception:
            return None
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            print()
            return None
        try:
            rlist, _, _ = select.select([sys.stdin], [], [], remaining)
        except Exception:
            print()
            return None
        if rlist:
            try:
                line = sys.stdin.readline()
            except Exception:
                return None
            if not line:
                return None
            return line.rstrip("\r\n")
        print()
        return None


def qte_type_phrase(target_phrase, allow_spaces=True, timeout=None):
    """Type-in QTE; submit with Enter.
    If timeout is set, the player must finish within that (scaled) window or fail.
    Untimed when timeout is None (rope / blade step chains).
    """
    target = target_phrase.upper().strip()
    target_cmp = target if allow_spaces else "".join(target.split())
    if allow_spaces:
        target_cmp = " ".join(target_cmp.split())
    print_big(target_phrase.upper())
    print(f"  Type exactly: {target_phrase}")
    print("  Press Enter when done.")
    flush_input_buffer()
    pause(0.35)
    if timeout is not None:
        limit = max(4.0, qte_scale(float(timeout)))
        print(f"  [Time limit: {limit:.1f}s]")
        try:
            typed = timed_line_input("  > ", limit)
        except Exception:
            play_fail()
            return False
        if typed is None:
            play_fail()
            print("  [FAIL] Too slow — time ran out.")
            return False
        typed = typed.strip()
    else:
        try:
            typed = input("  > ").strip()
        except Exception:
            play_fail()
            return False

    typed_cmp = typed.upper().strip()
    if not allow_spaces:
        typed_cmp = "".join(typed_cmp.split())
    else:
        typed_cmp = " ".join(typed_cmp.split())

    if typed_cmp == target_cmp:
        play_success()
        print("  [OK]")
        return True
    play_fail()
    print("  [FAIL] Wrong words.")
    return False


# ---------------------------------------------------------------------------
ROPE_TYPE_STEPS = [
    "I WILL HOLD THE ROPE",  # <-- EDIT ME (office / noose step 1)
    "I WILL STAND ON THE CHAIR",  # <-- EDIT ME
    "I WILL PUT THE ROPE AROUND MY NECK",  # <-- EDIT ME
    "I WILL LET GO",  # <-- EDIT ME (final step before ending screen)
]

BLADE_TYPE_STEPS = [
    "I WILL HOLD THE KNIFE",  # <-- EDIT ME (kitchen / knife step 1)
    "I WILL PRESS THE KNIFE AGAINST MYSELF",  # <-- EDIT ME
    "I WILL FEEL WARM OUTSIDE",  # <-- EDIT ME
    "I AM COLD INSIDE",  # <-- EDIT ME (final step before ending screen)
]


def run_type_step_chain(steps):
    """Run 4 untimed type-phrase QTEs in order. Wrong input retries the same step.
    2 second pause between successful steps (no action countdown)."""
    for i, phrase in enumerate(steps, 1):
        print()
        typewriter(f"Step {i} of {len(steps)}...")
        pause(0.4)
        flush_input_buffer()
        while True:
            if qte_type_phrase(phrase):
                break
            typewriter("Again.")
            flush_input_buffer()
            pause(0.3)
        if i < len(steps):
            pause(2.0)  # breathe between suicide type-steps
            flush_input_buffer()
    return True


def qte_hold_release(key=" ", hold_time=1.6, window=1.1):
    label = "SPACE" if key == " " else key.upper()
    print_big(f"HOLD {label}")
    print("  Hold until the bar fills, then RELEASE in the window.")
    pause(1.1)
    print("  HOLD: ", end="", flush=True)
    start = time.time()
    held = False
    key_l = key.lower()
    deadline = start + hold_time + window + 1.2
    while time.time() < deadline:
        try:
            if os.name == "nt":
                import msvcrt
                if msvcrt.kbhit():
                    c = msvcrt.getch().decode("utf-8", errors="ignore").lower()
                    if c == key_l or (key == " " and c == " "):
                        held = True
                        break
            else:
                import select
                if select.select([sys.stdin], [], [], 0.02)[0]:
                    c = sys.stdin.read(1).lower()
                    if c == key_l or (key == " " and c in (" ", "\n")):
                        held = True
                        break
                    continue
        except Exception:
            pass
        time.sleep(0.02)
    if not held:
        play_fail()
        print("\n  [X] Never held.")
        return False
    for _ in range(9):
        print("#", end="", flush=True)
        time.sleep(hold_time / 9)
    print("  <- RELEASE")
    release_start = time.time()
    released = False
    while time.time() - release_start < window:
        try:
            if os.name == "nt":
                import msvcrt
                if not msvcrt.kbhit():
                    released = True
                    break
            else:
                # On POSIX, treat absence of further input as release after brief settle
                import select
                if not select.select([sys.stdin], [], [], 0.05)[0]:
                    released = True
                    break
                else:
                    try:
                        sys.stdin.read(1)
                    except Exception:
                        pass
                    released = False
        except Exception:
            released = True
            break
        time.sleep(0.01)
    if released:
        play_success()
        print("  [OK] Perfect release.")
        return True
    play_fail()
    print("  [X] Bad timing.")
    return False


def qte_double_tap(key="f", max_interval=0.7):
    print_big(f"TAP [{key.upper()}] x2")
    print("  Exactly two presses. A third fails.")
    pause(1.0)
    first = timed_keypress("  First: ", key, 2.8)
    if first != key.lower():
        play_fail()
        print("  [X] Missed first.")
        return False
    second = timed_keypress("  Second: ", key, max_interval)
    if second != key.lower():
        play_fail()
        print("  [X] Missed second.")
        return False
    third = timed_keypress("  (stop)", key, 0.35)
    if third == key.lower():
        play_fail()
        print("  [X] Triple.")
        return False
    play_success()
    print("  [OK] Clean double-tap.")
    return True


def qte_growing_window(key="f", base=1.6):
    window = max(1.1, min(3.2, base + random.uniform(-0.25, 0.9)))
    print_big(f"WINDOW {window:.1f}S")
    print("  Time is warping. Press inside the shifting window.")
    pause(1.1)
    play_qte_tick()
    result = timed_keypress(f"  Press [{key.upper()}]: ", key, window)
    if result == key.lower():
        play_success()
        print("  [OK] Caught it.")
        return True
    play_fail()
    print("  [X] Closed.")
    return False


def qte_rhythm_chain():
    print_big("RHYTHM CHAIN")
    print("  1) Double-tap  2) Heartbeat x2  3) Final press")
    pause(1.5)
    if not qte_double_tap("f", 0.75):
        return False
    time.sleep(0.5)
    if not qte_heartbeat("f", bpm=48, windows=4):
        return False
    time.sleep(0.4)
    print_big("FINAL [F]")
    result = timed_keypress("  Press [F]: ", "f", 2.4)
    if result == "f":
        play_success()
        print("  [OK] Chain complete.")
        return True
    play_fail()
    print("  [X] Broken.")
    return False


# ============================================================
# Utility
# ============================================================

def clear():
    os.system("cls" if os.name == "nt" else "clear")


def pause(sec=1.8):
    time.sleep(sec)


def typewriter(text, delay=None, click=True):
    """Print with optional clicks. Speed scales with sanity; low sanity inserts red intrusive lines."""
    if SETTINGS.get("skip_typewriter"):
        print(text)
        time.sleep(0.12)
        return
    st = _ACTIVE_STATE
    sanity = 100
    if st is not None:
        sanity = max(0, min(100, int(getattr(st, "sanity", 100))))
    # Lower sanity → faster typing (floor so it stays readable)
    if delay is None:
        # 100 san → 0.036; 0 san → ~0.012
        delay = 0.012 + 0.024 * (sanity / 100.0)
    # Intrusive mistake (only substantial lines, below 50 sanity)
    if (
        st is not None
        and sanity < 50
        and len(text.strip()) > 28
        and random.random() < 0.22
        and not SETTINGS.get("reduce_motion")
    ):
        intrusions = [
            "go to the office  the rope is waiting",
            "the kitchen knife knows your name",
            "the roof  jump  end it",
            "climb the stairs  the noose remembers",
            "one cut in the kitchen and the night stops",
            "open the hatch  the moon will catch you",
            "director's chair  then the quiet rope",
            "jump from the roof before she finds you",
        ]
        mistake = random.choice(intrusions)
        # frantic red type
        for c in mistake:
            sys.stdout.write(colorize(c, RED + BOLD))
            sys.stdout.flush()
            if click and c not in " \n\t" and random.random() < 0.35:
                play_type_click()
            time.sleep(max(0.004, delay * 0.35))
        time.sleep(0.12)
        # delete the mistake
        for _ in range(len(mistake)):
            sys.stdout.write("\b \b")
            sys.stdout.flush()
            time.sleep(0.008)
        time.sleep(0.08)
    for c in text:
        sys.stdout.write(c)
        sys.stdout.flush()
        if click and c not in " \n\t":
            play_type_click()
        time.sleep(delay)
    print()
    time.sleep(0.12)



def type_fast(text):
    """Fast type for repeated hallway flavor."""
    typewriter(text, delay=0.008, click=False)


def show(art):
    print(art)
    time.sleep(1.4)


def get_choice(prompt, options, state=None):
    """Show options; after pick, replace list with only the chosen line.
    At low sanity may inject a false option that vanishes when chosen."""
    opts = list(options)
    false_idx = None
    if state is not None and getattr(state, "sanity", 100) < 40 and random.random() < 0.35:
        fakes = [
            "Open the door that isn't there",
            "Follow the voice under the floor",
            "Step through the mirror",
            "Answer the phone that never rings",
        ]
        false_idx = random.randint(0, len(opts))
        opts.insert(false_idx, random.choice(fakes))
    print()
    for i, opt in enumerate(opts, 1):
        print(f"  [{i}] {opt}")
    time.sleep(0.55)
    if SETTINGS.get("debug_mode"):
        print("  [9] Debug tools")
    time.sleep(0.55)
    while True:
        try:
            raw = input(f"\n{prompt} ").strip()
            if SETTINGS.get("debug_mode") and raw.lower() in ("9", "debug"):
                debug_menu(state if state is not None else State())
                # If debug returns without teleport, re-show choices
                print()
                for i, opt in enumerate(opts, 1):
                    print(f"  [{i}] {opt}")
                if SETTINGS.get("debug_mode"):
                    print("  [9] Debug tools")
                continue
            idx = int(raw) - 1
            if 0 <= idx < len(opts):
                chosen = opts[idx]
                print("\n" + "-" * 42)
                print(f"  > {chosen}")
                print("-" * 42 + "\n")
                time.sleep(0.3)
                if false_idx is not None and idx == false_idx:
                    typewriter("That choice collapses. It was never real.")
                    pause(0.7)
                    state.change_sanity(-4)
                    state.add_note("Sanity offered a false path that vanished.")
                    return get_choice(prompt, options, state)
                # Map false-option index back to original option list
                result = idx - 1 if (false_idx is not None and idx > false_idx) else idx
                autosave(state if state is not None else _ACTIVE_STATE)
                return result
            print("  Invalid number.")
        except ValueError:
            print("  Enter a number.")


# ============================================================
# Large fullscreen jumpscares
# ============================================================

def jumpscare_face(context="body"):
    screen_flicker(3)
    clear()
    if context == "body":
        play_samples(generate_stinger_body(1.0), wait=True)
    else:
        play_samples(generate_stinger_classic(1.0), wait=True)
    face = r"""
+==================================================================+
|                                                                  |
|              ########################################            |
|            ##                                ##                  |
|          ##      ****            ****          ##                |
|         ##        **              **            ##               |
|        ##                                        ##              |
|       ##           ################               ##             |
|      ##          ####################              ##            |
|     ##          ########################              ##           |
|    ##            ##################                ##          |
|   ##              ################                    ##         |
|  ##                  ########                          ##        |
| ##                    ######                            ##       |
|##                      ####                              ##      |
|#                        ##                                #      |
|################################################################  |
|                                                                  |
|                    I     S E E     Y O U                         |
|                                                                  |
+==================================================================+
"""
    print(face)
    time.sleep(1.6)
    clear()
    time.sleep(0.7)


def jumpscare_hand(context="generic"):
    screen_flicker(3)
    clear()
    if context == "nurse":
        play_samples(generate_stinger_nurse(0.93), wait=True)
    else:
        play_samples(generate_stinger_metal_scrape(0.88), wait=True)
    art = r"""
+==================================================================+
|                                                                  |
|              +================================+                  |
|              |                                |                  |
|              |      +==================+      |                  |
|              |      |  ################  |      |                  |
|              |      | ################## |      |                  |
|              |      |####################|      |                  |
|              |      |####  NAILS  #######|      |                  |
|              |      +==================+      |                  |
|              |         |  |  |  |  |          |                  |
|              |         |  |  |  |  |          |                  |
|              |         |  |  |  |  |          |                  |
|              |      ---+--+--+--+--+---       |                  |
|              |                                |                  |
|              +================================+                  |
|                                                                  |
|                     I T   T O U C H E D   Y O U                  |
|                                                                  |
+==================================================================+
"""
    print(art)
    time.sleep(1.5)
    clear()
    time.sleep(0.6)


def jumpscare_nurse():
    screen_flicker(4)
    clear()
    play_samples(generate_stinger_nurse(0.95), wait=True)
    art = r"""
+==================================================================+
|                                                                  |
|                     ####################                         |
|                   ##                  ##                         |
|                 ##    oooo    oooo      ##                       |
|                ##      oo      oo        ##                      |
|               ##                          ##                     |
|              ##         \______/           ##                    |
|             ##           \____/             ##                   |
|            ##            ------              ##                  |
|           ##                                  ##                 |
|          ########################################                |
|                        ||||||                                    |
|                   ################                               |
|                                                                  |
|              S H E   I S   R I G H T   B E H I N D   Y O U       |
|                                                                  |
+==================================================================+
"""
    print(art)
    time.sleep(1.7)
    clear()
    time.sleep(0.6)


def jumpscare_fake_crash():
    screen_flicker(2)
    clear()
    play_samples(generate_stinger_whisper_scream(0.9), wait=True)
    print(r"""
+==================================================================+
|  FATAL ERROR                                                     |
|  --------------------------------------------------------------- |
|  process asylum.exe lost                                         |
|  Memory address 0x00000047 corrupted                             |
|                                                                  |
|  Stack trace:                                                    |
|    0x00471A2B  unknown                                           |
|    0x00471A2B  unknown                                           |
|    0x00471A2B  unknown                                           |
|    0x00471A2B  YOU                                               |
|    0x00471A2B  YOU                                               |
|    0x00471A2B  YOU                                               |
|                                                                  |
|  Press any key to terminate...                                   |
+==================================================================+
""")
    time.sleep(2.0)
    clear()
    time.sleep(0.6)


def jumpscare_whisper_flood():
    screen_flicker(2)
    clear()
    play_samples(generate_stinger_child_laugh(0.85), wait=True)
    for _ in range(20):
        print("HELP HELP HELP HELP HELP HELP HELP HELP HELP HELP HELP HELP")
    time.sleep(1.1)
    clear()
    time.sleep(0.5)


def jumpscare_shadow():
    screen_flicker(3)
    clear()
    play_samples(generate_stinger_heartstop(0.95), wait=True)
    art = r"""
+==================================================================+
|                                                                  |
|                         ########                                 |
|                       ##        ##                               |
|                     ##    ##      ##                             |
|                   ##    ######      ##                           |
|                 ##    ##########      ##                         |
|               ##    ##############      ##                       |
|              ##                          ##                      |
|             ##    ****        ****        ##                     |
|            ##      **          **          ##                    |
|           ####################################                   |
|                                                                  |
|                 I T   I S   A L R E A D Y   H E R E              |
|                                                                  |
+==================================================================+
"""
    print(art)
    time.sleep(1.6)
    clear()
    time.sleep(0.6)


# ============================================================
# State + Patient Folder
# ============================================================



# Trait counters updated by decisions; fed into personality algorithm
TRAIT_KEYS = (
    "mercy", "cruelty", "honesty", "deceit", "courage", "cowardice",
    "curiosity", "faith", "violence", "selflessness", "selfishness",
    "obedience", "defiance", "compassion", "detachment", "resolve",
)

# At least 32 personality descriptors for the Patient 47 profile algorithm
PERSONALITY_DESCRIPTORS = [
    ("The Merciful", lambda t: t.get("mercy", 0) >= 3 and t.get("cruelty", 0) == 0),
    ("The Cruel Hand", lambda t: t.get("cruelty", 0) >= 3),
    ("Promise-Keeper", lambda t: t.get("honesty", 0) >= 2 and t.get("deceit", 0) == 0),
    ("Oath-Breaker", lambda t: t.get("deceit", 0) >= 2 or t.get("broken_promises", 0) >= 1),
    ("The Curious", lambda t: t.get("curiosity", 0) >= 4),
    ("Tunnel-Visioned", lambda t: t.get("curiosity", 0) <= 1 and t.get("resolve", 0) >= 2),
    ("The Devout", lambda t: t.get("faith", 0) >= 2),
    ("Godless in the Ward", lambda t: t.get("faith", 0) == 0 and t.get("defiance", 0) >= 2),
    ("Violent Solution", lambda t: t.get("violence", 0) >= 3),
    ("Pacifist Under Glass", lambda t: t.get("violence", 0) == 0 and t.get("mercy", 0) >= 2),
    ("Self-Sacrificing", lambda t: t.get("selflessness", 0) >= 3),
    ("Self-Preserving", lambda t: t.get("selfishness", 0) >= 3),
    ("Institutional", lambda t: t.get("obedience", 0) >= 2),
    ("Insubordinate", lambda t: t.get("defiance", 0) >= 3),
    ("Soft Voice", lambda t: t.get("compassion", 0) >= 3),
    ("Clinical Detachment", lambda t: t.get("detachment", 0) >= 2),
    ("Iron Resolve", lambda t: t.get("resolve", 0) >= 3),
    ("Easily Unraveled", lambda t: t.get("cowardice", 0) >= 2),
    ("Brave to a Fault", lambda t: t.get("courage", 0) >= 3),
    ("The Listener", lambda t: t.get("curiosity", 0) >= 2 and t.get("compassion", 0) >= 2),
    ("The Silencer", lambda t: t.get("cruelty", 0) >= 1 and t.get("detachment", 0) >= 1),
    ("Caretaker Impulse", lambda t: t.get("compassion", 0) >= 2 and t.get("selflessness", 0) >= 1),
    ("Abandonment Pattern", lambda t: t.get("selfishness", 0) >= 2 and t.get("broken_promises", 0) >= 1),
    ("Truth-Seeker", lambda t: t.get("curiosity", 0) >= 3 and t.get("honesty", 0) >= 1),
    ("Memory-Burner", lambda t: t.get("detachment", 0) >= 1 and t.get("defiance", 0) >= 1),
    ("Shadow-Adjacent", lambda t: t.get("curiosity", 0) >= 2 and t.get("defiance", 0) >= 2),
    ("Staff-Mimic", lambda t: t.get("obedience", 0) >= 1 and t.get("selfishness", 0) >= 1),
    ("Child's Advocate", lambda t: t.get("compassion", 0) >= 2 and t.get("honesty", 0) >= 1),
    ("Gate-Rusher", lambda t: t.get("courage", 0) >= 2 and t.get("resolve", 0) >= 2),
    ("Hallway Drifter", lambda t: t.get("cowardice", 0) >= 1 and t.get("curiosity", 0) >= 2),
    ("Calculated Mercy", lambda t: t.get("mercy", 0) >= 2 and t.get("detachment", 0) >= 1),
    ("Reckless Savior", lambda t: t.get("selflessness", 0) >= 2 and t.get("courage", 0) >= 2),
    ("Quiet Collaborator", lambda t: t.get("obedience", 0) >= 2 and t.get("deceit", 0) >= 1),
    ("Honest Monster", lambda t: t.get("honesty", 0) >= 2 and t.get("violence", 0) >= 2),
    ("Reluctant Saint", lambda t: t.get("mercy", 0) >= 2 and t.get("cowardice", 0) >= 1),
    ("Unresolved", lambda t: sum(t.values()) < 3),
    ("The Hollow", lambda t: t.get("self_end", 0) >= 1),
]



def add_trait(state, trait, amount=1):
    if not hasattr(state, "traits") or state.traits is None:
        state.traits = {k: 0 for k in TRAIT_KEYS}
        state.traits["broken_promises"] = 0
    if trait not in state.traits:
        state.traits[trait] = 0
    state.traits[trait] = state.traits.get(trait, 0) + amount


def compute_personality(state):
    """Algorithm: score descriptors from trait vector; return exactly one label."""
    traits = getattr(state, "traits", None) or {k: 0 for k in TRAIT_KEYS}
    # Oath-breaker overrides when promises were broken
    if traits.get("broken_promises", 0) >= 1:
        return "Oath-Breaker"
    matched = []
    for name, pred in PERSONALITY_DESCRIPTORS:
        if name == "Unresolved":
            continue
        try:
            if pred(traits):
                matched.append(name)
        except Exception:
            pass
    if not matched:
        return "Unresolved"
    # Prefer the last (often more specific compound) match; fall back to first
    # Score by total trait mass involved roughly via order in list — pick strongest:
    # use first matched for broad, but prefer longer compound names when tied
    matched.sort(key=lambda n: (-len(n), n))
    return matched[0]



def restore_trust(state, which="child"):
    """Fulfilling a promise after a prior-run betrayal softens their stance."""
    if which != "child":
        return
    if not getattr(state, "prior_child_betrayed", False):
        return
    if getattr(state, "trust_restored_child", False):
        return
    state.trust_restored_child = True
    state.prior_child_betrayed = False  # this run: she can trust again
    state.child_favor = getattr(state, "child_favor", 0) + 3
    state.promises_broken = max(0, getattr(state, "promises_broken", 0) - 1)
    typewriter('Something in her face loosens. "You broke it before. You kept it this time."')
    pause(0.5)
    typewriter("The frost on the memory of the gates thins.")
    state.add_note("Restored the child's trust after a prior betrayal.")
    add_trait(state, "honesty", 2)
    add_trait(state, "compassion", 1)
    # soften permanent scar for future runs if we save a mark
    try:
        data = load_folder() or {}
        scars = set(data.get("permanent_scars", []) or [])
        if "betrayed_child" in scars:
            scars.discard("betrayed_child")
            scars.add("trust_mended")
            data["permanent_scars"] = list(scars)
            with open(FOLDER_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
    except Exception:
        pass


def break_promise(state, which="child"):
    """Punish breaking a promise — traits, sanity, favor, gate lock."""
    add_trait(state, "deceit", 2)
    add_trait(state, "broken_promises", 1)
    add_trait(state, "selfishness", 1)
    state.promises_broken = getattr(state, "promises_broken", 0) + 1
    if which in ("child", "thread", "courtyard"):
        state.child_favor = max(-5, getattr(state, "child_favor", 0) - 4)
    state.shadow_awareness = getattr(state, "shadow_awareness", 0) + 1
    state.change_sanity(-12)
    state.checkpoint("broke_promise", f"Broke promise ({which})")
    state.add_note(f"Broke a promise ({which}). The building noticed.")
    mark_world(state, f"broke_{which}")
    typewriter("Something in the walls shifts — a promise unkept has weight here.")


def keep_promise(state, which="child"):
    """Mark a promise kept — traits, ledger, visible residual."""
    state.promises_kept = getattr(state, "promises_kept", 0) + 1
    add_trait(state, "honesty", 1)
    add_trait(state, "resolve", 1)
    mark_world(state, f"kept_{which}")
    state.checkpoint("kept_promise", f"Kept promise ({which})")
    state.add_note(f"Kept a promise ({which}).")
    print(colorize("  [The building felt a promise settle.]", DIM))


def promise_debt(state):
    """How many open promises still weigh on the gates."""
    debt = int(getattr(state, "promises_broken", 0) or 0)
    if getattr(state, "promised_child", False) and not getattr(state, "freed_ghost_child", False):
        if getattr(state, "child_fate", None) not in ("freed", "hidden", "courtyard"):
            debt += 1
    if getattr(state, "promised_thread_to_child", False) and not getattr(state, "fulfilled_thread_to_child", False):
        debt += 1
    if getattr(state, "promised_pills_to_shadow", False) and not getattr(state, "fulfilled_pills_to_shadow", False):
        debt += 1
    return debt


def set_truth_path(state, path, quiet=False):
    """Record a major identity/reading of the night (feeds Director's Cut)."""
    if not path:
        return
    prev = getattr(state, "truth_path", None)
    # Allow first path or upgrade to more specific
    if prev is None or prev == path:
        state.truth_path = path
    else:
        # Keep list of all paths this run
        multi = getattr(state, "truth_paths_this_run", None) or []
        if prev not in multi:
            multi.append(prev)
        if path not in multi:
            multi.append(path)
        state.truth_paths_this_run = multi
        state.truth_path = path
    mark_world(state, f"truth_{path}")
    if not quiet:
        state.add_note(f"Truth path: {path}.")


def mark_world(state, tag):
    """Stamp a verb into the run so later rooms can react."""
    marks = getattr(state, "world_marks", None)
    if marks is None:
        state.world_marks = set()
        marks = state.world_marks
    if not isinstance(marks, set):
        marks = set(marks)
        state.world_marks = marks
    marks.add(tag)
    # Gate heat rises with decisive acts
    heat = getattr(state, "gate_heat", 0)
    hot_tags = (
        "truth_orderly", "truth_patient", "truth_merge", "truth_burn", "truth_child_rest",
        "killed_nurse", "banished_nurse", "kept_child", "kept_thread", "kept_pills",
        "freed_child", "sabotaged_boiler", "opened_drawer47", "wore_uniform", "side_mercy",
    )
    if tag in hot_tags or tag.startswith("kept_") or tag.startswith("truth_"):
        state.gate_heat = heat + 1
    autosave(state)


def residual_for_room(state, room_id):
    """One short line so a prior verb is still visible in this room. None if nothing fits."""
    m = getattr(state, "world_marks", None) or set()
    if not isinstance(m, set):
        m = set(m)
    killed = getattr(state, "killed_nurse", False) or "killed_nurse" in m or "banished_nurse" in m
    lines = []
    if room_id in ("nurse_station", "east_corridor", "gallery") and killed:
        lines.append("No white shoes. Charts sit where she left them.")
    if room_id == "nurse_station" and "wore_uniform" in m:
        lines.append("The uniform on your shoulders changes how the desk looks at you.")
    if room_id == "cell" and "called_out" in m:
        lines.append("The vent still remembers your voice.")
    if room_id == "cell" and getattr(state, "knows_true_identity", False):
        lines.append("The mattress feels like a prop now — something staff would issue.")
    if room_id == "side_cell" and "side_mercy" in m:
        lines.append("The carvings seem quieter since you left them alone.")
    if room_id == "side_cell" and "side_cruel" in m:
        lines.append("Fresh dust where numbers used to be. The wall has not forgiven you.")
    if room_id == "office" and "burned_file" in m:
        lines.append("Ash still ghosts the desk edge.")
    if room_id == "office" and "read_file" in m:
        lines.append("Patient 47's folder lies open like an accusation.")
    if room_id == "basement" and "sabotaged_boiler" in m:
        lines.append("The boiler is a cold carcass. Pipes tick without heat.")
    if room_id == "basement" and ("kept_pills" in m or getattr(state, "shadow_overdosed", False)):
        lines.append("The cage is quieter than the rest of the dark.")
    if room_id == "basement" and getattr(state, "shadow_overdosed", False):
        lines.append("Whatever answered with your voice does not answer now.")
    if room_id == "morgue" and "opened_drawer47" in m:
        lines.append("Drawer 47's label is worn where your thumb worried it.")
    if room_id == "chapel" and ("kept_child" in m or "kept_thread" in m or "freed_child" in m):
        lines.append("The altar air is thinner — a weight moved on.")
    if room_id == "chapel" and "broke_child" in m:
        lines.append("A cold draft from the altar steps. Disappointment has a temperature.")
    if room_id == "stairwell" and getattr(state, "upper_unlocked", False):
        lines.append("Upper air bleeds down the stairwell like a second climate.")
    if room_id == "gallery" and "gallery_watched" in m and not killed:
        lines.append("You already know her route from above. The glass remembers your eyes.")
    if room_id == "roof" and getattr(state, "shadow_overdosed", False):
        lines.append("Against the moon: nothing matching your stance. The outline is gone.")
    if room_id == "roof" and getattr(state, "shadow_active", False) and not getattr(state, "shadow_overdosed", False):
        lines.append("The outline on the moon shares a posture with the thing in the basement cage.")
    if room_id == "laundry" and "wore_uniform" in m:
        lines.append("An empty hanger swings where the orderly whites used to be.")
    if promise_debt(state) >= 1 and room_id in ("east_corridor", "stairwell", "chapel"):
        lines.append("Something unfinished pulls at the hinges of the night.")
    if not lines:
        return None
    return random.choice(lines)


def show_residual(state, room_id):
    line = residual_for_room(state, room_id)
    if line:
        typewriter(line)
        pause(0.45)


def gate_temperature_line(state):
    """Vague progress fantasy at the gates — no checklist."""
    heat = int(getattr(state, "gate_heat", 0) or 0)
    debt = promise_debt(state)
    echoes = int(getattr(state, "collected_all_echoes", 0) or 0)
    if debt >= 2:
        return random.choice([
            "The frost on the bars is thicker than last time.",
            "Unfinished words make the iron heavier.",
            "Someone is still waiting. The gates can hear it.",
        ])
    if heat >= 6 and echoes >= 5:
        return random.choice([
            "The frost is thinner. The lock has begun to hesitate.",
            "The bars know more of your name than before.",
            "Night air threads through the seam — not open, not closed.",
        ])
    if heat >= 3 or echoes >= 4:
        return random.choice([
            "A hairline of cold air finds your wrist.",
            "The hinges remember a different weight of hand.",
            "Something you did elsewhere has followed you here.",
        ])
    if getattr(state, "gate_attempts", 0) >= 1:
        return random.choice([
            "The lock listens. It is not convinced.",
            "Whatever you are missing does not have a shape yet.",
        ])
    return None


def nurse_world_line(state):
    """Post-Nurse consequence line for halls/station."""
    if not getattr(state, "killed_nurse", False):
        return None
    return random.choice([
        "Without her rounds, the corridors lose their beat.",
        "The smile is gone from the building. What replaces it is not kinder.",
        "Charts curl at the edges. No one is updating the night.",
    ])


ACHIEVEMENT_SEALS = {
    "_default": "  [####]",
    "true_escape": "  {**}",
    "sacrifice": "  (+)",
    "mercy": "  (~)",
    "blank": "  [  ]",
    "merge": "  <||>",
    "loop": "  (oo)",
    "child_promise": "  <3",
    "free_child": "  *o*",
    "banish_nurse": "  XN",
    "all_echoes": "  E6",
    "side_mercy": "  +s",
    "shadow_confess": "  /S\\",
    "crypt": "  [C]",
    "passage": "  >>",
    "chase_survive": "  !!",
    "hide_child": "  ..",
    "courtyard_child": "  ^o",
    "director_cut": "  DC",
    "hard_escape": "  H!",
    "story_escape": "  S~",
    "standard_escape": "  ==",
    "ng_plus": "  +1",
    "chase_door": "  [|]",
    "hold_master": "  __",
}


class State:
    def __init__(self):
        self.sanity = 100
        self.health = 100
        self.time_of_night = 0
        self.scenes_visited = 0
        self.jumpscares_seen = 0
        self.last_jumpscare_scene = -99
        self.has_cell_key = False
        self.has_master_key = False
        self.has_flashlight = False
        self.has_pills = False
        self.has_syringe = False
        self.has_photo = False
        self.has_locket = False
        self.has_note = False
        self.has_uniform = False
        self.has_bolt_cutters = False
        self.has_crowbar = False
        self.betrayed_child = False
        self.trust_restored_child = False
        self.asked_cut_thread = False
        self.cut_black_thread = False
        self.upper_unlocked = False
        self.upper_visited = False
        self.has_roof_key = False
        self.has_upper_suite_key = False
        self.therapy_tape_played = False
        self.gallery_log_read = False
        self.read_director_letter = False
        self.upper_debris_cleared = False
        self.has_holy_water = False
        self.has_tape = False
        self.read_patient_file = False
        self.knows_true_identity = False
        self.saw_own_body = False
        self.freed_ghost_child = False
        self.killed_nurse = False
        self.nurse_death_room = None  # room id where her body remains (physical kill only)
        self.nurse_banished = False   # vanished — no body anywhere
        self.helped_nurse = False
        self.listened_to_whispers = False
        self.entered_chapel = False
        self.destroyed_boiler = False
        self.took_medication = False
        self.saw_future_self = False
        self.trusted_the_voice = False
        self.collected_all_echoes = 0
        self.nurse_hostility = 0
        self.shadow_awareness = 0
        self.child_favor = 0
        self.refused_to_search_cell = False
        self.called_out_early = False
        self.spared_the_body = False
        self.left_child_alone = False
        self.burned_the_file = False
        self.took_the_uniform = False
        self.sabotaged_power = False
        self.listened_to_tape = False
        self.opened_courtyard = False
        self.mercy_on_nurse = False
        self.explored_laundry = False
        self.explored_kitchen = False
        self.explored_archives = False
        self.explored_patient_wing = False
        self.promised_child = False
        self.lied_to_child = False
        self.accepted_merge_temptation = False
        self.hallway_visits = 0
        self.notes = []
        self.ending_reached = None
        # --- systems added in Deep Cut ---
        self.examined = set()
        self.current_room = "cell"
        self.hidden_cell_passage = False
        self.hidden_chapel_crypt = False
        self.side_cell_mercy = False
        self.side_cell_cruel = False
        self.nurse_location = "nurse_station"
        self.nurse_pacified = False
        self.truth_path = None
        self.moral_checkpoints = []
        self.flashbacks_seen = []
        self.achievements = []
        self.confessed_to_shadow = False
        self.prayed_in_chapel = False
        self.destroyed_photo = False
        self.has_cross = False
        self.has_wall_fragment = False
        self.child_fate = None  # freed | hidden | courtyard | abandoned | lied
        self.shadow_location = "basement"
        self.shadow_active = False
        self.chases_survived = 0
        self.dialogue_flags = []
        self.ng_plus = False
        self.prior_truths = []
        self.prior_achievements = []
        self.prior_notes = []
        self.prior_ending = None
        self.prior_profile = []
        self.prior_mercy = False
        self.prior_child_saved = False
        self.prior_burned = False
        self.prior_killed_nurse = False
        self.traits = {k: 0 for k in TRAIT_KEYS}
        self.traits["broken_promises"] = 0
        self.promises_broken = 0
        self.promises_kept = 0
        self.gate_attempts = 0
        self.gate_heat = 0
        self.world_marks = set()
        self.truth_paths_this_run = []
        self.prayed_in_chapel = getattr(self, "prayed_in_chapel", False)
        # Promise ledger
        self.promised_pills_to_shadow = False
        self.fulfilled_pills_to_shadow = False
        self.shadow_overdosed = False
        self.promised_thread_to_child = False
        self.fulfilled_thread_to_child = False

    def add_note(self, text):
        if text not in self.notes:
            self.notes.append(text)

    def add_achievement(self, aid, label):
        if aid not in self.achievements:
            self.achievements.append(aid)
            seal = ACHIEVEMENT_SEALS.get(aid, ACHIEVEMENT_SEALS.get("_default"))
            print()
            print(colorize(seal, BOLD))
            print(colorize(f"  * {label}", BOLD))
            print()
            time.sleep(0.7)

    def checkpoint(self, cid, label):
        if cid not in self.moral_checkpoints:
            self.moral_checkpoints.append(cid)
            self.add_note(f"Checkpoint: {label}")

    def night_flavor(self, early, mid, late):
        if self.time_of_night >= 5:
            return late
        if self.time_of_night >= 3:
            return mid
        return early

    def can_jumpscare(self):
        return (self.scenes_visited - self.last_jumpscare_scene) >= 4 and self.jumpscares_seen < 6

    def register_jumpscare(self):
        self.jumpscares_seen += 1
        self.last_jumpscare_scene = self.scenes_visited

    def change_sanity(self, amount):
        self.sanity = max(0, min(100, self.sanity + amount))
        if amount < 0:
            print(f"  [Sanity {self.sanity}/100]")
            time.sleep(0.45)
        return self.sanity <= 0

    def change_health(self, amount):
        self.health = max(0, min(100, self.health + amount))
        if amount < 0:
            print(f"  [Health {self.health}/100]")
            time.sleep(0.45)
        return self.health <= 0

    def advance_time(self, amount=1):
        self.time_of_night += amount
        self.scenes_visited += 1


def build_folder_dossier(state):
    identity = "Unknown subject – identity unresolved."
    if state.burned_the_file:
        identity = "Subject burned their own file. Memory intentionally erased."
    elif state.knows_true_identity and state.saw_own_body:
        identity = "Night orderly who vanished in 1973. Subject is both patient and staff."
    elif state.knows_true_identity:
        identity = "Subject answers to the name of the vanished night orderly."
    elif state.read_patient_file:
        identity = "Patient 47 – sedation recommended. Claims staff died in 1973."

    # Action log (facts)
    facts = []
    if state.promised_child:
        facts.append("Made a promise to the child in the chapel.")
    if state.lied_to_child:
        facts.append("Lied to the child about help arriving.")
    if getattr(state, "promises_broken", 0) >= 1:
        facts.append("Broke one or more promises. The record is marked.")
    if state.freed_ghost_child:
        facts.append("Helped free the girl in the nightgown.")
    if getattr(state, "child_fate", None) == "hidden":
        facts.append("Hid the child rather than freeing her openly.")
    if getattr(state, "child_fate", None) == "courtyard":
        facts.append("Planned a courtyard escape with the child.")
    if getattr(state, "left_child_alone", False):
        facts.append("Left the chapel child without aiding her.")
    if state.mercy_on_nurse:
        facts.append("Showed mercy to the Night Nurse.")
    if state.killed_nurse:
        facts.append("Confronted and banished / destroyed the Nurse.")
    if state.took_the_uniform:
        facts.append("Wore staff uniform; partially accepted institutional role.")
    if state.accepted_merge_temptation:
        facts.append("Considered merging with the shadow self.")
    if state.trusted_the_voice:
        facts.append("Trusted the voice from the cage.")
    if state.sabotaged_power:
        facts.append("Sabotaged the basement systems.")
    if state.called_out_early:
        facts.append("Called out from the cell – may have drawn attention.")
    if state.listened_to_whispers:
        facts.append("Listened too long at the cell door.")
    if state.listened_to_tape:
        facts.append("Played the cassette of their own past voice.")
    if state.saw_own_body:
        facts.append("Opened drawer 47 and saw their older self.")
    if getattr(state, "prayed_in_chapel", False):
        facts.append("Prayed at the warped altar.")
    if getattr(state, "confessed_to_shadow", False):
        facts.append("Confessed aloud to the shadow in the cage.")
    if getattr(state, "betrayed_child", False):
        facts.append("Betrayed the child after giving her a promise.")
    if state.ending_reached in ("The Rope", "The Blade", "The Fall"):
        facts.append("Chose self-destruction inside the asylum.")
    if getattr(state, "upper_visited", False):
        facts.append("Forced a way into the upper offices.")
    if getattr(state, "read_director_letter", False):
        facts.append("Read the Director's letter on the orderly's commitment.")
    if not facts:
        facts.append("Few decisive actions recorded this attempt.")

    personality = compute_personality(state)
    profile = list(facts)

    return {
        "identity": identity,
        "ending": state.ending_reached or "Incomplete",
        "final_sanity": state.sanity,
        "final_health": state.health,
        "echoes": state.collected_all_echoes,
        "profile": profile,
        "personality": personality,
        "notes": list(state.notes),
        "items": [],
        "promises_broken": getattr(state, "promises_broken", 0),
        "menu_scars": collect_menu_scars(state),
    }


def collect_menu_scars(state):
    """Significant actions that alter the main menu appearance on future boots."""
    scars = []
    if getattr(state, "prayed_in_chapel", False):
        scars.append("prayed")
    if state.killed_nurse:
        scars.append("nurse_dead")
    if state.freed_ghost_child or getattr(state, "child_fate", None) in ("freed", "hidden", "courtyard"):
        scars.append("child_touched")
    if state.accepted_merge_temptation or state.ending_reached == "Integration":
        scars.append("merged")
    if state.ending_reached == "True Escape":
        scars.append("escaped")
    if state.ending_reached == "Loop":
        scars.append("looped")
    if state.burned_the_file:
        scars.append("burned_file")
    if getattr(state, "promises_broken", 0) >= 1 or state.lied_to_child:
        scars.append("oath_broken")
    if state.saw_own_body:
        scars.append("drawer47")
    if state.took_the_uniform:
        scars.append("uniform")
    if state.ending_reached == "Quiet Sacrifice":
        scars.append("sacrifice")
    if state.ending_reached == "The New Nurse":
        scars.append("became_nurse")
    if getattr(state, "betrayed_child", False):
        scars.append("betrayed_child")
    if state.ending_reached in ("The Rope", "The Blade", "The Fall"):
        scars.append("self_end")
    return scars



def save_folder(state):
    record = build_folder_dossier(state)
    record["truth_path"] = getattr(state, "truth_path", None)
    record["achievements"] = list(getattr(state, "achievements", []))
    record["checkpoints"] = list(getattr(state, "moral_checkpoints", []))
    record["shadow_awareness"] = getattr(state, "shadow_awareness", 0)
    record["child_favor"] = getattr(state, "child_favor", 0)
    # Run finished — clear in-progress autosave so Continue doesn't restore a dead state
    if getattr(state, "ending_reached", None):
        clear_autosave()
    data = load_folder() or {"runs": [], "permanent_achievements": [], "director_unlocked": False}
    if "runs" not in data:
        data = {"runs": [data] if isinstance(data, dict) else [], "permanent_achievements": [], "director_unlocked": False}
    data["runs"].append(record)
    data["runs"] = data["runs"][-12:]
    perm = set(data.get("permanent_achievements", []))
    for a in record.get("achievements", []):
        perm.add(a)
    data["permanent_achievements"] = sorted(perm)
    if state.ending_reached == "True Escape":
        data["director_unlocked"] = True
        SETTINGS["director_unlocked"] = True
        save_settings()
    # accumulate truth paths across runs for Director's Cut
    truths = set(data.get("truths_seen", []))
    tp = record.get("truth_path") or getattr(state, "truth_path", None)
    if tp:
        truths.add(tp)
    for extra in getattr(state, "truth_paths_this_run", None) or []:
        if extra:
            truths.add(extra)
    data["truths_seen"] = sorted(truths)
    # promise ledger for NG+
    data["last_promises_broken"] = getattr(state, "promises_broken", 0)
    data["last_promises_kept"] = getattr(state, "promises_kept", 0)
    scars = set(data.get("permanent_scars", []))
    for s in record.get("menu_scars", []) or []:
        scars.add(s)
    data["permanent_scars"] = sorted(scars)
    if len(truths) >= 3:
        data["director_cut_unlocked"] = True
        SETTINGS["director_cut_unlocked"] = True
        save_settings()
    data["last"] = record
    try:
        with open(FOLDER_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def load_folder():
    try:
        with open(FOLDER_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def export_case_file():
    data = load_folder()
    lines = ["BLACKWOOD ASYLUM — PATIENT 47 CASE FILE", "=" * 50, ""]
    if not data:
        lines.append("No records on file.")
    else:
        if data.get("director_unlocked"):
            lines.append("STATUS: Director commentary authorized.\n")
        perm = data.get("permanent_achievements", [])
        if perm:
            lines.append("PERMANENT MARKS:")
            for a in perm:
                lines.append(f"  * {a}")
            lines.append("")
        runs = data.get("runs", [])
        if not runs and data.get("last"):
            runs = [data["last"]]
        for i, run in enumerate(runs, 1):
            lines.append(f"--- ATTEMPT {i} ---")
            lines.append(f"Ending : {run.get('ending')}")
            lines.append(f"Identity: {run.get('identity')}")
            lines.append(f"Sanity/Health: {run.get('final_sanity')}/{run.get('final_health')}")
            for p in run.get("profile", []):
                lines.append(f"  - {p}")
            for n in run.get("notes", []):
                lines.append(f"  note: {n}")
            lines.append("")
    try:
        with open(CASE_EXPORT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return CASE_EXPORT
    except Exception:
        return None


NURSE_EDGES = {
    "east_corridor": ["nurse_station", "stairwell", "laundry"],
    "nurse_station": ["east_corridor", "office", "staff_wing"],
    "office": ["nurse_station"],
    "staff_wing": ["nurse_station", "archives"],
    "archives": ["staff_wing"],
    # Upper floor graph (room ids only — flavor lives in ROOM_FLAVOR)
    "upper_landing": ["upper_corridor", "stairwell", "roof"],
    "upper_corridor": ["upper_landing", "gallery", "therapy_b", "records_annex", "dir_suite"],
    "gallery": ["upper_corridor"],
    "therapy_b": ["upper_corridor"],
    "records_annex": ["upper_corridor"],
    "dir_suite": ["upper_corridor"],
    "roof": ["upper_landing"],
    "stairwell": ["east_corridor", "basement", "chapel", "upper_landing"],
    "basement": ["stairwell", "morgue"],
    "morgue": ["basement"],
    "chapel": ["stairwell"],
    "laundry": ["east_corridor", "kitchen"],
    "kitchen": ["laundry", "courtyard"],
    "courtyard": ["kitchen"],
    "cell": ["east_corridor"],
    "side_cell": ["east_corridor"],
}


def nurse_step(state, player_room):
    if getattr(state, "killed_nurse", False) or getattr(state, "nurse_pacified", False):
        return False
    loc = getattr(state, "nurse_location", None)
    # Recover from corrupted saves where location became a non-string
    if not isinstance(loc, str) or loc not in NURSE_EDGES:
        state.nurse_location = "nurse_station"
        return False
    neighbors = NURSE_EDGES.get(state.nurse_location, ["nurse_station"])
    if not neighbors or not all(isinstance(n, str) for n in neighbors):
        neighbors = ["nurse_station"]
    hunt = difficulty_nurse_aggression()
    if state.nurse_hostility >= 2 and random.random() < hunt:
        state.nurse_location = player_room if player_room in neighbors else random.choice(neighbors)
    else:
        state.nurse_location = random.choice(neighbors)
    return state.nurse_location == player_room and state.nurse_hostility >= 1


def shadow_step(state, player_room):
    """Second stalker — activates after seeing drawer 47."""
    if not getattr(state, "shadow_active", False):
        return False
    if getattr(state, "accepted_merge_temptation", False):
        return False
    loc = getattr(state, "shadow_location", None)
    if not isinstance(loc, str) or loc not in NURSE_EDGES:
        state.shadow_location = "basement"
    neighbors = NURSE_EDGES.get(state.shadow_location, ["basement"])
    if not neighbors or not all(isinstance(n, str) for n in neighbors):
        neighbors = ["basement"]
    if random.random() < difficulty_nurse_aggression():
        if player_room in neighbors:
            state.shadow_location = player_room
        else:
            state.shadow_location = random.choice(neighbors)
    else:
        state.shadow_location = random.choice(neighbors)
    return state.shadow_location == player_room



def load_ng_plus(state):
    """Apply slight carry-over from prior runs (dialogue & discoveries)."""
    data = load_folder()
    if not data:
        return
    state.ng_plus = True
    state.prior_truths = list(data.get("truths_seen", []))
    state.prior_achievements = list(data.get("permanent_achievements", []))
    last = data.get("last") or {}
    state.prior_notes = list(last.get("notes", []) or [])
    state.prior_ending = last.get("ending")
    state.prior_profile = list(last.get("profile", []) or [])
    # flags inferred from prior profile / notes
    joined = " ".join(state.prior_profile + state.prior_notes).lower()
    state.prior_mercy = "mercy" in joined or "mercy" in " ".join(state.prior_achievements)
    state.prior_child_saved = "free_child" in state.prior_achievements or "child" in joined
    state.prior_burned = "burned" in joined or last.get("ending") == "Blank Slate"
    state.prior_killed_nurse = "nurse" in joined and ("banish" in joined or "destroyed" in joined or "killed" in joined)
    scars = set(data.get("permanent_scars", []) or [])
    last_end = (last.get("ending") or "")
    state.prior_child_betrayed = (
        "betrayed_child" in scars
        or "betray" in joined
        or last.get("promises_broken", 0) >= 1 and "child" in joined
        or last_end in ("Integration", "The Rope", "The Blade") and ("promise" in joined or "courtyard" in joined)
    )
    if state.prior_notes or state.prior_truths:
        state.add_achievement("ng_plus", "New Game+")
        commentary("New Game+ echoes from prior attempts are active.")


def ng_graffiti_for(state, room):
    """Return optional wall text based on prior runs."""
    if not getattr(state, "ng_plus", False):
        return None
    lines = []
    if room == "cell":
        if state.prior_burned:
            lines.append("Someone scrawled under the bunk: THE FILE WAS ASH — STILL HERE.")
        if state.prior_child_saved:
            lines.append("A child's mark near the vent: THANK YOU (again).")
        if state.prior_ending == "Loop":
            lines.append("Faint chalk on the wall: YOU WAKE UP AGAIN.")
    elif room == "east_corridor":
        if state.prior_killed_nurse:
            lines.append("White shoe print, old: SHE FELL HERE BEFORE.")
        if state.prior_mercy:
            lines.append("Pencil on plaster: MERCY CHANGED NOTHING / MERCY CHANGED EVERYTHING.")
        if "orderly" in getattr(state, "prior_truths", []):
            lines.append("A staff stamp ghosted on the wall: NIGHT ORDERLY — RETURNING.")
    elif room == "chapel":
        if state.prior_child_saved:
            lines.append("Dust on the altar is disturbed in the shape of small knees.")
    elif room == "basement":
        if "other" in getattr(state, "prior_truths", []):
            lines.append("On the cage bars, your handwriting from a night you do not remember.")
    return lines[0] if lines else (lines[0] if False else (random.choice(lines) if lines else None))

def chase_sequence(state, hunter="nurse"):
    """Multi-room QTE chase. Branch: force fire door OR hide in locker."""
    clear()
    play_ambient("tension", state.sanity)
    key = qte_primary_key()
    if hunter == "nurse":
        typewriter("White shoes on wet tile — she is running.")
        if state.can_jumpscare():
            jumpscare_nurse()
    else:
        typewriter("Something with your face is behind you.")
        if state.can_jumpscare():
            jumpscare_shadow()
    typewriter("RUN. Fail a check and you are caught.")
    pause(0.7)

    # leg 1 — sprint
    print_big(f"SPRINT [{key.upper()}]")
    if not qte_press_or_hold(f"  [{key.upper()}]: ", key, timeout=2.2):
        play_fail()
        return False
    play_success()
    typewriter("Corridor blurs. Ahead: a stuck fire door — and an open staff locker.")

    # BRANCH: force door vs hide
    branch = get_choice("Split second:", [
        "Force the fire door (faster, louder)",
        "Hide in the locker (quieter, tighter window)",
    ], state)

    if branch == 0:
        # Force door path
        print_big("DOOR")
        typewriter("The bar sticks. Force it.")
        if SETTINGS.get("hold_qte"):
            ok = hold_keypress(f"  HOLD [{key.upper()}] on the bar: ", key, hold_seconds=0.9, timeout=3.8)
        else:
            ok = qte_double_tap(key, 0.7)
        if not ok:
            typewriter("The door holds. Footsteps fill the hall.")
            play_fail()
            return False
        play_success()
        typewriter("The door screams open. You slam it — they know where you went.")
        state.add_achievement("chase_door", "Forced the Fire Door")
        # louder path: harder final heart window
        print_big(f"HEART [{key.upper()}]")
        if not qte_heartbeat(key, bpm=52, windows=4):
            return False
    else:
        # Hide in locker path
        print_big("HIDE")
        typewriter("You fold into the locker. Metal ticks. Hold still.")
        if SETTINGS.get("hold_qte"):
            ok = hold_keypress(f"  HOLD [{key.upper()}] still: ", key, hold_seconds=1.1, timeout=4.2)
        else:
            # single precise press in a short window after a beat
            time.sleep(0.8)
            ok = qte_press_or_hold(f"  Do not breathe — press [{key.upper()}]: ", key, timeout=1.5)
        if not ok:
            typewriter("The door opens. Light finds your eyes.")
            play_fail()
            return False
        play_success()
        typewriter("Steps pass. You wait, then slip out the other way.")
        state.add_achievement("chase_hide", "Hid in the Locker")
        state.change_sanity(-3)
        # quieter path: one turn check, softer heart
        print_big("SLIP")
        if not qte_press_or_hold(f"  Slip out [{key.upper()}]: ", key, timeout=2.4):
            play_fail()
            return False
        print_big(f"HEART [{key.upper()}]")
        if not qte_heartbeat(key, bpm=46, windows=4):
            return False

    play_success()
    typewriter("You lose them in the dark — for now.")
    state.chases_survived = getattr(state, "chases_survived", 0) + 1
    state.add_achievement("chase_survive", "Survived the Chase")
    if SETTINGS.get("hold_qte"):
        state.add_achievement("hold_master", "Hold-Mode Escape")
    state.change_sanity(-6)
    state.change_health(-4)
    return True



def dialogue_tree(state, title, nodes):
    """Simple branching dialogue.
    nodes: dict id -> {text, options: [(label, next_id|None, callback|None)]}
    Start at 'start'. callback(state) optional.
    """
    nid = "start"
    while nid:
        node = nodes[nid]
        for line in node.get("text", []):
            typewriter(line)
            pause(0.45)
        opts = node.get("options", [])
        if not opts:
            break
        labels = [o[0] for o in opts]
        choice = get_choice(title, labels, state)
        label, nxt, cb = opts[choice]
        if cb:
            cb(state)
        nid = nxt



def enter_room(state, room_id):
    set_active_state(state)
    state.current_room = room_id
    state.advance_time()
    autosave(state)
    # activate second stalker after body is seen
    if getattr(state, "saw_own_body", False) and not getattr(state, "shadow_active", False):
        if not getattr(state, "shadow_overdosed", False):
            state.shadow_active = True
            state.shadow_location = "morgue"
            commentary("The shadow self begins to move.")
    # Visible consequence of prior verbs
    show_residual(state, room_id)
    # Body only in the room where she was physically killed
    if nurse_body_in(state, room_id) and room_id != "nurse_station":
        typewriter("Her body is still here — porcelain face cracked, white shoes at wrong angles.")
        pause(0.4)
    if getattr(state, "killed_nurse", False) and room_id in ("east_corridor", "nurse_station", "stairwell"):
        nw = nurse_world_line(state)
        if nw and random.random() < 0.4:
            typewriter(nw)
            pause(0.35)
    if state.killed_nurse:
        pass  # she cannot appear anywhere once dead this run
    elif nurse_step(state, room_id):
        typewriter("Footsteps stop. She is already here.")
        pause(0.7)
        # hard difficulty: chance of chase instead of static fight
        if SETTINGS.get("difficulty") == "hard" and random.random() < 0.45:
            if chase_sequence(state, "nurse"):
                state.nurse_location = "nurse_station"
                return False
            game_over("She caught you in the halls.", state)
            return True
        if state.can_jumpscare():
            jumpscare_nurse()
            state.register_jumpscare()
        encounter_nurse(state)
        return True
    if shadow_step(state, room_id):
        typewriter("The air goes thin. Something wearing your outline stands in the doorway.")
        pause(0.8)
        if SETTINGS.get("difficulty") != "story" and random.random() < 0.4:
            if chase_sequence(state, "shadow"):
                state.shadow_location = "basement"
                return False
            game_over("You ran into yourself.", state)
            return True
        if state.can_jumpscare():
            jumpscare_shadow()
            state.register_jumpscare()
        # brief confrontation
        typewriter('"You left me in the drawer," it says in your voice.')
        state.shadow_awareness += 1
        state.change_sanity(-8)
        key = qte_primary_key()
        if timed_keypress(f"  Push past [{key.upper()}]: ", key, 2.5) != key:
            state.change_health(-10)
        state.shadow_location = "basement"
    return False


def try_flashback(state, key, lines):
    if key in state.moral_checkpoints and key not in state.flashbacks_seen:
        state.flashbacks_seen.append(key)
        print()
        typewriter("--- memory surfaces ---")
        for line in lines:
            typewriter(line)
            pause(0.5)
        print()
        commentary(f"Flashback: {key}")


def _serialize_state(state):
    """JSON-safe snapshot of State (sets → lists)."""
    data = {}
    for k, v in state.__dict__.items():
        if isinstance(v, set):
            data[k] = list(v)
        else:
            data[k] = v
    data["_autosave_room"] = getattr(state, "current_room", None) or "cell"
    return data


def _apply_state_dict(st, data):
    """Restore fields onto a State instance."""
    set_fields = ("examined", "world_marks")
    for k, v in data.items():
        if k.startswith("_"):
            continue
        if k in set_fields and isinstance(v, list):
            setattr(st, k, set(v))
        elif hasattr(st, k):
            setattr(st, k, v)
    if not isinstance(getattr(st, "examined", None), set):
        st.examined = set(getattr(st, "examined", []) or [])
    if not isinstance(getattr(st, "world_marks", None), set):
        st.world_marks = set(getattr(st, "world_marks", []) or [])
    return st


def set_active_state(state):
    global _ACTIVE_STATE
    _ACTIVE_STATE = state


def set_active_slot(slot):
    global _ACTIVE_SLOT
    slot = str(slot) if slot is not None else None
    _ACTIVE_SLOT = slot if slot in ("1", "2", "3") else None


def slot_path(slot):
    return os.path.join(SAVE_DIR, f"slot{slot}.json")


def slot_exists(slot):
    return os.path.isfile(slot_path(slot))


def slot_summary(slot):
    """One-line description for the slot picker."""
    path = slot_path(slot)
    if not os.path.isfile(path):
        return "empty"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        room = data.get("current_room") or data.get("_autosave_room") or "?"
        echoes = data.get("collected_all_echoes", 0)
        ending = data.get("ending_reached")
        if ending:
            return f"finished ({ending})"
        return f"in progress — {room} — echoes {echoes}"
    except Exception:
        return "unreadable"


def autosave(state=None, quiet=True):
    """Silent progress save into the active player slot."""
    if state is None:
        state = _ACTIVE_STATE
    if state is None:
        return
    if getattr(state, "ending_reached", None):
        return
    slot = _ACTIVE_SLOT
    if slot not in ("1", "2", "3"):
        return
    try:
        os.makedirs(SAVE_DIR, exist_ok=True)
        data = _serialize_state(state)
        data["_slot"] = slot
        with open(slot_path(slot), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        if not quiet:
            print(colorize(f"  [Saved to slot {slot}]", DIM))
    except Exception:
        pass


def clear_slot(slot):
    try:
        path = slot_path(slot)
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass


def clear_autosave():
    """Clear the active slot when a run ends."""
    if _ACTIVE_SLOT in ("1", "2", "3"):
        clear_slot(_ACTIVE_SLOT)


def load_slot(slot):
    """Load state from a numbered slot, or None if empty/bad/finished."""
    path = slot_path(slot)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("ending_reached"):
            return None
        st = State()
        _apply_state_dict(st, data)
        if data.get("_autosave_room"):
            st.current_room = data["_autosave_room"]
        return st
    except Exception:
        return None


def choose_play_slot():
    """
    Player picks slot 1-3 when entering the asylum.
    Returns (slot, state_or_None). None state means new game in that slot.
    """
    clear()
    print(colorize("  SELECT SAVE SLOT", BOLD))
    print()
    for s in ("1", "2", "3"):
        print(f"  [{s}] Slot {s} — {slot_summary(s)}")
    print("  [0] Cancel")
    print()
    while True:
        c = input("  Slot: ").strip()
        if c == "0":
            return None, None
        if c not in ("1", "2", "3"):
            print("  Choose 1, 2, or 3.")
            continue
        existing = load_slot(c)
        if existing is not None:
            print(f"  Slot {c} has progress ({slot_summary(c)}).")
            print("  [1] Continue this run")
            print("  [2] Start over in this slot (erases progress)")
            print("  [0] Back")
            sub = input("  Choose: ").strip()
            if sub == "1":
                return c, existing
            if sub == "2":
                clear_slot(c)
                return c, None
            continue
        if slot_exists(c) and str(slot_summary(c)).startswith("finished"):
            print(f"  Slot {c} holds a finished run. Starting over will replace it.")
            yn = input("  New game here? [Y/N]: ").strip().lower()
            if yn not in ("y", "yes"):
                continue
            clear_slot(c)
        return c, None




# ============================================================
# Art
# ============================================================


ROOM_FLAVOR = {
    "cell": [
        ("Cold concrete. The smell of old blood and bleach.",
         "Your head feels split open. A name sits on your tongue... then vanishes."),
        ("The mattress ticks with cooling pipes. Something sweet under the bleach.",
         "You almost form a sentence about how you got here. It dies mid-word."),
        ("Stains on the wall map a coastline only you can read.",
         "For a second you are sure someone is standing behind the door."),
        ("The air is wet and metallic. Your wrists itch for restraints that are not there.",
         "A lullaby tries to surface. You refuse it."),
    ],
    "east_corridor": [
        ("The hallway breathes. Lights pulse in a slow, wrong rhythm.",
         "Wet footprints lead toward the nurse station — and stop midway."),
        ("Paint peels in long strips like shed skin.",
         "Somewhere a cart wheel squeaks, then thinks better of it."),
        ("The floor tilts a degree you cannot prove.",
         "Numbers on the doors have been scratched out and rewritten."),
        ("A draft from nowhere moves the hair on your arms.",
         "You hear your own footsteps half a second late."),
    ],
    "nurse_station": [
        ("Charts yellowed to the color of old teeth. A white shoe under the desk.",
         "The clipboard still wants a name written in."),
        ("The counter smells of antiseptic and cold coffee.",
         "A chair is pushed back as if someone stood in a hurry."),
        ("Fluorescent light buzzes like a fly in a jar.",
         "Ink on the rota has run, as if the paper sweated."),
    ],
    "office": [
        ("The desk lamp is the only honest light left.",
         "Behind the door, a noose hangs from the coat hook — old rope, careful knot."),
        ("Dust motes orbit the lamp like dead planets.",
         "The noose behind the door does not sway. It waits."),
        ("Patient files lean in a pile that was never meant to be neat.",
         "You notice the rope before the desk. That says something."),
        ("The window is bricked from the outside.",
         "A hangman's loop shares the office with the lamp and the ashtray."),
    ],
    "basement": [
        ("Water drips in rhythm with your heartbeat.",
         "The dark at the edges of the beam feels occupied."),
        ("Concrete sweats. The boiler ticks like a cooling gun.",
         "Something shifts in the cage whether you look or not."),
        ("The air tastes of iron and wet ash.",
         "Your light finds handprints that face the wrong way."),
    ],
    "chapel": [
        ("Candle wax has flooded the floor in pale deltas.",
         "The cross on the wall leans as if listening."),
        ("Pews are shoved aside to clear a path that no one admits making.",
         "Incense and mold share the same breath."),
        ("A hymn book lies open to a page torn out.",
         "Small footprints in the dust stop at the altar."),
    ],
    "kitchen": [
        ("Rusted pots. A freezer sealed with heavy chain.",
         "A knife on the counter catches the light and keeps it."),
        ("The drain ticks. Something organic once lived in the sink.",
         "Steam stains on the ceiling look like maps of nowhere."),
        ("Flour on the floor has footprints the size of a child.",
         "The freezer chain is newer than the rest of the room."),
    ],
    "laundry": [
        ("Industrial washers stand open like mouths.",
         "The air is thick with soap that failed to clean anything."),
        ("A dryer door swings once with no draft.",
         "Uniforms hang like emptied people."),
        ("Water stands in a tray the color of weak tea.",
         "Someone folded towels and never came back for them."),
    ],
    "morgue": [
        ("The cold is personal. It knows where you keep your pulse.",
         "Drawer handles shine from use."),
        ("Formaldehyde and floor wax. A clock with no hands.",
         "One drawer sits out a finger-width, as if breathing."),
        ("Your breath becomes weather in here.",
         "Tags on the drawers are written in two different hands."),
    ],
    "upper_landing": [
        ("Scorch marks map the fire's old climb.",
         "Below you, the asylum sounds like a different building."),
        ("Joists exposed like ribs.",
         "Wind finds the hole you made in the debris."),
    ],
    "upper_corridor": [
        ("Dust, pigeon bones, a nameplate half-melted.",
         "NIGHT SUPERVISOR — the name is scratched out."),
        ("The floor tilts toward the gallery glass.",
         "Your footsteps arrive a half-second late."),
    ],
    "gallery": [
        ("One-way glass. The lower hall does not know you are watching.",
         "A logbook on a chain still wants entries."),
        ("Cells look smaller from above.",
         "White movement on the nurse floor — or memory of it."),
    ],
    "therapy_b": [
        ("Chairs face a dead tape recorder.",
         "Curtains nailed shut as if the windows lied."),
        ("The air holds old breath.",
         "Someone practiced saying your name in this room."),
    ],
    "records_annex": [
        ("Overflow files. Transfer lists. Fire certificates.",
         "One envelope still sealed for the Director."),
        ("Ink has run as if the paper sweated.",
         "Names of the vanished staff share a night with the fire."),
    ],
    "dir_suite": [
        ("Too clean. Cleanliness is the wrongness.",
         "A noose-shaped shadow on the wall — no rope in sight."),
        ("The Director's chair faces the door, not the window.",
         "Sedation orders wait under a glass weight."),
    ],
    "roof": [
        ("Rain. Town lights far away.",
         "An outline against the moon that matches your stance."),
        ("The roof is the only honest sky left.",
         "Wind tries to finish sentences for you."),
    ],
    "stairwell": [
        ("Concrete steps worn to shallow bowls.",
         "Sound falls up and down at the same time."),
        ("A light fixture swings without wind.",
         "Handrails are polished by decades of the same grip."),
        ("Graffiti under the landing: DO NOT FOLLOW HER UP.",
         "You cannot tell if the wet on the steps is water."),
    ],
    "side_cell": [
        ("Another cell. The walls remember a different occupant.",
         "Numbers carved in hundreds: 47 47 47."),
        ("The mattress is thinner than yours. Or you are heavier now.",
         "Scratches near the floor look like someone counted nights."),
        ("Dust hangs in a stripe of light from a cracked pane.",
         "The door sticks as if it prefers closed."),
    ],
}


def room_lines(room_id):
    """Return two flavor lines for a room, random each visit."""
    pool = ROOM_FLAVOR.get(room_id)
    if not pool:
        return None
    return random.choice(pool)


def speak_room(room_id, state=None):
    pair = room_lines(room_id)
    if not pair:
        if state is not None:
            show_residual(state, room_id)
        return
    typewriter(pair[0])
    pause(0.7)
    typewriter(pair[1])
    pause(0.6)
    if state is not None:
        show_residual(state, room_id)



CHANGELOG = """
ECHOES OF THE ASYLUM — CHANGELOG
================================


v0.15 — Tighter type windows
  - Escape type QTE limits compressed: shortest 7s (HOLD FAST), mid 8s
    (OPEN THE GATE, CRAWL FORWARD, BLACKWOOD-OUT), longest 9s
    (FOLLOW THE PIPES, BREATHE THE NIGHT, LEAVE THIS PLACE)
  - Still scaled by difficulty and QTE time scale; rope/blade untimed

v0.14 — Timed escape typing
  - Escape-gauntlet type QTEs now enforce a real time limit (fail if too slow)
  - Rope / blade ending type chains remain untimed (retry until correct)
  - Cross-platform timed line input (Windows msvcrt / POSIX select)
  - Prior: audio performance, ambient bed cache, debug option-9 fix

v0.1 — Foundation
  - Text horror adventure in Blackwood Asylum (Patient 47)
  - Procedural audio (drones, stingers, typewriter clicks)
  - Typewriter text, QTEs, sanity/health, contextual jumpscares

v0.2 — Slow burn & structure
  - Slower pacing, nested building layout (not all rooms on one hall)
  - Choice UI clears to the selected line only
  - Large ASCII jumpscares and QTE banners
  - Patient 47 folder on main menu

v0.3 — Systems
  - Continuous ambient bed, vanishing false options at low sanity
  - Examine objects, night-flavor text, hidden passages
  - Side cell, three truth paths, reputations (child/nurse/shadow)
  - Moral checkpoints & flashbacks, multi-run folder, nurse stalker
  - Saves, accessibility options, achievements, director commentary
  - Case file export

v0.4 — Depth
  - Chase sequences (force door vs hide in locker)
  - Branching dialogue (child & shadow)
  - Difficulty presets, QTE key rebind, hold-to-complete QTEs
  - Second stalker (shadow after drawer 47)
  - Multiple child fates, NG+ graffiti & dialogue echoes
  - Difficulty achievements, Director's Cut ending

v0.5 — Consequences
  - Promise-breaking punishments; harder multi-stage front gates
  - Personality algorithm (36 types; one type per run)
  - Main menu ANSI scars from significant past runs (one at a time)
  - Dead Nurse stays dead; search body for master key
  - Folder flips through past runs (N/P)
  - Item routing across wings (cutters, keys, holy water, etc.)
  - Child betrayal across runs if promises are broken
  - Suicide endings: The Rope (office), The Blade (kitchen)
  - Personality: The Hollow
  - Randomized room flavor lines; office noose called out on entry
  - Changelog option on main menu

v0.6 — Audio & mystery polish
  - Louder looping ambient per room and main menu; higher overtones
  - Jumpscare stingers play fully (wait-through) so SFX is reliable
  - Bassy, dissonant suicide soundscapes (no rising saw sweeps)
  - Ambient may play under typewriter text
  - Opening cell text no longer spoils staff/orderly identity
  - Behavioral profile lists actions only (no type subheading)
  - Encounter notes shown in full in the Patient 47 folder
  - Title banner simplified to ECHOES OF THE ASYLUM
  - How to Play rewritten as actual instructions

v0.7 — Upper offices
  - Crowbar (laundry) or destroyed boiler opens upper powered staff door
  - Rhythmic force-door QTE (heartbeat-style)
  - Upper landing, corridor, gallery, therapy B, records annex, director suite, roof
  - Gallery log / nurse watch; therapy intake tape; director letter identity reveal
  - Roof: moon-outline shadow, climb-down escape, jump ending (The Fall)
  - Debug mode (Accessibility; password-gated tools, teleport, flags, QTE tests)

v0.8 — Escape gauntlets & polish
  - Physical escapes (gates, tunnel, courtyard, roof): 6-step chains of type + hold + heartbeat QTEs
  - Route-specific flavor lines for each escape method
  - Roof climb: any failed QTE triggers The Fall
  - 3-second live READY countdown between non-suicide QTE prompts and action
  - Input buffer flushed during countdown and at QTE start (prevents key spillover)
  - Suicide type-step chains: 2-second pause between successful steps
  - Autosave, intro cinematic, world marks / truth paths (as in current build)
  - Gallery respects nurse death location for body visibility

v0.9 — QTE feel
  - Type QTEs no longer advertise case-insensitivity
  - Heartbeat QTEs: early presses fail; only open-window presses count
  - Heartbeat sequences use 4–6 beats
  - Escape gauntlet ASCII titles derived from flavor text (not ESCAPE 1/2/…)

v0.10 — Last-chance confirmations
  - Non-suicide run-ending actions warn in flavor, then require typing a situation-specific phrase
  - Covers: merge, quiet-alcove loop, physical escapes, roof climb, child gate choices, Director's Cut
  - Suicides (rope/blade/jump) unchanged

v0.11 — Sanity typewriter & mended trust
  - Typewriter speed scales with sanity (lower = faster)
  - Below 50 sanity: occasional bold red intrusive suicide-hint lines that erase themselves
  - Fulfilling a child promise after a prior-run betrayal restores trust (favor, dialogue, scar softens)

v0.12 — Child path
  - No instant "free her now" talk option; freedom via thread/locket/hide/courtyard paths
  - Child may hint about black thread in the morgue after you listen
  - Morgue child's drawer: cut the black thread if she asked
  - Promising escape after a prior betrayal clears her gate-block this run

v0.13 — Debug meta tools
  - Debug [13]: delete specific (or all) previous runs from the Patient 47 folder
  - Debug [14]: edit meta factors — girl trust/distrust, child favor, promises, scars,
    prior NG+ flags, director unlocks, reload NG+ into current state


"""


ART_TITLE = r"""
    #######  #####  #    #  #####  ######  ######
    #       #     # #    # #     # #       #
    #####   #       ###### #     # #####   ######
    #       #       #    # #     # #            #
    #######  #####  #    #  #####  ######  ######
                   OF THE ASYLUM
"""

ART_CELL = r"""
    +------------------------------------------------------+
    | #### stains drip down the plaster like old tears ### |
    | ##  +------------+        +----------+            ## |
    | ##  |  MATTRESS  |        |   DOOR   |            ## |
    | ##  |  (stained) |        |   ajar   |            ## |
    | ##  +------------+        |  rusted  |            ## |
    | ##                        +----------+            ## |
    | ##            [ YOU ]  floor tiles loose          ## |
    +------------------------------------------------------+
"""

ART_HALLWAY = r"""
    ========================================================
      EAST CORRIDOR - MAIN WING
      peeling paint / buzzing fluorescents / wet footprints
    --------------------------------------------------------
      [CELL]--[YOU]--[NURSE STATION]----[STAIRWELL]
                     |
                  (locked west wing needs key)
    ========================================================
"""

ART_NURSE_STATION = r"""
    +------------------------------------------------------+
    |              N U R S E   S T A T I O N               |
    |  +-------------+   counter still sticky with rust    |
    |  |  CHART RACK |   a white shoe under the desk       |
    |  +-------------+   clipboard: "47 - SEDATE"          |
    |     door -> Director's Office                        |
    |     door -> Staff Wing (key)                         |
    +------------------------------------------------------+
"""

ART_SIDE_CELL = r"""
    +------------------------------------------------------+
    |              S I D E   C E L L   1 2                 |
    |  mattress thinner than yours     window: cracked     |
    |  wall: 47 47 47 47 47 (hundreds)                     |
    |  scratches near the floor — someone counted nights   |
    |     door -> East Corridor                            |
    +------------------------------------------------------+
"""

ART_STAFF_WING = r"""
    +------------------------------------------------------+
    |              S T A F F   W I N G                     |
    |  [break room]  coffee rings  calendar: week of fire  |
    |  uniform on a chair   logbook open to your hand      |
    |     door -> Archives                                 |
    |     door -> Nurse Station                            |
    +------------------------------------------------------+
"""


ART_UPPER_LANDING = r"""
    +======================================================+
    |              UPPER  LANDING  /  STAIR HEAD           |
    |  #### debris cleared — blackened joists exposed #### |
    |  |  corridor →                    roof hatch ↑       |
    |  |  observation glass (dark)                         |
    |  floor scorched in the shape of a long-ago fire      |
    +======================================================+
"""

ART_UPPER_CORRIDOR = r"""
    +======================================================+
    |              UPPER  CORRIDOR                         |
    |  L-shaped hall. Dust. Pigeon bones.                  |
    |  [Gallery]  [Therapy B]  [Records]  [Suite: locked]  |
    |  The lower asylum hums under the floorboards.        |
    +======================================================+
"""

ART_GALLERY = r"""
    +======================================================+
    |           OBSERVATION  GALLERY                       |
    |  ############################################        |
    |  #  one-way glass  —  cells / nurse desk below  #    |
    |  ############################################        |
    |  logbook on a chain    binocular shelf empty         |
    +======================================================+
"""

ART_THERAPY = r"""
    +======================================================+
    |           THERAPY  ROOM  B                           |
    |     (  )      (  )      (  )     chairs in a ring    |
    |            [=======]  dead tape recorder             |
    |     curtains nailed shut. Clock without hands.       |
    +======================================================+
"""

ART_RECORDS_ANNEX = r"""
    +======================================================+
    |           RECORDS  ANNEX  (overflow)                 |
    |  cabinets lean like drunks. Transfer lists.          |
    |  one SEALED envelope: FOR THE DIRECTOR ONLY          |
    |  fire-death certificates curled at the edges         |
    +======================================================+
"""

ART_DIR_SUITE = r"""
    +======================================================+
    |        DIRECTOR'S  PRIVATE  SUITE                    |
    |  sitting room → study → quiet alcove                 |
    |  too clean. the rest of the building is a lie.       |
    |  shadow of a noose on the far wall (no rope here)    |
    +======================================================+
"""

ART_ROOF = r"""
    +======================================================+
    |                    R O O F                           |
    |         .  *    .      moon    .    *                |
    |      /\___/\   outline against the light             |
    |     /  * *  \  (is it you — or the other you?)       |
    |  ----||||----  town lights far, rain closer          |
    +======================================================+
"""

ART_STAIRS = r"""
    +======================================================+
    |                    S T A I R W E L L                 |
    |   UP ------------------------------ side to Chapel   |
    |   |                                                  |
    |   |   steps worn by decades of the same feet         |
    |   |                                                  |
    |   DOWN ---------------------------- to Basement      |
    +======================================================+
"""

ART_MORGUE = r"""
    +------------------------------------------------------+
    |                    M O R G U E                       |
    |  The cold has a name. It is learning yours.          |
    |  +----+ +----+ +----+ +----+ +----+ +----+           |
    |  | 01 | | 12 | | 23 | | 31 | | 40 | | 47 |<- fresh   |
    |  +----+ +----+ +----+ +----+ +----+ +----+           |
    |  instruments table / drainage channels / tags        |
    +------------------------------------------------------+
"""

ART_OFFICE = r"""
    +------------------------------------------------------+
    |              DIRECTOR'S OFFICE - 1973                |
    |     desk lamp still burning / ashtray full           |
    |          +----------------------+                    |
    |          |  PATIENT FILE  #47   |  <- warm paper     |
    |          +----------------------+                    |
    |     photo on desk / drawers locked with time         |
    +------------------------------------------------------+
"""

ART_BASEMENT = r"""
    +======================================================+
    |                      BASEMENT                        |
    |  water drips in time with something that is not a    |
    |  pipe. The dark has depth.                           |
    |     +----------+              +------------+         |
    |     |  BOILER  |              |    CAGE    |         |
    |     |  cold    |              |  occupied  |         |
    |     +----------+              +------------+         |
    |          hatch -> maintenance tunnel                 |
    |          door  -> Morgue                             |
    +======================================================+
"""

ART_CHAPEL = r"""
    +------------------------------------------------------+
    |                     C H A P E L                      |
    |   dust on pews / prayer books fused shut by mold     |
    |                    +---------+                       |
    |                    |  CROSS  |  (warped)             |
    |                +---+---------+---+                   |
    |                |     ALTAR       |                   |
    |                |  a small figure |                   |
    +------------------------------------------------------+
"""

ART_KITCHEN = r"""
    +------------------------------------------------------+
    |                    K I T C H E N                     |
    |  pots fused with rust / stove like a sealed mouth    |
    |   +------+  +------+  +--------------------+         |
    |   |STOVE |  | SINK |  | FREEZER (chained)  |         |
    |   +------+  +------+  +--------------------+         |
    +------------------------------------------------------+
"""

ART_LAUNDRY = r"""
    +------------------------------------------------------+
    |                    L A U N D R Y                     |
    |  machines open like throats / lint gray as ash       |
    |   +----+ +----+ +----+      +------------+           |
    |   |Wash| |Wash| |Dry |      |  LOCKERS   |           |
    |   +----+ +----+ +----+      +------------+           |
    +------------------------------------------------------+
"""

ART_ARCHIVES = r"""
    +------------------------------------------------------+
    |                   A R C H I V E S                    |
    |  ######## ######## ######## ########                 |
    |  #FIRE73# #STAFF # #TRANSFR# #  47  #                |
    |  ######## ######## ######## ########                 |
    |  dust thick enough to write a name in                |
    +------------------------------------------------------+
"""

ART_COURTYARD = r"""
    ========================================================
              OVERGROWN COURTYARD - REAL AIR
         #### dead fountain #### ivy strangling brick
              [ rusted outer gate ]
    ========================================================
"""

ART_NURSE = r"""
              +-------------------+
              |   ooo     ooo     |
              |    o       o      |
              |        V          |
              |     -------       |
              +---------+---------+
                   She smiles
                   too wide.
                   The smile does not end.
"""

ART_CHILD = r"""
                   .       .
                .   |\_/|   .
                 \__| o o|__/
                   /|  =  |\
               stained nightgown
               thread at the eyes
"""

ART_SHADOW = r"""
                         ########
                       ##        ##
                     ##   ####     ##
                   ##   ########     ##
                 ##   ############     ##
                ##                        ##
               ##   ****      ****         ##
              ################################
"""

ART_ESCAPE = r"""
    ========================================================
                     THE FRONT GATES
         ########################################
         ##      +------------------+          ##
         ##      |     OPEN ?       |          ##
         ##      +------------------+          ##
         ########################################
    ========================================================
"""


# ============================================================
# Endings
# ============================================================


def ending_death(state):
    state.ending_reached = "Death"
    stop_ambient()
    save_folder(state)
    clear()
    typewriter("The asylum finishes what it started.")
    print("\n* DEATH *\n")
    input("Press Enter...")
    main_menu()


def ending_loop(state):
    state.ending_reached = "Loop"
    stop_ambient()
    save_folder(state)
    clear()
    typewriter("You open your eyes on a thin mattress.")
    typewriter("You do not remember arriving.")
    print("\n* LOOP *\n")
    input("Press Enter...")
    main_menu()


def ending_directors_cut(state):
    state.ending_reached = "Director's Cut"
    SETTINGS["director_cut_unlocked"] = True
    stop_ambient()
    save_folder(state)
    clear()
    typewriter("The file opens from the other side of the glass.")
    typewriter("You were never only Patient 47.")
    print("\n* DIRECTOR'S CUT *\n")
    input("Press Enter...")
    main_menu()


def ending_true_escape(state):
    state.ending_reached = "True Escape"
    promised = state.promised_child or getattr(state, "child_fate", None) == "courtyard"
    fulfilled = state.freed_ghost_child or getattr(state, "child_fate", None) in ("freed", "hidden")
    if getattr(state, "child_fate", None) == "courtyard":
        fulfilled = state.opened_courtyard and state.child_favor >= 3
    if promised and not fulfilled:
        state.betrayed_child = True
        state.promises_broken = getattr(state, "promises_broken", 0) + 1
        state.add_note("Escaped without the child after promising her.")
    diff = SETTINGS.get("difficulty", "standard")
    if diff == "hard":
        state.add_achievement("hard_escape", "Hard Escape")
    elif diff == "story":
        state.add_achievement("story_escape", "Story Escape")
    else:
        state.add_achievement("standard_escape", "Standard Escape")
    save_folder(state)
    clear()
    play_background_music(state.sanity)
    show(ART_ESCAPE)
    typewriter("The gates open. Rain hits your face.")
    pause(1.5)
    typewriter("You run until the asylum is only a black shape.")
    if state.knows_true_identity:
        pause(1.2)
        typewriter("You remember. You were the night orderly who tried to stop it.")
    print("\n* TRUE ESCAPE *\n")
    input("Press Enter...")
    main_menu()


def ending_false_escape(state):
    state.ending_reached = "Loop"
    save_folder(state)
    clear()
    show(ART_ESCAPE)
    typewriter("The gates swing open...")
    pause(1.8)
    typewriter("You wake up on the stained mattress.")
    print("\n* LOOP *\n")
    input("Press Enter...")
    main_menu()


def ending_become_staff(state):
    state.ending_reached = "The New Nurse"
    save_folder(state)
    clear()
    show(ART_NURSE)
    typewriter("The white dress fits perfectly.")
    print("\n* THE NEW NURSE *\n")
    input("Press Enter...")
    main_menu()



def ending_the_rope(state):
    state.ending_reached = "The Rope"
    add_trait(state, "self_end", 1)
    add_trait(state, "detachment", 2)
    if state.promised_child or getattr(state, "child_fate", None) == "courtyard":
        state.betrayed_child = True
        state.promises_broken = getattr(state, "promises_broken", 0) + 1
        state.add_note("Broke a promise (suicide_after_promise).")
        mark_world(state, "broke_child")
    stop_ambient()
    clear()
    # Text first — then the long audio, with no dead air after it ends
    typewriter("The noose was never meant for decoration.")
    pause(0.55)
    typewriter("The desk lamp swings. The file closes on nothing.")
    pause(0.35)
    play_suicide_audio("rope")
    print("\n* THE ROPE *\n")
    save_folder(state)
    input("Press Enter...")
    main_menu()


def ending_the_blade(state):
    state.ending_reached = "The Blade"
    add_trait(state, "self_end", 1)
    add_trait(state, "detachment", 2)
    if state.promised_child or getattr(state, "child_fate", None) == "courtyard":
        state.betrayed_child = True
        state.promises_broken = getattr(state, "promises_broken", 0) + 1
        state.add_note("Broke a promise (suicide_after_promise).")
        mark_world(state, "broke_child")
    stop_ambient()
    clear()
    typewriter("Steel is honest in a way the hallway is not.")
    pause(0.55)
    typewriter("You set nothing right. You only stop.")
    pause(0.35)
    play_suicide_audio("blade")
    print("\n* THE BLADE *\n")
    save_folder(state)
    input("Press Enter...")
    main_menu()


def ending_merge(state):
    state.ending_reached = "Integration"
    set_truth_path(state, "merge", quiet=True)
    mark_world(state, "truth_merge")
    if state.promised_child or getattr(state, "child_fate", None) in ("courtyard", "hidden"):
        state.betrayed_child = True
        state.promises_broken = getattr(state, "promises_broken", 0) + 1
        state.add_note("Merged after promising the child — betrayal recorded.")
    save_folder(state)
    clear()
    show(ART_SHADOW)
    typewriter("You and the thing in the cage stop pretending.")
    print("\n* INTEGRATION *\n")
    input("Press Enter...")
    main_menu()


def ending_sacrifice(state):
    state.ending_reached = "Quiet Sacrifice"
    save_folder(state)
    clear()
    typewriter("You stay behind so the child can leave.")
    print("\n* QUIET SACRIFICE *\n")
    input("Press Enter...")
    main_menu()


def ending_burned_memory(state):
    state.ending_reached = "Blank Slate"
    save_folder(state)
    clear()
    typewriter("You burned the file. The truth is ash.")
    pause(1.5)
    typewriter("The gates open for a man who no longer knows why he wanted out.")
    print("\n* BLANK SLATE *\n")
    input("Press Enter...")
    main_menu()


def game_over(reason, state=None):
    if state:
        state.ending_reached = "Death"
        state.add_note(f"Died: {reason}")
        stop_ambient()
        save_folder(state)
    clear()
    play_samples(generate_stinger_classic(0.75), wait=True)
    print(r"""
        +==========================================+
        |             *  YOU DIED  *               |
        +==========================================+
    """)
    pause(1.0)
    print(f"\n{reason}\n")
    if state:
        print(f"Final Sanity: {state.sanity}  |  Health: {state.health}")
    input("Press Enter...")
    main_menu()


# ============================================================
# SCENES – nested building
# ============================================================

def scene_cell(state):
    clear()
    play_ambient("cell", state.sanity)
    show(ART_CELL)
    g = ng_graffiti_for(state, "cell")
    if g:
        typewriter(g)
        state.add_note("NG+ wall: " + g[:60])
    speak_room("cell")
    typewriter("The door is not fully closed.")
    state.add_note("Woke in Cell 47 with no memory of arrival.")

    while True:
        options = [
            "Search the mattress and floor thoroughly",
            "Listen at the door for a long time",
            "Call out into the hallway",
            "Examine the scratches on the wall",
            "Leave the cell into the East Corridor",
        ]
        choice = get_choice("What do you do?", options)

        if choice == 0:
            if state.has_cell_key:
                typewriter("You already took everything useful.")
                continue
            typewriter("A loose floor tile. Underneath: a bent key, a dead flashlight, a child's drawing.")
            state.has_cell_key = True
            state.has_flashlight = True
            state.collected_all_echoes += 1
            state.add_note("Found cell key, flashlight, and a child's drawing under the floor.")
            typewriter("The flashlight flickers.")
            if qte_echo("f"):
                state.has_flashlight = True
                typewriter("It holds a weak beam.")
            else:
                state.has_flashlight = False
                typewriter("The light dies for good.")

        elif choice == 1:
            typewriter("Footsteps. Slow. Dragging. Then a wet sound like licking the walls.")
            state.listened_to_whispers = True
            state.shadow_awareness += 1
            state.add_note("Heard wet, rhythmic sounds beyond the cell door.")
            if state.change_sanity(-10):
                game_over("The sound never left your ears.", state)
                return
            if state.can_jumpscare() and state.jumpscares_seen == 0:
                time.sleep(1.5)
                jumpscare_whisper_flood()
                state.register_jumpscare()
                state.change_sanity(-6)

        elif choice == 2:
            typewriter('"Is anyone there?"')
            pause(1.6)
            typewriter('A child from the vent: "Don\'t let her hear you..."')
            state.child_favor += 1
            state.called_out_early = True
            state.nurse_hostility += 1
            state.checkpoint("called_out", "Called out from cell")
            state.add_note("Called out; a child warned not to let 'her' hear.")
            state.change_sanity(-6)
            mark_world(state, "called_out")

        elif choice == 3:
            typewriter("Scratches form words: SHE SMILES WHEN YOU FORGET / DO NOT TRUST THE ROUNDS / DRAWER 47")
            state.collected_all_echoes += 1
            state.add_note("Read wall scratches: warning about rounds, and Drawer 47.")
            mark_world(state, "read_scratches")

        else:
            if not state.has_cell_key and not state.refused_to_search_cell:
                leave = get_choice("Leave without searching?", ["Yes, leave now", "No, stay"])
                if leave == 1:
                    continue
                state.refused_to_search_cell = True
            typewriter("You push into the East Corridor.")
            pause(1.0)
            scene_east_corridor(state)
            return


def scene_east_corridor(state):
    clear()
    play_ambient("hallway", state.sanity)
    show(ART_HALLWAY)
    state.advance_time()
    state.hallway_visits += 1

    if state.hallway_visits == 1:
        typewriter("The hallway breathes. Lights flicker in sequence, like a pulse.")
        pause(1.2)
        typewriter("Wet footprints lead toward the nurse station. Behind you, the cell door settles.")
        pause(1.0)
    else:
        type_fast("East Corridor. The lights still pulse. The air is the same.")
        if state.time_of_night >= 3:
            type_fast("Thicker air. Harder to push through.")
        if state.sanity < 50:
            type_fast("Movement at the edge of vision - gone when you look.")

    if state.killed_nurse:
        type_fast("Drag marks where a white shoe used to be.")
    if state.freed_ghost_child:
        type_fast("The vents are only air now.")
    g = ng_graffiti_for(state, "east_corridor")
    if g:
        type_fast(g)
    try_flashback(state, "called_out", [
        "Your earlier shout returns from two directions.",
        "Something answered that was not the child.",
    ])
    options = [
        "Return to your cell",
        "Enter the Nurse Station",
        "Go to the Stairwell",
        "Try the locked West Wing door",
        "Check the side cell door",
        "Walk toward the front gates (far end)",
    ]
    choice = get_choice("Where?", options, state)

    if choice == 0:
        scene_cell(state)
    elif choice == 1:
        if enter_room(state, "nurse_station"):
            return
        scene_nurse_station(state)
    elif choice == 2:
        if enter_room(state, "stairwell"):
            return
        scene_stairwell(state)
    elif choice == 3:
        if state.has_master_key:
            typewriter("The west door unlocks with the master key. Laundry smell.")
            pause(1.0)
            if enter_room(state, "laundry"):
                return
            scene_laundry(state)
        else:
            typewriter("Locked. STAFF / SERVICE.")
            pause(1.0)
            scene_east_corridor(state)
    elif choice == 4:
        if enter_room(state, "side_cell"):
            return
        scene_side_cell(state)
    elif choice == 5:
        attempt_escape(state)



def scene_side_cell(state):
    clear()
    play_ambient("side_cell", state.sanity)
    show(ART_SIDE_CELL)
    state.current_room = "side_cell"
    speak_room("side_cell", state)
    state.add_note("Entered side cell 12.")
    while True:
        options = [
            "Examine the carvings",
            "Leave a kind word in the dark",
            "Scratch out the numbers in anger",
            "Return to East Corridor",
        ]
        choice = get_choice("What do you do?", options, state)
        if choice == 0:
            typewriter("'I was staff first.' Another hand: 'No. You were always ours.'")
            state.collected_all_echoes += 1
            state.add_note("Side cell: conflicting identity carvings.")
        elif choice == 1:
            typewriter("Something unclenches in the dark.")
            state.side_cell_mercy = True
            state.child_favor += 1
            add_trait(state, "mercy", 1)
            add_trait(state, "compassion", 1)
            state.checkpoint("side_mercy", "Kindness in side cell")
            state.add_achievement("side_mercy", "Side Cell Kindness")
            state.add_note("Showed kindness in side cell.")
            mark_world(state, "side_mercy")
        elif choice == 2:
            typewriter("The wall feels pleased.")
            state.side_cell_cruel = True
            state.shadow_awareness += 2
            add_trait(state, "cruelty", 2)
            add_trait(state, "violence", 1)
            state.change_sanity(-8)
            state.checkpoint("side_cruel", "Destroyed carvings")
            set_truth_path(state, "patient")
            mark_world(state, "side_cruel")
            state.add_note("Destroyed side-cell carvings.")
        else:
            if enter_room(state, "east_corridor"):
                return
            scene_east_corridor(state)
            return


def scene_nurse_station(state):
    clear()
    play_ambient("nurse", state.sanity)
    show(ART_NURSE_STATION)
    state.current_room = "nurse_station"
    state.advance_time()

    body_here = (
        getattr(state, "killed_nurse", False)
        and getattr(state, "nurse_death_room", None) == "nurse_station"
        and not getattr(state, "nurse_banished", False)
    )
    if body_here:
        typewriter("The station is wrong without her hum. Charts scattered.")
        typewriter("Her body is on the floor behind the counter — porcelain face cracked.")
        state.add_note("Nurse Station: her body is here.")
    elif state.killed_nurse:
        typewriter("The station is wrong without her hum. Charts scattered.")
        if getattr(state, "nurse_banished", False):
            typewriter("No body. She walked into the wall elsewhere — or nowhere.")
        else:
            typewriter("No body behind the counter. She fell somewhere else.")
        state.add_note("Nurse Station after her death — body not here.")
    else:
        speak_room("nurse_station", state)
        state.add_note("Entered the Nurse Station. Clipboard still marked '47 - SEDATE'.")
    pause(1.0)

    while True:
        if body_here:
            options = [
                "Search her body",
                "Read the clipboard and charts",
                "Enter the Director's Office",
                "Try the Staff Wing door",
                "Return to the East Corridor",
            ]
        else:
            options = [
                "Read the clipboard and charts",
                "Enter the Director's Office",
                "Try the Staff Wing door",
                "Hide behind the counter and listen",
                "Return to the East Corridor",
            ]
        choice = get_choice("What do you do?", options, state)

        if body_here and choice == 0:
            if getattr(state, "searched_nurse_body", False):
                typewriter("Pockets already turned out. Nothing new.")
            else:
                typewriter("Cold wrists. A ring of keys on a chain. A folded note in her apron.")
                typewriter("'If the orderly returns, sedate. If he remembers, end the round.'")
                state.searched_nurse_body = True
                state.has_master_key = True
                state.collected_all_echoes += 1
                add_trait(state, "curiosity", 1)
                add_trait(state, "detachment", 1)
                state.add_note("Searched the Nurse's body — keys and a kill-order note.")
                state.change_sanity(-6)
            continue

        # normalize indices when body-search option is present
        if body_here:
            act = choice
            if act == 0:
                continue
            read_i, office_i, staff_i, hide_i, leave_i = 1, 2, 3, None, 4
        else:
            read_i, office_i, staff_i, hide_i, leave_i = 0, 1, 2, 3, 4

        if choice == read_i:
            typewriter("'Patient 47 - continued identity reinforcement. Do not allow basement access.'")
            pause(0.8)
            if state.killed_nurse:
                typewriter("The line about rounds is crossed out in a shaking hand.")
            else:
                typewriter("Another note in different ink: 'She is still making rounds.'")
            state.collected_all_echoes += 1
            add_trait(state, "curiosity", 1)
        elif choice == office_i:
            if enter_room(state, "office"):
                return
            scene_office(state)
            return
        elif choice == staff_i:
            if state.has_master_key or state.has_cell_key:
                if enter_room(state, "staff_wing"):
                    return
                scene_staff_wing(state)
                return
            typewriter("Needs a key.")
        elif hide_i is not None and choice == hide_i:
            typewriter("Lullaby with wrong notes somewhere down the hall.")
            state.nurse_hostility += 1
            state.add_note("Heard Nurse lullaby while hiding.")
            if state.can_jumpscare() and state.nurse_hostility >= 2:
                jumpscare_nurse()
                state.register_jumpscare()
                encounter_nurse(state)
                return
        elif choice == leave_i:
            if enter_room(state, "east_corridor"):
                return
            scene_east_corridor(state)
            return



def scene_stairwell(state):
    clear()
    play_ambient("stairs", state.sanity)
    show(ART_STAIRS)
    state.current_room = "stairwell"
    state.advance_time()
    if state.scenes_visited <= 3:
        speak_room("stairwell")
    else:
        type_fast("Stairwell. The same worn steps.")

    upper_label = "Climb toward the upper offices"
    if state.upper_unlocked or state.upper_debris_cleared:
        upper_label = "Climb toward the upper offices (open)"
    elif state.has_crowbar or state.destroyed_boiler:
        upper_label = "Climb toward the upper offices (powered door — workable)"
    else:
        upper_label = "Climb toward the upper offices (powered door sealed)"

    options = [
        "Descend to the Basement",
        "Take the side passage toward the Chapel",
        upper_label,
        "Return to the East Corridor",
    ]
    choice = get_choice("Where?", options, state)

    if choice == 0:
        scene_basement(state)
    elif choice == 1:
        scene_chapel(state)
    elif choice == 2:
        try_upper_offices(state)
    else:
        scene_east_corridor(state)


def try_upper_offices(state):
    """Upper floor sealed by a powered staff door. Open via dead boiler or crowbar QTE."""
    if state.upper_unlocked or state.upper_debris_cleared:
        scene_upper_landing(state)
        return

    if not state.has_crowbar and not state.destroyed_boiler:
        typewriter("A heavy staff door blocks the upper flight — magnetic locks still humming.")
        typewriter("Without power loss or a hard lever, it will not yield.")
        state.add_note("Upper offices sealed by powered staff door.")
        pause(1.0)
        scene_stairwell(state)
        return

    key = qte_primary_key()

    if state.destroyed_boiler and not state.has_crowbar:
        typewriter("The boiler is dead. The magnetic lock on the upper door has gone dark.")
        typewriter("You still have to force the deadbolt by hand — on the rhythm of the failing current.")
        pause(0.8)
        if not qte_debris_clear(key, bpm=50, windows=5):
            typewriter("The bolt bites back. Your shoulders burn.")
            state.change_sanity(-6)
            state.change_health(-4)
            scene_stairwell(state)
            return
        state.upper_debris_cleared = True
        state.upper_unlocked = True
        state.collected_all_echoes += 1
        add_trait(state, "resolve", 2)
        state.add_note("Opened upper powered door after destroying the boiler.")
        typewriter("The staff door sighs open. Upper air is colder.")
        pause(0.8)
        scene_upper_landing(state)
        return

    # crowbar path — force the powered door
    typewriter("You wedge the crowbar into the powered staff door's seam.")
    if state.destroyed_boiler:
        typewriter("Locks are already soft from the power cut. Still — leverage on the beat.")
    else:
        typewriter("The magnets fight you. Lever only when the lock cycles — feel the hum drop.")
    pause(0.7)
    if not qte_debris_clear(key, bpm=52, windows=4):
        typewriter("The crowbar slips. The door seals harder, as if insulted.")
        state.change_health(-5)
        state.change_sanity(-5)
        scene_stairwell(state)
        return
    state.upper_debris_cleared = True
    state.upper_unlocked = True
    state.collected_all_echoes += 1
    add_trait(state, "resolve", 2)
    add_trait(state, "courage", 1)
    state.add_note("Forced the upper powered door open with the crowbar.")
    typewriter("Metal screams. The upper corridor exhales dust and old polish.")
    pause(0.8)
    scene_upper_landing(state)



def scene_office(state):
    clear()
    play_ambient("hallway", state.sanity)
    show(ART_OFFICE)
    state.advance_time()
    speak_room("office")

    while True:
        options = [
            "Read the entire patient file",
            "Search the desk drawers",
            "Look at the photograph on the desk",
            "Burn the patient file in the lamp",
            "Examine the noose behind the door",
            "Leave back to the Nurse Station",
        ]
        choice = get_choice("What do you examine?", options, state)

        if choice == 0:
            if state.read_patient_file:
                typewriter("You already know. The words still hurt.")
                continue
            typewriter("'Patient claims the staff died in 1973. Patient is incorrect.'")
            pause(1.3)
            typewriter("'Patient answers to the name of the night orderly who vanished.'")
            pause(1.3)
            typewriter("The final note is in your handwriting.")
            state.read_patient_file = True
            state.knows_true_identity = True
            state.collected_all_echoes += 1
            state.nurse_hostility += 2
            state.change_sanity(-15)
            state.add_note("Read Patient File 47 - you are the vanished orderly.")
            set_truth_path(state, "orderly")
            mark_world(state, "read_file")
            if state.can_jumpscare():
                time.sleep(1.2)
                jumpscare_fake_crash()
                state.register_jumpscare()

        elif choice == 1:
            if state.has_master_key:
                typewriter("Nothing new in the drawers.")
                continue
            typewriter("Master key. Unmarked pills. A cassette tape.")
            state.has_pills = True
            state.has_tape = True
            state.has_master_key = True
            state.collected_all_echoes += 1
            state.add_note("Found master key, pills, and a cassette in the Director's desk.")
            mark_world(state, "desk_loot")
            listen = get_choice("Play the cassette?", ["Yes", "Not now"])
            if listen == 0:
                typewriter("Your voice describes the fire. Going back for the children. Then her laugh.")
                state.listened_to_tape = True
                state.collected_all_echoes += 1
                state.change_sanity(-8)
                state.add_note("Listened to the cassette - your past self and her laughter.")
                mark_world(state, "heard_tape")
                set_truth_path(state, "orderly", quiet=True)

        elif choice == 2:
            if state.has_photo:
                typewriter("Still your face in the back of the staff photo.")
                continue
            typewriter("Staff party. Everyone smiles. The man in the back stares at the camera. Your face.")
            state.has_photo = True
            state.collected_all_echoes += 1
            state.add_note("Found a staff photo - the man in back has your face.")
            mark_world(state, "saw_photo")
            if state.change_sanity(-10):
                game_over("Recognition was fatal.", state)
                return

        elif choice == 3:
            if not state.read_patient_file:
                typewriter("You have not even read it yet.")
                continue
            typewriter("Pages blacken. The name 47 curls into ash.")
            state.burned_the_file = True
            add_trait(state, "detachment", 2)
            add_trait(state, "defiance", 1)
            state.knows_true_identity = False
            state.change_sanity(+12)
            state.add_note("Burned Patient File 47 - chose to forget.")
            set_truth_path(state, "burn")
            mark_world(state, "burned_file")

        elif choice == 4:
            typewriter("A noose hangs from the coat hook behind the door. Old rope. Careful knot.")
            pause(0.8)
            use = get_choice("What do you do?", [
                "Put your head through and step off the chair",
                "Leave the rope alone",
            ], state)
            if use == 0:
                typewriter("The lamp swings.")
                pause(1.0)
                run_type_step_chain(ROPE_TYPE_STEPS)
                ending_the_rope(state)
                return
            else:
                typewriter("You step back. The knot does not untie itself.")
                add_trait(state, "resolve", 1)
                state.add_note("Found the noose in the Director's office and walked away.")

        else:
            if enter_room(state, "nurse_station"):
                return
            scene_nurse_station(state)
            return


def scene_basement(state):
    clear()
    play_ambient("basement", state.sanity)
    show(ART_BASEMENT)
    state.advance_time()
    speak_room("basement")
    if not state.has_flashlight and state.time_of_night > 2:
        typewriter("Almost completely dark.")
        state.change_sanity(-6)

    while True:
        options = [
            "Approach the cage",
            "Inspect / sabotage the boiler",
            "Search the dark corners",
            "Enter the maintenance tunnel",
            "Open the door to the Morgue",
            "Climb back to the Stairwell",
        ]
        choice = get_choice("What do you do?", options)

        if choice == 0:
            clear()
            play_ambient("tension", state.sanity)
            show(ART_SHADOW)
            typewriter("A perfect copy of you sits inside. It speaks with your voice.")
            state.add_note("Confronted the shadow self in the basement cage.")
            if getattr(state, "prior_truths", None) and len(state.prior_truths) >= 2:
                typewriter('"You already said that in another file," it murmurs.')
                state.shadow_awareness += 1
            state.shadow_location = "basement"

            def _merge(s):
                s.accepted_merge_temptation = True
                s.checkpoint("merge_accept", "Accepted the merge")

            def _reject(s):
                if qte_rhythm_chain():
                    typewriter("You slam the cage shut.")
                    s.destroyed_boiler = True
                else:
                    game_over("You became the one in the cage.", s)

            def _ask(s):
                typewriter('"It costs the child. Or it costs you. There is no third price."')
                s.trusted_the_voice = True

            def _locket(s):
                if s.has_locket:
                    typewriter("It recoils from the locket. The bars frost.")
                    s.child_favor += 1
                else:
                    typewriter("Empty hands. It laughs with your laugh.")

            def _confess(s):
                typewriter("You list every choice since the mattress. It listens like a priest.")
                s.confessed_to_shadow = True
                s.shadow_awareness += 2
                s.checkpoint("confess", "Confessed to the shadow")
                s.add_achievement("shadow_confess", "Confession in the Dark")

            if state.knows_true_identity or state.saw_own_body:
                state.saw_future_self = True
                nodes = {
                    "start": {
                        "text": ['"You finally remember. Now we can stop being lonely."'],
                        "options": [
                            ("Ask what it wants", "wants", None),
                            ("Confess what you have done", "after", _confess),
                            ("Reject it violently", None, _reject),
                            ("Accept the merge", None, _merge),
                        ],
                    },
                    "wants": {
                        "text": ['"I want the night to stop resetting without me."'],
                        "options": [
                            ("Ask how to truly leave", "after", _ask),
                            ("Offer the locket", "after", _locket),
                            ("Accept the merge", None, _merge),
                            ("Back away", None, None),
                        ],
                    },
                    "after": {
                        "text": ['"Go. Or stay. Both are a kind of truth."'],
                        "options": [
                            ("Leave the cage", None, None),
                            ("Accept the merge after all", None, _merge),
                        ],
                    },
                }
                dialogue_tree(state, "The copy waits.", nodes)
                if state.accepted_merge_temptation:
                    if not confirm_run_end(
                        [
                            "The copy opens its hands. Two sets of memories cannot both keep a body.",
                            "If you take this, the run ends as one voice. There is no undoing the join.",
                        ],
                        "set us free",
                        'Type anything else to leave the cage closed.',
                    ):
                        state.accepted_merge_temptation = False
                        add_trait(state, "resolve", 1)
                    else:
                        ending_merge(state)
                        return
            else:
                typewriter('"Still pretending. Open the drawer. Then come back."')
                state.change_sanity(-12)
                if state.can_jumpscare():
                    jumpscare_shadow()
                    state.register_jumpscare()

        elif choice == 1:
            typewriter("Valves. A maintenance hatch.")
            if state.has_syringe or state.has_pills:
                if qte_growing_window("f", 1.8):
                    typewriter("The boiler dies permanently.")
                    state.destroyed_boiler = True
                    state.sabotaged_power = True
                    state.add_note("Sabotaged the basement boiler.")
                    mark_world(state, "sabotaged_boiler")
                    typewriter("Somewhere above, a magnetic lock loses its argument.")
                else:
                    typewriter("Steam explodes.")
                    state.change_health(-18)
            else:
                typewriter("You lack tools to force a shutdown.")

        elif choice == 2:
            typewriter("A handprint in the soot. Torn nightgown. Crayon note: 'She lies when she smiles.'")
            state.child_favor += 1
            state.has_note = True
            if not state.has_bolt_cutters:
                typewriter("Also: bolt cutters behind a shelf.")
                state.has_bolt_cutters = True
            state.add_note("Found crayon note and bolt cutters in the basement.")

        elif choice == 3:
            tunnel = get_choice("Enter the tunnel?", ["Yes", "Not yet"])
            if tunnel == 0:
                attempt_escape(state, from_tunnel=True)
                return

        elif choice == 4:
            scene_morgue(state)
            return

        else:
            scene_stairwell(state)
            return


def scene_morgue(state):
    clear()
    play_ambient("morgue", state.sanity)
    show(ART_MORGUE)
    state.advance_time()
    typewriter("The cold here is personal.")
    pause(1.1)
    state.add_note("Entered the Morgue. Drawer 47 is freshly labeled.")

    while True:
        options = [
            "Open drawer 47",
            "Open a random other drawer",
            "Search the instruments table",
            "Read the tags carefully",
            "Return to the Basement",
        ]
        choice = get_choice("What do you do?", options)

        if choice == 0:
            if state.saw_own_body:
                typewriter("You already opened it.")
                continue
            typewriter("You pull it open slowly...")
            pause(2.0)
            if state.can_jumpscare():
                jumpscare_face(context="body")
                state.register_jumpscare()
            show(ART_SHADOW)
            typewriter("A body that looks exactly like you - older. Its eyes open.")
            state.saw_own_body = True
            state.knows_true_identity = True
            state.collected_all_echoes += 1
            state.add_note("Opened drawer 47 - saw older self; eyes opened.")
            set_truth_path(state, "orderly")
            mark_world(state, "opened_drawer47")
            if state.change_sanity(-25):
                game_over("Understanding killed you.", state)
                return
            react = get_choice("It reaches for you.", [
                "Break free (inverted QTE)",
                "Speak to it",
                "Slam the drawer and run",
            ])
            if react == 0:
                if not qte_inverted("It grabs your wrist!", "w", "s", 3.0):
                    if state.change_health(-22):
                        game_over("The drawer closed on you.", state)
                        return
            elif react == 1:
                typewriter('"To stop being alone. Let me in."')
                state.accepted_merge_temptation = True
                add_trait(state, "detachment", 1)
                add_trait(state, "curiosity", 1)
            typewriter("A silver locket falls from its hand.")
            state.has_locket = True

        elif choice == 1:
            typewriter("A child's body. Eyes sewn with black thread.")
            state.add_note("Opened a child's drawer in the morgue.")
            opts = ["Listen (whisper filter)", "Close the drawer"]
            if getattr(state, "asked_cut_thread", False) and not getattr(state, "cut_black_thread", False):
                opts.insert(1, "Cut the black thread")
            elif not getattr(state, "cut_black_thread", False) and not state.freed_ghost_child:
                # still allow cut if they somehow know - only after she asked
                pass
            sub = get_choice("What do you do?", opts, state)
            if sub == 0:
                if not state.freed_ghost_child:
                    if qte_whisper_filter("HELP"):
                        state.child_favor += 2
                        typewriter("The thread loosens slightly.")
                    else:
                        state.change_sanity(-15)
                        state.shadow_awareness += 2
            elif getattr(state, "asked_cut_thread", False) and not getattr(state, "cut_black_thread", False) and sub == 1:
                typewriter("You work the black thread free. It is cold and thin as a lie.")
                pause(0.6)
                typewriter("Somewhere far above, a chapel breath she did not know she was holding leaves.")
                state.cut_black_thread = True
                state.freed_ghost_child = True
                state.child_fate = "freed"
                state.child_favor += 4
                state.collected_all_echoes += 1
                add_trait(state, "selflessness", 2)
                add_trait(state, "compassion", 2)
                state.add_achievement("free_child", "Freed the Child")
                state.add_note("Cut the black thread in the child's morgue drawer.")
                keep_promise(state, "child")
                if state.promised_child or getattr(state, "prior_child_betrayed", False):
                    restore_trust(state, "child")
                mark_world(state, "cut_thread")
                mark_world(state, "freed_child")
                set_truth_path(state, "child_rest", quiet=True)
            else:
                typewriter("You close the drawer. The thread stays.")

        elif choice == 2:
            if not state.has_syringe:
                typewriter("A syringe still full of something cloudy.")
                take = get_choice("Take it?", ["Yes", "Leave it"])
                if take == 0:
                    state.has_syringe = True
                    state.add_note("Took a cloudy syringe from the morgue.")

        elif choice == 3:
            typewriter("Drawer 12: 'Transferred - do not open - order of the Night Nurse'")
            state.collected_all_echoes += 1

        else:
            scene_basement(state)
            return


def scene_chapel(state):
    clear()
    play_ambient("chapel", state.sanity)
    show(ART_CHAPEL)
    state.current_room = "chapel"
    state.advance_time()
    state.entered_chapel = True

    if state.child_fate in ("freed", "hidden", "courtyard"):
        typewriter("The altar is empty. Dust motes where she sat.")
        options = ["Search under the altar", "Pray", "Leave to Stairwell"]
        choice = get_choice("What do you do?", options, state)
        if choice == 0:
            typewriter("A loose board. A folded paper: THANK YOU in a child's hand.")
            state.collected_all_echoes += 1
            scene_chapel(state)
            return
        elif choice == 1:
            typewriter("The cross creaks. You feel slightly less alone.")
            state.prayed_in_chapel = True
            state.change_sanity(+3)
            scene_chapel(state)
            return
        else:
            if enter_room(state, "stairwell"):
                return
            scene_stairwell(state)
            return

    typewriter("At the altar sits a little girl in a stained nightgown.")
    show(ART_CHILD)
    if getattr(state, "prior_child_betrayed", False):
        typewriter('"You left," she says flatly. "You always leave when it matters."')
        state.child_favor -= 2
        state.add_note("Child remembers prior betrayal — trust is broken.")
        commentary("NG+: prior betrayal hardens the child.")
    elif getattr(state, "prior_child_saved", False):
        typewriter('"You came back," she says. "Most versions do not."')
        state.child_favor += 1
    else:
        typewriter('"They keep putting me back," she says.')
    state.add_note("Met the girl in the chapel.")
    try_flashback(state, "promised_child", [
        "You already promised her in another life of this night.",
        "She is waiting to see which version of you shows up.",
    ])

    def _listen(s):
        typewriter("She tells of the fire. The orderly. The nurse who locked the exits.")
        typewriter('Quieter: "Down in the cold drawers... something black is sewn shut."')
        typewriter('"If the thread goes, maybe I do not have to come back."')
        s.asked_cut_thread = True
        s.collected_all_echoes += 1
        s.child_favor += 2
        s.add_note("Heard the child's story; she hinted about black thread in the morgue.")
        if "child_story" not in s.dialogue_flags:
            s.dialogue_flags.append("child_story")

    def _promise(s):
        typewriter('"I promise." Her hand is cold.')
        s.promised_child = True
        s.child_favor += 2
        s.checkpoint("promised_child", "Promised the child")
        s.add_note("Promised the child.")
        add_trait(s, "honesty", 2)
        add_trait(s, "compassion", 1)
        if getattr(s, "prior_child_betrayed", False):
            typewriter("She watches your mouth like she is weighing an old lie.")
            restore_trust(s, "child")

    def _lie(s):
        typewriter('"Help is coming." Her eyes go dull.')
        s.lied_to_child = True
        s.child_favor -= 3
        s.child_fate = "lied"
        add_trait(s, "deceit", 2)
        add_trait(s, "selfishness", 1)
        add_trait(s, "broken_promises", 1)
        s.promises_broken = getattr(s, "promises_broken", 0) + 1
        s.checkpoint("lied_child", "Lied to the child")
        s.add_note("Lied to the child.")
        mark_world(s, "broke_child")
        typewriter("Something in the walls shifts — a promise unkept has weight here.")

    def _free(s):
        typewriter("You take her hand. Warm light. She is gone — free.")
        s.freed_ghost_child = True
        s.child_fate = "freed"
        s.child_favor += 4
        add_trait(s, "selflessness", 2)
        add_trait(s, "compassion", 2)
        add_trait(s, "honesty", 1)
        s.collected_all_echoes += 1
        s.add_achievement("free_child", "Freed the Child")
        s.add_note("Freed the chapel child.")
        keep_promise(s, "child")
        mark_world(s, "freed_child")
        set_truth_path(s, "child_rest", quiet=True)

    def _hide(s):
        typewriter("You guide her under the altar boards. Stay quiet until the humming stops.")
        s.child_fate = "hidden"
        s.child_favor += 2
        s.freed_ghost_child = True  # counts as saved-from-rounds
        s.add_achievement("hide_child", "Hid the Child")
        s.add_note("Hid the child under the altar.")
        mark_world(s, "hid_child")
        keep_promise(s, "child")

    def _courtyard_plan(s):
        typewriter('"When I open the courtyard, come with me." She nods once.')
        s.child_fate = "courtyard"
        s.promised_child = True
        s.child_favor += 3
        s.checkpoint("promised_child", "Promised courtyard escape")
        s.add_note("Planned to take child through courtyard.")
        s.add_achievement("courtyard_child", "Courtyard Promise")
        mark_world(s, "promised_courtyard")
        if getattr(s, "prior_child_betrayed", False):
            typewriter('"If you mean it this time," she says, "I will not hold the latch against you."')
            restore_trust(s, "child")

    def _offer(s):
        if s.has_locket:
            typewriter("She holds the locket. Real smile. Light. Gone.")
            s.freed_ghost_child = True
            s.child_fate = "freed"
            s.child_favor += 5
            s.has_locket = False
            s.add_achievement("free_child", "Freed the Child")
            mark_world(s, "freed_child")
            keep_promise(s, "child")
        elif s.has_holy_water:
            typewriter("She softens. That helps a little.")
            s.child_favor += 2
        elif s.has_photo:
            typewriter("Black tears on the photo.")
            s.change_sanity(-8)
        else:
            typewriter("Nothing she wants right now.")
            s.child_favor -= 1

    nodes = {
        "start": {
            "text": ['"Are you going to leave me too?"'],
            "options": [
                ("Sit and listen to everything", "after_listen", _listen),
                ("Offer an item", "start", _offer),
                ("Talk about getting out", "escape_talk", None),
                ("Leave her for now", None, None),
            ],
        },
        "after_listen": {
            "text": ['"Choose differently than he did."'],
            "options": [
                ("Promise to free her", "end", _promise),
                ("Hide her under the altar", "end", _hide),
                ("Plan to take her through the courtyard", "end", _courtyard_plan),
                ("Lie that help is already coming", "end", _lie),
            ],
        },
        "escape_talk": {
            "text": ['"The nurse watches the stairs. The courtyard key is in the cold."'],
            "options": [
                ("Promise the courtyard path", "end", _courtyard_plan),
                ("Promise anything", "end", _promise),
                ("Admit you might fail", "end", None),
            ],
        },
        "end": {"text": [], "options": []},
    }
    dialogue_tree(state, "She waits.", nodes)

    # after dialogue, leave or stay menu
    while True:
        options = ["Speak with her again", "Pray at the altar", "Leave to Stairwell"]
        if state.child_fate in ("freed", "hidden", "courtyard", "lied"):
            options = ["Pray at the altar", "Leave to Stairwell"]
        choice = get_choice("What now?", options, state)
        label = options[choice]
        if label == "Speak with her again":
            dialogue_tree(state, "She waits.", nodes)
        elif label == "Pray at the altar":
            typewriter("Words you may not believe. The wood answers with a creak.")
            state.prayed_in_chapel = True
            state.change_sanity(+4)
            add_trait(state, "faith", 2)
            state.checkpoint("prayed", "Prayed in chapel")
        else:
            if enter_room(state, "stairwell"):
                return
            scene_stairwell(state)
            return


def scene_laundry(state):
    clear()
    play_ambient("laundry", state.sanity)
    show(ART_LAUNDRY)
    state.advance_time()
    state.explored_laundry = True
    speak_room("laundry")
    state.add_note("Entered the Laundry through the west wing.")

    while True:
        options = [
            "Search the lockers",
            "Check inside the machines",
            "Look for staff uniforms",
            "Continue through to the Kitchen",
            "Return to the East Corridor",
        ]
        choice = get_choice("What do you do?", options)

        if choice == 0:
            typewriter("A note: 'If you find this, the nurse is not what she seems.'")
            typewriter("Locker dust: a staff badge half-melted. No cutters here — those live below.")
            state.collected_all_echoes += 1
            add_trait(state, "curiosity", 1)
            state.add_note("Found bolt cutters and a warning note in laundry lockers.")

        elif choice == 1:
            if not state.has_crowbar:
                typewriter("Behind a rusted drum: a crowbar, still black with soot from the fire.")
                state.has_crowbar = True
                add_trait(state, "curiosity", 1)
                state.add_note("Found a crowbar in the laundry machines.")
                typewriter("Heavy. Good for debris — or worse.")
            else:
                typewriter("Lint, a child's sock, the sense of being watched.")
                state.change_sanity(-5)

        elif choice == 2:
            if state.has_uniform:
                typewriter("Already took one.")
                continue
            take = get_choice("Put on the orderly uniform?", ["Yes", "No"])
            if take == 0:
                state.has_uniform = True
                state.took_the_uniform = True
                add_trait(state, "obedience", 2)
                state.helped_nurse = True
                state.add_note("Put on a staff orderly uniform.")
                typewriter("It fits too perfectly.")
                mark_world(state, "wore_uniform")
                set_truth_path(state, "staff", quiet=True)

        elif choice == 3:
            scene_kitchen(state)
            return

        else:
            scene_east_corridor(state)
            return


def scene_kitchen(state):
    clear()
    play_ambient("kitchen", state.sanity)
    show(ART_KITCHEN)
    state.advance_time()
    state.explored_kitchen = True
    speak_room("kitchen")

    while True:
        options = [
            "Search cupboards",
            "Examine the freezer",
            "Pick up the kitchen knife on the counter",
            "Go out to the Courtyard (if unlocked)",
            "Return to the Laundry",
        ]
        choice = get_choice("What do you do?", options, state)

        if choice == 0:
            typewriter("Behind a false panel: a bottle labeled 'Holy Water - Chapel'.")
            state.has_holy_water = True
            state.add_note("Found hidden holy water in the kitchen.")

        elif choice == 1:
            if state.has_bolt_cutters:
                typewriter("Chain cut. Inside: transfer files and a key ring stamped COURTYARD.")
                # Courtyard key only — west wing still needs staff route / nurse keys
                state.opened_courtyard = True
                state.collected_all_echoes += 1
                add_trait(state, "curiosity", 1)
                state.add_note("Opened the kitchen freezer - found courtyard key.")
            else:
                typewriter("Chain too thick. You need bolt cutters from elsewhere.")

        elif choice == 2:
            typewriter("A chef's knife. Edge dark with something that is not rust.")
            pause(0.7)
            use = get_choice("What do you do?", [
                "Use it on yourself",
                "Put it back on the counter",
            ], state)
            if use == 0:
                typewriter("The kitchen tiles come up to meet you.")
                pause(1.0)
                run_type_step_chain(BLADE_TYPE_STEPS)
                ending_the_blade(state)
                return
            else:
                typewriter("You set the knife down. The counter takes it without comment.")
                add_trait(state, "resolve", 1)
                state.add_note("Picked up the kitchen knife and put it back.")

        elif choice == 3:
            if state.opened_courtyard:
                if enter_room(state, "courtyard"):
                    return
                scene_courtyard(state)
                return
            typewriter("The outer door is still chained from this side.")

        else:
            if enter_room(state, "laundry"):
                return
            scene_laundry(state)
            return


def scene_courtyard(state):
    clear()
    play_ambient("courtyard", state.sanity)
    show(ART_COURTYARD)
    state.advance_time()
    typewriter("Real night air. You had almost forgotten.")
    state.add_note("Reached the overgrown courtyard.")

    options = [
        "Try the outer gate",
        "Search the fountain",
        "Go back inside through the Kitchen",
    ]
    choice = get_choice("What do you do?", options)
    if choice == 0:
        attempt_escape(state, from_courtyard=True)
    elif choice == 1:
        typewriter("A small metal cross and a faded photo of children.")
        state.collected_all_echoes += 1
        state.child_favor += 1
        scene_courtyard(state)
    else:
        scene_kitchen(state)


def scene_staff_wing(state):
    clear()
    play_ambient("staff_wing", state.sanity)
    show(ART_STAFF_WING)
    state.current_room = "staff_wing"
    state.advance_time()
    state.explored_patient_wing = True
    typewriter("Staff Only unlocks. Break room frozen in 1973. Calendar still shows the week of the fire.")
    state.add_note("Entered the Staff Wing.")
    pause(1.4)

    options = [
        "Search the break room",
        "Enter the Archives room",
        "Leave to the Nurse Station",
    ]
    choice = get_choice("Where?", options)
    if choice == 0:
        typewriter("Logbook - last entry is yours. A clean uniform on a chair.")
        state.collected_all_echoes += 1
        take = get_choice("Take the uniform?", ["Yes", "No"])
        if take == 0:
            state.has_uniform = True
            state.took_the_uniform = True
            state.helped_nurse = True
        if state.nurse_hostility >= 2 or (state.knows_true_identity and random.random() < 0.55):
            encounter_nurse(state)
        else:
            scene_nurse_station(state)
    elif choice == 1:
        scene_archives(state)
    else:
        scene_nurse_station(state)


def scene_archives(state):
    clear()
    play_ambient("archives", state.sanity)
    show(ART_ARCHIVES)
    state.advance_time()
    state.explored_archives = True
    typewriter("Rows of yellowed folders. Dust thick enough to write in.")
    state.add_note("Searched the Archives.")

    while True:
        options = [
            "Search files about the 1973 fire",
            "Look up Patient 47 / the night orderly",
            "Examine transfer logs",
            "Leave to the Staff Wing",
        ]
        choice = get_choice("Research?", options)
        if choice == 0:
            typewriter("Official: electrical fire. Margin note: 'Not electrical. She locked the exits.'")
            state.collected_all_echoes += 1
            state.nurse_hostility += 1
            state.add_note("Archives: fire was not electrical - exits locked.")
        elif choice == 1:
            typewriter("Orderly file nearly empty. 'Subject insists the patients are already dead.'")
            state.collected_all_echoes += 1
            state.add_note("Archives: orderly file redacted; insisted patients were already dead.")
            if not state.knows_true_identity:
                state.change_sanity(-6)
        elif choice == 2:
            typewriter("Logs end 1974. Every name after a date is in the Nurse's hand.")
            state.collected_all_echoes += 1
        else:
            scene_staff_wing(state)
            return


def resume_scene(state, room_id=None):
    """Return player to a room scene after an interruption (Nurse fight, etc.)."""
    room_id = room_id or getattr(state, "current_room", None) or "east_corridor"
    mapping = {
        "cell": scene_cell,
        "east_corridor": scene_east_corridor,
        "nurse_station": scene_nurse_station,
        "stairwell": scene_stairwell,
        "office": scene_office,
        "basement": scene_basement,
        "morgue": scene_morgue,
        "chapel": scene_chapel,
        "laundry": scene_laundry,
        "kitchen": scene_kitchen,
        "courtyard": scene_courtyard,
        "staff_wing": scene_staff_wing,
        "archives": scene_archives,
        "side_cell": scene_side_cell,
        "upper_landing": scene_upper_landing,
        "upper_corridor": scene_upper_corridor,
        "gallery": scene_gallery,
        "therapy_b": scene_therapy_b,
        "records_annex": scene_records_annex,
        "dir_suite": scene_director_suite,
        "roof": scene_roof,
    }
    fn = mapping.get(room_id, scene_east_corridor)
    fn(state)


def nurse_body_in(state, room_id):
    """True only if she was physically killed in this room (not banished)."""
    return (
        getattr(state, "killed_nurse", False)
        and not getattr(state, "nurse_banished", False)
        and getattr(state, "nurse_death_room", None) == room_id
    )


def encounter_nurse(state):
    if state.killed_nurse:
        typewriter("She is already gone. The halls know it.")
        return
    origin = getattr(state, "current_room", None) or "east_corridor"
    clear()
    play_ambient("tension", state.sanity)
    show(ART_NURSE)
    typewriter('She is suddenly there. "You shouldn\'t be out of your room, dear."')
    state.add_note("Encountered the Night Nurse.")
    pause(1.3)
    if state.has_uniform:
        typewriter("Her eyes flick over the uniform. Something like approval.")
        state.nurse_hostility -= 1
    if getattr(state, "prior_mercy", False) and state.nurse_hostility <= 3:
        typewriter("For a heartbeat her face softens — as if she remembers a kindness from another night.")
        state.nurse_hostility -= 1
        commentary("NG+: prior mercy softens the Nurse slightly.")
    if state.can_jumpscare():
        time.sleep(0.9)
        jumpscare_nurse()
        state.register_jumpscare()
        state.change_sanity(-5)

    # Choice loop — empty offer re-prompts without recursion
    while True:
        options = [
            "Try to talk / reason",
            "Attack",
            "Run",
            "Offer pills or syringe",
            "Show mercy - lower your hands",
        ]
        choice = get_choice("How do you respond?", options)

        if choice == 0:
            if state.knows_true_identity and state.collected_all_echoes >= 4:
                typewriter("You speak the name she buried. She freezes, then walks into a wall and vanishes.")
                state.killed_nurse = True
                state.nurse_banished = True
                state.nurse_death_room = None
                state.add_note("Banished the Nurse by speaking her buried name — no body left behind.")
                mark_world(state, "banished_nurse")
                mark_world(state, "killed_nurse")
                typewriter("The smile is gone. There is nothing on the floor.")
                resume_scene(state, origin)
            else:
                if qte_double_tap("d", 0.65):
                    typewriter("You sidestep the syringe.")
                    resume_scene(state, origin)
                else:
                    if state.has_pills:
                        typewriter("Needle hits - pills weaken the dose.")
                        state.change_health(-15)
                        state.took_medication = True
                        resume_scene(state, origin)
                    else:
                        game_over("The world softens. You are very obedient now.", state)
            return

        elif choice == 1:
            if qte_rhythm_chain():
                typewriter("She staggers. Face cracks like porcelain. Black fluid.")
                state.killed_nurse = True
                state.nurse_banished = False
                state.nurse_death_room = origin
                state.change_sanity(-10)
                mark_world(state, "killed_nurse")
                typewriter("She falls where she stood. The smile leaves the halls with her.")
                state.add_note(f"Killed the Night Nurse in: {origin}.")
                resume_scene(state, origin)
            else:
                game_over("Her cold hand on your cheek was the last thing you felt.", state)
            return

        elif choice == 2:
            if qte_growing_window(" ", 1.8):
                typewriter("You burst away.")
                state.change_health(-6)
                resume_scene(state, origin)
            else:
                game_over("She catches your arm. Final.", state)
            return

        elif choice == 3:
            if state.has_pills or state.has_syringe:
                if state.helped_nurse or state.took_the_uniform or state.mercy_on_nurse:
                    typewriter("She accepts it. Almost human for a moment.")
                    state.helped_nurse = True
                    state.nurse_hostility -= 3
                else:
                    typewriter("She drives it into your shoulder.")
                    state.change_health(-30)
                resume_scene(state, origin)
                return
            typewriter("Nothing to offer. Empty hands.")
            pause(0.5)
            typewriter("She is still there. Choose again.")
            pause(0.35)
            # loop continues — no recursive call

        else:
            typewriter('"I don\'t want to fight you."')
            state.mercy_on_nurse = True
            state.nurse_hostility -= 2
            add_trait(state, "mercy", 2)
            add_trait(state, "compassion", 1)
            state.checkpoint("mercy_nurse", "Showed mercy to Nurse")
            state.add_note("Showed mercy to the Nurse.")
            if state.nurse_hostility <= 0 and state.collected_all_echoes >= 3:
                typewriter('"Go. Before I remember why I stay."')
                resume_scene(state, origin)
            else:
                if qte_double_tap("a", 0.7):
                    typewriter("You slip past.")
                    resume_scene(state, origin)
                else:
                    game_over("Mercy was not enough this time.", state)
            return



def _escape_fail(state, route, msg):
    typewriter(msg)
    state.change_sanity(-8)
    state.change_health(-6)
    return False



def run_physical_escape_gauntlet(state, route="gates"):
    """6 consecutive intense QTEs (type + hold + heartbeat) flavored per escape route.
    Returns True if all pass. Roof failures are handled by the caller (The Fall)."""
    key = qte_primary_key()
    play_ambient("tension", state.sanity)

    # (kind, ascii_title, flavor, ...params)
    # type:  phrase, timeout
    # hold:  key, hold_s, timeout
    # heart: key, bpm, windows
    if route == "gates":
        steps = [
            ("type", "WARD MARK", "Rust covers the plaque. Type the words to wake the lock.", "OPEN THE GATE", 8.0),
            ("hold", "IRON BAR", "Both hands on cold iron. Lean until it gives.", key, 1.0, 4.2),
            ("heart", "PATROL", "Match the old guard beat in your chest.", key, 58, 4),
            ("type", "NIGHT CODE", "A second plate under the first. Type the night code.", "BLACKWOOD-OUT", 8.0),
            ("hold", "LAST LINK", "One chain link left. Crush it in your grip.", key, 1.15, 4.5),
            ("heart", "SPRINT", "The yard is short. Do not look back.", key, 66, 5),
        ]
    elif route == "tunnel":
        steps = [
            ("type", "STENCIL", "Pipe letters in the dark. Read them with your hands.", "CRAWL FORWARD", 8.0),
            ("hold", "GRATE", "Shoulder the grate. Push until the bolt shears.", key, 1.05, 4.3),
            ("heart", "DRIP", "Water on your neck — keep time with the dark.", key, 56, 4),
            ("type", "SERVICE", "Older stencil. Type the service route.", "FOLLOW THE PIPES", 9.0),
            ("hold", "PINCH", "Squeeze through. Do not exhale early.", key, 1.2, 4.6),
            ("heart", "DAYLIGHT", "Light at the mouth. Match its pulse.", key, 70, 5),
        ]
    elif route == "courtyard":
        steps = [
            ("type", "IVY LATCH", "Ivy has grown through the latch words. Type them free.", "BREATHE THE NIGHT", 9.0),
            ("hold", "GATE BAR", "Haul the bar. It has not moved since the fire.", key, 1.1, 4.4),
            ("heart", "NIGHT AIR", "Real air is too fast. Steady your lungs.", key, 60, 4),
            ("type", "FOUNTAIN", "Half-worn engraving on the stone.", "LEAVE THIS PLACE", 9.0),
            ("hold", "OUTER LOCK", "The outer gate still wants a staff hand.", key, 1.0, 4.2),
            ("heart", "OUTRUN", "Memory howls behind you. Keep moving.", key, 68, 5),
        ]
    else:  # roof
        steps = [
            ("type", "STENCIL", "Paint under your palm. Type what it says.", "HOLD FAST", 7.0),
            ("hold", "LEDGE", "Fingers locked on the ledge. Do not let the drop win.", key, 1.1, 4.3),
            ("heart", "BRICK", "Dust in your mouth. Climb on the beat.", key, 64, 4),
            ("type", "OLD PAINT", "Older letters further down the wall.", "BLACKWOOD-OUT", 8.0),
            ("hold", "IVY", "The vine tears. Hold through it.", key, 1.2, 4.5),
            ("heart", "GROUND", "Last stretch of brick. The ground is close.", key, 74, 5),
        ]

    for n, step in enumerate(steps, 1):
        print()
        kind = step[0]
        title = step[1]
        flavor = step[2]
        print_big(title)
        typewriter(flavor)
        pause(0.35)
        countdown_before_qte(3.0, label=title)
        ok = False
        if kind == "type":
            phrase, to = step[3], step[4]
            ok = qte_type_phrase(phrase, timeout=to)
        elif kind == "heart":
            k, bpm, windows = step[3], step[4], step[5]
            ok = qte_heartbeat(k, bpm=bpm, windows=windows)
        elif kind == "hold":
            k, hold_s, to = step[3], step[4], step[5]
            print_big(f"HOLD [{k.upper()}]")
            ok = hold_keypress(f"  HOLD [{k.upper()}]: ", k, hold_seconds=hold_s, timeout=to)
        if not ok:
            return False
        play_success()
        typewriter("Still moving...")
        pause(0.25)

    return True



def attempt_escape(state, from_tunnel=False, from_courtyard=False):
    clear()
    play_ambient("tension", state.sanity)
    show(ART_ESCAPE)
    state.advance_time()
    state.gate_attempts = getattr(state, "gate_attempts", 0) + 1
    typewriter("The gates stand before you.")
    pause(1.2)

    # Promise debt: if you promised the child and did not follow through
    promised = state.promised_child or getattr(state, "child_fate", None) == "courtyard"
    fulfilled = (
        state.freed_ghost_child
        or getattr(state, "child_fate", None) in ("freed", "hidden", "courtyard")
        and state.child_favor >= 2
    )
    # courtyard promise needs opened courtyard + favor
    if getattr(state, "child_fate", None) == "courtyard":
        fulfilled = state.opened_courtyard and state.child_favor >= 3
    if promised and state.lied_to_child:
        fulfilled = False

    if promised and not fulfilled and not getattr(state, "_promise_punished_at_gate", False):
        state._promise_punished_at_gate = True
        typewriter('A small voice behind the bars of your memory: "You said you would come back."')
        pause(0.8)
        break_promise(state, "child")
        typewriter("The gates frost over. Leaving will not be simple.")
        pause(1.0)

    can_leave = False
    reason = ""
    ending = None
    echoes = state.collected_all_echoes
    debt = promise_debt(state)

    # Harder thresholds — single conditions no longer enough
    if state.burned_the_file and echoes >= 4 and state.sanity >= 25:
        can_leave, reason, ending = True, "You burned the truth. Even so, the building claws at your sleeves.", "burned"
    elif state.knows_true_identity and state.saw_own_body and echoes >= 6 and state.sanity >= 30:
        can_leave, reason, ending = True, "Name and body agree. The locks hesitate.", "true"
    elif state.knows_true_identity and echoes >= 7 and state.killed_nurse:
        can_leave, reason, ending = True, "You remember — and she is gone. The way opens a crack.", "true"
    elif getattr(state, "child_fate", None) == "courtyard" and state.opened_courtyard and state.child_favor >= 4 and echoes >= 4:
        can_leave, reason, ending = True, "She is at the fountain. The outer lock answers both of you.", "true"
    elif state.freed_ghost_child and state.child_favor >= 6 and state.promised_child and debt == 0 and echoes >= 5:
        can_leave, reason, ending = True, "You kept your word. She opens what you cannot.", "true"
    elif state.freed_ghost_child and state.child_favor >= 5 and echoes >= 6 and debt == 0:
        can_leave, reason, ending = True, "The child opens the way — barely.", "true"
    elif state.destroyed_boiler and state.has_master_key and state.sabotaged_power and echoes >= 5:
        can_leave, reason, ending = True, "Power dead. Keys true. Still one more test.", "true"
    elif from_tunnel and state.trusted_the_voice and echoes >= 4 and state.shadow_awareness >= 2:
        can_leave, reason, ending = True, "The tunnel accepts a price already paid in trust.", "true"
    elif state.killed_nurse and state.sanity > 45 and echoes >= 5 and state.knows_true_identity:
        can_leave, reason, ending = True, "With her gone and your name intact, the binding thins.", "true"
    elif state.helped_nurse and state.took_the_uniform and state.mercy_on_nurse and echoes >= 4:
        can_leave, reason, ending = True, "She lets you leave — as one of them.", "staff"
    elif getattr(state, "confessed_to_shadow", False) and state.shadow_awareness >= 5 and echoes >= 5:
        can_leave, reason, ending = True, "Confession bought a narrow door.", "true"
    elif state.side_cell_mercy and getattr(state, "prayed_in_chapel", False) and state.child_favor >= 4 and echoes >= 5:
        can_leave, reason, ending = True, "Small mercies stack into a key.", "true"


    # Prior-run betrayal: she may sabotage child-based exits
    if can_leave and getattr(state, "prior_child_betrayed", False) and not getattr(state, "trust_restored_child", False) and ending == "true":
        if state.freed_ghost_child or getattr(state, "child_fate", None) in ("courtyard", "freed", "hidden"):
            typewriter('A small hand pulls the latch the wrong way. "Not this time."')
            can_leave = False
            state.change_sanity(-10)
            state.add_note("Child sabotaged the gate — payback for prior betrayal.")
            add_trait(state, "deceit", 1)  # world deceives you

    # Broken promises make the gate refuse unless strongly qualified
    if can_leave and debt >= 1:
        if echoes < 7 or state.sanity < 40:
            typewriter("The bars remember what you promised. Not yet.")
            can_leave = False
            state.change_sanity(-5)
        else:
            typewriter("The bars remember — but you force the issue anyway. It costs you.")
            state.change_sanity(-10)
            state.change_health(-8)
            add_trait(state, "resolve", 1)
            add_trait(state, "deceit", 1)

    # Early attempts with almost nothing: hard no
    if state.gate_attempts <= 2 and echoes < 3 and not state.knows_true_identity:
        can_leave = False
        reason = ""

    if can_leave:
        typewriter(reason)
        pause(1.0)
        if from_tunnel:
            route = "tunnel"
            warn = [
                "The maintenance tunnel is a throat. Once you commit, the building will test every joint.",
                "Succeed and this attempt ends in open air. Fail and it spits you back — or worse.",
            ]
            phrase = "crawl into dark"
            deny = "Type anything else to back out of the tunnel mouth."
        elif from_courtyard:
            route = "courtyard"
            warn = [
                "The outer courtyard gate is honest iron. Night air is already on your tongue.",
                "If you force it now, this attempt ends beyond the walls — or the gate throws you back inside.",
            ]
            phrase = "breathe the night"
            deny = "Type anything else to stay in the courtyard a little longer."
        else:
            route = "gates"
            warn = [
                "The front gates will not open for hesitation.",
                "If you start the sequence, this attempt ends only when the iron decides — free, or dragged back.",
            ]
            phrase = "open the gate"
            deny = "Type anything else to step away from the bars."
        if not confirm_run_end(warn, phrase, deny):
            if from_tunnel:
                scene_basement(state)
            elif from_courtyard:
                scene_courtyard(state)
            else:
                scene_east_corridor(state)
            return
        pause(0.4)
        if not run_physical_escape_gauntlet(state, route=route):
            if from_tunnel:
                typewriter("The tunnel spits you back into the basement dark.")
                state.change_sanity(-10)
                state.change_health(-8)
                scene_basement(state)
            elif from_courtyard:
                typewriter("The outer gate rejects you. Night air turns thin again.")
                state.change_sanity(-8)
                scene_courtyard(state)
            else:
                typewriter("The gates slam. Iron laughs.")
                state.change_sanity(-10)
                if state.can_jumpscare():
                    jumpscare_face("body")
                    state.register_jumpscare()
                scene_east_corridor(state)
            return

        add_trait(state, "courage", 2)
        add_trait(state, "resolve", 2)
        if ending == "burned":
            ending_burned_memory(state)
        elif ending == "staff":
            ending_become_staff(state)
        elif state.freed_ghost_child and state.promised_child and state.child_favor >= 6 and state.sanity < 50 and debt == 0:
            final = get_choice("The child cannot cross alone.", [
                "Push her through and stay",
                "Take her hand — both try",
                "Leave her",
            ], state)
            if final == 0:
                if not confirm_run_end(
                    [
                        "You stay. She goes. The gates only have room for one story tonight.",
                        "This attempt ends with you on the wrong side of the iron.",
                    ],
                    "push her through",
                    "Type anything else to choose differently.",
                ):
                    scene_east_corridor(state)
                    return
                add_trait(state, "selflessness", 3)
                ending_sacrifice(state)
            elif final == 1:
                if not confirm_run_end(
                    [
                        "Two hands on the latch. Either you both leave this attempt behind, or the night keeps you.",
                    ],
                    "take her hand",
                    "Type anything else to let go.",
                ):
                    scene_east_corridor(state)
                    return
                add_trait(state, "courage", 1)
                if echoes >= 7:
                    ending_true_escape(state)
                else:
                    ending_false_escape(state)
            else:
                if not confirm_run_end(
                    [
                        "You leave her voice on the latch. Freedom for one is a debt for the other.",
                        "This attempt ends on the far side of the gate.",
                    ],
                    "leave her behind",
                    "Type anything else to stay with her a moment longer.",
                ):
                    scene_east_corridor(state)
                    return
                add_trait(state, "selfishness", 2)
                if promised:
                    break_promise(state, "left_child_at_gate")
                ending_true_escape(state)
        else:
            data = load_folder() or {}
            if data.get("director_cut_unlocked") or SETTINGS.get("director_cut_unlocked"):
                pick = get_choice("The night offers a deeper door.", [
                    "Take the Director's Cut ending",
                    "Walk into the rain (True Escape)",
                ], state)
                if pick == 0:
                    if not confirm_run_end(
                        [
                            "The deeper door is not freedom. It is authorship. This attempt ends in the margins.",
                        ],
                        "open the deeper door",
                        "Type anything else to take the rain instead.",
                    ):
                        ending_true_escape(state)
                    else:
                        ending_directors_cut(state)
                else:
                    if not confirm_run_end(
                        [
                            "Rain on the far side of the gates. This attempt ends in weather, not walls.",
                        ],
                        "walk into the rain",
                        "Type anything else to hesitate at the threshold.",
                    ):
                        scene_east_corridor(state)
                        return
                    ending_true_escape(state)
            else:
                ending_true_escape(state)
            if SETTINGS.get("difficulty") == "hard":
                state.add_achievement("hard_escape", "Hard Escape")
    else:
        typewriter("The gates do not know you yet — or they know you too well.")
        mood = gate_temperature_line(state)
        if mood:
            typewriter(mood)
        else:
            vague = [
                "Something unfinished pulls at the hinges from the other side.",
                "The lock listens. It is not convinced.",
                "Whatever you are missing does not have a shape yet.",
            ]
            typewriter(random.choice(vague))
        pause(0.9)
        add_trait(state, "resolve", 1)
        if state.sanity < 20:
            ending_false_escape(state)
        elif from_tunnel:
            typewriter("The maintenance dark closes behind you.")
            scene_basement(state)
        else:
            scene_east_corridor(state)



def view_patient_folder():
    clear()
    data = load_folder()
    print(r"""
+==============================================================+
|              PATIENT  FILE   # 4 7                           |
|              BLACKWOOD ASYLUM - CONFIDENTIAL                 |
+==============================================================+
""")
    if not data:
        print("  No prior attempt recorded.")
        print("  Complete a run (escape or death) to fill this folder.\n")
        input("Press Enter...")
        main_menu()
        return

    runs = []
    if isinstance(data, dict):
        runs = list(data.get("runs") or [])
        if not runs and data.get("last"):
            runs = [data["last"]]
        if not runs and data.get("identity"):
            runs = [data]
    if not runs:
        print("  Folder empty.\n")
        input("Press Enter...")
        main_menu()
        return

    idx = len(runs) - 1  # start at most recent
    while True:
        clear()
        rec = runs[idx] if isinstance(runs[idx], dict) else {}
        print(r"""
+==============================================================+
|              PATIENT  FILE   # 4 7                           |
|              BLACKWOOD ASYLUM - CONFIDENTIAL                 |
+==============================================================+
""")
        print(f"  Attempt {idx + 1} of {len(runs)}")
        print("  " + "-" * 40)
        print(f"  Identity : {rec.get('identity', '-')}")
        print(f"  Ending   : {rec.get('ending', '-')}")
        print(f"  Sanity   : {rec.get('final_sanity', '-')}   Health: {rec.get('final_health', '-')}")
        print(f"  Echoes   : {rec.get('echoes', 0)}")
        pers = rec.get("personality")
        if isinstance(pers, list):
            pers = pers[0] if pers else None
        if pers:
            print(f"  Type     : {pers}")
        print()
        print("  BEHAVIORAL PROFILE")
        print("  ------------------")
        for line in rec.get("profile", []) or []:
            if isinstance(line, str) and line.startswith("PERSONALITY:"):
                continue
            print(f"  * {line}")
        print()
        notes = rec.get("notes", []) or []
        if notes:
            print("  ENCOUNTER NOTES")
            print("  ---------------")
            for n in notes:
                print(f"  - {n}")
            print()
        items = rec.get("items", []) or []
        if items:
            print("  Items: " + ", ".join(items))
            print()

        perm = (data.get("permanent_achievements") if isinstance(data, dict) else None) or []
        if perm and idx == len(runs) - 1:
            print("  PERMANENT MARKS")
            for a in perm:
                print(f"    * {a}")
            print()

        print("  [N] Next attempt   [P] Previous   [E] Export case file   [Enter] Menu")
        c = input("  ").strip().lower()
        if c == "n":
            idx = (idx + 1) % len(runs)
        elif c == "p":
            idx = (idx - 1) % len(runs)
        elif c == "e":
            pth = export_case_file()
            print(f"  Wrote {pth}" if pth else "  Export failed.")
            input("Press Enter...")
        else:
            main_menu()
            return




def debug_menu(state=None):
    """Comprehensive developer tools. Requires SETTINGS debug_mode."""
    if not SETTINGS.get("debug_mode"):
        print("  Debug mode is off.")
        time.sleep(0.6)
        return
    if state is None:
        state = State()
        load_ng_plus(state)

    rooms = [
        ("cell", scene_cell),
        ("east_corridor", scene_east_corridor),
        ("nurse_station", scene_nurse_station),
        ("office", scene_office),
        ("stairwell", scene_stairwell),
        ("basement", scene_basement),
        ("morgue", scene_morgue),
        ("chapel", scene_chapel),
        ("laundry", scene_laundry),
        ("kitchen", scene_kitchen),
        ("courtyard", scene_courtyard),
        ("staff_wing", scene_staff_wing),
        ("archives", scene_archives),
        ("side_cell", scene_side_cell),
        ("upper_landing", scene_upper_landing),
        ("upper_corridor", scene_upper_corridor),
        ("gallery", scene_gallery),
        ("therapy_b", scene_therapy_b),
        ("records_annex", scene_records_annex),
        ("dir_suite", scene_director_suite),
        ("roof", scene_roof),
    ]

    while True:
        clear()
        print("\n=== DEBUG MODE ===\n")
        print(f"  Sanity {state.sanity}  Health {state.health}  Echoes {state.collected_all_echoes}")
        print(f"  Room={getattr(state,'current_room',None)}  Nurse dead={state.killed_nurse}  Upper={state.upper_unlocked}")
        print(f"  Identity={state.knows_true_identity}  Boiler={state.destroyed_boiler}  Crowbar={state.has_crowbar}")
        print()
        print("  [1]  Teleport to room")
        print("  [2]  Give all key items")
        print("  [3]  Toggle flags (nurse/boiler/upper/identity/...)")
        print("  [4]  Set sanity / health / echoes")
        print("  [5]  Trigger jumpscare")
        print("  [6]  Test QTEs (debris / heartbeat / phrase)")
        print("  [7]  Force ending")
        print("  [8]  Print full state dump")
        print("  [9]  Max child favor + promise kept")
        print("  [10] Unlock upper offices + roof key + suite key")
        print("  [11] Resume into chosen room")
        print("  [12] Reset jumpscare cooldown")
        print("  [13] Delete previous run(s) from Patient 47 folder")
        print("  [14] Edit meta factors (trust, scars, NG+ flags)")
        print("  [0]  Exit debug (to main menu)")
        c = input("\n  Debug: ").strip()

        if c == "0":
            main_menu()
            return
        elif c == "1" or c == "11":
            print("\n  Rooms:")
            for i, (name, _) in enumerate(rooms, 1):
                print(f"    [{i}] {name}")
            raw = input("  Number: ").strip()
            try:
                idx = int(raw) - 1
                if 0 <= idx < len(rooms):
                    name, fn = rooms[idx]
                    state.current_room = name
                    start_ambient()
                    play_ambient("hallway", state.sanity)
                    fn(state)
                    return
            except ValueError:
                print("  Invalid.")
                time.sleep(0.5)
        elif c == "2":
            for attr in ("has_cell_key", "has_flashlight", "has_master_key", "has_bolt_cutters",
                         "has_crowbar", "has_holy_water", "has_pills", "has_tape", "has_uniform",
                         "has_roof_key", "has_upper_suite_key", "opened_courtyard"):
                setattr(state, attr, True)
            print("  All key items granted.")
            time.sleep(0.7)
        elif c == "3":
            print("  [1] killed_nurse  [2] destroyed_boiler  [3] upper_unlocked")
            print("  [4] knows_true_identity  [5] read_patient_file  [6] saw_own_body")
            print("  [7] promised+freed child  [8] burned_the_file")
            print("  [9] helped/pacified nurse  [10] accepted_merge")
            tgl = input("  Flag: ").strip()
            if tgl == "1":
                state.killed_nurse = not state.killed_nurse
            elif tgl == "2":
                state.destroyed_boiler = not state.destroyed_boiler
            elif tgl == "3":
                state.upper_unlocked = not state.upper_unlocked
                state.upper_debris_cleared = state.upper_unlocked
            elif tgl == "4":
                state.knows_true_identity = not state.knows_true_identity
            elif tgl == "5":
                state.read_patient_file = not state.read_patient_file
            elif tgl == "6":
                state.saw_own_body = not state.saw_own_body
            elif tgl == "7":
                state.promised_child = not state.promised_child
                state.freed_ghost_child = not state.freed_ghost_child
            elif tgl == "8":
                state.burned_the_file = not state.burned_the_file
            elif tgl == "9":
                state.helped_nurse = not state.helped_nurse
                state.nurse_pacified = not getattr(state, "nurse_pacified", False)
            elif tgl == "10":
                state.accepted_merge_temptation = not state.accepted_merge_temptation
            print("  Toggled.")
            time.sleep(0.5)
        elif c == "4":
            try:
                state.sanity = max(0, min(100, int(input("  Sanity 0-100: ").strip())))
                state.health = max(0, min(100, int(input("  Health 0-100: ").strip())))
                state.collected_all_echoes = max(0, int(input("  Echoes: ").strip()))
            except ValueError:
                print("  Invalid number.")
            time.sleep(0.5)
        elif c == "5":
            print("  [1] nurse  [2] body  [3] classic  [4] child")
            j = input("  Scare: ").strip()
            if j == "1":
                jumpscare_nurse()
            elif j == "2":
                jumpscare_face("body")
            elif j == "3":
                jumpscare_face("classic")
            else:
                jumpscare_face("child")
            state.register_jumpscare()
            input("  Enter...")
        elif c == "6":
            key = qte_primary_key()
            print("  [1] debris  [2] heartbeat  [3] phrase  [4] double-tap")
            q = input("  QTE: ").strip()
            if q == "1":
                print("  Result:", qte_debris_clear(key))
            elif q == "2":
                print("  Result:", qte_heartbeat(key, bpm=64, windows=5))
            elif q == "3":
                print("  Result:", qte_type_phrase("HOLD FAST", timeout=7.0))
            elif q == "4":
                print("  Result:", qte_double_tap(key, 0.7))
            input("  Enter...")
        elif c == "7":
            print("  [1] True Escape  [2] Loop  [3] Death  [4] Fall  [5] Rope  [6] Blade  [7] Merge")
            e = input("  Ending: ").strip()
            if e == "1":
                ending_true_escape(state)
            elif e == "2":
                ending_loop(state)
            elif e == "3":
                ending_death(state)
            elif e == "4":
                state.ending_reached = "The Fall"
                save_folder(state)
                clear()
                print("\n* THE FALL *\n")
                input("Press Enter...")
                main_menu()
            elif e == "5":
                ending_the_rope(state)
            elif e == "6":
                ending_the_blade(state)
            elif e == "7":
                ending_merge(state)
            return
        elif c == "8":
            clear()
            print("--- STATE DUMP ---")
            for k, v in sorted(vars(state).items()):
                if not k.startswith("_"):
                    print(f"  {k}: {v}")
            input("\n  Enter...")
        elif c == "9":
            state.child_favor = 10
            state.promised_child = True
            state.freed_ghost_child = True
            if getattr(state, "promised_child", False) or getattr(state, "prior_child_betrayed", False):
                restore_trust(state, "child")
                state.lied_to_child = False
                state.promises_broken = 0
                state.betrayed_child = False
            print("  Child path primed.")
            time.sleep(0.6)
        elif c == "10":
            state.has_crowbar = True
            state.destroyed_boiler = True
            state.upper_unlocked = True
            state.upper_debris_cleared = True
            state.has_roof_key = True
            state.has_upper_suite_key = True
            state.has_master_key = True
            print("  Upper fully unlocked.")
            time.sleep(0.6)
        elif c == "12":
            state.jumpscares_seen = 0
            print("  Jumpscare pacing reset.")
            time.sleep(0.5)

        elif c == "13":
            data = load_folder() or {}
            runs = list(data.get("runs") or [])
            if not runs and data.get("last"):
                runs = [data["last"]]
            if not runs:
                print("  No runs stored in Patient 47 folder.")
                time.sleep(0.8)
                continue
            print("\n  Stored runs:")
            for i, rec in enumerate(runs):
                if not isinstance(rec, dict):
                    rec = {}
                print(f"    [{i}] ending={rec.get('ending','?')}  echoes={rec.get('echoes','?')}  type={rec.get('personality','?')}")
            print("    [A] Delete ALL runs")
            print("    [Enter] Cancel")
            raw = input("  Delete which? ").strip().lower()
            if raw == "a":
                data["runs"] = []
                data["last"] = {}
                try:
                    with open(FOLDER_PATH, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                    print("  All runs cleared.")
                except Exception as e:
                    print("  Failed:", e)
            elif raw.isdigit():
                idx = int(raw)
                if 0 <= idx < len(runs):
                    removed = runs.pop(idx)
                    data["runs"] = runs
                    data["last"] = runs[-1] if runs else {}
                    try:
                        with open(FOLDER_PATH, "w", encoding="utf-8") as f:
                            json.dump(data, f, indent=2)
                        print(f"  Removed run {idx}: {removed.get('ending', '?')}")
                    except Exception as e:
                        print("  Failed:", e)
                else:
                    print("  Invalid index.")
            else:
                print("  Cancelled.")
            time.sleep(0.9)

        elif c == "14":
            data = load_folder() or {}
            scars = set(data.get("permanent_scars", []) or [])
            print("\n  META / NG+ FACTORS")
            print(f"  prior_child_betrayed (state) = {getattr(state, 'prior_child_betrayed', False)}")
            print(f"  trust_restored_child       = {getattr(state, 'trust_restored_child', False)}")
            print(f"  child_favor                = {getattr(state, 'child_favor', 0)}")
            print(f"  promised_child             = {getattr(state, 'promised_child', False)}")
            print(f"  scars: {sorted(scars) or '(none)'}")
            print()
            print("  [1] Girl distrusts you (prior betrayal ON + scar)")
            print("  [2] Girl trusts you (clear betrayal + optional trust_mended)")
            print("  [3] Set child favor (-5 to 10)")
            print("  [4] Toggle promised_child")
            print("  [5] Toggle prior_killed_nurse / prior_mercy / prior_burned")
            print("  [6] Add/remove a permanent scar by name")
            print("  [7] Clear ALL permanent scars")
            print("  [8] Toggle director_unlocked / director_cut_unlocked")
            print("  [9] Reload NG+ from folder into this state")
            print("  [0] Back")
            m = input("  Meta: ").strip()
            if m == "1":
                state.prior_child_betrayed = True
                state.trust_restored_child = False
                state.betrayed_child = True
                scars.add("betrayed_child")
                scars.discard("trust_mended")
                data["permanent_scars"] = list(scars)
                try:
                    with open(FOLDER_PATH, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                except Exception:
                    pass
                print("  Girl will distrust you (scar + state).")
            elif m == "2":
                state.prior_child_betrayed = False
                state.trust_restored_child = True
                state.betrayed_child = False
                scars.discard("betrayed_child")
                scars.add("trust_mended")
                data["permanent_scars"] = list(scars)
                try:
                    with open(FOLDER_PATH, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                except Exception:
                    pass
                print("  Girl trusts you; betrayal scar cleared.")
            elif m == "3":
                try:
                    state.child_favor = max(-5, min(10, int(input("  Favor: ").strip())))
                    print(f"  child_favor = {state.child_favor}")
                except ValueError:
                    print("  Invalid.")
            elif m == "4":
                state.promised_child = not getattr(state, "promised_child", False)
                print(f"  promised_child = {state.promised_child}")
            elif m == "5":
                print("  [a] prior_killed_nurse  [b] prior_mercy  [c] prior_burned")
                x = input("  ").strip().lower()
                if x == "a":
                    state.prior_killed_nurse = not getattr(state, "prior_killed_nurse", False)
                    print(" ", state.prior_killed_nurse)
                elif x == "b":
                    state.prior_mercy = not getattr(state, "prior_mercy", False)
                    print(" ", state.prior_mercy)
                elif x == "c":
                    state.prior_burned = not getattr(state, "prior_burned", False)
                    print(" ", state.prior_burned)
            elif m == "6":
                name = input("  Scar name (e.g. betrayed_child, prayed, nurse_dead): ").strip()
                if not name:
                    print("  Empty.")
                elif name in scars:
                    scars.discard(name)
                    print(f"  Removed scar '{name}'.")
                else:
                    scars.add(name)
                    print(f"  Added scar '{name}'.")
                data["permanent_scars"] = list(scars)
                try:
                    with open(FOLDER_PATH, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                except Exception:
                    pass
            elif m == "7":
                data["permanent_scars"] = []
                try:
                    with open(FOLDER_PATH, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                    print("  All scars cleared.")
                except Exception as e:
                    print("  Failed:", e)
            elif m == "8":
                SETTINGS["director_unlocked"] = not SETTINGS.get("director_unlocked")
                SETTINGS["director_cut_unlocked"] = not SETTINGS.get("director_cut_unlocked")
                data["director_unlocked"] = SETTINGS["director_unlocked"]
                data["director_cut_unlocked"] = SETTINGS["director_cut_unlocked"]
                save_settings()
                try:
                    with open(FOLDER_PATH, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                except Exception:
                    pass
                print(f"  director_unlocked={SETTINGS['director_unlocked']} cut={SETTINGS['director_cut_unlocked']}")
            elif m == "9":
                load_ng_plus(state)
                print("  NG+ reloaded from folder.")
            time.sleep(0.8)

        else:
            print("  Unknown.")
            time.sleep(0.4)


def accessibility_menu():
    clear()
    print("\nACCESSIBILITY / OPTIONS\n")
    while True:
        print(f"  [1] Skip typewriter     : {SETTINGS.get('skip_typewriter')}")
        print(f"  [2] QTE time scale      : {SETTINGS.get('qte_time_scale')}x")
        print(f"  [3] Reduce motion       : {SETTINGS.get('reduce_motion')}")
        print(f"  [4] Director commentary : {SETTINGS.get('director_commentary')} (unlocked={SETTINGS.get('director_unlocked')})")
        print(f"  [5] Difficulty          : {SETTINGS.get('difficulty')}")
        print(f"  [6] QTE primary key     : [{SETTINGS.get('qte_key', 'f').upper()}]")
        print(f"  [7] Hold-to-complete QTE : {SETTINGS.get('hold_qte')}")
        dbg = "ON" if SETTINGS.get("debug_mode") else "OFF"
        print(f"  [8] Debug mode          : {dbg} (password required to enable)")
        if SETTINGS.get("debug_mode"):
            print("  [9] Open debug tools")
        print("  [0] Back")
        c = input("\n  Toggle: ").strip()
        if c == "1":
            SETTINGS["skip_typewriter"] = not SETTINGS.get("skip_typewriter")
        elif c == "2":
            raw = input("  Scale 1.0 / 1.5 / 2.0: ").strip()
            try:
                v = float(raw)
                if 0.8 <= v <= 3.0:
                    SETTINGS["qte_time_scale"] = v
            except ValueError:
                pass
        elif c == "3":
            SETTINGS["reduce_motion"] = not SETTINGS.get("reduce_motion")
        elif c == "4":
            if SETTINGS.get("director_unlocked"):
                SETTINGS["director_commentary"] = not SETTINGS.get("director_commentary")
            else:
                print("  Unlock by reaching True Escape once.")
                time.sleep(0.9)
        elif c == "5":
            order = ["story", "standard", "hard"]
            cur = SETTINGS.get("difficulty", "standard")
            SETTINGS["difficulty"] = order[(order.index(cur) + 1) % 3] if cur in order else "standard"
            print(f"  Difficulty -> {SETTINGS['difficulty']}")
            time.sleep(0.5)
        elif c == "6":
            raw = input("  Letter key for primary QTEs (e.g. f, e, j): ").strip().lower()[:1]
            if raw and raw.isalpha():
                SETTINGS["qte_key"] = raw
                print(f"  QTE key -> [{raw.upper()}]")
            else:
                print("  Invalid key.")
            time.sleep(0.5)
        elif c == "7":
            SETTINGS["hold_qte"] = not SETTINGS.get("hold_qte")
        elif c == "8":
            if SETTINGS.get("debug_mode"):
                SETTINGS["debug_mode"] = False
                print("  Debug mode disabled.")
                time.sleep(0.6)
            else:
                pw = input("  Password: ").strip()
                if pw == "echo67":
                    SETTINGS["debug_mode"] = True
                    print("  Debug mode ENABLED.")
                else:
                    print("  Incorrect password.")
                time.sleep(0.7)
        elif c == "9" and SETTINGS.get("debug_mode"):
            save_settings()
            debug_menu(State())
            return
        elif c == "0":
            save_settings()
            main_menu()
            return
        save_settings()



def render_menu_scars():
    """One significant past-run mark, drawn with ANSI shapes (not prose)."""
    data = load_folder() or {}
    scars = set(data.get("permanent_scars", []) or [])
    if not scars:
        return
    # Priority: most narratively heavy first — only one is shown
    priority = [
        "betrayed_child", "oath_broken", "merged", "sacrifice", "became_nurse", "nurse_dead", "self_end",
        "drawer47", "burned_file", "child_touched", "prayed", "escaped",
        "looped", "uniform",
    ]
    chosen = None
    for p in priority:
        if p in scars:
            chosen = p
            break
    if not chosen:
        chosen = next(iter(scars))
    print()
    if chosen == "prayed":
        # red warped cross
        print(colorize("            │", RED))
        print(colorize("      ──────┼──────", RED))
        print(colorize("            │", RED))
        print(colorize("            │", RED))
    elif chosen == "nurse_dead":
        # white shoe outline
        print(colorize("        .--\"\"--.", DIM))
        print(colorize("       /  o  o  \\", DIM))
        print(colorize("       \\   __   /", DIM))
        print(colorize("        '------'", DIM))
    elif chosen == "child_touched":
        # small handprint
        print(colorize("         ╱ ‾‾ ╲", DIM))
        print(colorize("        │ •  • │", DIM))
        print(colorize("         ╲ ▄▄ ╱", DIM))
        print(colorize("        ╱│││││╲", DIM))
    elif chosen == "merged":
        # dual silhouette
        print(colorize("       ████    ████", DIM))
        print(colorize("      ██**██  ██**██", DIM))
        print(colorize("      ██████████████", DIM))
    elif chosen == "escaped":
        # open gates
        print("      ┌──┐          ┌──┐")
        print("      │  │  ══════  │  │")
        print("      │  │          │  │")
        print("      │  │          │  │")
    elif chosen == "looped":
        # cycle arrows
        print(colorize("         ↻     ↺", DIM))
        print(colorize("      ╭── wake ──╮", DIM))
        print(colorize("      ╰── wake ──╯", DIM))
    elif chosen == "burned_file":
        # ash / burn
        print(colorize("       ░░▒▒▓▓██", DIM))
        print(colorize("      ▒▓████▓▒░", DIM))
        print(colorize("       ░▒▓▓▒░", DIM))
    elif chosen == "betrayed_child":
        # small figure turned away
        print(colorize("         ‿", RED))
        print(colorize("        ╱ ╲   ···", RED))
        print(colorize("       ╱   ╲", RED))
    elif chosen == "self_end":
        print(colorize("         │", DIM))
        print(colorize("         ○", DIM))
        print(colorize("        ╱ ╲", DIM))
    elif chosen == "oath_broken":
        # cracked text block
        print(colorize("      ╔═PRO═MISE═╗", RED))
        print(colorize("      ║ ▓▓▓╱  ▓▓ ║", RED))
        print(colorize("      ╚═════════╝", RED))
    elif chosen == "drawer47":
        # open drawer
        print("      ┌────────────┐")
        print("      │ 47 ═══════ │")
        print("      └────┐       │")
        print("           └───────┘")
    elif chosen == "uniform":
        # roster lines
        print(colorize("      ─ NIGHT STAFF ─", DIM))
        print(colorize("      │ ░░░░░░░░░ │", DIM))
        print(colorize("      │ ░░░░░░░░░ │", DIM))
    elif chosen == "sacrifice":
        print(colorize("         †", DIM))
        print(colorize("        ╱ ╲", DIM))
        print(colorize("       ╱   ╲", DIM))
    elif chosen == "became_nurse":
        print(colorize("       ○   ○", RED))
        print(colorize("         V", RED))
        print(colorize("       ─────", RED))
    print()



def main_menu():
    stop_ambient()
    clear()
    play_menu_music()
    show(ART_TITLE)
    render_menu_scars()
    print("  [1] Enter the Asylum")
    print("  [2] Patient 47 Folder")
    print("  [3] Accessibility / Options")
    print("  [4] How to Play")
    print("  [5] Changelog")
    print("  [6] Leave")
    if SETTINGS.get("debug_mode"):
        print("  [9] Debug tools")
    print()
    while True:
        c = input("  Choose: ").strip()
        if c == "1":
            slot, loaded = choose_play_slot()
            if slot is None:
                main_menu()
                break
            set_active_slot(slot)
            if loaded is not None:
                set_active_state(loaded)
                start_ambient()
                typewriter("The building remembers where you left off.")
                pause(0.8)
                resume(loaded)
            else:
                start_game()
            break
        elif c == "2":
            view_patient_folder()
            break
        elif c == "3":
            accessibility_menu()
            break
        elif c == "4":
            clear()
            print("""
HOW TO PLAY
-----------
GOAL
  Explore the asylum, uncover who you are, and find a way out.
  Small choices matter later — dialogue, escape routes, and endings.

MOVING
  Pick numbered options. After you choose, only your choice stays on screen.
  Not every room opens from the hall — go through rooms to reach others.
  You can leave a room when an option says so.

COMBAT / DANGER
  The Night Nurse (and later a shadow) can appear when you enter rooms.
  If a chase starts: follow the on-screen prompts quickly.
  Mid-chase you may choose to force a door or hide — different risks.

QTEs (quick prompts)
  Watch the large letter banners. Press the key shown before time runs out.
  Default action key is F (change it under Accessibility).
  Turn on "Hold-to-complete QTE" if timed taps are hard.

SANITY & HEALTH
  Scary events lower sanity/health. If either hits 0, the run ends.
  At low sanity, fake menu options can appear and vanish if picked.

SAVING
  Progress autosaves to the slot you pick when you Enter the Asylum.
  Choose that same slot next time to continue.

AFTER A RUN
  Open Patient 47 Folder for your profile, notes, and achievements.
  Press E there to export a text case file.
  Later runs can show faint echoes of earlier choices (NG+).

OPTIONS
  Accessibility: text speed, QTE timing, difficulty, keys, reduce motion.

Press Enter to return...
""")
            input()
            main_menu()
            break
        elif c == "5":
            clear()
            print(CHANGELOG)
            input("\nPress Enter to return...")
            main_menu()
            break
        elif c == "6":
            clear()
            print("\nThe door closes behind you.\n")
            sys.exit()
        elif c.lower() in ("d", "9") and SETTINGS.get("debug_mode"):
            debug_menu(State())
            break
        else:
            print("  Invalid.")



def scene_upper_landing(state):
    clear()
    play_ambient("upper_landing", state.sanity)
    show(ART_UPPER_LANDING)
    state.current_room = "upper_landing"
    state.upper_visited = True
    state.advance_time()
    speak_room("upper_landing")
    state.add_note("Reached the upper landing above the stairwell.")
    if state.shadow_awareness >= 3:
        typewriter("Something with your posture is already waiting higher still.")

    while True:
        options = [
            "Enter the upper corridor",
            "Inspect the scorched joists",
            "Return down the stairwell",
        ]
        if state.has_roof_key:
            options.insert(2, "Climb the roof hatch")
        choice = get_choice("Where?", options, state)
        if choice == 0:
            scene_upper_corridor(state)
            return
        elif choice == 1:
            typewriter("Blackened wood. The fire climbed here and failed to finish the floor.")
            typewriter("Someone wrote in soot: DO NOT FOLLOW HER UP.")
            state.collected_all_echoes += 1
            add_trait(state, "curiosity", 1)
        elif state.has_roof_key and choice == 2:
            scene_roof(state)
            return
        else:
            scene_stairwell(state)
            return


def scene_upper_corridor(state):
    clear()
    play_ambient("upper_corridor", state.sanity)
    show(ART_UPPER_CORRIDOR)
    state.current_room = "upper_corridor"
    state.advance_time()
    speak_room("upper_corridor")
    state.add_note("Walked the upper corridor.")

    while True:
        options = [
            "Observation Gallery",
            "Therapy Room B",
            "Records Annex",
            "Director's Private Suite",
            "Return to upper landing",
        ]
        choice = get_choice("Where?", options, state)
        if choice == 0:
            scene_gallery(state)
            return
        elif choice == 1:
            scene_therapy_b(state)
            return
        elif choice == 2:
            scene_records_annex(state)
            return
        elif choice == 3:
            scene_director_suite(state)
            return
        else:
            scene_upper_landing(state)
            return


def scene_gallery(state):
    clear()
    play_ambient("gallery", state.sanity)
    show(ART_GALLERY)
    state.current_room = "gallery"
    state.advance_time()
    speak_room("gallery")

    while True:
        options = [
            "Look through the one-way glass",
            "Read the chained logbook",
            "Listen for the Nurse's rounds",
            "Return to the upper corridor",
        ]
        choice = get_choice("What do you do?", options, state)
        if choice == 0:
            # Gallery overlooks the nurse station only — body visible solely if she died there
            if nurse_body_in(state, "nurse_station"):
                typewriter("Below: a white heap behind the station counter. Porcelain face cracked.")
                typewriter("From up here she is smaller. From up here you could have stopped this.")
                state.add_note("Gallery: saw the Nurse's body in the station below.")
            elif state.killed_nurse:
                typewriter("Below: the nurse station is still. No white shoes. No body in view.")
                if getattr(state, "nurse_banished", False):
                    typewriter("She did not fall where the glass can see.")
                else:
                    typewriter("Wherever she fell, it is not under this window.")
                state.add_note("Gallery: station empty of her body.")
            else:
                typewriter("Below: the Night Nurse glides past the station. She does not look up.")
                typewriter("From here she is smaller. From here you could have warned someone.")
                state.nurse_hostility = max(0, state.nurse_hostility - 1)
            state.collected_all_echoes += 1
            add_trait(state, "curiosity", 1)
        elif choice == 1:
            if state.gallery_log_read:
                typewriter("You already know what the log wants from you.")
            else:
                typewriter("'Patient 47 — responds to staff voice. Do not allow basement access.'")
                typewriter("Later hand: 'Subject answers to the night orderly's name.'")
                state.gallery_log_read = True
                state.collected_all_echoes += 1
                if not state.knows_true_identity:
                    typewriter("A cold thought: staff voice. Whose?")
                else:
                    typewriter("Confirmation, from above, of what the lower office already taught.")
                state.add_note("Read observation gallery log on Patient 47.")
                add_trait(state, "curiosity", 1)
                mark_world(state, "gallery_log")
                set_truth_path(state, "orderly", quiet=True)
        elif choice == 2:
            if state.killed_nurse:
                typewriter("You press your ear to the glass-frame.")
                typewriter("Nothing. No shoes. No lullaby. The dead do not make rounds.")
                state.add_note("Gallery listen: silence — nurse is dead.")
                mark_world(state, "gallery_silence")
            else:
                typewriter("You press your ear to the glass-frame.")
                key = qte_primary_key()
                if qte_heartbeat(key, bpm=44, windows=4):
                    typewriter("You catch her pattern — she will favor the east corridor next.")
                    state.nurse_hostility = max(0, state.nurse_hostility - 1)
                    state.add_note("Gallery listen: learned Nurse patrol bias.")
                    add_trait(state, "resolve", 1)
                    mark_world(state, "gallery_watched")
                else:
                    typewriter("Only your pulse. She could be anywhere.")
                    state.change_sanity(-4)
        else:
            scene_upper_corridor(state)
            return


def scene_therapy_b(state):
    clear()
    play_ambient("therapy", state.sanity)
    show(ART_THERAPY)
    state.current_room = "therapy_b"
    state.advance_time()
    speak_room("therapy_b")

    while True:
        options = [
            "Play the dead tape recorder",
            "Sit in the intake chair",
            "Search under the chairs",
            "Return to the upper corridor",
        ]
        choice = get_choice("What do you do?", options, state)
        if choice == 0:
            if state.therapy_tape_played:
                typewriter("The tape is chewed. It will not speak twice.")
            else:
                typewriter("A motor coughs. Your voice — younger, steadier — fills the ring of chairs.")
                typewriter("'Name for the record.' A child's answer, too quiet to catch.")
                typewriter("Your voice again: 'We will keep you safe through the night.'")
                state.therapy_tape_played = True
                state.collected_all_echoes += 1
                state.change_sanity(-10)
                state.add_note("Therapy B tape: your voice running a child's intake.")
                add_trait(state, "compassion", 1)
                add_trait(state, "honesty", 1)
                if state.promised_child or state.freed_ghost_child:
                    typewriter("The chapel girl was not the first promise this voice made.")
                moral = get_choice("The memory offers a hand.", [
                    "Accept it — you did this work",
                    "Reject it — that is not you",
                ], state)
                if moral == 0:
                    state.knows_true_identity = True
                    add_trait(state, "honesty", 2)
                    typewriter("The chairs stop facing you. They face the door.")
                    set_truth_path(state, "orderly")
                    mark_world(state, "therapy_accept")
                else:
                    add_trait(state, "detachment", 2)
                    state.change_sanity(-5)
                    typewriter("The tape squeals. Denial has a frequency.")
                    set_truth_path(state, "patient", quiet=True)
                    mark_world(state, "therapy_reject")
        elif choice == 1:
            typewriter("Wood remembers weight. For a second you are signing a sedation order.")
            state.change_sanity(-6)
            add_trait(state, "detachment", 1)
            if state.read_patient_file:
                typewriter("The same hand that wrote 47's file rested here.")
        elif choice == 2:
            typewriter("A brass key taped under a chair leg — private suite.")
            state.has_upper_suite_key = True
            state.add_note("Found Director's suite key under therapy chair.")
            add_trait(state, "curiosity", 1)
        else:
            scene_upper_corridor(state)
            return


def scene_records_annex(state):
    clear()
    play_ambient("records_annex", state.sanity)
    show(ART_RECORDS_ANNEX)
    state.current_room = "records_annex"
    state.advance_time()
    speak_room("records_annex")

    while True:
        options = [
            "Open the sealed Director envelope",
            "Read fire-death certificates",
            "Scan transfer lists",
            "Return to the upper corridor",
        ]
        choice = get_choice("What do you do?", options, state)
        if choice == 0:
            if state.read_director_letter:
                typewriter("The letter is already in your blood.")
            else:
                typewriter("'We committed the orderly as Patient 47 to end the fire inquiry.'")
                typewriter("'If he remembers, sedate. If he climbs, the upper floor is sealed.'")
                state.read_director_letter = True
                state.knows_true_identity = True
                state.collected_all_echoes += 2
                state.change_sanity(-12)
                state.shadow_awareness += 2
                state.add_note("Director letter: orderly committed as Patient 47.")
                add_trait(state, "curiosity", 2)
                add_trait(state, "defiance", 1)
                typewriter("A roof-access tag falls from the envelope.")
                state.has_roof_key = True
                set_truth_path(state, "orderly")
                mark_world(state, "director_letter")
        elif choice == 1:
            typewriter("Names. Dates. All the same night. One line blank where a staff name was scraped.")
            state.collected_all_echoes += 1
            state.change_sanity(-5)
            add_trait(state, "curiosity", 1)
            mark_world(state, "fire_certs")
        elif choice == 2:
            typewriter("'All patients transferred or deceased.' The ink on deceased is darker.")
            if state.burned_the_file:
                typewriter("Your burned file is still listed. Paper remembers ash.")
            state.collected_all_echoes += 1
            mark_world(state, "transfer_lists")
        else:
            scene_upper_corridor(state)
            return


def scene_director_suite(state):
    clear()
    play_ambient("dir_suite", state.sanity)
    show(ART_DIR_SUITE)
    state.current_room = "dir_suite"
    state.advance_time()

    if not state.has_upper_suite_key and not state.read_director_letter:
        typewriter("The suite door holds. Brass lock. No give.")
        typewriter("Therapy B or the sealed letter would know the way.")
        pause(1.0)
        scene_upper_corridor(state)
        return

    speak_room("dir_suite")
    state.add_note("Entered the Director's private suite.")

    while True:
        options = [
            "Sit in the Director's chair",
            "Search the private study",
            "Enter the quiet alcove",
            "Leave to the upper corridor",
        ]
        choice = get_choice("What do you do?", options, state)
        if choice == 0:
            typewriter("The chair faces the door. Authority never turns its back.")
            typewriter("Flash: a pen, a name, SEDATE written twice.")
            state.change_sanity(-8)
            if state.knows_true_identity:
                typewriter("You signed for yourself. The building needed a patient more than a witness.")
            add_trait(state, "detachment", 1)
            state.collected_all_echoes += 1
        elif choice == 1:
            typewriter("Drafts of the fire story. True pages under false ones.")
            typewriter("A gate-override code is scrawled on blotting paper: BLACKWOOD-OUT")
            state.collected_all_echoes += 1
            state.add_note("Found gate override phrase BLACKWOOD-OUT in Director study.")
            state.has_roof_key = True
            add_trait(state, "curiosity", 1)
            if state.shadow_awareness >= 2:
                typewriter("In the glass of a dark frame: your outline, delayed.")
        elif choice == 2:
            typewriter("Narrow bed. Restraints looped neat. A child's drawing under the pillow.")
            if getattr(state, "prior_child_betrayed", False) or getattr(state, "betrayed_child", False):
                typewriter("The drawing is of a figure walking away from a smaller one.")
                state.change_sanity(-10)
            elif state.promised_child or state.freed_ghost_child:
                typewriter("The drawing is open gates and two stick figures holding hands.")
                state.change_sanity(+4)
            else:
                typewriter("The drawing is a stairwell with the top scribbled black.")
            lie = get_choice("The quiet room offers sleep.", [
                "Lie down and close your eyes",
                "Step back into the suite",
            ], state)
            if lie == 0:
                if not confirm_run_end(
                    [
                        "The quiet room is not rest. It is a lid.",
                        "If you lie down here, this attempt ends — you wake on the mattress again, or not at all.",
                    ],
                    "I was always here",
                    'Type anything else to stand back up.',
                ):
                    add_trait(state, "resolve", 1)
                    continue
                typewriter("The upper floor claims you were always here.")
                add_trait(state, "detachment", 2)
                ending_loop(state)
                return
            add_trait(state, "resolve", 1)
        else:
            scene_upper_corridor(state)
            return


def scene_roof(state):
    clear()
    play_ambient("roof", state.sanity)
    show(ART_ROOF)
    state.current_room = "roof"
    state.advance_time()
    speak_room("roof", state)
    state.add_note("Stood on the asylum roof.")
    if getattr(state, "shadow_overdosed", False):
        typewriter("Against the moon: empty sky. The outline that shared your stance is gone.")
        typewriter("Whatever took the quiet medicine in the basement is not climbing.")
        mark_world(state, "roof_no_shadow")
    else:
        typewriter("Against the moon: an outline that matches your stance.")
        if state.shadow_awareness >= 2 or state.saw_own_body:
            typewriter("It turns its head a fraction before you do.")
            typewriter("The same posture as the cage below. Same delay in the neck.")
            state.shadow_awareness += 1
            mark_world(state, "roof_shadow_link")
        else:
            typewriter("Only when you stop moving does it stop.")

    while True:
        options = [
            "Call out to the outline against the moon",
            "Climb down the outer wall (escape)",
            "Jump",
            "Return through the hatch",
        ]
        if getattr(state, "shadow_overdosed", False):
            options[0] = "Search the empty skyline"
        choice = get_choice("What do you do?", options, state)
        if choice == 0:
            if getattr(state, "shadow_overdosed", False):
                typewriter("Wind only. No delayed answer.")
                state.change_sanity(-3)
            else:
                typewriter("Wind eats the name you try to use.")
                typewriter("The outline answers with your voice, delayed: 'Still climbing.'")
                state.shadow_awareness += 2
                state.change_sanity(-8)
                add_trait(state, "curiosity", 1)
                if state.accepted_merge_temptation:
                    typewriter("It offers a hand made of the same darkness as the basement cage.")
                    set_truth_path(state, "merge", quiet=True)
        elif choice == 1:
            roof_escape_sequence(state)
            return
        elif choice == 2:
            typewriter("The moon does not catch you.")
            add_trait(state, "self_end", 1)
            add_trait(state, "detachment", 2)
            state.ending_reached = "The Fall"
            stop_ambient()
            save_folder(state)
            clear()
            typewriter("Wind. Then nothing the folder can interview.")
            print("\n* THE FALL *\n")
            input("Press Enter...")
            main_menu()
            return
        else:
            scene_upper_landing(state)
            return


def roof_escape_sequence(state):
    """Climb-down escape: full physical gauntlet. Any failure = The Fall."""
    clear()
    play_ambient("tension", state.sanity)
    typewriter("Ivy, brick, rain. The ground is a long sentence away.")
    pause(0.8)
    if not confirm_run_end(
        [
            "Once you leave the hatch, the wall is the only path.",
            "Succeed and this attempt ends on the grass. Fail any hold and the fall ends it instead.",
        ],
        "climb down",
        "Type anything else to crawl back through the hatch.",
    ):
        scene_roof(state)
        return
    if not run_physical_escape_gauntlet(state, route="roof"):
        typewriter("Your grip becomes a theory. The moon outline does not reach for you.")
        pause(0.9)
        typewriter("The fall is shorter than the climb.")
        add_trait(state, "self_end", 1)
        add_trait(state, "detachment", 2)
        state.ending_reached = "The Fall"
        stop_ambient()
        save_folder(state)
        clear()
        typewriter("Wind. Then nothing the folder can interview.")
        print("\n* THE FALL *\n")
        input("Press Enter...")
        main_menu()
        return
    typewriter("Boots hit wet grass. The asylum's roof is a black rectangle behind you.")
    state.collected_all_echoes += 1
    add_trait(state, "courage", 3)
    add_trait(state, "resolve", 2)
    state.add_note("Escaped by climbing down from the roof.")
    if state.knows_true_identity or state.collected_all_echoes >= 5:
        ending_true_escape(state)
    else:
        ending_false_escape(state)



def resume(state):
    set_active_state(state)
    mapping = {
        "cell": scene_cell, "east_corridor": scene_east_corridor,
        "nurse_station": scene_nurse_station, "stairwell": scene_stairwell,
        "office": scene_office, "basement": scene_basement, "morgue": scene_morgue,
        "chapel": scene_chapel, "laundry": scene_laundry, "kitchen": scene_kitchen,
        "courtyard": scene_courtyard, "staff_wing": scene_staff_wing,
        "archives": scene_archives, "side_cell": scene_side_cell,
        "upper_landing": scene_upper_landing, "upper_corridor": scene_upper_corridor,
        "gallery": scene_gallery, "therapy_b": scene_therapy_b,
        "records_annex": scene_records_annex, "dir_suite": scene_director_suite,
        "roof": scene_roof,
    }
    mapping.get(getattr(state, "current_room", None) or "cell", scene_cell)(state)


def start_game():
    state = State()
    set_active_state(state)
    data = load_folder()
    if data and data.get("director_unlocked"):
        SETTINGS["director_unlocked"] = True
    if data and data.get("director_cut_unlocked"):
        SETTINGS["director_cut_unlocked"] = True
    load_ng_plus(state)
    start_ambient()
    clear()
    typewriter("Blackwood Asylum - Closed 1974 after the fire.")
    pause(1.4)
    typewriter("Official records: patients transferred or deceased.")
    pause(1.4)
    typewriter("Unofficial records: the building still accepts arrivals.")
    pause(2.0)
    typewriter("You open your eyes on a thin mattress.")
    pause(1.3)
    typewriter("You do not remember arriving.")
    pause(1.0)
    autosave(state)
    scene_cell(state)


def intro_cinematic():
    """Boot sequence: big typewriter title, credit, red flicker, then menu."""
    stop_ambient()
    clear()
    pause(0.6)
    # Large ASCII title typed line by line
    title_block = big_text("ECHOES")
    for line in title_block.splitlines():
        if not line.strip():
            print()
            continue
        for ch in line:
            sys.stdout.write(ch)
            sys.stdout.flush()
            time.sleep(0.008 if not SETTINGS.get("skip_typewriter") else 0)
        print()
        time.sleep(0.04 if not SETTINGS.get("skip_typewriter") else 0)
    pause(0.35)
    of_line = "                   OF THE ASYLUM"
    for ch in of_line:
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(0.02 if not SETTINGS.get("skip_typewriter") else 0)
    print()
    pause(0.9)
    credit = "by Nathaniel Ni"
    print()
    for ch in credit:
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(0.045 if not SETTINGS.get("skip_typewriter") else 0)
    print()
    pause(1.2)
    # Red flicker used before jumpscares
    screen_flicker(6)
    pause(0.25)


if __name__ == "__main__":
    load_settings()
    try:
        intro_cinematic()
        main_menu()
    except KeyboardInterrupt:
        stop_ambient()
        clear()
        print("\n\nThe whispers fade... for now.\n")
        sys.exit()
