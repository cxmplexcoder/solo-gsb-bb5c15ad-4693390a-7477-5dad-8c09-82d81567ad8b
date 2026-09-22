"""安全替代方案：在避开指定过敏原的前提下，为整方枚举替代组合。

候选规则（每种配料）
--------------------
- 保留「原料本身」（长度 0 的路径，无论它是否有出边）；
- 以及沿现有替代边可达的「无环终点」：路径为简单路径（不重复经过节点），
  终点出度为 0。
- 候选是否合规取决于**所选终点（最终入料）自身**是否携带禁用过敏原：
  原料本身或终点命中即排除该候选。替代的语义是用终点替换原配料，中间
  节点不会进入最终配方，因此不因中间节点含禁用过敏原而剪枝
  （路径上各节点过敏原仍随结果展示，供人工复核）。
- 命中缺失节点、回到已访问节点（成环）、超过深度上限的路径同样排除。

整方组合
--------
各配料的安全候选做笛卡尔积，使用最小堆按
(替换项数, 路径总长度) 最佳优先枚举（候选按路径长度升序，保证入堆键
单调不减），最多产出 MAX_PLANS 组——无需展开完整笛卡尔积即可保证
返回的就是排序最靠前的前 N 组，即组合搜索过程中的提前剪枝。

当某配料没有任何安全候选时整方无解（例如原料缺失，或原料自身与全部
可达无环终点都含禁用过敏原），此时返回阻塞配料及被排除候选路径的诊断。
"""
import heapq
from typing import Any, Dict, List, Optional, Set, Tuple

from . import crud, graph
from .schemas import normalize_allergens

MAX_PLANS = 50
MAX_SAMPLE_PATHS = 10


# ---------------- 单配料候选枚举 ----------------

def _node_dict(snap: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "code": snap["code"],
        "name": snap["name"],
        "allergens": list(snap["allergens"]),
        "exists": snap["exists"],
    }


def _edge_dict(edges: List[Dict[str, str]]) -> List[Dict[str, str]]:
    return [
        {"source_code": e["source_code"], "target_code": e["target_code"], "note": e.get("note", "")}
        for e in edges
    ]


def _excluded(reason: str, path, edges, trigger_node, trigger_allergens) -> Dict[str, Any]:
    return {
        "reason": reason,
        "trigger_node": trigger_node,
        "trigger_allergens": list(trigger_allergens),
        "path": [_node_dict(n) for n in path],
        "edges": _edge_dict(edges),
    }


def _make_option(path, edges) -> Dict[str, Any]:
    endpoint = path[-1]
    return {
        "selected_code": endpoint["code"],
        "selected_name": endpoint["name"],
        "path_length": len(edges),
        "path": [_node_dict(n) for n in path],
        "edges": _edge_dict(edges),
        "allergens": list(endpoint["allergens"]),
    }


