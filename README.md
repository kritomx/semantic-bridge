# schema-bridge

Parses two data model files — an ILAP GraphQL schema and an SDO OWL ontology — into plain Python dictionaries, then extracts and classifies concepts and their relationships for mapping and alignment work.

---

## What it does

The project solves a specific problem: two data models describing overlapping domains (project scheduling) use different formats, different terminology, and different structural conventions. Before any mapping or NLP matching can happen, both models need to be readable in a common form.

The pipeline has two steps:

```
ilap_graphql_schema.graphql  ──► extractor.py ──► python dict ──┐
                                                                  ├──► concepts.py ──► scoped subgraph
sdo.ttl  ────────────────────► extractor.py ──► python dict ──┘
```

**Step 1 — `extractor.py`**: parses both files once into plain Python dicts. After this step no GraphQL or RDF library is needed — everything is native Python.

**Step 2 — `concepts.py`**: takes those dicts and extracts only the nodes reachable from a set of seed concepts you specify. Follows relations, subclass hierarchies, and property domain/range links automatically via breadth-first walk.

---

## Project structure

```
ilap_sdo_mapping_test/
├── data_models/
│   ├── ilap_graphql_schema.graphql   ILAP operational data model
│   └── sdo.ttl                       SDO OWL ontology (Turtle format)
├── etl/
│   ├── extractor.py                  parses both files into Python dicts
│   └── concepts.py                   extracts concept subgraphs from those dicts
├── main.py                           entry point
├── pyproject.toml                    project config and dependencies
├── uv.lock                           locked dependency versions
└── README.md
```

---

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) for dependency management

Dependencies (defined in `pyproject.toml`):

| Package | Purpose |
|---|---|
| `graphql-core` | Parses `.graphql` schema files |
| `rdflib` | Parses `.ttl` OWL ontology files |

---

## Setup

```bash
# clone the repo
git clone https://github.com/kritomx/model-matcher.git
cd model-matcher

# create virtual environment and install dependencies
uv venv
uv sync
```

Activate the virtual environment:

```bash
# Windows (Git Bash)
source .venv/Scripts/activate

# macOS / Linux
source .venv/bin/activate
```

Or skip activation entirely and prefix every command with `uv run`.

---

## Commands

### Run the extractor — parse both files and print a summary

```bash
uv run python etl/extractor.py
```

Parses both model files and prints a summary of what was found:

```
Parsing GraphQL schema...
Parsing SDO TTL ontology...

──────────────────────────────────────────────────
GraphQL index  — 246 entries
  enum                   21
  input                 155
  object                 63
  scalar                  7

SDO index      — 163 entries
  class                  72
  datatype_property      51
  object_property        30

──────────────────────────────────────────────────
Activity fields (GraphQL): 278
  relation fields: [('predecessors', 'Successor'), ('calendar', 'Calendar'), ...] ...

ScheduleActivity (SDO):
  subclass_of : ['Activity']
  subclasses  : ['CancelledActivity', 'AlwaysOnScheduleActivity', ...]
  properties  : ['earlyStart', 'lateFinish', 'freeFloatHours', ...]
```

You can also pass explicit file paths:

```bash
uv run python etl/extractor.py data_models/ilap_graphql_schema.graphql data_models/sdo.ttl
```

---

### Run concept extraction — extract subgraph for specific concepts

```bash
uv run python etl/concepts.py
```

Uses default seeds `Activity`, `Schedule`, `Project`. Prints a structured summary of every node reachable from those seeds in both models, the hop distance from each seed, and the SDO class hierarchy within the subgraph.

Pass different seeds as arguments:

```bash
uv run python etl/concepts.py Activity Schedule Project
uv run python etl/concepts.py Resource Calendar
```

Pass seeds and explicit file paths together using `--` as separator:

```bash
uv run python etl/concepts.py Activity Schedule -- data_models/ilap_graphql_schema.graphql data_models/sdo.ttl
```

---

## Using the modules in code

Both files are importable. After importing, the parsers run once and you work entirely with plain Python dicts.

