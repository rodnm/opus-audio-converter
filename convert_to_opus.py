#!/usr/bin/env python3
import os
import re
import sys
import hashlib
import json
import shutil
import subprocess
import base64
import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing

from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.flac import Picture, FLAC
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE
from mutagen.asf import ASF
from mutagen.id3 import APIC, PictureType

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

MUSIC_DIR = Path("/storage/emulated/0/SdCardBackUp/Music")
BACKUP_DIR = MUSIC_DIR / "_backup"
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".opus", ".flac", ".ogg", ".wma", ".wav", ".aac"}
BITRATE = "192k"
MAX_FILENAME_LENGTH = 200
PROGRESS_FILE = MUSIC_DIR / '.conversion_progress.json'
SAVE_PROGRESS_INTERVAL = 10
WORKER_TIMEOUT = 300
MAX_COVER_SIZE = 10 * 1024 * 1024


def setup_logging(verbose=False):
    log_format = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    file_handler = RotatingFileHandler(
        MUSIC_DIR / 'conversion.log',
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(log_format)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler.setFormatter(logging.Formatter('%(message)s'))

    logger = logging.getLogger('opus_converter')
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger


log = setup_logging()


def load_progress():
    if not PROGRESS_FILE.exists():
        return {'processed': [], 'failed': [], 'skipped': []}

    try:
        with open(PROGRESS_FILE, 'r') as f:
            data = json.load(f)

        if not isinstance(data, dict):
            log.warning("Progress file corrupto (no es dict), reiniciando")
            return {'processed': [], 'failed': [], 'skipped': []}

        required_keys = {'processed', 'failed', 'skipped'}
        if not required_keys.issubset(data.keys()):
            log.warning("Progress file incompleto, reiniciando")
            return {'processed': [], 'failed': [], 'skipped': []}

        for key in required_keys:
            if not isinstance(data[key], list):
                log.warning(f"Progress file[{key}] no es lista, reiniciando")
                return {'processed': [], 'failed': [], 'skipped': []}

        return data
    except (json.JSONDecodeError, IOError) as e:
        log.warning(f"Error leyendo progress file: {e}, reiniciando")
        return {'processed': [], 'failed': [], 'skipped': []}


def save_progress(progress):
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f, indent=2)


def clear_progress():
    if PROGRESS_FILE.exists():
        PROGRESS_FILE.unlink()


def cleanup_temp_files():
    count = 0
    for temp_file in MUSIC_DIR.rglob('_cover_temp_*.jpg'):
        try:
            temp_file.unlink()
            count += 1
        except Exception:
            pass
    if count > 0:
        log.info(f"Limpiados {count} archivos temporales huerfanos")


def check_dependencies():
    errors = []
    if not shutil.which('ffmpeg'):
        errors.append("ffmpeg no encontrado. Instala: pkg install ffmpeg")
    try:
        import mutagen
    except ImportError:
        errors.append("mutagen no instalado. Instala: pip3 install mutagen")
    if errors:
        log.critical("Dependencias faltantes")
        for err in errors:
            log.critical(f"  - {err}")
        sys.exit(1)


def sanitize_filename(name):
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = name.strip('. ')
    return name[:MAX_FILENAME_LENGTH] if name else "Unknown"


def extract_metadata_mp3(filepath):
    audio = MP3(filepath)
    tags = audio.tags or {}

    def get_text(frame_id):
        frame = tags.get(frame_id)
        return str(frame.text[0]) if frame and frame.text else None

    metadata = {
        'title': get_text('TIT2'),
        'artist': get_text('TPE1'),
        'album': get_text('TALB'),
        'date': get_text('TDRC') or get_text('TYER'),
        'genre': get_text('TCON'),
        'tracknumber': get_text('TRCK'),
        'discnumber': get_text('TPOS'),
        'albumartist': get_text('TPE2'),
        'composer': get_text('TCOM'),
    }

    if metadata['date'] and len(metadata['date']) > 4:
        metadata['date'] = metadata['date'][:4]

    return metadata


