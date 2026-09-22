"""有向图递归分析：替代链传播过敏原、检测循环、标记受影响路径。

图模型
------
- 节点：原料（携带过敏原集合）
- 有向边：source -> target 表示 "source 可被 target 替代"

从配方中的每个原料出发递归 DFS，枚举出所有替代路径（含路径上每个节点），
对每条路径聚合过敏原；当目标节点已经出现在当前路径上时，该路径构成循环并被标记。

文件后半部分实现「安全替代方案」：为配方每种配料枚举安全候选
（原料本身 + 沿替代边无环可达的终点），再最佳优先组合为整方方案。
"""
import heapq
from typing import Any, Dict, List, Optional, Set, Tuple

import sqlite3

MAX_DEPTH = 60  # 保护性深度上限（循环已由 on_path 集合提前截断）

# ---- 安全替代方案（safe-plans）常量 ----
MAX_PLANS = 50                 # 返回方案数硬上限
MAX_CANDIDATES_PER_ITEM = 100  # 每种配料保留的候选数上限（短路径优先）
MAX_PATHS_PER_ITEM = 2000      # 单配料路径枚举保护上限（防稠密图爆炸）
MAX_SEARCH_POPS = 5000         # 组合搜索弹堆保护上限


def _build_graph(conn: sqlite3.Connection) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[Dict[str, str]]]]:
    nodes: Dict[str, Dict[str, Any]] = {}
    for row in conn.execute("SELECT code, name, allergens FROM ingredients"):
        nodes[row["code"]] = {
            "code": row["code"],
            "name": row["name"],
            "allergens": [a.strip() for a in row["allergens"].split(",") if a.strip()],
            "exists": True,
        }

    adjacency: Dict[str, List[Dict[str, str]]] = {}
    for row in conn.execute("SELECT source_code, target_code, note FROM substitutions ORDER BY id"):
        adjacency.setdefault(row["source_code"], []).append(
            {"target_code": row["target_code"], "note": row["note"] or ""}
        )
    return nodes, adjacency


def _node_snapshot(nodes: Dict[str, Dict[str, Any]], code: str) -> Dict[str, Any]:
    if code in nodes:
        n = nodes[code]
        return {"code": code, "name": n["name"], "allergens": list(n["allergens"]), "exists": True}
    # 图中被引用但原料库里不存在的节点
    return {"code": code, "name": f"(缺失原料:{code})", "allergens": [], "exists": False}


def _union(values: List[List[str]]) -> List[str]:
    seen: Set[str] = set()
    result: List[str] = []
    for group in values:
        for v in group:
            if v not in seen:
                seen.add(v)
                result.append(v)
    return result


def _dfs_paths(
    root_code: str,
    nodes: Dict[str, Dict[str, Any]],
    adjacency: Dict[str, Dict[str, str]],
    missing: Set[str],
    next_id,
) -> List[Dict[str, Any]]:
    """从一个根节点出发枚举全部替代路径。

    每条路径结构：
      nodes: 路径上的节点快照（长度 = edges + 1，循环时首尾代码可重复）
      edges: 走过的替代边
      cyclic / cycle_node / missing / terminal / truncated 标记位
      allergens: 路径上全部过敏原并集；new_allergens: 非根节点引入的过敏原
    """
    paths: List[Dict[str, Any]] = []

    root_snap = _node_snapshot(nodes, root_code)
    if not root_snap["exists"]:
        missing.add(root_code)

    # 元素为 (路径节点, 路径边)；循环由 "目标已在当前路径上" 判定
    stack: List[Tuple[List[Dict[str, Any]], List[Dict[str, str]]]] = [
        ([root_snap], [])
    ]

    while stack:
        path_nodes, path_edges = stack.pop()
        current_code = path_nodes[-1]["code"]
        last_exists = path_nodes[-1]["exists"]

        edges_out = adjacency.get(current_code, []) if last_exists else []
        prior_codes = {n["code"] for n in path_nodes[:-1]}
        cyclic = current_code in prior_codes
        truncated = len(path_edges) >= MAX_DEPTH

        path_allergens = _union([n["allergens"] for n in path_nodes if n["exists"]])
        root_allergens = set(path_nodes[0]["allergens"]) if path_nodes[0]["exists"] else set()
        new_allergens = [a for a in path_allergens if a not in root_allergens]

        emitted: Dict[str, Any] = {
            "id": next_id(),
            "nodes": path_nodes,
            "edges": path_edges,
            "cyclic": cyclic,
            "cycle_node": current_code if cyclic else None,
            "missing": not last_exists,
            "truncated": truncated,
            "terminal": (not edges_out) and not cyclic and not truncated and last_exists,
            "allergens": path_allergens,
            "new_allergens": new_allergens,
        }
        paths.append(emitted)

        if cyclic or not last_exists or truncated:
            continue  # 循环/缺失/超深路径不再展开

        for edge in reversed(edges_out):  # 反转入栈以保持录入顺序优先
            target = edge["target_code"]
            edge_snap = {"source_code": current_code, "target_code": target, "note": edge["note"]}
            child_snap = _node_snapshot(nodes, target)
            if not child_snap["exists"]:
                missing.add(target)

            new_nodes = path_nodes + [child_snap]
            new_edges = path_edges + [edge_snap]
            stack.append((new_nodes, new_edges))

    return paths


