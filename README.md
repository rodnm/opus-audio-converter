# Opus Audio Converter

Convierte archivos de audio (MP3, M4A, FLAC, OGG, WMA, WAV, AAC) a
Opus 192k VBR preservando metadatos, caratulas y estructura de
directorios. Procesamiento paralelo, checkpoint/resume, validacion
pre/post conversion y sistema de backup automatico.

## Estructura del Proyecto

```
.
├── convert_to_opus.py            # Script principal de conversion
├── restore_for_reconversion.py   # Restaura originales desde _backup
├── diagnose_failures.py          # Diagnostico de archivos sin .opus
├── find_failures.py              # Prueba conversion en archivos fallidos
├── move_corrupted.py             # Aisla archivos corruptos conocidos
├── requirements.txt              # Dependencias Python
├── _backup/                      # Originales respaldados (822 archivos)
├── _corrupted/                   # Archivos corruptos aislados
├── conversion.log                # Log detallado con rotacion (5 MB x 3)
├── .conversion_progress.json     # Checkpoint para --resume
└── conversion_failures.log       # Fallos de la ultima ejecucion
```

## Dependencias

**Python**: `mutagen` — lectura/escritura de metadatos multi-formato.

```bash
pip install -r requirements.txt
```

**Sistema**: `ffmpeg` — conversion de audio y extraccion de caratulas.

```bash
pkg install ffmpeg        # Termux (Android)
apt install ffmpeg        # Debian/Ubuntu
brew install ffmpeg       # macOS
```

## Uso

```bash
# Simulacion (sin modificar archivos)
python3 convert_to_opus.py --dry-run

# Conversion completa con todos los CPU
python3 convert_to_opus.py

# Limitar workers paralelos
python3 convert_to_opus.py --workers 4

# Reanudar tras interrupcion
python3 convert_to_opus.py --resume

# Bitrate personalizado
python3 convert_to_opus.py --bitrate 128k

# Salida detallada en consola
python3 convert_to_opus.py --verbose
```

## Flags

| Flag | Default | Descripcion |
|------|---------|-------------|
| `--dry-run` | — | Simula sin modificar archivos |
| `--resume` | — | Reanuda desde `.conversion_progress.json` |
| `--workers N` | `0` (todos) | Numero de procesos paralelos |
| `--bitrate Xk` | `192k` | Bitrate objetivo (ej. `128k`, `256k`) |
| `--verbose, -v` | — | Salida DEBUG en consola |

## Arquitectura

### Flujo de Conversion

```
Archivo original
    │
    ├─ 1. validate_input_file()   ffprobe: ¿valido? ¿tiene audio?
    ├─ 2. extract_metadata()      mutagen: ID3/MP4/Vorbis/ASF tags
    ├─ 3. extract_covers()        ffprobe + ffmpeg: imagen embebida
    ├─ 4. convert_to_opus()       ffmpeg: libopus, VBR, compression 10
    ├─ 5. verify_conversion()     ffprobe: duracion, canales, sample_rate
    ├─ 6. insert_metadata...()    mutagen OggOpus: tags + caratula base64
    └─ 7. move_to_backup()        original → _backup/ (preserva ruta)
```

### Logica Anti-Upscaling

```
bitrate_original < 192k  →  mantiene el bitrate original
bitrate_original >= 192k →  usa --bitrate (default 192k)
input .opus y >= 192k    →  SKIP (evita re-codificacion)
```

### Procesamiento Paralelo

- `ProcessPoolExecutor` con N workers (0 = `cpu_count()`)
- Workers aislados sin memoria compartida
- Nombres de salida con hash MD5 del path para evitar colisiones
- Timeout de 300 segundos por archivo
- Checkpoint automatico cada 10 archivos

### Sistema de Verificacion

| Etapa | Que verifica | Falla si |
|-------|-------------|----------|
| Pre-conversion | ffprobe, streams de audio | Archivo corrupto o sin audio |
| Post-conversion | Duracion (tol. 1.5s o 1%) | Diferencia excesiva |
| Post-conversion | Sample rate | Reduccion respecto al original |
| Post-conversion | Canales | Perdida de canales (ej. stereo → mono) |
| Post-metadatos | Tags insertados | `OggOpus.tags` es None |
| Post-carátula | Cover embebida | `metadata_block_picture` ausente |

### Sistema de Logs

