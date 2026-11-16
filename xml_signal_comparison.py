import xml.etree.ElementTree as ET
from collections import defaultdict
import pandas as pd

# Настройте имена файлов и названия XML-тегов под свою структуру.
FILE_1, FILE_2 = "file1.xml", "file2.xml"
SIGNAL_TAG, SCHEME_TAG = "Signal", "ConnectionScheme"
MATCH_ATTRS = ("iri", "id", "name")

def local(tag):
    return tag.rsplit("}", 1)[-1]

def val(element, attr):
    return (element.get(attr) or "").strip()

def extract(root):
    parents = {child: parent for p in root.iter() for child in p}
    records = []
    for signal in root.iter():
        if local(signal.tag) != SIGNAL_TAG:
            continue
        p = parents.get(signal)
        while p is not None and local(p.tag) != SCHEME_TAG:
            p = parents.get(p)
        if p is not None:
            records.append((signal, p))
    return records

def compare(s1, s2):
    equal, different, missing = [], [], []
    for key in MATCH_ATTRS:
        x, y = val(s1, key), val(s2, key)
        if x and y:
            (equal if x == y else different).append(key)
        elif x or y:
            missing.append(key)
    return equal, different, missing

def main():
    t1, t2 = ET.parse(FILE_1), ET.parse(FILE_2)
    a, b = extract(t1.getroot()), extract(t2.getroot())
    index = defaultdict(set)
    for j, (s, _) in enumerate(b):
        for key in MATCH_ATTRS:
            if val(s, key):
                index[(key, val(s, key))].add(j)

    candidates = []
    by_left, by_right = defaultdict(list), defaultdict(list)
    for i, (s, _) in enumerate(a):
        js = set()
        for key in MATCH_ATTRS:
            if val(s, key):
                js.update(index[(key, val(s, key))])
        for j in sorted(js):
            eq, diff, missing = compare(s, b[j][0])
            if eq:
                pair = (i, j, eq, diff, missing)
                candidates.append(pair)
                by_left[i].append(pair)
                by_right[j].append(pair)

    unique = [p for p in candidates if len(by_left[p[0]]) == 1 and len(by_right[p[1]]) == 1]
    full = [p for p in unique if len(p[2]) == len(MATCH_ATTRS)]
    partial = [p for p in unique if len(p[2]) != len(MATCH_ATTRS)]
    ambiguous = [p for p in candidates if len(by_left[p[0]]) > 1 or len(by_right[p[1]]) > 1]

    def pair_row(p):
        i, j, eq, diff, missing = p
        return {"index1": i, "index2": j,
                **{"file1_" + k: a[i][0].get(k, "") for k in MATCH_ATTRS},
                **{"file2_" + k: b[j][0].get(k, "") for k in MATCH_ATTRS},
                "matched": ", ".join(eq), "conflicts": ", ".join(diff),
                "missing": ", ".join(missing)}

    # Только полные, однозначные совпадения могут менять родительские схемы.
    # Сначала собираем предложения, чтобы не перезаписывать конфликтующие значения.
    proposals = defaultdict(set)
    conflicts, changes = [], []
    for i, j, *_ in full:
        s1, p1 = a[i]
        _, p2 = b[j]
        for key in set(p1.attrib) | set(p2.attrib):
            x, y = val(p1, key), val(p2, key)
            if x and not y:
                proposals[(2, id(p2), key)].add(x)
            elif y and not x:
                proposals[(1, id(p1), key)].add(y)
            elif x and y and x != y:
                conflicts.append({"signal": str(s1.attrib), "attribute": key,
                                  "file1_value": x, "file2_value": y})

    objects = {1: {id(p): p for _, p in a}, 2: {id(p): p for _, p in b}}
    for (file_no, parent_id, key), values in proposals.items():
        parent = objects[file_no][parent_id]
        if len(values) == 1 and not val(parent, key):
            value = next(iter(values))
            parent.set(key, value)
            changes.append({"file": file_no, "attribute": key, "new_value": value})
        elif len(values) > 1:
            conflicts.append({"signal": "shared parent", "attribute": key,
                              "file1_value": "", "file2_value": " | ".join(sorted(values))})

    sheets = {
        "Full_matches": [pair_row(p) for p in full],
        "Partial_matches": [pair_row(p) for p in partial],
        "Ambiguous": [pair_row(p) for p in ambiguous],
        "Unmatched_file1": [{"index": i, **s.attrib} for i, (s, _) in enumerate(a) if i not in by_left],
        "Unmatched_file2": [{"index": j, **s.attrib} for j, (s, _) in enumerate(b) if j not in by_right],
        "Changes": changes,
        "Scheme_conflicts": conflicts,
    }
    with pd.ExcelWriter("comparison_report.xlsx", engine="openpyxl") as writer:
        for name, rows in sheets.items():
            pd.DataFrame(rows).to_excel(writer, sheet_name=name, index=False)

    ET.indent(t1, space="    ")
    ET.indent(t2, space="    ")
    t1.write("file1_updated.xml", encoding="utf-8", xml_declaration=True)
    t2.write("file2_updated.xml", encoding="utf-8", xml_declaration=True)
    for name, rows in sheets.items():
        print(f"{name}: {len(rows)}")

if __name__ == "__main__":
    main()