def extract_metadata_m4a(filepath):
    audio = MP4(filepath)
    tags = audio.tags or {}

    def get_text(key):
        val = tags.get(key)
        return str(val[0]) if val else None

    metadata = {
        'title': get_text('\xa9nam'),
        'artist': get_text('\xa9ART'),
        'album': get_text('\xa9alb'),
        'date': get_text('\xa9day'),
        'genre': get_text('\xa9gen'),
        'albumartist': get_text('aART'),
        'composer': get_text('\xa9wrt'),
    }

    trkn = tags.get('trkn')
    if trkn:
        metadata['tracknumber'] = f"{trkn[0][0]}/{trkn[0][1]}"

    disk = tags.get('disk')
    if disk:
        metadata['discnumber'] = f"{disk[0][0]}/{disk[0][1]}"

    if metadata['date'] and len(metadata['date']) > 4:
        metadata['date'] = metadata['date'][:4]

    return metadata


def extract_metadata_opus(filepath):
    audio = OggOpus(filepath)
    tags = audio.tags or {}

    def get_text(key):
        val = tags.get(key)
        return str(val[0]) if val else None

    return {
        'title': get_text('title'),
        'artist': get_text('artist'),
        'album': get_text('album'),
        'date': get_text('date'),
        'genre': get_text('genre'),
        'tracknumber': get_text('tracknumber'),
        'discnumber': get_text('discnumber'),
        'albumartist': get_text('albumartist'),
        'composer': get_text('composer'),
    }


def extract_metadata_flac(filepath):
    audio = FLAC(filepath)
    tags = audio.tags or {}

    def get_text(key):
        val = tags.get(key)
        return str(val[0]) if val else None

    return {
        'title': get_text('title'),
        'artist': get_text('artist'),
        'album': get_text('album'),
        'date': get_text('date'),
        'genre': get_text('genre'),
        'tracknumber': get_text('tracknumber'),
        'discnumber': get_text('discnumber'),
        'albumartist': get_text('albumartist'),
        'composer': get_text('composer'),
    }


def extract_metadata_ogg(filepath):
    audio = OggVorbis(filepath)
    tags = audio.tags or {}

    def get_text(key):
        val = tags.get(key)
        return str(val[0]) if val else None

    return {
        'title': get_text('title'),
        'artist': get_text('artist'),
        'album': get_text('album'),
        'date': get_text('date'),
        'genre': get_text('genre'),
        'tracknumber': get_text('tracknumber'),
        'discnumber': get_text('discnumber'),
        'albumartist': get_text('albumartist'),
        'composer': get_text('composer'),
    }


def extract_metadata_wav(filepath):
    audio = WAVE(filepath)
    tags = audio.tags or {}

    def get_text(frame_id):
        frame = tags.get(frame_id)
        return str(frame.text[0]) if frame and frame.text else None

    metadata = {
        'title': get_text('TIT2'),
        'artist': get_text('TPE1'),
        'album': get_text('TALB'),
        'date': get_text('TDRC') or get_text('TYER'),
        'genre': get_text('TCON'),
        'tracknumber': get_text('TRCK'),
        'discnumber': get_text('TPOS'),
        'albumartist': get_text('TPE2'),
        'composer': get_text('TCOM'),
    }

    if metadata['date'] and len(metadata['date']) > 4:
        metadata['date'] = metadata['date'][:4]

    return metadata


def extract_metadata_wma(filepath):
    audio = ASF(filepath)
    tags = audio.tags or {}

    def get_text(key):
        val = tags.get(key)
        return str(val[0]) if val else None

    metadata = {
        'title': get_text('Title'),
        'artist': get_text('Author'),
        'album': get_text('WM/AlbumTitle'),
        'date': get_text('WM/Year'),
        'genre': get_text('WM/Genre'),
        'tracknumber': get_text('WM/TrackNumber'),
        'albumartist': get_text('WM/AlbumArtist'),
        'composer': get_text('WM/Composer'),
    }

    if metadata['date'] and len(metadata['date']) > 4:
        metadata['date'] = metadata['date'][:4]

    return metadata


def extract_metadata(filepath):
    ext = filepath.suffix.lower()
    if ext == '.mp3':
        return extract_metadata_mp3(filepath)
    elif ext == '.m4a':
        return extract_metadata_m4a(filepath)
    elif ext == '.opus':
        return extract_metadata_opus(filepath)
    elif ext == '.flac':
        return extract_metadata_flac(filepath)
    elif ext == '.ogg':
        return extract_metadata_ogg(filepath)
    elif ext == '.wav':
        return extract_metadata_wav(filepath)
    elif ext == '.wma':
        return extract_metadata_wma(filepath)
    return {}


