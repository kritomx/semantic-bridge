"""
concepts.py
-----------
Generic subgraph extractor and classifier.

Given an index produced by extractor.py and a list of seed concept names,
this module:
  1. Walks all reachable nodes from the seeds (following relations,
     subclass hierarchies, and property domain/range links).
  2. Classifies every reachable node as:
       - relation  : a field/property that points to another node
       - hierarchy : a subclass / subClassOf link
       - attribute : a scalar field / datatype property (no pointer)
  3. Returns the subgraph as a plain dict you can iterate, filter, or export.

Usage
-----
    from extractor import parse_graphql, parse_sdo
    from concepts  import extract, describe

    graphql_index = parse_graphql("ilap_graphql_schema.graphql")
    sdo_index     = parse_sdo("sdo.ttl")

    seeds = ["Activity", "Schedule", "Project"]   # ← specify any concepts here

    gql_subgraph = extract(graphql_index, seeds)
    sdo_subgraph = extract(sdo_index,     seeds)

    describe(gql_subgraph, label="GraphQL")
    describe(sdo_subgraph, label="SDO")

Run standalone:
    python concepts.py                        # uses default seeds
    python concepts.py Activity Schedule      # pass seeds as CLI args
"""


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _neighbours(node: dict, index: dict) -> list[str]:
    """
    Return all names reachable from a single node in one step.

    Covers:
      GraphQL object type  → follow is_relation fields
      GraphQL enum         → no neighbours (leaf)
      SDO class            → follow subclasses + subclass_of + property names
      SDO property         → follow domain + range
    """
    #  The logic differs by what kind of node it is:
    # GraphQL object — follow fields where is_relation=True
    # SDO class — follow subclasses, subclass_of, and properties
    # SDO property — follow domain, range, and inverse_of
    
    kind   = node.get("kind", "")
    found  = []

    # ── GraphQL object / input ────────────────────────────────────────────────
    if kind in ("object", "input"):
        for field in node.get("fields", {}).values():
            if field.get("is_relation"):
                found.append(field["type"])

    # ── SDO class ─────────────────────────────────────────────────────────────
    elif kind == "class":
        found.extend(node.get("subclasses",  []))   # downward
        found.extend(node.get("subclass_of", []))   # upward
        found.extend(node.get("properties",  []))   # properties whose domain = this class

    # ── SDO property ──────────────────────────────────────────────────────────
    elif kind in ("object_property", "datatype_property"):
        found.extend(node.get("domain", []))
        found.extend(node.get("range",  []))
        inv = node.get("inverse_of")
        if inv:
            found.append(inv)

    return [n for n in found if n and n in index]


# ═════════════════════════════════════════════════════════════════════════════
# MAIN EXTRACTOR
# ═════════════════════════════════════════════════════════════════════════════

def extract(index: dict, seeds: list[str]) -> dict:
    """
    Walk the index starting from `seeds` and return the reachable subgraph.

    The walk is a breadth-first traversal following:
      - relation fields        (GraphQL)
      - subclass / subClassOf  (SDO class hierarchy)
      - property domain/range  (SDO properties)

    Seeds that do not exist in the index are silently skipped so you can
    pass the same seed list to both graphql_index and sdo_index without errors.

    Returns
    -------
    dict[str, dict]  — subset of `index` containing only reachable nodes,
    each enriched with a "_reachability" key that records:
      {
        "seed":      bool,        # True if this node was a starting seed
        "via":       str | None,  # name of the node that led here
        "step":      int,         # how many hops from the nearest seed
        "node_type": str,         # "concept" | "relation" | "attribute" | "hierarchy"
      }
    """
    visited = {}   # name → enriched node dict
    queue   = []   # list of (name, via, step)

    # seed normalisation — try exact, then case-insensitive, then plural strip
    def resolve_seed(raw: str) -> str | None:
        if raw in index:
            return raw
        lower_map = {k.lower(): k for k in index}
        # try lower
        if raw.lower() in lower_map:
            return lower_map[raw.lower()]
        # try stripping trailing 's' or 'es' (schedules → schedule, activities → activity)
        for suffix in ("ies", "es", "s"):
            stripped = raw.lower()
            if stripped.endswith(suffix):
                candidate = stripped[: -len(suffix)]
                if candidate in lower_map:
                    return lower_map[candidate]
                # ies → y  (activities → activity)
                if suffix == "ies":
                    candidate_y = stripped[: -len("ies")] + "y"
                    if candidate_y in lower_map:
                        return lower_map[candidate_y]
        return None

    for raw_seed in seeds:
        resolved = resolve_seed(raw_seed)
        if resolved:
            queue.append((resolved, None, 0))
        else:
            print(f"  [concepts] Warning: seed '{raw_seed}' not found in index — skipped")

    while queue:
        name, via, step = queue.pop(0)   # BFS

        if name in visited:
            continue

        node = dict(index[name])          # shallow copy — don't mutate original

        # classify node_type
        kind = node.get("kind", "")
        if step == 0:
            node_type = "seed"
        elif kind == "class":
            node_type = "concept"
        elif kind in ("object_property",):
            node_type = "relation"
        elif kind in ("datatype_property",):
            node_type = "attribute"
        elif kind == "object":
            node_type = "concept"
        elif kind == "enum":
            node_type = "enum"
        elif kind == "input":
            node_type = "input"
        else:
            node_type = "attribute"

        node["_reachability"] = {
            "seed":      step == 0,
            "via":       via,
            "step":      step,
            "node_type": node_type,
        }

        visited[name] = node

        # queue neighbours
        for neighbour in _neighbours(node, index):
            if neighbour not in visited:
                queue.append((neighbour, name, step + 1))

    return visited


