#!/usr/bin/env python3
import os
import subprocess
from pathlib import Path

MUSIC_DIR = Path("/storage/emulated/0/SdCardBackUp/Music")
BACKUP_DIR = MUSIC_DIR / "_backup"

def find_backup_files():
    backup_files = []
    for root, dirs, files in os.walk(BACKUP_DIR):
        for file in files:
            filepath = Path(root) / file
            backup_files.append(filepath)
    return backup_files

def get_expected_opus_path(backup_path):
    rel_path = backup_path.relative_to(BACKUP_DIR)
    opus_path = MUSIC_DIR / rel_path.with_suffix('.opus')
    return opus_path

def check_file_integrity(filepath):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_format', str(filepath)],
        capture_output=True, text=True, encoding='utf-8', errors='replace'
    )
    return result.returncode == 0, result.stderr

def try_manual_conversion(filepath):
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
    print("Diagnóstico de archivos fallidos")
    print("=" * 60)
    
    backup_files = find_backup_files()
    print(f"Archivos en backup: {len(backup_files)}")
    
    failed_files = []
    for backup_path in backup_files:
        expected_opus = get_expected_opus_path(backup_path)
        
        opus_exists = False
        if expected_opus.exists():
            opus_exists = True
        else:
            parent = expected_opus.parent
            stem = expected_opus.stem
            for i in range(2, 100):
                alt_path = parent / f"{stem}_{i}.opus"
                if alt_path.exists():
                    opus_exists = True
                    break
        
        if not opus_exists:
            failed_files.append(backup_path)
    
    print(f"Archivos sin .opus correspondiente: {len(failed_files)}")
    print()
    
    if not failed_files:
        print("No se encontraron archivos fallidos")
        return
    
    print("=" * 60)
    print("Análisis de archivos fallidos")
    print("=" * 60)
    
    for i, filepath in enumerate(failed_files, 1):
        print(f"\n[{i}/{len(failed_files)}] {filepath.name}")
        print(f"  Ruta: {filepath}")
        print(f"  Tamaño: {filepath.stat().st_size} bytes")
        
        integrity_ok, integrity_error = check_file_integrity(filepath)
        if integrity_ok:
            print(f"  Integridad: OK")
        else:
            print(f"  Integridad: FALLIDA")
            if integrity_error:
                print(f"  Error: {integrity_error[:200]}")
        
        conversion_ok, conversion_error = try_manual_conversion(filepath)
        if conversion_ok:
            print(f"  Conversión manual: OK")
        else:
            print(f"  Conversión manual: FALLIDA")
            if conversion_error:
                print(f"  Error: {conversion_error[:300]}")

if __name__ == "__main__":
    main()
