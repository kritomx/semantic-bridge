"""
semantic_matcher.py
-------------------
Second-pass matching using sentence-transformer embeddings.

Intended to run after matcher.py — it re-examines the equivalent
and unique_gql buckets where rapidfuzz found no strong lexical match,
and finds conceptual matches based on meaning.
"""

from importlib import import_module
from sentence_transformers import SentenceTransformer, util
import re

THRESHOLD_SEMANTIC = 0.75   # cosine similarity — tune this
MODEL_NAME = "all-MiniLM-L6-v2"


def _make_text(name: str, node: dict) -> str:
    # break camelCase into separate words before embedding
    # WorkPattern → "Work Pattern", earlyStart → "early Start"
    readable_name = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', name)

    parts = [readable_name]

    desc = node.get("description", "")
    if desc:
        parts.append(desc)

    defn = node.get("definition", "")
    if defn:
        parts.append(defn)

    return " ".join(parts)


def semantic_match(
    gql_subgraph: dict,
    sdo_subgraph: dict,
    candidates: list = None,       # if given, only re-examine these gql names
    threshold: float = THRESHOLD_SEMANTIC,
    model_name: str = MODEL_NAME,
) -> dict:
    """
    Compare GraphQL nodes against SDO nodes using semantic embeddings.

    Parameters
    ----------
    gql_subgraph  : full graphql subgraph from concepts.extract()
    sdo_subgraph  : full sdo subgraph from concepts.extract()
    candidates    : optional list of gql node names to check
                    if None — checks everything in gql_subgraph
    threshold     : cosine similarity cutoff (0-1)

    Returns
    -------
    {
      "matched":  [ {gql_name, sdo_name, score, gql_text, sdo_text}, ... ],
      "unmatched": [ {gql_name, best_sdo_name, best_score}, ... ],
    }
    """
    print(f"Loading model: {model_name} ...")
    model = SentenceTransformer(model_name)

    # scope to candidates if given, else full subgraph
    gql_scope = (
        {k: gql_subgraph[k] for k in candidates if k in gql_subgraph}
        if candidates
        else gql_subgraph
    )

    # build text representations
    gql_texts  = {name: _make_text(name, node) for name, node in gql_scope.items()}
    sdo_texts  = {name: _make_text(name, node) for name, node in sdo_subgraph.items()}

    sdo_names  = list(sdo_texts.keys())
    sdo_inputs = list(sdo_texts.values())

    print(f"Encoding {len(gql_texts)} GraphQL nodes ...")
    gql_embeddings = model.encode(list(gql_texts.values()), show_progress_bar=True)

    print(f"Encoding {len(sdo_inputs)} SDO nodes ...")
    sdo_embeddings = model.encode(sdo_inputs, show_progress_bar=True)

    matched   = []
    unmatched = []

    gql_names = list(gql_texts.keys())

    for i, gql_name in enumerate(gql_names):
        # cosine similarity against all SDO embeddings at once
        scores     = util.cos_sim(gql_embeddings[i], sdo_embeddings)[0]
        best_idx   = int(scores.argmax())
        best_score = float(scores[best_idx])
        best_name  = sdo_names[best_idx]

        entry = {
            "gql_name":  gql_name,
            "sdo_name":  best_name,
            "score":     round(best_score, 4),
            "gql_text":  gql_texts[gql_name],
            "sdo_text":  sdo_texts[best_name],
        }

        if best_score >= threshold:
            matched.append(entry)
        else:
            unmatched.append({
                "gql_name":      gql_name,
                "best_sdo_name": best_name,
                "best_score":    round(best_score, 4),
            })

    return {
        "matched":   sorted(matched,   key=lambda x: x["score"], reverse=True),
        "unmatched": sorted(unmatched, key=lambda x: x["gql_name"]),
    }


def summary(results: dict) -> None:
    print(f"\n{'═'*60}")
    print(f"  SEMANTIC MATCH RESULTS")
    print(f"{'═'*60}")
    print(f"  matched   : {len(results['matched'])}")
    print(f"  unmatched : {len(results['unmatched'])}")

    print(f"\n── Top semantic matches ──")
    for r in results["matched"][:10]:
        print(f"  {r['gql_name']:<35} ↔  {r['sdo_name']:<35}  score={r['score']}")

    print(f"\n── Unmatched (no semantic equivalent found) ──")
    for r in results["unmatched"][:10]:
        print(f"  {r['gql_name']:<35}  best={r['best_sdo_name']:<30}  score={r['best_score']}")