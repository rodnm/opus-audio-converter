#!/usr/bin/env python3
import os
import subprocess
from pathlib import Path

BACKUP_DIR = Path("/storage/emulated/0/SdCardBackUp/Music/_backup")
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".opus", ".flac", ".ogg", ".wma", ".wav", ".aac"}

def find_audio_files(directory):
    audio_files = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            filepath = Path(root) / file
            if filepath.suffix.lower() in AUDIO_EXTENSIONS:
                audio_files.append(filepath)
    return audio_files

def try_conversion(filepath):
    output = filepath.parent / f"test_{filepath.stem}.opus"
    result = subprocess.run(
        ['ffmpeg', '-i', str(filepath), '-c:a', 'libopus', '-b:a', '192k',
         '-vbr', 'on', '-compression_level', '10', '-application', 'audio',
         '-vn', '-y', str(output)],
        capture_output=True, text=True, encoding='utf-8', errors='replace'
    )
    if output.exists():
        output.unlink()
    return result.returncode == 0, result.stderr

def main():
    print("Buscando archivos fallidos en backup...")
    print("=" * 60)
    
    backup_files = find_audio_files(BACKUP_DIR)
    print(f"Total de archivos en backup: {len(backup_files)}")
    print()
    
    failed_files = []
    
    for i, filepath in enumerate(backup_files, 1):
        if i % 50 == 0:
            print(f"Progreso: {i}/{len(backup_files)}")
        
        success, error = try_conversion(filepath)
        if not success:
            failed_files.append((filepath, error))
    
    print()
    print("=" * 60)
    print(f"Archivos que fallaron en conversión: {len(failed_files)}")
    print("=" * 60)
    
    if not failed_files:
        print("No se encontraron archivos fallidos")
        return
    
    for i, (filepath, error) in enumerate(failed_files, 1):
        print(f"\n[{i}/{len(failed_files)}] {filepath.name}")
        print(f"  Ruta: {filepath}")
        print(f"  Tamaño: {filepath.stat().st_size} bytes")
        print(f"  Error de ffmpeg:")
        print(f"  {error[:500]}")
        print()

if __name__ == "__main__":
    main()