# ═════════════════════════════════════════════════════════════════════════════
# INSPECTION HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def describe(subgraph: dict, label: str = "subgraph") -> None:
    """Print a structured summary of the subgraph to stdout."""
    from collections import defaultdict

    by_type  = defaultdict(list)
    by_step  = defaultdict(list)

    for name, node in subgraph.items():
        r = node.get("_reachability", {})
        by_type[r.get("node_type", "unknown")].append(name)
        by_step[r.get("step", -1)].append(name)

    print(f"\n{'═'*60}")
    print(f"  {label} subgraph  —  {len(subgraph)} nodes")
    print(f"{'═'*60}")

    print("\nBy node type:")
    for nt, names in sorted(by_type.items()):
        print(f"  {nt:<18} ({len(names):>3})  "
              f"{', '.join(sorted(names)[:6])}"
              f"{'...' if len(names) > 6 else ''}")

    print("\nBy hop distance from seeds:")
    for step in sorted(by_step.keys()):
        names = sorted(by_step[step])
        print(f"  step {step}  ({len(names):>3} nodes)  "
              f"{', '.join(names[:8])}"
              f"{'...' if len(names) > 8 else ''}")


def get_concepts(subgraph: dict) -> dict:
    """Return only the concept/class nodes (not properties or enums)."""
    return {
        name: node for name, node in subgraph.items()
        if node.get("_reachability", {}).get("node_type") in ("seed", "concept")
    }


def get_relations(subgraph: dict) -> dict:
    """Return only relation nodes (object properties / relation fields)."""
    return {
        name: node for name, node in subgraph.items()
        if node.get("_reachability", {}).get("node_type") == "relation"
    }


def get_hierarchy(subgraph: dict) -> dict:
    """
    Return a simple parent → [children] hierarchy dict
    for all class nodes in the subgraph.
    """
    hierarchy = {}
    for name, node in subgraph.items():
        if node.get("kind") == "class":
            hierarchy[name] = node.get("subclasses", [])
    return hierarchy


def get_attributes(subgraph: dict) -> dict:
    """
    Return scalar fields per concept.
    GraphQL: fields where is_relation=False
    SDO:     datatype_property nodes
    """
    attrs = {}

    for name, node in subgraph.items():
        kind = node.get("kind")

        # GraphQL object type — collect scalar fields
        if kind == "object":
            scalars = {
                fname: fnode
                for fname, fnode in node.get("fields", {}).items()
                if not fnode.get("is_relation")
            }
            if scalars:
                attrs[name] = scalars

        # SDO datatype property — record it against its domain classes
        elif kind == "datatype_property":
            for domain_cls in node.get("domain", []):
                if domain_cls not in attrs:
                    attrs[domain_cls] = {}
                attrs[domain_cls][name] = node

    return attrs