def _enumerate_options(
    root_code: str,
    nodes: Dict[str, Dict[str, Any]],
    adjacency: Dict[str, List[Dict[str, str]]],
    avoid_set: Set[str],
):
    """枚举单个配料的安全候选。

    返回 (options, excluded, root_meta)：
    - options: 通过全部约束的候选（已按偏好排序）；
    - excluded: 被排除候选/分支的诊断（含禁用过敏原、缺失、循环、超深）；
    - root_meta: 原料节点是否存在、原料自身命中的禁用过敏原。
    """
    options: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    root = graph._node_snapshot(nodes, root_code)

    if not root["exists"]:
        excluded.append(_excluded("missing", [root], [], root_code, []))
        return options, excluded, {"exists": False, "self_forbidden": []}

    root_hits = sorted(set(root["allergens"]) & avoid_set)
    if root_hits:
        excluded.append(_excluded("forbidden", [root], [], root_code, root_hits))
    else:
        # 原料本身始终保留为候选
        options.append(_make_option([root], []))

    # 栈元素：(路径节点, 已走边)；简单路径由"目标已在路径上"判定为环
    stack: List[Tuple[List[Dict[str, Any]], List[Dict[str, str]]]] = [([root], [])]

    while stack:
        path, edges = stack.pop()
        current = path[-1]
        out = adjacency.get(current["code"], [])

        if not out:
            # 出度为 0 的无环终点（长度 0 的自身已单独收录）
            if len(path) > 1:
                hits = sorted(set(current["allergens"]) & avoid_set)
                if hits:
                    excluded.append(_excluded("forbidden", path, edges, current["code"], hits))
                else:
                    options.append(_make_option(path, edges))
            continue

        if len(edges) >= graph.MAX_DEPTH:
            excluded.append(_excluded("truncated", path, edges, None, []))
            continue

        for edge in reversed(out):  # 反转入栈以保持录入顺序优先
            target = edge["target_code"]
            new_edges = edges + [
                {"source_code": current["code"], "target_code": target, "note": edge["note"]}
            ]

            # 成环：目标已在当前路径上
            if any(n["code"] == target for n in path):
                excluded.append(
                    _excluded("cyclic", path + [graph._node_snapshot(nodes, target)], new_edges, target, [])
                )
                continue

            child = graph._node_snapshot(nodes, target)
            if not child["exists"]:
                excluded.append(_excluded("missing", path + [child], new_edges, target, []))
                continue

            # 中间节点含禁用过敏原不剪枝：更下游的无环终点仍可能安全
            stack.append((path + [child], new_edges))

    options.sort(
        key=lambda o: (
            o["path_length"],
            o["selected_code"],
            tuple(n["code"] for n in o["path"]),
        )
    )
    return options, excluded, {"exists": True, "self_forbidden": root_hits}


# ---------------- 整方最佳优先组合 ----------------

def _combine(
    options_per_item: List[List[Dict[str, Any]]],
    limit: int = MAX_PLANS,
) -> Tuple[List[Tuple[int, ...]], bool]:
    """以 (替换项数, 路径总长度) 升序，用最小堆枚举候选索引组合。

    每个配料的候选已按路径长度升序排列，因此沿"某一配料换下一个候选"
    生成后继时键值单调不减；配合 visited 去重，弹出序列即为全局有序，
    且最多只生成 limit × 配料数 数量级的状态。
    返回 (索引组合列表, 是否还有更多未产出的方案)。
    """
    n = len(options_per_item)
    if n == 0:
        return [()], False

    def key_of(idx: Tuple[int, ...]):
        sub = total = 0
        for k, j in enumerate(idx):
            length = options_per_item[k][j]["path_length"]
            total += length
            if length > 0:
                sub += 1
        return sub, total

    start = (0,) * n
    heap: List[Tuple[Tuple[int, int], Tuple[int, ...]]] = [(key_of(start), start)]
    visited = {start}
    picked: List[Tuple[int, ...]] = []

    while heap and len(picked) < limit:
        _, idx = heapq.heappop(heap)
        picked.append(idx)
        for k in range(n):
            if idx[k] + 1 < len(options_per_item[k]):
                nxt = idx[:k] + (idx[k] + 1,) + idx[k + 1:]
                if nxt not in visited:
                    visited.add(nxt)
                    heapq.heappush(heap, (key_of(nxt), nxt))

    return picked, bool(heap)


# ---------------- 响应组装 ----------------

