"""
extractor.py
------------
Parses ilap_graphql_schema.graphql and sdo.ttl into plain Python dicts.
Call this once at startup. After this file runs you never touch either
library (graphql-core, rdflib) again — everything downstream is pure Python.

Outputs
-------
graphql_index : dict[str, dict]   keyed by type name
sdo_index     : dict[str, dict]   keyed by short class/property name

Run standalone to print a summary:
    python extractor.py
"""

from pprint import pprint

from graphql import build_ast_schema, parse as gql_parse
from graphql.type import (
    GraphQLObjectType,
    GraphQLEnumType,
    GraphQLScalarType,
    GraphQLInputObjectType,
    GraphQLList,
    GraphQLNonNull,
)
from rdflib import Graph, RDF, RDFS, OWL, Namespace, BNode, URIRef
from rdflib.namespace import SKOS

# ── namespaces used in the TTL ────────────────────────────────────────────────
SDO = Namespace("https://posccaesar.org/ontology/sdo/rdl/")
IOF = Namespace(
    "https://spec.industrialontologies.org/ontology/core/meta/AnnotationVocabulary/"
)

# ── scalars that are NOT relations in GraphQL ─────────────────────────────────
GQL_SCALARS = {
    "String", "Float", "Boolean", "DateTime", "Int",
    "Any", "ID", "Date", "Long",
}


# ═════════════════════════════════════════════════════════════════════════════
# GRAPHQL PARSER
# ═════════════════════════════════════════════════════════════════════════════

def _unwrap_gql_type(t):
    """
    Strip GraphQLList and GraphQLNonNull wrappers to get the bare named type.
    Returns (type_name: str, is_list: bool, is_required: bool).
    """
    
    # we get field.type   →   "Activity", is_list=True, is_required=True for ex.
    is_list     = False
    is_required = False

    if isinstance(t, GraphQLNonNull):
        is_required = True
        t = t.of_type

    if isinstance(t, GraphQLList):
        is_list = True
        t = t.of_type

    if isinstance(t, GraphQLNonNull):   # inner NonNull inside List
        t = t.of_type

    return t.name, is_list, is_required


def parse_graphql(path: str) -> dict:
    """
    Parse a .graphql schema file into a dict of dicts.

    Structure
    ---------
    {
      "TypeName": {
        "kind":        "object" | "enum" | "scalar" | "input",
        "description": str,
        "source":      "graphql",
        "fields": {                        # only for object / input types
          "fieldName": {
            "description": str,
            "type":        str,            # bare type name, stripped of List/NonNull
            "is_list":     bool,
            "is_required": bool,
            "is_relation": bool,           # True if type is another object type
          }
        },
        "values": [str, ...]               # only for enum types
      }
    }
    """
    with open(path) as f:
    # build_ast_schema imported from library gives us a GraphQLSchema object with a type_map dict of all types
        schema = build_ast_schema(gql_parse(f.read())) 
    index = {}

    for type_name, type_def in schema.type_map.items():
        # skip GraphQL built-ins
        if type_name.startswith("__"):
            continue

        # ── enums ────────────────────────────────────────────────────────────
        if isinstance(type_def, GraphQLEnumType):
            index[type_name] = {
                "kind":        "enum",
                "description": type_def.description or "",
                "source":      "graphql",
                "values":      list(type_def.values.keys()),
            }

        # ── scalars ──────────────────────────────────────────────────────────
        elif isinstance(type_def, GraphQLScalarType):
            index[type_name] = {
                "kind":        "scalar",
                "description": type_def.description or "",
                "source":      "graphql",
            }

        # ── object types and input types ─────────────────────────────────────
        elif isinstance(type_def, (GraphQLObjectType, GraphQLInputObjectType)):
            kind = "object" if isinstance(type_def, GraphQLObjectType) else "input"

            # all names in the schema at this point — used for is_relation check
            all_names = set(schema.type_map.keys())

            fields = {}
            for field_name, field in type_def.fields.items():
                bare_type, is_list, is_required = _unwrap_gql_type(field.type)
                is_relation = (
                    bare_type in all_names
                    and bare_type not in GQL_SCALARS
                    and not bare_type.startswith("__")
                )
                fields[field_name] = {
                    "description": field.description or "",
                    "type":        bare_type,
                    "is_list":     is_list,
                    "is_required": is_required,
                    "is_relation": is_relation,
                }

            index[type_name] = {
                "kind":        kind,
                "description": type_def.description or "",
                "source":      "graphql",
                "fields":      fields,
            }

    return index