def has_cover_art(filepath):
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'stream=codec_type',
         '-of', 'json', str(filepath)],
        capture_output=True, text=True, encoding='utf-8', errors='replace'
    )
    if result.returncode != 0 or not result.stdout.strip():
        return False
    try:
        data = json.loads(result.stdout)
        streams = data.get('streams', [])
        return any(s.get('codec_type') == 'video' for s in streams)
    except json.JSONDecodeError:
        return False


def extract_cover_art(filepath, output_path):
    result = subprocess.run(
        ['ffmpeg', '-y', '-i', str(filepath), '-an', '-c:v', 'copy', str(output_path)],
        capture_output=True, text=True
    )
    return result.returncode == 0 and os.path.exists(output_path)


def extract_covers(filepath):
    covers = []
    temp_cover = filepath.parent / f'_cover_temp_{os.getpid()}.jpg'

    try:
        if has_cover_art(filepath):
            if extract_cover_art(filepath, temp_cover):
                with open(temp_cover, 'rb') as f:
                    cover_data = f.read(MAX_COVER_SIZE + 1)
                if len(cover_data) > MAX_COVER_SIZE:
                    log.warning(f"Cover demasiado grande, omitiendo: {filepath.name}")
                    return covers

                mime = 'image/jpeg'
                if cover_data[:4] == b'\x89PNG':
                    mime = 'image/png'
                elif cover_data[:4] == b'GIF8':
                    mime = 'image/gif'

                covers.append({
                    'data': cover_data,
                    'mime': mime,
                    'type': PictureType.COVER_FRONT,
                    'desc': '',
                })
    finally:
        if temp_cover.exists():
            temp_cover.unlink()

    return covers


def validate_input_file(filepath):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(filepath)],
        capture_output=True, text=True, encoding='utf-8', errors='replace'
    )
    if result.returncode != 0:
        return False, f"Archivo corrupto: {result.stderr[:200]}"

    try:
        data = json.loads(result.stdout)
        streams = data.get('streams', [])
        audio_streams = [s for s in streams if s.get('codec_type') == 'audio']
        if not audio_streams:
            return False, "No contiene streams de audio"
    except json.JSONDecodeError:
        return False, "No se pudo analizar salida de ffprobe"

    return True, None


