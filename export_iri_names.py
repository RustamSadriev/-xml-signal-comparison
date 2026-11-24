"""Дополнить сохранённые пары старый IRI -> новый IRI наименованиями из исходной OWL-модели."""

import argparse
import codecs
import csv
import re
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

from owlready2 import World, comment


IRI = r"[A-Za-z][A-Za-z0-9+.\-]*:[^\s<>]+"
PAIR = re.compile(rf"({IRI})\s+(?:->|→)\s+({IRI})")


def read_pairs(path):
    data = path.read_bytes()
    encoding = "utf-16" if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else "utf-8-sig"
    pairs = {}
    owners = {}
    for number, line in enumerate(data.decode(encoding).splitlines(), 1):
        if line.strip() == "Кортежи (старое наименование, старый IRI, новый IRI):":
            break
        if "->" not in line and "→" not in line:
            continue  # В сохранённом выводе также встречаются приглашение терминала и итог.
        match = PAIR.fullmatch(line.strip())
        if not match:
            raise ValueError(f"Строка {number}: ожидается пара «старый_IRI -> новый_IRI».")
        old, new = match.groups()
        if old in pairs and pairs[old] != new:
            raise ValueError(f"Строка {number}: для {old} указаны разные новые IRI.")
        if new in owners and owners[new] != old:
            raise ValueError(f"Строка {number}: у {old} и {owners[new]} один новый IRI {new}.")
        pairs[old] = new
        owners[new] = old
    if not pairs:
        raise ValueError("В тексте нет строк вида «старый_IRI -> новый_IRI».")
    return pairs


def export_report(source, log, destination):
    source = Path(source).resolve(strict=True)
    log = Path(log).resolve(strict=True)
    destination = Path(destination).resolve()
    if destination.exists() and any(destination.samefile(path) for path in (source, log)):
        raise ValueError("Выберите отдельный файл для отчёта CSV.")
    pairs = read_pairs(log)
    missing = []
    row_count = 0
    world = World()
    try:
        with source.open("rb") as model_file:
            world.get_ontology(source.as_uri()).load(fileobj=model_file)
        output = NamedTemporaryFile(
            dir=destination.parent, mode="w", encoding="utf-8-sig", newline="", delete=False
        )
        temporary = Path(output.name)
        try:
            with output:
                writer = csv.writer(output, delimiter=";")
                writer.writerow(["Старое наименование", "Старый IRI", "Новый IRI"])
                for old, new in pairs.items():
                    entity = world[old]
                    names = []
                    if entity is None:
                        missing.append(old)
                    else:
                        names = sorted({
                            str(value) for value in comment[entity]
                            if isinstance(value, str) and value.strip()
                        })
                    for name in names or [""]:
                        writer.writerow([name, old, new])
                        row_count += 1
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    finally:
        world.close()
    return row_count, missing


def choose_files():
    from tkinter import TclError, Tk, filedialog

    try:
        root = Tk()
    except TclError as error:
        raise OSError("Окно недоступно. Передайте три пути в командной строке.") from error
    try:
        root.withdraw()
        source = filedialog.askopenfilename(
            parent=root, title="Выберите исходную OWL-модель со старыми IRI",
            filetypes=[("Модель OWL", "*.owl"), ("Все файлы", "*")],
        )
        if not source:
            return None
        log = filedialog.askopenfilename(
            parent=root, title="Выберите сохранённые строки со старыми и новыми IRI",
            initialdir=str(Path(source).parent), filetypes=[("Текст", "*.txt"), ("Все файлы", "*")],
        )
        if not log:
            return None
        destination = filedialog.asksaveasfilename(
            parent=root, title="Сохранить таблицу наименований", initialdir=str(Path(log).parent),
            initialfile=f"{Path(source).stem}_iri.csv", defaultextension=".csv",
            filetypes=[("Таблица CSV", "*.csv")],
        )
        if destination:
            return source, log, destination
    finally:
        root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", help="Исходная модель .owl")
    parser.add_argument("log", nargs="?", help="Сохранённый вывод переименования .txt")
    parser.add_argument("output", nargs="?", help="Куда сохранить .csv")
    args = parser.parse_args()
    if args.source and not args.output:
        parser.error("Передайте все три пути или запустите без аргументов для выбора файлов.")
    try:
        paths = (args.source, args.log, args.output) if args.source else choose_files()
        if not paths:
            return
        rows, missing = export_report(*paths)
    except (ImportError, OSError, ValueError) as error:
        parser.exit(1, f"Ошибка: {error}\n")
    print(f"Сохранено в {paths[2]}. Строк данных: {rows}.")
    if missing:
        print("Следующие старые IRI не найдены в исходной модели. Их наименования оставлены пустыми:",
              file=sys.stderr)
        for iri in missing:
            print(iri, file=sys.stderr)


if __name__ == "__main__":
    main()
