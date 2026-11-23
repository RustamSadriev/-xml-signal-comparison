"""Обновить наименования в rdfs:comment и сохранить отдельную копию OWL-модели."""

import argparse
from itertools import chain
from pathlib import Path
from tempfile import NamedTemporaryFile

from owlready2 import OwlReadyOntologyParsingError, Thing, World, comment


ROOT_NAME = "asutp_class"  # Локальное имя или полный IRI класса-контейнера
ROOT_TITLE = "ЛТМ"
TAG = "[ЛТМ]"


def bare_title(text):
    return text.strip().removesuffix(TAG).rstrip()


def language(datatype):
    return datatype.lower() if isinstance(datatype, str) and datatype.startswith("@") else ""


def branch_title(branch, titles, datatype):
    """Предпочесть имя ветви на языке комментария, затем имя без языковой метки."""
    lang = language(datatype)
    for candidates in (titles.get(lang), titles.get(""), set().union(*titles.values())):
        if candidates:
            if len(candidates) == 1:
                return next(iter(candidates))
            break
    raise ValueError(
        f"У ветви {branch.iri} нет однозначного названия для языка {lang or 'без метки'}. "
        "Проверьте rdfs:comment этой ветви."
    )


def find_root(classes, comments):
    matches = [cls for cls in classes if ROOT_NAME in (cls.name, cls.iri)]
    if not matches:
        matches = [
            cls for cls in classes if Thing in cls.is_a and (
                cls.name == ROOT_TITLE or any(bare_title(text) == ROOT_TITLE for text, _ in comments[cls])
            )
        ]
    if len(matches) != 1:
        raise ValueError(f"Не удалось однозначно найти класс {ROOT_TITLE}. Укажите его полный IRI в ROOT_NAME.")
    return matches[0]


def update_comments(ontology):
    classes = list(ontology.classes())
    entities = chain(classes, ontology.object_properties(), ontology.data_properties())
    # Читаем литералы в графе выбранного файла. Сохраняем исходный язык и тип RDF.
    predicate = comment.storid
    string_type = ontology.world._abbreviate("http://www.w3.org/2001/XMLSchema#string")
    comments = {
        entity: [
            (text, datatype)
            for text, datatype in ontology._get_data_triples_sp_od(entity.storid, predicate)
            if (datatype in (0, string_type) or language(datatype)) and text.strip()
        ]
        for entity in entities
    }
    root = find_root(classes, comments) if classes else None
    branches = {cls for cls in classes if root in cls.is_a}
    titles = {}
    for branch in branches:
        titles[branch] = {}
        for text, datatype in comments[branch]:
            name = bare_title(text)
            if name:
                titles[branch].setdefault(language(datatype), set()).add(name)

    changes = []
    class_set = set(classes)
    for entity, literals in comments.items():
        if entity is root or entity is Thing or not literals:
            continue
        branch = None
        if entity in class_set and entity not in branches:
            ancestors = entity.ancestors() & branches
            if len(ancestors) > 1:
                options = ", ".join(sorted(item.iri for item in ancestors))
                raise ValueError(f"У класса {entity.iri} несколько верхнеуровневых ветвей: {options}")
            if ancestors:
                branch = ancestors.pop()

        for old, datatype in literals:
            name = bare_title(old)
            if branch is not None:
                prefix = branch_title(branch, titles[branch], datatype) + ": "
                if not name.startswith(prefix):
                    name = prefix + name
            new = f"{name} {TAG}".strip()
            if new != old:
                changes.append((entity.storid, old, new, datatype))

    for subject, old, new, datatype in changes:
        ontology._del_data_triple_spod(subject, predicate, old, datatype)
        ontology._add_data_triple_spod(subject, predicate, new, datatype)
    return len(changes)


def update_model(source, destination):
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve()
    if source == destination or (destination.exists() and source.samefile(destination)):
        raise ValueError("Выберите для результата отдельный файл.")
    world = World()
    try:
        with source.open("rb") as model:
            ontology = world.get_ontology(source.as_uri()).load(fileobj=model)
        count = update_comments(ontology)
        with NamedTemporaryFile(dir=destination.parent, delete=False) as temp:
            temporary = Path(temp.name)
        try:
            ontology.save(file=str(temporary), format="rdfxml")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return count
    finally:
        world.close()


def choose_files():
    try:
        from tkinter import Tk, TclError, filedialog
    except ImportError as error:
        raise OSError("Установите Tkinter или передайте пути к двум файлам.") from error
    try:
        root = Tk()
    except TclError as error:
        raise OSError("Не удалось открыть окно выбора. Передайте пути к двум файлам.") from error
    try:
        root.withdraw()
        types = [("Модели OWL", "*.owl"), ("Все файлы", "*")]
        source = filedialog.askopenfilename(parent=root, title="Выберите OWL-модель", filetypes=types)
        if source:
            path = Path(source)
            output = filedialog.asksaveasfilename(
                parent=root, title="Сохранить модель с обновлёнными наименованиями",
                initialdir=str(path.parent), initialfile=f"{path.stem}_ltm.owl",
                defaultextension=".owl", filetypes=types,
            )
            if output:
                return path, Path(output)
    finally:
        root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path, help="Исходная OWL-модель")
    parser.add_argument("output", nargs="?", type=Path, help="Куда сохранить результат")
    args = parser.parse_args()
    if args.source and not args.output:
        parser.error("Передайте оба пути или запустите без аргументов для выбора файлов.")
    try:
        files = (args.source, args.output) if args.source else choose_files()
        if not files:
            return
        count = update_model(*files)
    except (ValueError, OSError, OwlReadyOntologyParsingError) as error:
        parser.exit(1, f"Ошибка: {error}\n")
    print(f"Сохранено в {files[1]}. Изменено комментариев: {count}")


if __name__ == "__main__":
    main()
