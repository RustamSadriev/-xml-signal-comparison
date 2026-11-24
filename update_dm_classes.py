"""Обновить IRI и добавить комментарии классам из dm_comment_pairs.py."""

import argparse
from collections import defaultdict
from pathlib import Path
from tempfile import NamedTemporaryFile

from owlready2 import OwlReadyOntologyParsingError, World, comment

from dm_comment_pairs import DM_COMMENT_PAIRS


def has_comment(cls, text):
    return any(isinstance(value, str) and str(value) == text for value in comment[cls])


def apply_rows(ontology, rows):
    by_name = defaultdict(list)
    by_iri = {}
    for cls in ontology.classes():
        by_name[cls.name].append(cls)
        by_iri[cls.iri] = cls

    if len({name for name, _, _ in rows}) != len(rows):
        raise ValueError("В списке кортежей повторяется исходное имя.")
    if len({iri for _, _, iri in rows}) != len(rows):
        raise ValueError("В списке кортежей повторяется целевой IRI.")

    plan = []
    missing = []
    for name, text, new_iri in rows:
        matches = by_name.get(name, [])
        if len(matches) > 1:
            iris = ", ".join(cls.iri for cls in matches)
            raise ValueError(f"Имя {name} встречается у нескольких классов: {iris}")
        if not matches:
            target = by_iri.get(new_iri)
            if target is None or not has_comment(target, text):
                missing.append(name)
            continue
        cls = matches[0]
        if new_iri != cls.iri and ontology.world._abbreviate(new_iri, False) is not None:
            raise ValueError(f"Невозможно переименовать {cls.iri}: уже занят {new_iri}")
        plan.append((cls, text, new_iri))

    changes = {}
    added = 0
    with ontology:
        for cls, text, new_iri in plan:
            if not has_comment(cls, text):
                comment[cls].append(text)
                added += 1
            if cls.iri != new_iri:
                changes[cls.iri] = new_iri
                cls.iri = new_iri
    return changes, added, missing


def update_model(source, destination):
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve()
    if destination.exists() and source.samefile(destination):
        raise ValueError("Для результата выберите отдельный файл.")
    world = World()
    try:
        with source.open("rb") as model:
            ontology = world.get_ontology(source.as_uri()).load(fileobj=model)
        result = apply_rows(ontology, DM_COMMENT_PAIRS)
        output = NamedTemporaryFile(dir=destination.parent, delete=False)
        temporary = Path(output.name)
        try:
            with output:
                ontology.save(file=output, format="rdfxml")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return result
    finally:
        world.close()


def choose_files():
    from tkinter import Tk, TclError, filedialog

    try:
        root = Tk()
    except TclError as error:
        raise OSError("Передайте пути к исходной модели и выходному файлу в командной строке.") from error
    try:
        root.withdraw()
        types = [("Модели OWL", "*.owl"), ("Все файлы", "*")]
        selected = filedialog.askopenfilename(parent=root, title="Выберите модель с классами DM", filetypes=types)
        if selected:
            source = Path(selected)
            output = filedialog.asksaveasfilename(
                parent=root, title="Сохранить копию модели", initialdir=str(source.parent),
                initialfile=f"{source.stem}_dm.owl", defaultextension=".owl", filetypes=types,
            )
            if output:
                return source, Path(output)
    finally:
        root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path, help="Исходная OWL-модель")
    parser.add_argument("output", nargs="?", type=Path, help="Путь к копии модели")
    args = parser.parse_args()
    if args.source and not args.output:
        parser.error("Укажите два пути или запустите без аргументов для выбора файлов.")
    try:
        files = (args.source, args.output) if args.source else choose_files()
        if not files:
            return
        changes, added, missing = update_model(*files)
    except (ImportError, OSError, ValueError, OwlReadyOntologyParsingError) as error:
        parser.exit(1, f"Ошибка: {error}\n")
    print(f"Сохранено в {files[1]}. Заменено IRI: {len(changes)}. Добавлено комментариев: {added}.")
    if missing:
        print("Пропущены классы, для которых не найдено соответствие:", *missing, sep="\n")
    for old, new in changes.items():
        print(f"{old} -> {new}")


if __name__ == "__main__":
    main()
