"""
matcher.py
----------
First-pass matching layer using rapidfuzz lexical scoring.

Takes two subgraphs produced by concepts.py and compares every node in
the GraphQL subgraph against every node in the SDO subgraph, returning
three buckets:

    identical   — score >= THRESHOLD_IDENTICAL  (very likely the same)
    equivalent  — score >= THRESHOLD_EQUIVALENT (similar, needs review)
    unique      — score <  THRESHOLD_EQUIVALENT (no counterpart found)

This is intentionally the simplest possible matcher — lexical only.
Semantic embedding matching (sentence-transformers) comes next and will
re-examine the equivalent and unique buckets.

Usage
-----
    from etl.extractor import parse_graphql, parse_sdo
    from etl.concepts  import extract
    from matching.matcher   import match, summary

    graphql_index = parse_graphql("data_models/ilap_graphql_schema.graphql")
    sdo_index     = parse_sdo("data_models/sdo.ttl")

    seeds = ["Activity", "Schedule", "Project"]

    gql_subgraph = extract(graphql_index, seeds)
    sdo_subgraph = extract(sdo_index,     seeds)

    results = match(gql_subgraph, sdo_subgraph)
    summary(results)
"""

from rapidfuzz import fuzz


# ── thresholds ────────────────────────────────────────────────────────────────
THRESHOLD_IDENTICAL  = 92   # treat as same concept
THRESHOLD_EQUIVALENT = 60   # treat as related, needs review


# ═════════════════════════════════════════════════════════════════════════════
# SCORING
# ═════════════════════════════════════════════════════════════════════════════

def _score(name_a: str, name_b: str) -> float:
    """
    Compute a composite lexical similarity score between two names.

    Uses three rapidfuzz scorers and takes the highest:
      - token_sort_ratio : handles word-order differences
                           startNoEarlierThan vs noEarlierThanStart → 100
      - partial_ratio    : handles prefix/suffix containment
                           earlyStart vs earlyStartDateTime → 100
      - ratio            : plain character-level similarity

    Returns a float 0–100.
    """
    a = name_a.lower()
    b = name_b.lower()

    token_sort = fuzz.token_sort_ratio(a, b)
    partial    = fuzz.partial_ratio(a, b)
    ratio      = fuzz.ratio(a, b)

    return max(token_sort, partial, ratio)


def _best_match(name: str, candidates: dict) -> tuple[str, float]:
    """
    Find the best matching name in candidates dict for a given name.
    Returns (best_name, best_score).
    """
    best_name  = None
    best_score = 0.0

    for candidate_name in candidates:
        score = _score(name, candidate_name)
        if score > best_score:
            best_score = score
            best_name  = candidate_name

    return best_name, best_score


# ═════════════════════════════════════════════════════════════════════════════
# MAIN MATCHER
# ═════════════════════════════════════════════════════════════════════════════