def analyze_recipe(conn: sqlite3.Connection, recipe_id: int) -> Optional[Dict[str, Any]]:
    recipe = conn.execute(
        "SELECT id, code, version, name, note FROM recipes WHERE id = ?", (recipe_id,)
    ).fetchone()
    if recipe is None:
        return None

    nodes, adjacency = _build_graph(conn)
    items = conn.execute(
        """SELECT ri.ingredient_code, ri.quantity, ri.unit,
                  COALESCE(i.name, '') AS ingredient_name
             FROM recipe_items ri
             LEFT JOIN ingredients i ON i.code = ri.ingredient_code
            WHERE ri.recipe_id = ?
            ORDER BY ri.id""",
        (recipe_id,),
    ).fetchall()

    pid = 0

    def next_id() -> int:
        nonlocal pid
        pid += 1
        return pid

    missing: Set[str] = set()
    roots: List[Dict[str, Any]] = []
    all_direct: List[List[str]] = []
    all_total: List[List[str]] = []
    cycles: List[Dict[str, Any]] = []

    for item in items:
        code = item["ingredient_code"]
        paths = _dfs_paths(code, nodes, adjacency, missing, next_id)

        direct = _union([paths[0]["nodes"][0]["allergens"]]) if paths and paths[0]["nodes"][0]["exists"] else []
        total = _union([p["allergens"] for p in paths])
        introduced = [a for a in total if a not in set(direct)]

        for p in paths:
            if p["cyclic"]:
                cycles.append(
                    {
                        "path_id": p["id"],
                        "root": code,
                        "cycle_node": p["cycle_node"],
                        "nodes": [n["code"] for n in p["nodes"]],
                        "edges": [{"source": e["source_code"], "target": e["target_code"]} for e in p["edges"]],
                    }
                )

        # 过敏原 -> 受影响路径索引（用于"标记每条受影响路径"）
        allergen_index: Dict[str, List[Dict[str, Any]]] = {}
        for p in paths:
            root_allergen_set = set(p["nodes"][0]["allergens"]) if p["nodes"][0]["exists"] else set()
            for allergen in p["allergens"]:
                carriers = [n["code"] for n in p["nodes"] if allergen in n["allergens"]]
                allergen_index.setdefault(allergen, []).append(
                    {
                        "path_id": p["id"],
                        "chain": [n["code"] for n in p["nodes"]],
                        "introduced_by": carriers[-1] if carriers else None,
                        "via_substitution": allergen not in root_allergen_set,
                    }
                )

        all_direct.append(direct)
        all_total.append(total)
        roots.append(
            {
                "ingredient_code": code,
                "ingredient_name": item["ingredient_name"] or f"(缺失原料:{code})",
                "quantity": item["quantity"],
                "unit": item["unit"],
                "exists": code in nodes,
                "direct_allergens": direct,
                "total_allergens": total,
                "introduced_allergens": introduced,
                "path_count": len(paths),
                "has_cycle": any(p["cyclic"] for p in paths),
                "allergen_index": allergen_index,
                "paths": paths,
            }
        )

    direct_all = _union(all_direct)
    total_all = _union(all_total)

    return {
        "recipe": {
            "id": recipe["id"],
            "code": recipe["code"],
            "version": recipe["version"],
            "name": recipe["name"],
            "note": recipe["note"],
        },
        "direct_allergens": direct_all,
        "total_allergens": total_all,
        "introduced_allergens": [a for a in total_all if a not in set(direct_all)],
        "has_cycles": bool(cycles),
        "cycles": cycles,
        "missing_nodes": sorted(missing),
        "roots": roots,
    }


# ---------------- 安全替代方案 ----------------

