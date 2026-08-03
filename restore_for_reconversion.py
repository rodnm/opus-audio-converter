#!/usr/bin/env python3
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from convert_to_opus import (
    MUSIC_DIR, BACKUP_DIR, extract_metadata, generate_filename,
    get_unique_filepath, log
)

def find_opus_files():
    opus_files = []
    for root, dirs, files in os.walk(MUSIC_DIR):
        if '_backup' in dirs:
            dirs.remove('_backup')
        if '_corrupted' in dirs:
            dirs.remove('_corrupted')
        if 'tmp' in dirs:
            dirs.remove('tmp')
        for file in files:
            if file.endswith('.opus'):
                opus_files.append(Path(root) / file)
    return opus_files


def main():
    log.info("=== Restaurando archivos desde _backup para reconversion ===")

    # Encontrar todos los archivos en _backup
    backup_files = []
    for root, dirs, files in os.walk(BACKUP_DIR):
        for file in files:
            backup_files.append(Path(root) / file)

    log.info(f"Archivos en _backup: {len(backup_files)}")

    # Mapear backup -> opus correspondiente
    deleted_opus = 0
    restored = 0
    errors = []

    for backup_path in backup_files:
        rel_path = backup_path.relative_to(BACKUP_DIR)

        # Extraer metadatos del archivo en backup
        try:
            metadata = extract_metadata(backup_path)
        except Exception as e:
            errors.append(f"Metadata: {rel_path} - {e}")
            continue

        # Generar el nombre esperado del opus
        expected_name = generate_filename(metadata)
        target_dir = MUSIC_DIR / rel_path.parent
        opus_path = target_dir / expected_name

        # Eliminar el .opus si existe (incluyendo duplicados _2, _3, etc.)
        if opus_path.exists():
            opus_path.unlink()
            deleted_opus += 1

        # Buscar y eliminar variantes _2, _3, etc.
        stem = opus_path.stem
        for i in range(2, 100):
            alt_path = opus_path.parent / f"{stem}_{i}.opus"
            if alt_path.exists():
                alt_path.unlink()
                deleted_opus += 1
            else:
                break

        # Mover el original de _backup a MUSIC_DIR
        target_original = MUSIC_DIR / rel_path
        target_original.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(backup_path), str(target_original))
        restored += 1

    # Mover los mp3 de spotify-dl que ya tienen opus
    spotify_mp3_dir = MUSIC_DIR / "spotify-dl"
    for root, dirs, files in os.walk(spotify_mp3_dir):
        for file in files:
            filepath = Path(root) / file
            if filepath.suffix.lower() not in {'.mp3', '.m4a', '.flac', '.ogg', '.wav', '.aac'}:
                continue

            try:
                metadata = extract_metadata(filepath)
                expected_name = generate_filename(metadata)
                opus_check = filepath.parent / expected_name

                if opus_check.exists():
                    opus_check.unlink()
                    deleted_opus += 1
                    log.info(f"Eliminado .opus de spotify-dl: {opus_check.name}")
            except Exception:
                pass

    # Eliminar también cualquier .opus en la raíz de MUSIC_DIR que corresponda a backups
    opus_in_root = [f for f in find_opus_files()
                    if f.parent == MUSIC_DIR]
    for opus_path in opus_in_root:
        # Verificar si ya fue eliminado
        if not opus_path.exists():
            continue

        try:
            metadata = extract_metadata(opus_path)
        except Exception:
            continue

        expected_name = generate_filename(metadata)
        if opus_path.name != expected_name:
            continue

        stem = opus_path.stem
        # Verificar si hay un original correspondiente en ese directorio
        for ext in ['.mp3', '.m4a', '.flac', '.ogg', '.wav', '.aac']:
            original_candidate = MUSIC_DIR / f"{stem}{ext}"
            if original_candidate.exists():
                opus_path.unlink()
                deleted_opus += 1
                break

    # Resumen
    remaining_opus = len(find_opus_files())

    log.info("=" * 60)
    log.info(f"Restaurados: {restored}")
    log.info(f".opus eliminados: {deleted_opus}")
    log.info(f".opus restantes (sin backup conocido): {remaining_opus}")
    if errors:
        log.warning(f"Errores de metadata: {len(errors)}")
        for e in errors[:5]:
            log.warning(f"  {e}")

    log.info("=" * 60)
    log.info("Ejecuta: python3 convert_to_opus.py")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
