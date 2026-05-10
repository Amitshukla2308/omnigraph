"""Community detection over the cochange graph.

Primary: Louvain (python-louvain + networkx) — matches HR 10_build_communities.
Fallback: connected components (plain NetworkX) — used if python-louvain is
not installed. Cluster quality is lower but the API stays stable.

Input:  cochange bundle (the dict returned by build_cochange)
Output: { meta, communities: {id: {size, label, members}},
          module_to_community: {target: id} }
"""
from __future__ import annotations
from collections import Counter

try:
    import networkx as nx  # type: ignore
except ImportError:
    nx = None  # type: ignore

try:
    import community as community_louvain  # type: ignore
    _HAS_LOUVAIN = True
except ImportError:
    _HAS_LOUVAIN = False


def _auto_label(members: list[str]) -> str:
    """Cheap readable label from most-common short tokens in member ids."""
    tok_counts: Counter[str] = Counter()
    for m in members:
        for part in m.replace("_", "-").split("-"):
            p = part.strip().lower()
            if len(p) >= 3 and not p.isdigit():
                tok_counts[p] += 1
    top = [t for t, _ in tok_counts.most_common(3)]
    return ", ".join(top) or f"cluster[{len(members)}]"


def _pure_connected_components(edges: dict[str, list[dict]]) -> tuple[dict[str, int], int, int]:
    """Pure-Python CC — no networkx required.

    edges: {node: [{module, weight}, ...]}
    Returns (partition, n_nodes, n_edges).
    """
    adj: dict[str, set[str]] = {}
    n_edges = 0
    for mod, partners in edges.items():
        a = adj.setdefault(mod, set())
        for p in partners:
            other = p.get("module")
            if not other:
                continue
            if other not in a:
                a.add(other)
                adj.setdefault(other, set()).add(mod)
                n_edges += 1
    partition: dict[str, int] = {}
    cid = 0
    for start in adj:
        if start in partition:
            continue
        # BFS
        queue = [start]
        while queue:
            n = queue.pop()
            if n in partition:
                continue
            partition[n] = cid
            queue.extend(m for m in adj[n] if m not in partition)
        cid += 1
    return partition, len(adj), n_edges


def build_communities(cochange: dict, resolution: float = 1.0, seed: int = 42) -> dict:
    edges = cochange.get("edges") or {}
    if not edges:
        return {
            "meta": {"n_communities": 0, "method": "none", "total_modules": 0, "total_edges": 0},
            "communities": {},
            "module_to_community": {},
        }

    partition: dict[str, int]
    modularity: float | None = None
    method: str

    if nx is not None:
        G = nx.Graph()
        for mod, partners in edges.items():
            for p in partners:
                other = p.get("module")
                w = int(p.get("weight") or 1)
                if not other:
                    continue
                if G.has_edge(mod, other):
                    G[mod][other]["weight"] += w
                else:
                    G.add_edge(mod, other, weight=w)

        if _HAS_LOUVAIN and G.number_of_nodes() > 0:
            partition = community_louvain.best_partition(G, resolution=resolution, random_state=seed)
            try:
                modularity = community_louvain.modularity(partition, G)
            except Exception:
                modularity = None
            method = "louvain"
        else:
            partition = {node: i for i, comp in enumerate(nx.connected_components(G)) for node in comp}
            method = "connected_components_nx"
        n_nodes = G.number_of_nodes()
        n_edges = G.number_of_edges()
    else:
        partition, n_nodes, n_edges = _pure_connected_components(edges)
        method = "connected_components_pure"

    communities: dict[str, dict] = {}
    for comm_id in sorted(set(partition.values())):
        members = [m for m, c in partition.items() if c == comm_id]
        communities[str(comm_id)] = {
            "size": len(members),
            "label": _auto_label(members),
            "members": members[:25],  # cap to keep output tractable
        }

    return {
        "meta": {
            "n_communities": len(communities),
            "modularity": round(modularity, 4) if modularity is not None else None,
            "total_modules": n_nodes,
            "total_edges": n_edges,
            "resolution": resolution,
            "seed": seed,
            "method": method,
        },
        "communities": communities,
        "module_to_community": {m: str(c) for m, c in partition.items()},
    }
