"""Переименование классов, объектных свойств и свойств данных по rdfs:comment."""

import argparse
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4
from xml.etree import ElementTree

from owlready2 import ThingClass, World, comment


BASE_IRI = "http://company.classification/asutp"
LINK_PREFIX = "link"
ATTRIBUTE_PREFIX = "attr"

# Замените примеры своими кортежами (comment, short_name).
OBJECT_PROPERTY_SHORT_NAMES = [
    ("Используется в", "used-in"),
    ("Используется в системе", "used-in-system"),
]

DATA_PROPERTY_SHORT_NAMES = [
    ("Имеет имя", "has-name"),
    ("Имеет значение", "has-value"),
]


# Ветви группировки — непосредственные подклассы этого контейнера.
CLASS_ROOT_NAME = "asutp_class"
CLASS_SHORT_NAMES = [
    ("Сигнал", "signal"),
    ("Команда", "command"),
    ("Режим управления САР", "sar-control-mode"),  # пример (rdfs:comment, short_name)
]


def matching_short_names(entity, names_by_comment):
    return {
        names_by_comment[text]
        for text in map(str, comment[entity])
        if text in names_by_comment
    }


def class_iri_prefix(cls, base, names_by_comment):
    branches = {
        ancestor
        for ancestor in cls.ancestors()
        if any(
            isinstance(parent, ThingClass) and parent.name == CLASS_ROOT_NAME
            for parent in ancestor.is_a
        )
    }
    if not branches:
        return None
    if len(branches) > 1:
        iris = ", ".join(sorted(branch.iri for branch in branches))
        raise ValueError(f"У класса {cls.iri} несколько верхнеуровневых ветвей: {iris}")
    branch = branches.pop()
    if cls is branch:
        return "class"
    branch_names = matching_short_names(branch, names_by_comment)
    if len(branch_names) > 1:
        raise ValueError(f"Разные short name для {branch.iri}: {sorted(branch_names)}")
    if branch_names:
        return f"class_{branch_names.pop()}"
    if branch.iri.startswith(f"{base}#class_"):
        return branch.name
    return f"class_{branch.name}"


def is_xml_local_name(name):
    """Проверить, что IRI свойства можно записать как имя элемента RDF/XML."""
    if ":" in name:
        return False
    try:
        return ElementTree.fromstring(f"<{name}/>").tag == name
    except (ElementTree.ParseError, UnicodeError):
        return False


def index_short_names(short_names):
    names_by_comment = {}
    for text, short_name in short_names:
        if not isinstance(text, str):
            raise ValueError(f"Комментарий должен быть строкой: {text!r}")
        if not isinstance(short_name, str) or not short_name or not is_xml_local_name(f"p_{short_name}"):
            raise ValueError(f"Некорректный short name для {text!r}: {short_name!r}")
        if names_by_comment.setdefault(text, short_name) != short_name:
            raise ValueError(f"Разные short name для комментария {text!r}")
    return names_by_comment


