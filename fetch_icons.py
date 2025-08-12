"""
fetch_icons.py

Читает JSON-файл с ярлыками (массив объектов с полями title, url, icon),
скачивает иконки в указанную папку и подставляет локальные пути в JSON.

Особенности этого варианта:
- Поддерживает и сохраняет русские (и любые юникодные) названия файлов, когда это возможно.
- Если файловая система не позволяет создать файл с таким именем, делается безопасный fallback-имя (ASCII).
- Делает резервную копию исходного JSON перед перезаписью.
- Параллельная загрузка через ThreadPoolExecutor.
- При ошибке скачивания оставляет оригинальный URL в поле `icon` (не затирает).
- Записывает JSON с ensure_ascii=False (чтобы русские заголовки остались в файле).

Пример:
    uv run fetch_icons.py --input bookmarks.json --outdir /home/New_tab/icons/

Зависимости:
    uv add requests tqdm

"""

from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Tuple
from urllib.parse import unquote, urlparse

import requests
from tqdm import tqdm
import mimetypes

# ---------- Конфигурация ----------
DEFAULT_TIMEOUT = 8  # секунд на HTTP запрос
MAX_WORKERS = 8      # число concurrent загрузок (можно уменьшить)
# ----------------------------------

# Content-Type -> расширение
CONTENT_TYPE_EXT = {
    'image/png': '.png',
    'image/x-icon': '.ico',
    'image/vnd.microsoft.icon': '.ico',
    'image/vnd.microsoft.icon; charset=binary': '.ico',
    'image/jpeg': '.jpg',
    'image/jpg': '.jpg',
    'image/svg+xml': '.svg',
    'image/webp': '.webp',
    'image/gif': '.gif',
    'image/vnd.microsoft.icon; charset=utf-8': '.ico',
}


def safe_filename_unicode(name: str, max_len: int = 120) -> str:
    """
    Возвращает безопасное имя файла, позволяя юникодные буквы (включая кириллицу),
    цифры, подчёркивания, дефисы и точки. Удаляет слэши и управляющие символы.
    """
    if not name:
        return 'unnamed'
    name = unquote(name).strip()
    # удалим путь и лишние пробелы
    name = name.replace('/', '_').replace('\\', '_')
    # заменим последовательности пробельных символов
    name = re.sub(r'\s+', '_', name)
    # Разрешаем unicode-слова (\w в Python поддерживает юникод), плюс точки, дефисы и подчёркивания
    # Удаляем всё, что не является словом или .-_
    # Флаг re.UNICODE по умолчанию активен в Python3
    name = re.sub(r'[^\w\-\._]', '', name)
    # Обрежем до max_len
    if len(name) > max_len:
        name = name[:max_len]
    # Если после очистки пусто, вернём 'file'
    if not name:
        return 'file'
    return name


def ascii_fallback_name(name: str, max_len: int = 80) -> str:
    """
    Попытка привести имя к ASCII: удалить не-ASCII символы.
    Если после этого пусто — вернуть 'file'.
    """
    try:
        ascii_name = name.encode('ascii', errors='ignore').decode('ascii')
        ascii_name = re.sub(r'[^A-Za-z0-9\-\._]', '', ascii_name)
        ascii_name = ascii_name.strip() or 'file'
        if len(ascii_name) > max_len:
            ascii_name = ascii_name[:max_len]
        return ascii_name
    except Exception:
        return 'file'


def ext_from_url(url: str) -> str:
    """Попытка взять расширение из URL-пути."""
    try:
        p = urlparse(url).path
        _, ext = os.path.splitext(p)
        return ext.lower()
    except Exception:
        return ''


def choose_extension(resp: requests.Response, url: str) -> str:
    """
    Определяем расширение файла по Content-Type, затем по URL, иначе .png.
    """
    ct = resp.headers.get('Content-Type', '').split(';')[0].strip().lower()
    if ct in CONTENT_TYPE_EXT:
        return CONTENT_TYPE_EXT[ct]
    # по URL
    ext = ext_from_url(url)
    if ext and len(ext) <= 5:
        return ext
    # по mime
    guessed, _ = mimetypes.guess_type(url)
    if guessed and guessed in CONTENT_TYPE_EXT:
        return CONTENT_TYPE_EXT[guessed]
    # fallback
    return '.png'


