"""Составить таблицу с исходными наименованиями или заменить ссылки IRI в модели маппинга."""

import argparse
import codecs
import csv
import re
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from xml.etree import ElementTree
from xml.sax import SAXException


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


def checked_paths(source, log, destination):
    source = Path(source).resolve(strict=True)
    log = Path(log).resolve(strict=True)
    destination = Path(destination).resolve()
    if destination.exists() and any(destination.samefile(path) for path in (source, log)):
        raise ValueError("Выберите отдельный выходной файл.")
    return source, log, destination


def export_report(source, log, destination):
    from owlready2 import World, comment

    source, log, destination = checked_paths(source, log, destination)
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


def replace_iris(source, log, destination):
    """Заменить полные IRI в графе RDF/XML за один проход по исходным утверждениям."""
    # В офлайн-комплекте Python импортирует модули прямо из файлов wheel.
    package_dir = Path(__file__).resolve().with_name("iri_packages")
    if package_dir.is_dir():
        wheels = [str(path) for path in sorted(package_dir.glob("*.whl"))]
        sys.path[:0] = [path for path in wheels if path not in sys.path]
    try:
        import rdflib
        from rdflib import Graph, Literal, URIRef
        from rdflib.exceptions import ParserError
    except ImportError as error:
        raise ImportError(
            f"Не удалось загрузить RDFLib ({error}). Распакуйте полный архив ветки "
            "export-iri-names и запустите скрипт рядом с папкой iri_packages."
        ) from error

    source, log, destination = checked_paths(source, log, destination)
    pairs = {URIRef(old): URIRef(new) for old, new in read_pairs(log).items()}
    original = Graph()
    with source.open("rb") as model:
        # OWL/XML и сокращённый RDF/XML могут оба начинаться с owl:Ontology.
        elements = ElementTree.iterparse(model, events=("start",))
        root = next(elements)[1]
        owl = "{http://www.w3.org/2002/07/owl#}"
        if root.tag == owl + "Ontology":
            first_child = next(elements, (None, None))[1]
            is_axiom = (first_child is not None and first_child.tag.startswith(owl)
                        and first_child.tag[len(owl):][:1].isupper())
            if is_axiom or "ontologyIRI" in root.attrib or "versionIRI" in root.attrib:
                raise ValueError("Сохраните модель маппинга в Protégé в формате RDF/XML.")
        model.seek(0)
        # RDFLib по умолчанию превращает, например, "007"^^xsd:integer в "7".
        normalize = rdflib.NORMALIZE_LITERALS
        try:
            rdflib.NORMALIZE_LITERALS = False
            original.parse(model, format="xml", publicID=source.as_uri())
        except (SAXException, ParserError) as error:
            raise ValueError(f"Не удалось прочитать модель маппинга как RDF/XML: {error}") from error
        finally:
            rdflib.NORMALIZE_LITERALS = normalize

    result = Graph()
    for prefix, namespace in original.namespaces():
        result.bind(prefix, namespace)
    found = set()

    def mapped(term):
        if term in pairs:
            found.add(term)
            return pairs[term]
        return term

    for subject, predicate, obj in original:
        if isinstance(obj, URIRef):
            obj = mapped(obj)
        elif isinstance(obj, Literal) and obj.datatype in pairs:
            # Тип литерала — ссылка, его текст остаётся прежним.
            obj = Literal(str(obj), datatype=mapped(obj.datatype), normalize=False)
        result.add((mapped(subject), mapped(predicate), obj))

    output = NamedTemporaryFile(dir=destination.parent, delete=False)
    temporary = Path(output.name)
    try:
        with output:
            result.serialize(destination=output, format="xml", encoding="utf-8")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return sum(pairs[old] != old for old in found), sorted(set(pairs) - found)


def choose_files(replace=False):
    from tkinter import TclError, Tk, filedialog, messagebox

    try:
        root = Tk()
    except TclError as error:
        raise OSError("Окно недоступно. Передайте три пути в командной строке.") from error
    try:
        root.withdraw()
        if not replace:
            replace = messagebox.askyesnocancel(
                "Действие", "Заменить ссылки IRI в модели маппинга?\n\n"
                "Да — сохранить обновлённую OWL-модель.\nНет — составить таблицу CSV.",
                parent=root,
            )
            if replace is None:
                return None
        title = "Выберите модель маппинга для замены IRI" if replace else "Выберите исходную OWL-модель со старыми IRI"
        source = filedialog.askopenfilename(
            parent=root, title=title,
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
        if replace:
            path = Path(source)
            destination = filedialog.asksaveasfilename(
                parent=root, title="Сохранить обновлённую модель маппинга", initialdir=str(path.parent),
                initialfile=f"{path.stem}_updated.owl", defaultextension=".owl", filetypes=[("Модель OWL", "*.owl")],
            )
        else:
            destination = filedialog.asksaveasfilename(
                parent=root, title="Сохранить таблицу наименований", initialdir=str(Path(log).parent),
                initialfile=f"{Path(source).stem}_iri.csv", defaultextension=".csv", filetypes=[("Таблица CSV", "*.csv")],
            )
        if destination:
            return replace, source, log, destination
    finally:
        root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replace-iris", action="store_true", help="Заменить IRI в модели маппинга (RDF/XML)")
    parser.add_argument("source", nargs="?", help="Старая модель для CSV или модель маппинга для замены IRI")
    parser.add_argument("log", nargs="?", help="Сохранённый вывод переименования .txt")
    parser.add_argument("output", nargs="?", help="Куда сохранить .csv или обновлённый .owl")
    args = parser.parse_args()
    if args.source and not args.output:
        parser.error("Передайте все три пути или запустите без аргументов для выбора файлов.")
    try:
        job = (args.replace_iris, args.source, args.log, args.output) if args.source else choose_files(args.replace_iris)
        if not job:
            return
        replace, source, log, destination = job
        if replace:
            count, absent = replace_iris(source, log, destination)
            print(f"Сохранено в {destination}. Заменено различных IRI: {count}.")
            if absent:
                print("В модели маппинга отсутствуют следующие старые IRI:", *absent, sep="\n")
            return
        rows, missing = export_report(source, log, destination)
    except (ImportError, OSError, ValueError, ElementTree.ParseError) as error:
        parser.exit(1, f"Ошибка: {error}\n")
    print(f"Сохранено в {destination}. Строк данных: {rows}.")
    if missing:
        print("Следующие старые IRI не найдены в исходной модели. Их наименования оставлены пустыми:",
              file=sys.stderr)
        for iri in missing:
            print(iri, file=sys.stderr)


if __name__ == "__main__":
    main()
