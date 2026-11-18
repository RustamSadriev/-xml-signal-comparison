import argparse

from owlready2 import get_ontology


parser = argparse.ArgumentParser(
    description="Find ontology links missing from the reference list."
)
parser.add_argument("ontology", help="Path to the OWL file")
args = parser.parse_args()

res = [('Расположен в', 'installed-on')]
onto = get_ontology(args.ontology).load()

known_comments = {row[0] for row in res}
missing_links = []

for prop in onto.object_properties():
    if not prop.comment:
        missing_links.append(prop.name)
        continue

    comment = prop.comment[0]
    if comment not in known_comments:
        missing_links.append(comment)

print(*missing_links, sep='\n')