# ═════════════════════════════════════════════════════════════════════════════
# TTL / RDF PARSER
# ═════════════════════════════════════════════════════════════════════════════

def _short(uri) -> str:
    """Return the local name of a URI (everything after # or last /)."""
    if uri is None:
        return None
    s = str(uri)
    return s.split("#")[-1] if "#" in s else s.split("/")[-1]


def _parse_rdf_list(g: Graph, node) -> list:
    """Walk an rdf:List and return its members."""
    # RDF has no native list type. Instead it encodes a list as a linked chain of nodes,
    # where each node has two pointers:
    # rdf:first — the current item
    # rdf:rest — the next node in the chain
    # The chain ends with a special terminator value called rdf:nil.
    items = []
    while node and node != RDF.nil:
        first = g.value(node, RDF.first)
        if first:
            items.append(first)
        node = g.value(node, RDF.rest)
    return items


def _resolve_domain(g: Graph, prop: URIRef) -> list:
    """
    Return domain class names as a list.
    Handles both simple URI domains and owl:unionOf BNode domains.
    """
    #one call to _resolve_domain ends up connecting the property
    # to every class it belongs to
    domain_node = g.value(prop, RDFS.domain)
    if domain_node is None:
        return []
    if isinstance(domain_node, BNode):
        union = g.value(domain_node, OWL.unionOf)
        if union:
            return [_short(m) for m in _parse_rdf_list(g, union)]
        return []
    #print(f"Domain: [{_short(domain_node)}]")
    return [_short(domain_node)]


def _resolve_range(g: Graph, prop: URIRef) -> list:
    """
    Return range class names as a list.
    Handles both simple URI ranges and owl:unionOf BNode ranges.
    """
    # same as domain but for range — connects the property to every class/type it can point to
    range_node = g.value(prop, RDFS.range)
    if range_node is None:
        return []
    if isinstance(range_node, BNode):
        union = g.value(range_node, OWL.unionOf)
        if union:
            return [_short(m) for m in _parse_rdf_list(g, union)]
        return []
    return [_short(range_node)]


def _resolve_subclass_of(g: Graph, cls: URIRef) -> list:
    """
    Return parent class names for a given class URI.
    Skips BNode restrictions (owl:Restriction) — only returns named parents.
    """
    parents = []
    for sc in g.objects(cls, RDFS.subClassOf):
        if isinstance(sc, URIRef):       # named class — keep it
            parents.append(_short(sc))
        # BNode = owl:Restriction (e.g. someValuesFrom) — skip
    #print(f"Class {_short(cls)} subclass_of: {parents}")
    return parents