def match(
    gql_subgraph: dict,
    sdo_subgraph: dict,
    threshold_identical:  float = THRESHOLD_IDENTICAL,
    threshold_equivalent: float = THRESHOLD_EQUIVALENT,
) -> dict:
    """
    Compare every node in gql_subgraph against all nodes in sdo_subgraph.

    Parameters
    ----------
    gql_subgraph        : output of concepts.extract(graphql_index, seeds)
    sdo_subgraph        : output of concepts.extract(sdo_index, seeds)
    threshold_identical : score >= this → identical bucket
    threshold_equivalent: score >= this → equivalent bucket

    Returns
    -------
    {
      "identical":  [ {gql, sdo, score, scores}, ... ],
      "equivalent": [ {gql, sdo, score, scores}, ... ],
      "unique_gql": [ {gql}, ... ],           nodes in GraphQL with no SDO match
      "unique_sdo": [ {sdo}, ... ],           nodes in SDO with no GraphQL match
    }
    """
    identical   = []
    equivalent  = []
    unique_gql  = []

    matched_sdo = set()   # track which SDO nodes got matched

    for gql_name, gql_node in gql_subgraph.items():

        best_sdo_name, best_score = _best_match(gql_name, sdo_subgraph)

        # individual scorer breakdown for transparency
        scores = {}
        if best_sdo_name:
            a = gql_name.lower()
            b = best_sdo_name.lower()
            scores = {
                "token_sort": fuzz.token_sort_ratio(a, b),
                "partial":    fuzz.partial_ratio(a, b),
                "ratio":      fuzz.ratio(a, b),
            }

        entry = {
            "gql":   {"name": gql_name,      "node": gql_node},
            "sdo":   {"name": best_sdo_name,  "node": sdo_subgraph.get(best_sdo_name)},
            "score": round(best_score, 1),
            "scores": scores,
        }

        if best_score >= threshold_identical:
            identical.append(entry)
            matched_sdo.add(best_sdo_name)

        elif best_score >= threshold_equivalent:
            equivalent.append(entry)
            matched_sdo.add(best_sdo_name)

        else:
            unique_gql.append({"name": gql_name, "node": gql_node, "best_score": round(best_score, 1)})

    # SDO nodes that were never matched
    unique_sdo = [
        {"name": sdo_name, "node": sdo_node}
        for sdo_name, sdo_node in sdo_subgraph.items()
        if sdo_name not in matched_sdo
    ]

    return {
        "identical":  sorted(identical,  key=lambda x: x["score"], reverse=True),
        "equivalent": sorted(equivalent, key=lambda x: x["score"], reverse=True),
        "unique_gql": sorted(unique_gql, key=lambda x: x["name"]),
        "unique_sdo": sorted(unique_sdo, key=lambda x: x["name"]),
    }


# ═════════════════════════════════════════════════════════════════════════════
# INSPECTION
# ═════════════════════════════════════════════════════════════════════════════

def summary(results: dict) -> None:
    """Print a count summary and top matches to stdout."""

    print(f"\n{'═'*60}")
    print(f"  MATCH RESULTS")
    print(f"{'═'*60}")
    print(f"  identical   : {len(results['identical'])}")
    print(f"  equivalent  : {len(results['equivalent'])}")
    print(f"  unique_gql  : {len(results['unique_gql'])}")
    print(f"  unique_sdo  : {len(results['unique_sdo'])}")

    print(f"\n── Top identical matches ──")
    for r in results["identical"][:10]:
        print(f"  {r['gql']['name']:<35} ↔  {r['sdo']['name']:<35}  score={r['score']}")

    print(f"\n── Top equivalent matches (need review) ──")
    for r in results["equivalent"][:10]:
        print(f"  {r['gql']['name']:<35} ↔  {r['sdo']['name']:<35}  score={r['score']}")

    print(f"\n── Unique to GraphQL (no SDO match) — first 10 ──")
    for r in results["unique_gql"][:10]:
        print(f"  {r['name']:<35}  best_score={r['best_score']}")

    print(f"\n── Unique to SDO (no GraphQL match) — first 10 ──")
    for r in results["unique_sdo"][:10]:
        print(f"  {r['name']}")


def to_dataframe(results: dict):
    """
    Flatten results into a pandas DataFrame for inspection or export.
    Requires pandas — `uv add pandas`.

    Returns a DataFrame with columns:
        bucket | gql_name | sdo_name | score | token_sort | partial | ratio
    """
    import pandas as pd

    rows = []

    for bucket in ("identical", "equivalent"):
        for r in results[bucket]:
            rows.append({
                "bucket":     bucket,
                "gql_name":   r["gql"]["name"],
                "sdo_name":   r["sdo"]["name"],
                "score":      r["score"],
                "token_sort": r["scores"].get("token_sort"),
                "partial":    r["scores"].get("partial"),
                "ratio":      r["scores"].get("ratio"),
            })

    for r in results["unique_gql"]:
        rows.append({
            "bucket":   "unique_gql",
            "gql_name": r["name"],
            "sdo_name": None,
            "score":    r["best_score"],
        })

    for r in results["unique_sdo"]:
        rows.append({
            "bucket":   "unique_sdo",
            "gql_name": None,
            "sdo_name": r["name"],
            "score":    None,
        })

    return pd.DataFrame(rows)