# ═════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    import os

    # allow passing seeds and file paths as CLI args
    # usage: python concepts.py [seed1 seed2 ...] [-- gql_path ttl_path]
    args = sys.argv[1:]

    # split on '--' separator if present
    if "--" in args:
        sep      = args.index("--")
        seeds    = args[:sep] if args[:sep] else ["Activity", "Schedule", "Project", "Data"]
        paths    = args[sep+1:]
    else:
        seeds = args if args else ["Activity", "Schedule", "Project", "Data"]
        paths = []

    gql_path = paths[0] if len(paths) > 0 else "/mnt/user-data/uploads/ilap_graphql_schema.graphql"
    ttl_path = paths[1] if len(paths) > 1 else "/mnt/user-data/uploads/sdo.ttl"

    # import here so the file can also be imported without running the CLI block
    import importlib.util, pathlib

    # load extractor from same directory
    spec = importlib.util.spec_from_file_location(
        "extractor",
        pathlib.Path(__file__).parent / "extractor.py"
    )
    extractor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(extractor)

    print(f"Seeds            : {seeds}")
    print(f"GraphQL schema   : {gql_path}")
    print(f"SDO ontology     : {ttl_path}")

    print("\nParsing files...")
    graphql_index = extractor.parse_graphql(gql_path)
    sdo_index     = extractor.parse_sdo(ttl_path)

    print(f"  GraphQL index  : {len(graphql_index)} total entries")
    print(f"  SDO index      : {len(sdo_index)} total entries")

    print("\nExtracting subgraphs...")
    gql_subgraph = extract(graphql_index, seeds)
    sdo_subgraph = extract(sdo_index,     seeds)

    describe(gql_subgraph, label="GraphQL")
    describe(sdo_subgraph, label="SDO")

    # ── detailed view for each seed ──────────────────────────────────────────
    print(f"\n{'═'*60}")
    print("  SEED DETAIL")
    print(f"{'═'*60}")

    for seed in seeds:
        print(f"\n── {seed} ──")

        # GraphQL side
        gql_node = gql_subgraph.get(seed) or gql_subgraph.get(seed.capitalize())
        if gql_node and gql_node.get("kind") == "object":
            rel_fields = {k: v["type"] for k, v in gql_node.get("fields", {}).items()
                          if v.get("is_relation")}
            scalar_fields = [k for k, v in gql_node.get("fields", {}).items()
                             if not v.get("is_relation")]
            print(f"  GraphQL  — {len(gql_node.get('fields', {}))} fields")
            print(f"    relations : {list(rel_fields.items())[:8]}")
            print(f"    scalars   : {scalar_fields[:8]} ...")
        elif gql_node:
            print(f"  GraphQL  — kind={gql_node.get('kind')}")
        else:
            print(f"  GraphQL  — not found as direct match")

        # SDO side — try both exact name and "Schedule" → "ScheduleActivity" etc.
        sdo_candidates = [k for k in sdo_subgraph
                          if seed.lower() in k.lower()
                          and sdo_subgraph[k].get("kind") == "class"]
        for sc in sdo_candidates[:3]:
            sdo_node = sdo_subgraph[sc]
            print(f"  SDO  [{sc}]")
            print(f"    definition  : {sdo_node.get('definition', '')[:80]}")
            print(f"    subclass_of : {sdo_node.get('subclass_of')}")
            print(f"    subclasses  : {sdo_node.get('subclasses')}")
            print(f"    properties  : {sdo_node.get('properties', [])[:8]} ...")

    # ── hierarchy view ───────────────────────────────────────────────────────
    print(f"\n{'═'*60}")
    print("  SDO CLASS HIERARCHY (within subgraph)")
    print(f"{'═'*60}")

    hierarchy = get_hierarchy(sdo_subgraph)
    def print_tree(name, tree, indent=0):
        children = tree.get(name, [])
        print(f"  {'  ' * indent}{'└─ ' if indent else ''}{name}"
              f"  ({len(children)} children)" if children else
              f"  {'  ' * indent}{'└─ ' if indent else ''}{name}")
        for child in children:
            if child in tree:
                print_tree(child, tree, indent + 1)

    roots = [n for n in hierarchy if not any(
        n in hierarchy.get(other, []) for other in hierarchy
    )]
    for root in sorted(roots):
        print_tree(root, hierarchy)