def rename_property_iris(
    input_file, output_file, object_short_names, data_short_names, class_short_names=()
):
    """Сохранить результат в отдельный RDF/XML и вернуть словарь старых и новых IRI."""
    source = Path(input_file).resolve(strict=True)
    destination = Path(output_file).resolve()
    if source == destination:
        raise ValueError("Укажите отдельный выходной файл")
    for prefix in (LINK_PREFIX, ATTRIBUTE_PREFIX):
        if not is_xml_local_name(prefix):
            raise ValueError(f"Некорректный префикс свойства: {prefix!r}")

    world = World()
    try:
        with source.open("rb") as model_file:
            ontology = world.get_ontology(source.as_uri()).load(fileobj=model_file)
        base = BASE_IRI.removesuffix("#")
        plan = {}
        target_owners = {}
        entity_groups = (
            (ontology.object_properties(), LINK_PREFIX, object_short_names),
            (ontology.data_properties(), ATTRIBUTE_PREFIX, data_short_names),
            (ontology.classes(), None, class_short_names),
        )
        for entities, prefix, short_names in entity_groups:
            names_by_comment = index_short_names(short_names)
            if not names_by_comment:
                continue
            for entity in entities:
                matches = matching_short_names(entity, names_by_comment)
                if not matches:
                    continue
                entity_prefix = prefix
                if prefix is None:
                    entity_prefix = class_iri_prefix(entity, base, names_by_comment)
                if entity_prefix is None:
                    continue
                if len(matches) > 1:
                    raise ValueError(f"Разные short name для {entity.iri}: {sorted(matches)}")

                old_iri = entity.iri
                short_name = matches.pop()
                new_iri = f"{base}#{entity_prefix}_{short_name}"
                owner = target_owners.setdefault(new_iri, old_iri)
                if owner != old_iri:
                    raise ValueError(f"Сущности {owner} и {old_iri} получают один IRI: {new_iri}")
                if old_iri != new_iri:
                    plan[old_iri] = (entity, new_iri)

        for _, new_iri in plan.values():
            # world[iri] пропускает IRI, которые лишь упоминаются в RDF-графе.
            if new_iri not in plan and world._abbreviate(new_iri, False) is not None:
                raise ValueError(f"Целевой IRI уже используется: {new_iri}")

        if target_owners.keys() & plan.keys():
            # Освобождаем старые IRI для цепочек переименований и обмена именами.
            temporary_namespace = f"urn:owl-iri-rename:{uuid4().hex}#"
            for index, (entity, _) in enumerate(plan.values()):
                entity.iri = f"{temporary_namespace}{index}"
        for entity, new_iri in plan.values():
            entity.iri = new_iri

        output = NamedTemporaryFile(dir=destination.parent, delete=False)
        temporary_file = Path(output.name)
        try:
            with output:
                ontology.save(file=output, format="rdfxml")
            temporary_file.replace(destination)
        finally:
            temporary_file.unlink(missing_ok=True)
        return {old_iri: new_iri for old_iri, (_, new_iri) in plan.items()}
    finally:
        world.close()


def choose_model_files():
    try:
        from tkinter import Tk, TclError, filedialog
    except ImportError as error:
        raise OSError("Для окна выбора файла нужен Python с поддержкой Tkinter.") from error

    try:
        root = Tk()
    except TclError as error:
        raise OSError("Не удалось открыть окно. Запустите скрипт на рабочем столе или передайте два пути.") from error
    try:
        root.withdraw()
        selected = filedialog.askopenfilename(
            parent=root, title="Выберите модель OWL",
            filetypes=[("Модели OWL", "*.owl"), ("Все файлы", "*")],
        )
        if not selected:
            return None
        source = Path(selected)
        destination = filedialog.asksaveasfilename(
            parent=root, title="Сохранить модель с новыми IRI",
            initialdir=str(source.parent), initialfile=f"{source.stem}_renamed.owl",
            defaultextension=".owl", filetypes=[("Модели OWL", "*.owl")],
        )
        if destination:
            return source, Path(destination)
        return None
    finally:
        root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path, help="Исходный файл .owl")
    parser.add_argument("output", nargs="?", type=Path, help="Выходной файл .owl")
    args = parser.parse_args()
    if args.input is not None and args.output is None:
        parser.error("Передайте оба пути или запустите без аргументов для выбора модели.")
    try:
        if args.input is None:
            paths = choose_model_files()
            if paths is None:
                return
            source, destination = paths
        else:
            source, destination = args.input, args.output
        changes = rename_property_iris(
            source, destination, OBJECT_PROPERTY_SHORT_NAMES, DATA_PROPERTY_SHORT_NAMES, CLASS_SHORT_NAMES
        )
    except (ValueError, OSError) as error:
        parser.exit(1, f"Ошибка: {error}\n")

    print(f"Сохранено в {destination}. Переименовано сущностей: {len(changes)}")
    for old_iri, new_iri in sorted(changes.items()):
        print(f"{old_iri} -> {new_iri}")


if __name__ == "__main__":
    main()