def parse_sdo(path: str) -> dict:
    """
    Parse a Turtle (.ttl) OWL ontology file into a dict of dicts.

    Structure
    ---------
    {
      "ClassName": {
        "kind":        "class",
        "source":      "sdo",
        "label":       str,
        "description": str,           # rdfs:comment
        "definition":  str,           # iof-av:naturalLanguageDefinition
        "example":     str,           # skos:example
        "usage_note":  str,           # iof-av:usageNote
        "subclass_of": [str],         # named parent classes (no BNode restrictions)
        "subclasses":  [str],         # direct child classes (reverse lookup)
        "properties":  [str],         # property names whose domain includes this class
      },

      "propertyName": {
        "kind":        "object_property" | "datatype_property",
        "source":      "sdo",
        "label":       str,
        "description": str,
        "definition":  str,
        "domain":      [str],         # class names (union-aware)
        "range":       [str],         # class/type names (union-aware)
        "inverse_of":  str | None,
        "subproperty_of": str | None,
      }
    }
    """
    g = Graph()
    g.parse(path, format="turtle")

    index = {}

    # ── 1. classes ────────────────────────────────────────────────────────────
    for cls in g.subjects(RDF.type, OWL.Class):
        if not isinstance(cls, URIRef):
            continue                    # skip anonymous BNode classes

        name = _short(cls)
        index[name] = {
            "kind":        "class",
            "source":      "sdo",
            "label":       str(g.value(cls, RDFS.label) or name),
            "description": str(g.value(cls, RDFS.comment) or ""),
            "definition":  str(g.value(cls, IOF.naturalLanguageDefinition) or ""),
            "example":     str(g.value(cls, SKOS.example) or ""),
            "usage_note":  str(g.value(cls, IOF.usageNote) or ""),
            "subclass_of": _resolve_subclass_of(g, cls),
            "subclasses":  [],          # filled in pass 2
            "properties":  [],          # filled in pass 3
        }

    # ── 2. reverse-populate subclasses ───────────────────────────────────────
    for child_name, child_node in index.items():
        if child_node["kind"] != "class":
            continue
        for parent_name in child_node["subclass_of"]:
            if parent_name in index:
                index[parent_name]["subclasses"].append(child_name)

    # ── 3. properties ─────────────────────────────────────────────────────────
    for prop_type, kind_label in (
        (OWL.ObjectProperty,   "object_property"),
        (OWL.DatatypeProperty, "datatype_property"),
    ):
        for prop in g.subjects(RDF.type, prop_type):
            if not isinstance(prop, URIRef):
                continue

            name       = _short(prop)
            domain     = _resolve_domain(g, prop)
            range_     = _resolve_range(g, prop)
            inverse    = g.value(prop, OWL.inverseOf)
            subprop    = g.value(prop, RDFS.subPropertyOf)

            index[name] = {
                "kind":             kind_label,
                "source":           "sdo",
                "label":            str(g.value(prop, RDFS.label) or name),
                "description":      str(g.value(prop, RDFS.comment) or ""),
                "definition":       str(g.value(prop, IOF.naturalLanguageDefinition) or ""),
                "domain":           domain,
                "range":            range_,
                "inverse_of":       _short(inverse) if inverse else None,
                "subproperty_of":   _short(subprop) if subprop else None,
            }

            # back-link: add property name to each domain class's properties list
            for cls_name in domain:
                if cls_name in index and index[cls_name]["kind"] == "class":
                    index[cls_name]["properties"].append(name)

    return index


# ═════════════════════════════════════════════════════════════════════════════
# ENTRY POINT — run standalone for a quick summary
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    import os

    gql_path = sys.argv[1] if len(sys.argv) > 1 else "data_models/ilap_graphql_schema.graphql"
    ttl_path = sys.argv[2] if len(sys.argv) > 2 else "data_models/sdo.ttl"

    # print("Parsing GraphQL schema...")
    graphql_index = parse_graphql(gql_path)

    # print("Parsing SDO TTL ontology...")
    sdo_index = parse_sdo(ttl_path)
    #pprint(sdo_index["ContinuousWorkPatternAssignment"])
    # summary by kind
    from collections import Counter
    gql_kinds = Counter(v["kind"] for v in graphql_index.values())
    sdo_kinds = Counter(v["kind"] for v in sdo_index.values())
  
    print(f"\n{'─'*50}")
    print(f"GraphQL index  — {len(graphql_index)} entries")
    for k, n in sorted(gql_kinds.items()):
        print(f"  {k:<22} {n}")
    #pprint(graphql_index["Schedule"])
    
    print(f"\nSDO index      — {len(sdo_index)} entries")
    for k, n in sorted(sdo_kinds.items()):
        print(f"  {k:<22} {n}")

    # spot-check Activity
    # print(f"\n{'─'*50}")
    # act = graphql_index.get("Activity", {})
    # print(f"Activity fields (GraphQL): {len(act.get('fields', {}))}")
    # relations = {k: v["type"] for k, v in act.get("fields", {}).items()
    #              if v["is_relation"]}
    # print(f"  relation fields: {list(relations.items())[:6]} ...")

    # sa = sdo_index.get("ScheduleActivity", {})
    # print(f"\nScheduleActivity (SDO):")
    # print(f"  subclass_of : {sa.get('subclass_of')}")
    # print(f"  subclasses  : {sa.get('subclasses')}")
    # print(f"  properties  : {sa.get('properties')}")\