def get_audio_info(path):
    cmd = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration:stream=codec_type,sample_rate,channels',
        '-of', 'json', str(path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def verify_conversion(original_path, converted_path):
    orig_info = get_audio_info(original_path)
    conv_info = get_audio_info(converted_path)

    if not orig_info:
        return False, f"No se pudo analizar original: {original_path.name}"
    if not conv_info:
        return False, f"No se pudo analizar convertido: {converted_path.name}"

    orig_dur = float(orig_info.get('format', {}).get('duration', 0))
    conv_dur = float(conv_info.get('format', {}).get('duration', 0))

    if orig_dur > 0:
        tolerance = max(1.5, orig_dur * 0.01)
        if abs(orig_dur - conv_dur) > tolerance:
            return False, f"Duracion no coincide: {orig_dur:.1f}s vs {conv_dur:.1f}s"

    orig_streams = orig_info.get('streams', [])
    conv_streams = conv_info.get('streams', [])

    orig_audio = next((s for s in orig_streams if s.get('codec_type') == 'audio'), None)
    conv_audio = next((s for s in conv_streams if s.get('codec_type') == 'audio'), None)

    if orig_audio and conv_audio:
        orig_sr = orig_audio.get('sample_rate')
        conv_sr = conv_audio.get('sample_rate')
        if orig_sr and conv_sr and conv_sr < orig_sr:
            return False, f"Sample rate reducido: {orig_sr} vs {conv_sr}"
        if orig_audio.get('channels') != conv_audio.get('channels'):
            return False, f"Canales no coinciden: {orig_audio.get('channels')} vs {conv_audio.get('channels')}"

    return True, None


def generate_filename(metadata):
    artist = sanitize_filename(metadata.get('artist') or 'Unknown')
    year = metadata.get('date') or 'Unknown'
    if year != 'Unknown':
        year = sanitize_filename(year)
    title = sanitize_filename(metadata.get('title') or 'Unknown')

    return f"{artist}_{year}_{title}.opus"


def get_audio_bitrate(filepath):
    try:
        cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=bit_rate',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            str(filepath)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        if result.returncode == 0 and result.stdout.strip():
            bitrate = int(result.stdout.strip())
            return bitrate // 1000
    except (ValueError, subprocess.SubprocessError):
        pass
    return None


def convert_to_opus(input_path, output_path, bitrate=BITRATE):
    cmd = [
        'ffmpeg', '-i', str(input_path),
        '-c:a', 'libopus',
        '-b:a', bitrate,
        '-vbr', 'on',
        '-compression_level', '10',
        '-application', 'audio',
        '-vn',
        '-y',
        str(output_path)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    return result.returncode == 0, result.stderr


def insert_metadata_and_covers(filepath, metadata, covers):
    audio = OggOpus(filepath)
    if audio.tags is None:
        audio.add_tags()

    tags = audio.tags

    field_mapping = {
        'title': 'title',
        'artist': 'artist',
        'album': 'album',
        'date': 'date',
        'genre': 'genre',
        'tracknumber': 'tracknumber',
        'discnumber': 'discnumber',
        'albumartist': 'albumartist',
        'composer': 'composer',
    }

    for key, value in metadata.items():
        if value and key in field_mapping:
            tags[field_mapping[key]] = [value]

    if covers:
        picture_blocks = []
        for cover in covers:
            picture = Picture()
            picture.data = cover['data']
            picture.mime = cover['mime']
            picture.type = cover['type']
            picture.desc = cover['desc']
            picture.width = 0
            picture.height = 0
            picture.depth = 0

            picture_data = picture.write()
            encoded_data = base64.b64encode(picture_data).decode('ascii')
            picture_blocks.append(encoded_data)

        tags['metadata_block_picture'] = picture_blocks

    audio.save()


def verify_opus_cover(opus_path):
    try:
        audio = OggOpus(str(opus_path))
        return 'metadata_block_picture' in audio
    except Exception:
        return False


def move_to_backup(original_path):
    rel_path = original_path.relative_to(MUSIC_DIR)
    backup_path = BACKUP_DIR / rel_path
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(original_path), str(backup_path))


def get_unique_filepath(filepath, input_path=None):
    if not filepath.exists():
        return filepath

    stem = filepath.stem
    suffix = filepath.suffix
    parent = filepath.parent

    if input_path:
        file_hash = hashlib.md5(str(input_path).encode()).hexdigest()[:8]
        return parent / f"{stem}_{file_hash}{suffix}"

    counter = 2
    while True:
        new_path = parent / f"{stem}_{counter}{suffix}"
        if not new_path.exists():
            return new_path
        counter += 1


def process_file(filepath, dry_run=False):
    result = {
        'status': 'failed',
        'input': str(filepath),
        'output': None,
        'error': None,
        'size_before': filepath.stat().st_size,
        'size_after': 0,
        'has_cover': False,
        'cover_verified': False,
        'cover_expected': False,
    }

    try:
        valid, val_error = validate_input_file(filepath)
        if not valid:
            result['error'] = f"Validacion: {val_error}"
            return result

        if filepath.suffix.lower() == '.opus':
            opus_br = get_audio_bitrate(filepath)
            if opus_br and opus_br >= 192:
                result['status'] = 'skipped'
                result['error'] = f"Ya es Opus {opus_br}kbps"
                return result

        try:
            metadata = extract_metadata(filepath)
        except Exception as e:
            log.warning(f"No se pudieron extraer metadatos de {filepath.name}: {e}")
            metadata = {}
        new_filename = generate_filename(metadata)
        output_path = filepath.parent / new_filename

        if output_path.exists():
            result['status'] = 'skipped'
            result['error'] = f"Ya existe: {output_path.name}"
            return result

        output_path = get_unique_filepath(output_path, input_path=filepath)

        if dry_run:
            result['status'] = 'success'
            result['output'] = str(output_path)
            return result

        covers = extract_covers(filepath)
        result['has_cover'] = len(covers) > 0
        result['cover_expected'] = len(covers) > 0

        original_bitrate = get_audio_bitrate(filepath)
        if original_bitrate and original_bitrate < 192:
            target_bitrate = f"{original_bitrate}k"
        else:
            target_bitrate = BITRATE

        success, ffmpeg_error = convert_to_opus(filepath, output_path, target_bitrate)
        if not success:
            result['error'] = f"Conversion fallida: {ffmpeg_error[:300]}"
            return result

        verified, verify_error = verify_conversion(filepath, output_path)
        if not verified:
            if output_path.exists():
                output_path.unlink()
            result['error'] = f"Verificacion fallida: {verify_error}"
            return result

        insert_metadata_and_covers(output_path, metadata, covers)

        try:
            test_audio = OggOpus(output_path)
            if test_audio.tags is None:
                log.warning(f"Tags vacios despues de insercion en {output_path.name}")
        except Exception as e:
            log.warning(f"Error verificando tags en {output_path.name}: {e}")

        if covers:
            if verify_opus_cover(output_path):
                result['cover_verified'] = True
            else:
                log.warning(f"Caratula no se inserto en {output_path.name}")

        try:
            move_to_backup(filepath)
        except Exception as e:
            log.warning(f"No se pudo mover a backup {filepath.name}: {e}")

        result['status'] = 'success'
        result['output'] = str(output_path)
        result['size_after'] = output_path.stat().st_size

    except Exception as e:
        result['error'] = str(e)

    return result


def main():
    parser = argparse.ArgumentParser(
        description='Convierte archivos de audio a Opus con metadatos y carátulas'
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Muestra qué se haría sin ejecutar cambios'
    )
    parser.add_argument(
        '--resume', action='store_true',
        help='Reanuda desde el último checkpoint'
    )
    parser.add_argument(
        '--workers', type=int, default=0,
        help='Número de workers (0 = todos los CPU)'
    )
    parser.add_argument(
        '--bitrate', type=str, default='192k',
        help='Bitrate objetivo (ej. 192k, 128k)'
    )
    parser.add_argument(
        '--verbose', '-v', action='store_true',
        help='Salida detallada en consola'
    )
    args = parser.parse_args()

    global log, BITRATE
    log = setup_logging(verbose=args.verbose)
    BITRATE = args.bitrate

    check_dependencies()

    log.info("=== Iniciando conversion de audio a Opus ===")
    log.info(f"Bitrate objetivo: {BITRATE}")
    log.info(f"Directorio: {MUSIC_DIR}")
    log.info(f"Backup: {BACKUP_DIR}")

    if args.dry_run:
        log.info("Modo: DRY RUN (sin cambios)")

    audio_files = []
    for root, dirs, files in os.walk(MUSIC_DIR):
        if '_backup' in dirs:
            dirs.remove('_backup')
        if '_corrupted' in dirs:
            dirs.remove('_corrupted')

        for file in files:
            filepath = Path(root) / file
            if filepath.suffix.lower() in AUDIO_EXTENSIONS:
                audio_files.append(filepath)

    total_files = len(audio_files)
    log.info(f"Encontrados {total_files} archivos de audio")

    BACKUP_DIR.mkdir(exist_ok=True)
    cleanup_temp_files()

    progress = load_progress() if args.resume else {'processed': [], 'failed': [], 'skipped': []}

    if args.resume and progress.get('processed'):
        already = len(progress['processed'])
        remaining = len(set(str(f) for f in audio_files) - set(progress['processed']))
        log.info(f"Reanudando: {already} ya procesados, {remaining} restantes")

    success_count = 0
    failed_count = 0
    skipped_count = 0
    original_size = 0
    new_size = 0
    failures = []

    workers = args.workers or multiprocessing.cpu_count()

    if workers > 1 and not args.dry_run:
        log.info(f"Usando {workers} workers en paralelo")

        pending = []
        for idx, filepath in enumerate(audio_files, 1):
            filepath_str = str(filepath)
            if filepath_str in progress['processed']:
                skipped_count += 1
                continue
            if filepath_str in progress['skipped']:
                skipped_count += 1
                continue
            pending.append((filepath, idx))

        with ProcessPoolExecutor(max_workers=workers) as executor:
            future_map = {}
            for filepath, idx in pending:
                future = executor.submit(process_file, filepath, args.dry_run)
                future_map[future] = (filepath, idx)

            completed = 0
            processed_since_save = 0
            for future in as_completed(future_map):
                completed += 1
                try:
                    result = future.result(timeout=WORKER_TIMEOUT)
                except TimeoutError:
                    log.error(f"[{completed}/{len(pending)}] TIMEOUT: archivo tardo mas de {WORKER_TIMEOUT}s")
                    failed_count += 1
                    continue
                except Exception as e:
                    log.error(f"[{completed}/{len(pending)}] Error inesperado: {e}")
                    failed_count += 1
                    continue

                filepath_str = result['input']
                fname = Path(filepath_str).name

                if result['status'] == 'success':
                    log.info(f"[{completed}/{len(pending)}] OK  {fname}")
                    progress['processed'].append(filepath_str)
                    success_count += 1
                    original_size += result['size_before']
                    new_size += result['size_after']
                    processed_since_save += 1
                elif result['status'] == 'skipped':
                    log.info(f"[{completed}/{len(pending)}] SKIP {fname}")
                    progress['skipped'].append(filepath_str)
                    skipped_count += 1
                    processed_since_save += 1
                elif result['status'] == 'failed':
                    log.error(f"[{completed}/{len(pending)}] FAIL {fname}: {result.get('error', '')[:120]}")
                    progress['failed'].append(filepath_str)
                    failed_count += 1
                    failures.append((Path(filepath_str), result.get('error')))
                    processed_since_save += 1

                if processed_since_save >= SAVE_PROGRESS_INTERVAL:
                    save_progress(progress)
                    processed_since_save = 0

            if processed_since_save > 0:
                save_progress(progress)
    else:
        processed_since_save = 0
        for idx, filepath in enumerate(audio_files, 1):
            filepath_str = str(filepath)
            if filepath_str in progress['processed']:
                log.info(f"[{idx}/{total_files}] SKIP (ya procesado) {filepath.name}")
                skipped_count += 1
                continue

            result = process_file(filepath, dry_run=args.dry_run)
            original_size += result['size_before']

            if result['status'] == 'success':
                log.info(f"[{idx}/{total_files}] OK  {filepath.name}")
                progress['processed'].append(filepath_str)
                success_count += 1
                new_size += result['size_after']
                processed_since_save += 1
            elif result['status'] == 'skipped':
                log.info(f"[{idx}/{total_files}] SKIP {filepath.name}")
                skipped_count += 1
            elif result['status'] == 'failed':
                log.error(f"[{idx}/{total_files}] FAIL {filepath.name}: {result.get('error', '')[:120]}")
                failed_count += 1
                failures.append((filepath, result.get('error')))
                processed_since_save += 1

            if processed_since_save >= SAVE_PROGRESS_INTERVAL:
                save_progress(progress)
                processed_since_save = 0

        if processed_since_save > 0:
            save_progress(progress)

    log_file = MUSIC_DIR / "conversion_failures.log"
    if failures and not args.dry_run:
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write("Archivos que fallaron en la conversion\n")
            f.write("=" * 60 + "\n\n")
            for filepath, error in failures:
                f.write(f"Archivo: {filepath}\n")
                if filepath.exists():
                    f.write(f"Tamaño: {filepath.stat().st_size} bytes\n")
                f.write(f"Error:\n{error}\n")
                f.write("-" * 60 + "\n\n")
        log.warning(f"Log de fallos guardado en: {log_file}")
    elif log_file.exists() and not failures and not args.dry_run:
        log_file.unlink()

    if not failures and not args.dry_run:
        clear_progress()

    log.info("=" * 60)
    log.info("RESUMEN")
    log.info("=" * 60)
    log.info(f"Archivos: {total_files} | OK: {success_count} | Fallidos: {failed_count} | Saltados: {skipped_count}")

    if not args.dry_run and success_count > 0:
        original_mb = original_size / (1024 * 1024)
        new_mb = new_size / (1024 * 1024)
        if original_size > 0:
            savings = ((original_size - new_size) / original_size) * 100
            log.info(f"Original: {original_mb:.2f} MB | Nuevo: {new_mb:.2f} MB | Ahorro: {savings:.1f}%")

    log.info("=" * 60)


if __name__ == "__main__":
    main()
