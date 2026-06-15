1. **Parse** — reads both the schema and ontology into plain Python dicts
2. **Scope** — extracts only the concepts you specify (e.g. Activity, Schedule, Project) and everything connected to them
3. **Lexical match** — compares names using fuzzy string matching
4. **Semantic match** — compares meaning using sentence embeddings for cases where names differ but concepts are the same
5. **Output** — identical / equivalent / unique buckets, exportable to Excel for SME review

## Stack

- `graphql-core` — parses GraphQL schema
- `rdflib` — parses OWL ontology (Turtle)
- `rapidfuzz` — lexical matching
- `sentence-transformers` — semantic matching
- `pandas` — output and export

## Setup

```bash
git clone https://github.com/kritomx/semantic-bridge.git
cd semantic-bridge
uv venv --seed
uv sync
```

Place your model files in `data_models/` then open the notebook:

```bash
uv run jupyter notebook matcher_test.ipynb
```