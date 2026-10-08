#!/usr/bin/env python3
"""
ECHOES OF THE ASYLUM
A Choose-Your-Own-Adventure Horror Game
Runs entirely in the console (VS Code terminal / any terminal)
"""

import os
import sys
import time
import random

# -------------------------------------------------
# Utility functions
# -------------------------------------------------

def clear():
    """Clear the console screen."""
    os.system('cls' if os.name == 'nt' else 'clear')

def pause(seconds=1.5):
    time.sleep(seconds)

def typewriter(text, delay=0.03):
    """Print text with a slight typewriter effect."""
    for char in text:
        sys.stdout.write(char)
        sys.stdout.flush()
        time.sleep(delay)
    print()

def show_art(art):
    """Print ASCII art centered-ish."""
    print(art)

def get_choice(prompt, options):
    """
    Display numbered options and return the chosen index (0-based).
    options = list of strings
    """
    print()
    for i, opt in enumerate(options, 1):
        print(f"  [{i}] {opt}")
    while True:
        try:
            choice = input(f"\n{prompt} ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return idx
            print("Invalid choice. Try again.")
        except ValueError:
            print("Please enter a number.")

def game_over(reason):
    clear()
    show_art(r"""
        ╔══════════════════════════════════════╗
        ║           ★  YOU DIED  ★             ║
        ╚══════════════════════════════════════╝
    """)
    print(f"\n{reason}\n")
    print("The darkness claims another soul...\n")
    input("Press Enter to return to the void...")
    main_menu()

def victory(reason):
    clear()
    show_art(r"""
        ╔══════════════════════════════════════╗
        ║         ★  YOU ESCAPED  ★            ║
        ╚══════════════════════════════════════╝
    """)
    print(f"\n{reason}\n")
    print("You stumble into the cold night air, gasping.\nBut the whispers... they follow you still.\n")
    input("Press Enter to face the darkness again...")
    main_menu()

# -------------------------------------------------
# ASCII Art Scenes
# -------------------------------------------------

ART_TITLE = r"""
    ███████╗ ██████╗██╗  ██╗ ██████╗ ███████╗███████╗
    ██╔════╝██╔════╝██║  ██║██╔═══██╗██╔════╝██╔════╝
    █████╗  ██║     ███████║██║   ██║█████╗  ███████╗
    ██╔══╝  ██║     ██╔══██║██║   ██║██╔══╝  ╚════██║
    ███████╗╚██████╗██║  ██║╚██████╔╝███████╗███████║
    ╚══════╝ ╚═════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚══════╝
              OF THE ASYLUM
    ────────────────────────────────────────────────
         A Text Horror Adventure
"""

ART_CELL = r"""
    ┌─────────────────────────────────────┐
    │  ████████████████████████████████   │
    │  ██                            ██   │
    │  ██   ┌──────┐                 ██   │
    │  ██   │ BED  │    ┌────┐       ██   │
    │  ██   │      │    │DOOR│       ██   │
    │  ██   └──────┘    └────┘       ██   │
    │  ██                            ██   │
    │  ██      [YOU]                 ██   │
    │  ██                            ██   │
    │  ████████████████████████████████   │
    └─────────────────────────────────────┘
         A cold concrete cell. The air reeks
         of mildew and something far worse.
"""

ART_HALLWAY = r"""
    ════════════════════════════════════════════
         │        │        │        │
         │  ROOM  │  ROOM  │  ROOM  │
         │   12   │   13   │   14   │
    ─────┴────────┴────────┴────────┴─────
              ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
              ▓   HALLWAY    ▓
              ▓     [YOU]    ▓
              ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    ─────┬────────┬────────┬────────┬─────
         │  ROOM  │ STAIRS │  ROOM  │
         │   15   │   ↓↑   │   16   │
         │        │        │        │
    ════════════════════════════════════════════
         Flickering lights. Long shadows.
         Something drips... somewhere.
"""

ART_MORGUE = r"""
    ┌──────────────────────────────────────────┐
    │  ▓▓▓  MORGUE  ▓▓▓                        │
    │                                          │
    │   ┌─────┐  ┌─────┐  ┌─────┐  ┌─────┐     │
    │   │     │  │     │  │     │  │     │     │
    │   │BODY │  │BODY │  │BODY │  │ ??? │     │
    │   │     │  │     │  │     │  │     │     │
    │   └─────┘  └─────┘  └─────┘  └─────┘     │
    │                                          │
    │              [YOU]                       │
    │                                          │
    │   The drawers are cold. Too cold.        │
    └──────────────────────────────────────────┘
"""

ART_OFFICE = r"""
    ┌──────────────────────────────────────────┐
    │              DIRECTOR'S OFFICE           │
    │  ┌────────────────────────────────────┐  │
    │  │  DESK          ┌──────────────┐    │  │
    │  │                │  FILES       │    │  │
    │  │   [CHAIR]      │  PATIENT 47  │    │  │
    │  │                └──────────────┘    │  │
    │  └────────────────────────────────────┘  │
    │                                          │
    │     A single lamp still burns.           │
    │     The photo on the desk watches you.   │
    └──────────────────────────────────────────┘
"""

ART_BASEMENT = r"""
    ╔══════════════════════════════════════════╗
    ║               BASEMENT                   ║
    ║  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  ║
    ║  ░  ┌────┐                    ┌────┐  ░  ║
    ║  ░  │BOILER│                  │CAGE │  ░  ║
    ║  ░  └────┘                    └────┘  ░  ║
    ║  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  ║
    ║                                          ║
    ║           Something moves in the dark.   ║
    ║                  [YOU]                   ║
    ╚══════════════════════════════════════════╝
"""

ART_GHOST = r"""
              .     .
           .  |\_/|  .
            \_| a a|_/
            /|  =  |\
           / | === | \
          /  |_   _|  \
         /     | |     \
        /      | |      \
               | |
            ___| |___
           /         \
          |  R E S T  |
          |___________|
"""

ART_SHADOW = r"""
                    ████
                  ██    ██
                ██  ▓▓  ██
               ██  ▓▓▓▓  ██
              ██  ▓▓▓▓▓▓  ██
             ██  ▓▓▓▓▓▓▓▓  ██
            ██  ▓▓▓▓▓▓▓▓▓▓  ██
           ██                ██
          ██   ●●      ●●    ██
         ██     ▓▓▓▓▓▓       ██
        ██       ▓▓▓▓         ██
       ██         ▓▓           ██
      ██                       ██
     █████████████████████████████
"""

ART_ESCAPE = r"""
    ══════════════════════════════════════
              THE FRONT GATES
    ══════════════════════════════════════
         ████████████████████████
         ██                    ██
         ██   ╔══════════╗     ██
         ██   ║   OPEN   ║     ██
         ██   ╚══════════╝     ██
         ██                    ██
         ████████████████████████
              [ YOU ARE FREE? ]
    ══════════════════════════════════════
"""

ART_NURSE = r"""
              ┌─────────┐
              │  ○   ○  │
              │    ▽    │
              │  ─────  │
              └────┬────┘
                   │
              ┌────┴────┐
              │  NURSE  │
              │  WHITE  │
              │  DRESS  │
              └────┬────┘
                   │
              ─────┴─────
         She smiles too wide.
"""

# -------------------------------------------------
# Game State
# -------------------------------------------------

class State:
    def __init__(self):
        self.has_key = False
        self.has_flashlight = False
        self.seen_ghost = False
        self.sanity = 100

# -------------------------------------------------
# Scenes
# -------------------------------------------------

def scene_cell(state):
    clear()
    show_art(ART_CELL)
    typewriter("You wake on a thin, stained mattress. Your head throbs.")
    typewriter("The door is slightly ajar. A faint red light bleeds through the crack.")
    typewriter("Something is scratching at the walls... or is it inside your skull?")

    choice = get_choice("What do you do?", [
        "Search the cell carefully",
        "Open the door and step into the hallway",
        "Call out for help"
    ])

    if choice == 0:
        typewriter("\nYou feel under the mattress... your fingers brush cold metal.")
        typewriter("A rusty key. And a dead flashlight with one last flicker of life.")
        state.has_key = True
        state.has_flashlight = True
        pause()
        typewriter("The scratching grows louder.")
        scene_hallway(state)
    elif choice == 1:
        scene_hallway(state)
    else:
        typewriter("\n\"Hello...? Is anyone there?\"")
        pause(1)
        typewriter("Silence.")
        pause(1)
        typewriter("Then... a child's giggle from the vent above you.")
        state.sanity -= 20
        typewriter("Your hands shake. You decide the hallway is safer.")
        pause()
        scene_hallway(state)

def scene_hallway(state):
    clear()
    show_art(ART_HALLWAY)
    typewriter("The hallway stretches in both directions. Doors hang open like broken jaws.")
    typewriter("A single bulb swings overhead, casting dancing shadows.")
    if state.sanity < 80:
        typewriter("You swear the shadows move when you're not looking.")

    options = [
        "Enter Room 13 (the door is marked 'MORQUE')",
        "Enter the Director's Office (door at the end)",
        "Take the stairs down to the basement",
        "Try the front exit (requires a key)"
    ]

    choice = get_choice("Where do you go?", options)

    if choice == 0:
        scene_morgue(state)
    elif choice == 1:
        scene_office(state)
    elif choice == 2:
        scene_basement(state)
    else:
        if state.has_key:
            typewriter("\nThe key slides into the lock with a heavy click.")
            scene_escape(state)
        else:
            typewriter("\nThe heavy double doors are locked tight. You need a key.")
            pause()
            scene_hallway(state)

def scene_morgue(state):
    clear()
    show_art(ART_MORGUE)
    typewriter("The temperature drops the moment you enter.")
    typewriter("Three drawers are closed. The fourth is slightly open...")
    typewriter("A pale hand dangles from it, fingers twitching.")

    choice = get_choice("What do you do?", [
        "Pull the open drawer all the way out",
        "Close the drawer and leave immediately",
        "Search the other drawers"
    ])

    if choice == 0:
        clear()
        show_art(ART_GHOST)
        typewriter("A figure sits up. Its eyes are sewn shut, but it turns toward you.")
        typewriter("\"You shouldn't have come back,\" it whispers with your own voice.")
        state.seen_ghost = True
        state.sanity -= 40
        pause(2)
        if state.sanity <= 0:
            game_over("Your mind fractures. You join the drawers.")
        typewriter("You stumble backward into the hallway, heart hammering.")
        scene_hallway(state)
    elif choice == 1:
        typewriter("\nYou slam the drawer shut. Something pounds from the inside.")
        typewriter("You don't wait to see if it opens again.")
        scene_hallway(state)
    else:
        typewriter("\nThe other drawers contain... patients. Or what remains of them.")
        typewriter("One holds a small silver key and a note: 'The basement remembers.'")
        state.has_key = True
        typewriter("You take the key. The air grows heavier.")
        scene_hallway(state)

def scene_office(state):
    clear()
    show_art(ART_OFFICE)
    typewriter("Dust covers everything except a single manila folder.")
    typewriter("Patient 47. The photograph shows a face that looks disturbingly like yours.")

    choice = get_choice("What do you examine?", [
        "Read the patient file",
        "Look through the desk drawers",
        "Leave the office"
    ])

    if choice == 0:
        typewriter("\n'Patient exhibits severe dissociative episodes...'")
        typewriter("'Claims the staff are already dead...'")
        typewriter("'Recommend indefinite containment in Cell 12.'")
        typewriter("\nThe last entry is dated today.")
        state.sanity -= 25
        pause()
        typewriter("A cold hand rests on your shoulder.")
        clear()
        show_art(ART_NURSE)
        typewriter("\"Time for your medication, dear.\"")
        pause(2)
        if random.random() < 0.5 or state.sanity < 50:
            game_over("The needle finds your neck. Darkness is soft this time.")
        else:
            typewriter("You wrench free and run.")
            scene_hallway(state)
    elif choice == 1:
        typewriter("\nYou find a working flashlight and a bottle of pills labeled 'Forget'.")
        state.has_flashlight = True
        typewriter("You pocket the light. The pills you leave.")
        scene_hallway(state)
    else:
        scene_hallway(state)

def scene_basement(state):
    clear()
    show_art(ART_BASEMENT)
    typewriter("The stairs groan under your weight. Water drips somewhere unseen.")
    typewriter("A massive boiler sits cold and silent. In the corner, an old cage.")

    if not state.has_flashlight:
        typewriter("It's too dark. You can barely see your own hands.")
        typewriter("Something brushes past your leg.")
        state.sanity -= 15

    choice = get_choice("What do you do?", [
        "Approach the cage",
        "Inspect the boiler",
        "Turn around and go back upstairs"
    ])

    if choice == 0:
        clear()
        show_art(ART_SHADOW)
        typewriter("Inside the cage sits a figure that looks exactly like you...")
        typewriter("...except its mouth is stitched into a permanent smile.")
        typewriter("It raises a finger to its lips.")
        typewriter("\"Shhh. They're listening.\"")
        pause(2)
        state.sanity -= 30
        if state.sanity <= 30:
            game_over("You realize the figure in the cage is the real you. You never left.")
        typewriter("You back away slowly. The stairs feel much farther now.")
        scene_hallway(state)
    elif choice == 1:
        typewriter("\nBehind the boiler you find a maintenance tunnel.")
        typewriter("Faint moonlight leaks from the far end.")
        typewriter("This might be a way out...")
        choice2 = get_choice("Enter the tunnel?", [
            "Yes — crawl toward the light",
            "No — too risky"
        ])
        if choice2 == 0:
            scene_escape(state)
        else:
            scene_hallway(state)
    else:
        scene_hallway(state)

def scene_escape(state):
    clear()
    show_art(ART_ESCAPE)
    typewriter("The gates creak open. Night air hits your face.")
    typewriter("Freedom tastes like rain and copper.")

    if state.seen_ghost and state.sanity < 50:
        typewriter("\nBut as you step through, you hear soft footsteps behind you.")
        typewriter("A voice that sounds like yours whispers:")
        typewriter("\"You forgot someone.\"")
        pause(2)
        game_over("You turn around. The gates slam shut. You are still inside.")
    else:
        victory("You don't look back. You never look back.")

# -------------------------------------------------
# Main Menu & Start
# -------------------------------------------------

def main_menu():
    clear()
    show_art(ART_TITLE)
    print("  [1] Begin the Nightmare")
    print("  [2] How to Play")
    print("  [3] Leave this place")
    print()
    while True:
        choice = input("  Choose: ").strip()
        if choice == "1":
            start_game()
            break
        elif choice == "2":
            clear()
            print("""
HOW TO PLAY
───────────
• Read each scene carefully.
• Type the number of your choice and press Enter.
• Your decisions matter. Sanity is fragile.
• There are multiple endings — some kinder than others.
• The asylum remembers those who enter.

Press Enter to return...
""")
            input()
            main_menu()
            break
        elif choice == "3":
            clear()
            print("\nThe door closes behind you.\nFor now.\n")
            sys.exit()
        else:
            print("  Invalid choice.")

def start_game():
    state = State()
    clear()
    typewriter("The year is 1978.")
    typewriter("Blackwood Asylum was shut down after the incident.")
    typewriter("Official records say all patients were transferred.")
    typewriter("Official records lie.")
    pause(2)
    typewriter("\nYou open your eyes.")
    pause(1)
    scene_cell(state)

# -------------------------------------------------
# Entry Point
# -------------------------------------------------

if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        clear()
        print("\n\nThe whispers fade... for now.\n")
        sys.exit()