| Destino | Nivel | Descripcion |
|---------|-------|-------------|
| Consola | INFO | Progreso por archivo (formato limpio) |
| `conversion.log` | DEBUG | `RotatingFileHandler` (5 MB, 3 backups) |
| `conversion_failures.log` | — | Fallos de la ultima ejecucion |
| `.conversion_progress.json` | — | Checkpoint para `--resume` |

## Formatos Soportados

| Extension | Libreria | Metadatos | Caratula |
|-----------|----------|-----------|----------|
| `.mp3` | mutagen MP3 (ID3) | Completo | Si |
| `.m4a` | mutagen MP4 | Completo | Si |
| `.flac` | mutagen FLAC | Completo | Si |
| `.ogg` | mutagen OggVorbis | Completo | Si |
| `.wav` | mutagen WAVE (ID3) | Completo | Si |
| `.wma` | mutagen ASF | Completo | Si |
| `.opus` | mutagen OggOpus | Completo | Si |

## Scripts Auxiliares

| Script | Proposito |
|--------|-----------|
| `restore_for_reconversion.py` | Restaura originales de `_backup/`, elimina `.opus` correspondientes y prepara para reconversion total |
| `diagnose_failures.py` | Busca archivos en `_backup/` sin `.opus` correspondiente y prueba conversion manual |
| `find_failures.py` | Prueba conversion ffmpeg en todos los archivos de `_backup/` para identificar corruptos |
| `move_corrupted.py` | Mueve archivos corruptos conocidos a `_corrupted/` |

### Reconversion Completa

Para regenerar todos los `.opus` desde los originales en `_backup/`:

```bash
python3 restore_for_reconversion.py
python3 convert_to_opus.py
```

## Buenas Practicas

- **Transacciones atomicas** — backup solo tras verificacion exitosa de conversion
- **Validacion pre-conversion** — ffprobe verifica integridad y streams de audio
- **Verificacion post-conversion** — duracion, sample rate y canales
- **Checkpoint / Resume** — `.conversion_progress.json` permite reanudar
- **Logging estructurado** — `RotatingFileHandler` con niveles y rotacion
- **Limpieza de temporales** — `try/finally` en extraccion de covers
- **Timeout de workers** — 300s por archivo evita bloqueos
- **Limite de cover** — 10 MB maximo previene OOM
- **Nombres deterministicos** — hash MD5 del path de entrada
- **Dry-run** — simulacion sin modificar archivos
- **Progress file validado** — schema check previene crashes por JSON corrupto
- **Batch save** — checkpoint cada 10 archivos en vez de cada uno

## Troubleshooting

```
"Archivo corrupto" / "No contiene streams de audio"
    → El archivo no es valido o no tiene audio. Mover a _corrupted/.

"Duracion no coincide"
    → Diferencia > 1.5 s o 1% de la duracion original. Posible corrupcion.

"Canales no coinciden"
    → Perdida de canales (stereo → mono). Verificar ffmpeg.

"Sample rate reducido"
    → El output tiene menor sample rate que el input. Conversion defectuosa.

"TIMEOUT: archivo tardo mas de 300s"
    → Archivo muy grande o corrupto que bloqueo al worker.

"Progress file corrupto"
    → Se reinicia automaticamente. Eliminar .conversion_progress.json si persiste.

"Tags vacios despues de insercion"
    → Warning no critico. El audio es valido pero sin metadatos.

"Caratula no se inserto"
    → Warning no critico. El audio es valido pero sin imagen de portada.

"Cover demasiado grande, omitiendo"
    → La imagen embebida excede 10 MB. Se omite para prevenir OOM.
```

## Configuracion

Las constantes principales estan al inicio de `convert_to_opus.py`:

```python
MUSIC_DIR = Path("/storage/emulated/0/SdCardBackUp/Music")
BACKUP_DIR = MUSIC_DIR / "_backup"
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".opus", ".flac", ".ogg", ".wma", ".wav", ".aac"}
BITRATE = "192k"
MAX_FILENAME_LENGTH = 200
SAVE_PROGRESS_INTERVAL = 10   # checkpoint cada N archivos
WORKER_TIMEOUT = 300           # segundos por archivo
MAX_COVER_SIZE = 10 * 1024 * 1024  # 10 MB
```

## Resultados

Conversion de 822 archivos originales (792 MP3 + 25 M4A + 5 OPUS):

```
Originales (_backup):  7,644 MB
Opus convertidos:      4,800 MB
Ahorro:                2,844 MB  (37.2%)
```
