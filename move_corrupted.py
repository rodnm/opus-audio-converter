#!/usr/bin/env python3
import shutil
from pathlib import Path

MUSIC_DIR = Path("/storage/emulated/0/SdCardBackUp/Music")
CORRUPTED_DIR = MUSIC_DIR / "_corrupted"

corrupted_files = [
    "spotify-dl/Some vocaloids/マトリョシカ - ORIGINAL.mp3",
    "spotify-dl/Some vocaloids/砂の惑星 feat.初音ミク.mp3",
    "spotify-dl/Some vocaloids/結んで開いて羅刹と骸 - ORIGINAL.mp3",
    "spotify-dl/Best of Fallout (Fallout 3, 4 & New Vegas)/Dear Hearts And Gentle People.mp3",
    "spotify-dl/Anime Hits 80's/NIGHT OF SUMMER SIDE.mp3",
    "Soundbound/Soundbound/IN.SHABINDER.SOUNDBOUND.EXTENSIONS.SPOTIFY/Operation Pine Soot_塞壬唱片-MSR.m4a",
]

def main():
    print("Moviendo archivos corruptos a _corrupted/")
    print("=" * 60)
    
    CORRUPTED_DIR.mkdir(exist_ok=True)
    
    moved = 0
    for rel_path in corrupted_files:
        src = MUSIC_DIR / rel_path
        dst = CORRUPTED_DIR / rel_path
        
        if not src.exists():
            print(f"  No encontrado: {rel_path}")
            continue
        
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        print(f"  Movido: {rel_path}")
        moved += 1
    
    print()
    print(f"Archivos movidos: {moved}/{len(corrupted_files)}")
    print(f"Ubicación: {CORRUPTED_DIR}")

if __name__ == "__main__":
    main()