def _normalize_banned(values) -> List[str]:
    """规范化禁用过敏原列表：去空、去空白、去重且保持顺序。"""
    seen: Set[str] = set()
    result: List[str] = []
    for v in values or []:
        item = (v or "").strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _enumerate_item_candidates(
    root_code: str,
    nodes: Dict[str, Dict[str, Any]],
    adjacency: Dict[str, List[Dict[str, str]]],
    banned_set: Set[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """枚举一种配料的全部安全候选：原料本身 + 沿替代边无环简单路径可达的终点。

    候选 = 从根出发的一条无环路径（终点即该配料在方案中选用的原料）。排除规则：
      - 终点含禁用过敏原的候选不入列（路径中间节点不参与成品，不做限制）
      - 触及缺失节点的延伸直接剪枝（缺失节点不进入路径）
      - 会回到路径上已有节点的延伸直接剪枝（循环）
    返回 (candidates, stats)；candidates 按 (路径长度, 终点代码, 路径) 排序，
    超出 MAX_CANDIDATES_PER_ITEM 时截断并在 stats 中标记。
    """
    candidates: List[Dict[str, Any]] = []
    stats: Dict[str, Any] = {
        "missing_root": False,
        "excluded_allergen": 0,
        "excluded_missing": 0,
        "excluded_cycle": 0,
        "truncated": False,
    }

    root_snap = _node_snapshot(nodes, root_code)
    if not root_snap["exists"]:
        stats["missing_root"] = True
        return candidates, stats

    visited = 0
    stack: List[Tuple[List[Dict[str, Any]], List[Dict[str, str]]]] = [([root_snap], [])]
    while stack:
        path_nodes, path_edges = stack.pop()
        visited += 1
        if visited > MAX_PATHS_PER_ITEM:
            stats["truncated"] = True
            break

        current = path_nodes[-1]
        if any(a in banned_set for a in current["allergens"]):
            stats["excluded_allergen"] += 1
        else:
            candidates.append({"nodes": path_nodes, "edges": path_edges})

        if len(path_edges) >= MAX_DEPTH:
            continue

        prior = {n["code"] for n in path_nodes}
        for edge in reversed(adjacency.get(current["code"], [])):  # 保持录入顺序优先
            target = edge["target_code"]
            if target in prior:
                stats["excluded_cycle"] += 1
                continue
            snap = _node_snapshot(nodes, target)
            if not snap["exists"]:
                stats["excluded_missing"] += 1
                continue
            stack.append((path_nodes + [snap], path_edges + [edge]))

    candidates.sort(
        key=lambda c: (len(c["edges"]), c["nodes"][-1]["code"], tuple(n["code"] for n in c["nodes"]))
    )
    if len(candidates) > MAX_CANDIDATES_PER_ITEM:
        candidates = candidates[:MAX_CANDIDATES_PER_ITEM]
        stats["truncated"] = True
    return candidates, stats


def _combine_plans(
    per_item_candidates: List[List[Dict[str, Any]]],
    limit: int,
) -> Tuple[List[Tuple[int, ...]], bool]:
    """跨配料的最佳优先组合搜索（带提前剪枝）。

    状态 = 已确定前 k 种配料的候选下标；代价 = (替换项数, 路径总长度)。
    两个代价分量都随展开单调不减，因此小顶堆弹出的完整方案即按该关键字升序；
    一旦收满 limit + 1 条完整方案（多取 1 条用于判断截断）即停止搜索，
    无需展开其余组合 —— 这就是组合过程中的提前剪枝。
    弹堆次数设 MAX_SEARCH_POPS 保护上限。

    返回 (plans, truncated)，plans 元素为每种配料的候选下标元组。
    """
    n = len(per_item_candidates)
    heap: List[Tuple[int, int, int, int, Tuple[int, ...]]] = [(0, 0, 0, 0, ())]
    counter = 0
    pops = 0
    plans: List[Tuple[int, ...]] = []
    truncated = False

    while heap and len(plans) <= limit:
        pops += 1
        if pops > MAX_SEARCH_POPS:
            truncated = True
            break
        subs, total_len, _, depth, chosen = heapq.heappop(heap)
        if depth == n:
            plans.append(chosen)
            continue
        for idx, cand in enumerate(per_item_candidates[depth]):
            counter += 1
            step = len(cand["edges"])
            heapq.heappush(heap, (
                subs + (1 if step else 0),
                total_len + step,
                counter,
                depth + 1,
                chosen + (idx,),
            ))

    if len(plans) > limit:
        plans = plans[:limit]
        truncated = True
    return plans, truncated


def build_safe_plans(
    conn: sqlite3.Connection,
    recipe_id: int,
    banned_allergens: Optional[List[str]],
    limit: int = MAX_PLANS,
) -> Optional[Dict[str, Any]]:
    """安全替代方案主入口。

    为配方每种配料保留「原料本身 + 无环可达终点」作为候选，排除含禁用过敏原、
    缺失节点或循环的候选后组合为整方方案；按 (替换项数, 路径总长度) 升序，
    最多返回 MAX_PLANS 组。无解时通过 blocked_items 指出阻塞配料。
    """
    recipe = conn.execute(
        "SELECT id, code, version, name, note FROM recipes WHERE id = ?", (recipe_id,)
    ).fetchone()
    if recipe is None:
        return None

    banned = _normalize_banned(banned_allergens)
    banned_set = set(banned)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = MAX_PLANS
    limit = max(1, min(limit, MAX_PLANS))

    nodes, adjacency = _build_graph(conn)
    items = conn.execute(
        """SELECT ri.ingredient_code, ri.quantity, ri.unit,
                  COALESCE(i.name, '') AS ingredient_name
             FROM recipe_items ri
             LEFT JOIN ingredients i ON i.code = ri.ingredient_code
            WHERE ri.recipe_id = ?
            ORDER BY ri.id""",
        (recipe_id,),
    ).fetchall()

    per_item: List[Dict[str, Any]] = []
    for item in items:
        code = item["ingredient_code"]
        candidates, stats = _enumerate_item_candidates(code, nodes, adjacency, banned_set)
        per_item.append({
            "ingredient_code": code,
            "ingredient_name": item["ingredient_name"] or f"(缺失原料:{code})",
            "quantity": item["quantity"],
            "unit": item["unit"],
            "exists": code in nodes,
            "candidates": candidates,
            "stats": stats,
        })

    # 阻塞配料：没有任何安全候选（根本身缺失，或根含禁用过敏原且无安全替代终点）
    blocked: List[Dict[str, Any]] = []
    for it in per_item:
        if it["candidates"]:
            continue
        stats = it["stats"]
        root_hits: List[str] = []
        if it["exists"]:
            root_hits = [a for a in nodes[it["ingredient_code"]]["allergens"] if a in banned_set]
        if stats["missing_root"]:
            reason = "原料在原料库中缺失，无法评估过敏原，也没有可用的替代路径"
        elif root_hits:
            reason = f"原料本身含禁用过敏原（{'、'.join(root_hits)}），且替代链上没有安全终点"
        else:
            reason = "没有可用的安全候选（替代终点均含禁用过敏原，或路径触及缺失节点/循环）"
        blocked.append({
            "ingredient_code": it["ingredient_code"],
            "ingredient_name": it["ingredient_name"],
            "quantity": it["quantity"],
            "unit": it["unit"],
            "missing_root": stats["missing_root"],
            "banned_on_root": root_hits,
            "reason": reason,
        })

    plans_out: List[Dict[str, Any]] = []
    truncated = any(it["stats"]["truncated"] for it in per_item)
    if not blocked:
        raw_plans, search_truncated = _combine_plans([it["candidates"] for it in per_item], limit)
        truncated = truncated or search_truncated
        for rank, chosen in enumerate(raw_plans, 1):
            selections: List[Dict[str, Any]] = []
            sub_count = 0
            path_len_total = 0
            for it, idx in zip(per_item, chosen):
                cand = it["candidates"][idx]
                endpoint = cand["nodes"][-1]
                step = len(cand["edges"])
                if step:
                    sub_count += 1
                path_len_total += step
                selections.append({
                    "ingredient_code": it["ingredient_code"],
                    "ingredient_name": it["ingredient_name"],
                    "quantity": it["quantity"],
                    "unit": it["unit"],
                    "selected_code": endpoint["code"],
                    "selected_name": endpoint["name"],
                    "selected_allergens": list(endpoint["allergens"]),
                    "substituted": bool(step),
                    "path": [
                        {"code": n["code"], "name": n["name"], "allergens": list(n["allergens"])}
                        for n in cand["nodes"]
                    ],
                    "edges": [dict(e) for e in cand["edges"]],
                })
            plans_out.append({
                "rank": rank,
                "substitution_count": sub_count,
                "total_path_length": path_len_total,
                "allergens": _union([s["selected_allergens"] for s in selections]),
                "selections": selections,
            })

    return {
        "recipe": {
            "id": recipe["id"],
            "code": recipe["code"],
            "version": recipe["version"],
            "name": recipe["name"],
            "note": recipe["note"],
        },
        "banned_allergens": banned,
        "limit": limit,
        "plan_count": len(plans_out),
        "truncated": truncated,
        "plans": plans_out,
        "blocked_items": blocked,
        "items": [
            {
                "ingredient_code": it["ingredient_code"],
                "ingredient_name": it["ingredient_name"],
                "quantity": it["quantity"],
                "unit": it["unit"],
                "exists": it["exists"],
                "candidate_count": len(it["candidates"]),
                "candidates_truncated": it["stats"]["truncated"],
                "excluded": {
                    "banned_allergen": it["stats"]["excluded_allergen"],
                    "missing_node": it["stats"]["excluded_missing"],
                    "cycle": it["stats"]["excluded_cycle"],
                },
            }
            for it in per_item
        ],
    }