def _blocked_entry(item, root_meta: Dict[str, Any], excluded: List[Dict[str, Any]]) -> Dict[str, Any]:
    missing = not root_meta["exists"]
    reason = "missing" if missing else "forbidden"
    counts = {"forbidden": 0, "missing": 0, "cyclic": 0, "truncated": 0}
    samples: List[Dict[str, Any]] = []
    for exc in excluded:
        if exc["reason"] in counts:
            counts[exc["reason"]] += 1
        if len(samples) < MAX_SAMPLE_PATHS:
            samples.append(exc)

    if missing:
        text = "原料节点缺失：该配料引用的原料不在原料库中，无法参与替代"
    else:
        text = (
            f"原料本身含禁用过敏原（{'、'.join(root_meta['self_forbidden'])}），"
            "且沿替代边不存在合规的无环终点"
        )
        detail = []
        if counts["forbidden"]:
            detail.append(f"{counts['forbidden']} 个无环终点含禁用过敏原")
        if counts["cyclic"]:
            detail.append(f"{counts['cyclic']} 条替代路径成环")
        if counts["missing"]:
            detail.append(f"{counts['missing']} 条替代路径到达缺失节点")
        if detail:
            text += "（" + "、".join(detail) + "）"

    return {
        "ingredient_code": item["ingredient_code"],
        "ingredient_name": item["ingredient_name"] or f"(缺失原料:{item['ingredient_code']})",
        "quantity": item["quantity"],
        "unit": item["unit"],
        "reason": reason,
        "reason_text": text,
        "self_forbidden": root_meta["self_forbidden"],
        "excluded_counts": counts,
        "samples": samples,
    }


def build_safe_plans(conn, recipe_id: int, avoid_raw: Optional[List[str]]) -> Optional[Dict[str, Any]]:
    recipe = crud.get_recipe(conn, recipe_id)
    if recipe is None:
        return None

    nodes, adjacency = graph._build_graph(conn)
    items = conn.execute(
        """SELECT ri.ingredient_code, ri.quantity, ri.unit,
                  COALESCE(i.name, '') AS ingredient_name
             FROM recipe_items ri
             LEFT JOIN ingredients i ON i.code = ri.ingredient_code
            WHERE ri.recipe_id = ?
            ORDER BY ri.id""",
        (recipe_id,),
    ).fetchall()

    avoid: List[str] = []
    for raw in avoid_raw or []:
        for allergen in normalize_allergens(raw):
            if allergen not in avoid:
                avoid.append(allergen)
    avoid_set = set(avoid)

    options_per_item: List[List[Dict[str, Any]]] = []
    blocked: List[Dict[str, Any]] = []

    for item in items:
        options, excluded, root_meta = _enumerate_options(
            item["ingredient_code"], nodes, adjacency, avoid_set
        )
        options_per_item.append(options)
        if not options:
            blocked.append(_blocked_entry(item, root_meta, excluded))

    plans: List[Dict[str, Any]] = []
    has_more = False
    if not blocked:
        chosen, has_more = _combine(options_per_item)
        for rank, idx in enumerate(chosen, start=1):
            plan_items = []
            allergen_lists: List[List[str]] = []
            sub_count = 0
            total_length = 0
            for k, item in enumerate(items):
                opt = options_per_item[k][idx[k]]
                if opt["path_length"] > 0:
                    sub_count += 1
                total_length += opt["path_length"]
                allergen_lists.append(opt["allergens"])
                plan_items.append(
                    {
                        "ingredient_code": item["ingredient_code"],
                        "ingredient_name": item["ingredient_name"]
                        or f"(缺失原料:{item['ingredient_code']})",
                        "quantity": item["quantity"],
                        "unit": item["unit"],
                        "substituted": opt["path_length"] > 0,
                        "selected_code": opt["selected_code"],
                        "selected_name": opt["selected_name"],
                        "path_length": opt["path_length"],
                        "path": opt["path"],
                        "edges": opt["edges"],
                        "allergens": opt["allergens"],
                    }
                )
            plans.append(
                {
                    "rank": rank,
                    "substitution_count": sub_count,
                    "total_path_length": total_length,
                    "allergens": graph._union(allergen_lists),
                    "items": plan_items,
                }
            )

    return {
        "recipe": {
            "id": recipe["id"],
            "code": recipe["code"],
            "version": recipe["version"],
            "name": recipe["name"],
            "note": recipe["note"],
        },
        "avoid_allergens": avoid,
        "solvable": not blocked,
        "plan_count": len(plans),
        "limit": MAX_PLANS,
        "has_more": has_more,
        "plans": plans,
        "blocked": blocked,
    }