def download_icon(session: requests.Session, item: dict, outdir: Path, timeout: int = DEFAULT_TIMEOUT) -> Tuple[bool, str, int]:
    """
    Скачивает и сохраняет иконку.
    Возвращает (ok: bool, info: str (path or error), index)
    """
    title = (item.get('title') or '').strip()
    url = (item.get('url') or '').strip()
    icon_url = (item.get('icon') or '').strip()

    if not icon_url:
        return False, 'no_icon_url', -1

    # базовое имя: title или hostname или "bookmark"
    base_name = title or (urlparse(url).hostname or 'bookmark')
    base_name = safe_filename_unicode(base_name)

    headers = {'User-Agent': 'Mozilla/5.0 (compatible; fetch-icons/1.0)'}
    try:
        resp = session.get(icon_url, headers=headers, timeout=timeout, allow_redirects=True, stream=True)
    except Exception as e:
        return False, f'error:{e}', -1

    if resp.status_code != 200:
        return False, f'http_{resp.status_code}', -1

    # определяем расширение
    ext = choose_extension(resp, icon_url)
    filename = f"{base_name}{ext}"
    filepath = outdir / filename
    idx = 1
    # Если такой файл существует — добавим индекс
    while filepath.exists():
        filename = f"{base_name}_{idx}{ext}"
        filepath = outdir / filename
        idx += 1

    # Запись в файл: пробуем сохранить с юникодным именем
    try:
        with open(filepath, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return True, str(filepath.resolve()), -1
    except OSError as e:
        # Падение может быть из-за недопустимых символов в имени на некоторой ФС.
        # Попробуем сделать ASCII-fallback имя
        ascii_base = ascii_fallback_name(base_name)
        filename2 = f"{ascii_base}{ext}"
        filepath2 = outdir / filename2
        idx2 = 1
        while filepath2.exists():
            filename2 = f"{ascii_base}_{idx2}{ext}"
            filepath2 = outdir / filename2
            idx2 += 1
        try:
            with open(filepath2, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            return True, str(filepath2.resolve()), -1
        except Exception as e2:
            return False, f'write_error:{e2}', -1
    except Exception as e:
        return False, f'error:{e}', -1


def process_all(input_path: Path, outdir: Path, backup: bool = True, workers: int = MAX_WORKERS, timeout: int = DEFAULT_TIMEOUT, write_relative: bool = False) -> None:
    """
    Главная функция:
    - читает JSON
    - скачивает иконки в outdir
    - при успешной загрузке заменяет item['icon'] на локальный путь (абсолютный или относительный)
    - сохраняет JSON обратно (с резервной копией)
    """
    if not input_path.exists():
        raise FileNotFoundError(f"Input file {input_path} not found")
    outdir.mkdir(parents=True, exist_ok=True)

    # читаем JSON
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("Input JSON must be an array of bookmark objects")

    # резервная копия
    if backup:
        bak = input_path.with_suffix(input_path.suffix + '.bak')
        shutil.copy2(input_path, bak)
        print(f"[INFO] Backup created: {bak}")

    session = requests.Session()
    results = []  # list of tuples (index, ok, info)

    # Подготовим список задач: сохраняем индексы для привязки результатов
    tasks = []
    for i, item in enumerate(data):
        icon_url = (item.get('icon') or '').strip()
        if not icon_url:
            # пропускаем — пометим сразу
            results.append((i, False, 'no_icon_url'))
            continue
        tasks.append((i, item))

    # Скачиваем параллельно
    if tasks:
        with ThreadPoolExecutor(max_workers=workers) as exe:
            futures = {exe.submit(download_icon, session, item, outdir, timeout): idx for idx, item in tasks}
            for fut in tqdm(as_completed(futures), total=len(futures), desc="Downloading icons"):
                idx = futures[fut]
                try:
                    ok, info, _ = fut.result()
                    results.append((idx, ok, info))
                except Exception as e:
                    results.append((idx, False, f'exception:{e}'))

    # применяем результаты: если ok — заменяем data[idx]['icon'] на локальный путь (либо относительный)
    success_count = 0
    fail_count = 0
    for idx, ok, info in results:
        if ok:
            success_count += 1
            if write_relative:
                # относительный путь от директории с JSON
                try:
                    rel = Path(info).relative_to(input_path.parent)
                    data[idx]['icon'] = str(rel)
                except Exception:
                    data[idx]['icon'] = str(info)
            else:
                data[idx]['icon'] = str(info)
        else:
            fail_count += 1
            # оставляем прежний icon (не затираем)
            # при желании можно сделать data[idx]['icon'] = ''
            pass

    # пишем во временный файл, затем атомарно заменяем
    tmp_path = input_path.with_suffix(input_path.suffix + '.tmp')
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    shutil.move(str(tmp_path), str(input_path))

    print(f"[DONE] Success: {success_count}, Fail: {fail_count}. JSON updated: {input_path}")


def main():
    parser = argparse.ArgumentParser(description="Download bookmark icons and replace JSON icon URLs with local paths")
    parser.add_argument('--input', '-i', required=True, help='path to bookmarks.json (export file)')
    parser.add_argument('--outdir', '-o', required=True, help='output directory where to save icons (absolute preferred)')
    parser.add_argument('--no-backup', dest='backup', action='store_false', help='do not create a .bak backup')
    parser.add_argument('--workers', '-w', type=int, default=MAX_WORKERS, help='concurrent download threads')
    parser.add_argument('--timeout', type=int, default=DEFAULT_TIMEOUT, help='HTTP timeout seconds per request')
    parser.add_argument('--relative', dest='relative', action='store_true', help='store relative paths in JSON (relative to JSON file location)')
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    outdir = Path(args.outdir).expanduser().resolve()

    try:
        process_all(input_path=input_path, outdir=outdir, backup=args.backup, workers=args.workers, timeout=args.timeout, write_relative=args.relative)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