```python
from etl.extractor import parse_graphql, parse_sdo
from etl.concepts  import extract, describe, get_concepts, get_relations, get_hierarchy, get_attributes

# parse once
graphql_index = parse_graphql("data_models/ilap_graphql_schema.graphql")
sdo_index     = parse_sdo("data_models/sdo.ttl")

# specify which concepts to scope to
seeds = ["Activity", "Schedule", "Project"]

# extract reachable subgraphs
gql_subgraph = extract(graphql_index, seeds)
sdo_subgraph = extract(sdo_index,     seeds)

# print summaries
describe(gql_subgraph, label="GraphQL")
describe(sdo_subgraph, label="SDO")

# filter to specific node types
concepts   = get_concepts(gql_subgraph)    # class / object nodes only
relations  = get_relations(sdo_subgraph)   # object properties only
hierarchy  = get_hierarchy(sdo_subgraph)   # parent → [children] dict
attributes = get_attributes(gql_subgraph)  # scalar fields per type
```

---

## Output data structures

### `parse_graphql()` — returns `dict[str, dict]`

Keyed by GraphQL type name. Each entry describes one type:

```python
{
  "Activity": {
    "kind":        "object",          # object | enum | scalar | input
    "description": "Activity that is part of the schedule",
    "source":      "graphql",
    "fields": {
      "earlyStart": {
        "description": "Earliest possible start date",
        "type":        "DateTime",    # bare type name, wrappers stripped
        "is_list":     False,
        "is_required": False,
        "is_relation": False,         # False — DateTime is a scalar
      },
      "schedule": {
        "description": "Schedule this activity belongs to",
        "type":        "Schedule",
        "is_list":     False,
        "is_required": False,
        "is_relation": True,          # True — Schedule is another object type
      }
    }
  },
  "ActivityType": {
    "kind":   "enum",
    "values": ["NOT_SET", "REGULAR_ACTIVITY", "MILESTONE_START", ...]
  }
}
```

### `parse_sdo()` — returns `dict[str, dict]`

Keyed by the short local name of each class or property URI:

```python
{
  "ScheduleActivity": {
    "kind":        "class",
    "source":      "sdo",
    "label":       "ScheduleActivity",
    "description": "",
    "definition":  "An activity that identifies a piece of work...",
    "subclass_of": ["Activity"],                          # named parents only
    "subclasses":  ["CancelledActivity", "EarlyStartActivity", ...],
    "properties":  ["earlyStart", "lateFinish", "freeFloatHours", ...]
  },
  "earlyStart": {
    "kind":           "datatype_property",
    "source":         "sdo",
    "label":          "earlyStart",
    "domain":         ["ScheduleActivity"],               # union-aware
    "range":          ["dateTime"],
    "inverse_of":     None,
    "subproperty_of": None,
  }
}
```

### `extract()` — returns subgraph `dict[str, dict]`

Same structure as the index, but scoped to reachable nodes only. Each node gains a `_reachability` key:

```python
{
  "Activity": {
    ...                          # all fields from the original index
    "_reachability": {
      "seed":      True,         # was this a starting seed
      "via":       None,         # which node led here (None for seeds)
      "step":      0,            # hops from nearest seed
      "node_type": "seed"        # seed | concept | relation | attribute | enum
    }
  },
  "Calendar": {
    "_reachability": {
      "seed":      False,
      "via":       "Activity",   # reached via Activity.calendar field
      "step":      1,
      "node_type": "concept"
    }
  }
}
```

---

## Seed resolution

Seeds passed to `extract()` are resolved flexibly — you do not need to match the exact capitalisation or singular/plural form used in the model:

| Seed passed | Resolves to |
|---|---|
| `"Activity"` | `"Activity"` (exact) |
| `"activity"` | `"Activity"` (case-insensitive) |
| `"activities"` | `"Activity"` (plural stripped) |
| `"schedules"` | `"Schedule"` (plural stripped) |
| `"Project"` | not found in GraphQL — skipped with warning |

Seeds not found in a given index are skipped silently, so the same seed list can be passed to both `graphql_index` and `sdo_index` without errors.

---

## What comes next

The `matcher.py` module (planned) will take the two subgraphs produced by `concepts.py` and classify every concept pair as:

- **identical** — same name, same meaning
- **equivalent** — different name, same concept (NLP matching)
- **unique** — exists in only one model, no counterpart

This will use a combination of lexical matching (`rapidfuzz`), semantic embedding matching (`sentence-transformers`), and synonym detection (`nltk` WordNet).