# 配方替代过敏原追踪（Allergen Tracker）

面向食品研发的 **配方版本 / 原料 / 替代关系** 管理与过敏原影响追踪 Web 应用。

- 技术栈：Python 3.11 + FastAPI + SQLite + 原生 HTML/CSS/JS（无前端构建、无第三方账号）
- 页面与 API 统一暴露在 **8000** 端口
- 核心能力：以有向图递归计算替代链带来的过敏原影响、检测循环并标记每条受影响路径、两版配方差异对比、**按需避开的过敏原生成安全替代方案**、原料/配方 JSON 导入导出

---

## 一键启动（Docker Compose，推荐）

```bash
docker compose up --build
```

启动后访问：

| 地址 | 说明 |
| --- | --- |
| http://localhost:8000/ | Web 页面 |
| http://localhost:8000/docs | FastAPI 交互式 API 文档 |

SQLite 数据保存在 Docker volume `allergen-data`（容器内 `/data/app.db`），容器重建不丢数据。

仅用 Docker：

```bash
docker build -t allergen-tracker .
docker run -p 8000:8000 -v "$PWD/data:/data" -e DB_PATH=/data/app.db allergen-tracker
```

## 本地直接运行（无需 Docker）

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

默认数据库位于 `./data/app.db`，可用环境变量 `DB_PATH` 覆盖。

---

## 快速体验

1. 打开 http://localhost:8000/
2. 进入「**导入导出**」页 → 点击「🌱 载入示例数据」
   - 12 种原料、8 条替代边、2 个版本的曲奇配方（`COOKIE-001` v1.0 / v2.0）
   - 内含一条 **双向替代循环**：`BUTTER → MARGARINE → BUTTER`
3. 在「**过敏原分析**」页选择配方并分析：
   - 直接过敏原 / 含替代链的全部过敏原 / 替代新引入过敏原
   - 每条替代路径被编号标记（`#1`、`#2` …），循环路径红色高亮并指出回到哪个节点
   - 「过敏原受影响路径索引」表可按过敏原反查受影响路径
4. 在「**安全替代**」页选择配方、勾选需避开的过敏原（如 `乳`），点击「生成方案」：
   - 每组方案可展开，查看每种配料的所选原料、替代路径与过敏原
   - 无解时红色提示阻塞配料及原因（如 `乳`+`大豆` 同时避开时，黄油与酱油均无安全替代）
5. 在「**版本对比**」页选择 v1.0 与 v2.0，查看原料增删、用量变化与过敏原集合差异。

---

## 图模型与计算规则

- **节点**：原料，携带过敏原集合（如黄油 → {乳}）
- **有向边** `source → target`：source 可被 target 替代
- 从配方中的每个原料出发做深度优先（DFS）递归枚举**全部替代路径**：
  - 路径上每个节点的过敏原取并集，即该替代方案下的过敏原集合
  - `直接过敏原` = 配方原料自身过敏原；`替代新引入` = 路径下游新增、根节点没有的过敏原
  - 当目标节点已出现在当前路径上时，判定为**循环**：该路径以红色标记并立即停止展开（防止无限递归），同时在顶部汇总所有循环
  - 图中引用了但原料库缺失的节点标记为「缺失节点」
  - 另设有 60 层深度保护上限（`app/graph.py` 的 `MAX_DEPTH`）
- 每条路径带唯一编号；「过敏原 → 路径」索引让每一条受影响路径都可被标记与追溯。

## 安全替代方案（safe-plans）

入口：页面「**安全替代**」标签页，或 `POST /api/recipes/{id}/safe-plans`。

- **候选生成**：对配方中每种配料，保留「原料本身」及沿替代边**无环简单路径可达的每个终点**作为候选（记录完整替代路径）。
- **候选排除**：终点含禁用过敏原、路径触及缺失节点、或形成循环的候选一律排除（路径中间节点不进入成品，不参与过敏原判定）。
- **组合搜索**：跨配料做最佳优先组合，代价为 `(替换项数, 路径总长度)` 且单调不减，堆中弹出的完整方案即按该关键字升序；收满上限即停止，实现组合过程中的提前剪枝。
- **返回上限**：最多 **50** 组（`limit` 参数会被钳制到 1~50），超出时 `truncated=true`。
- **每组方案**展示：每种配料的所选原料、替代路径（含每跳备注）与过敏原，以及整方过敏原并集。
- **无解时**：`blocked_items` 列出阻塞配料及原因（原料缺失 / 本身含禁用过敏原且替代链上无安全终点）；`items` 数组始终给出每种配料的候选数与各类排除计数。

请求体示例：

```json
{
  "banned_allergens": ["乳", "大豆"],
  "limit": 50
}
```

## 主要 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST | `/api/ingredients`，PUT/DELETE `/api/ingredients/{code}` | 原料 CRUD |
| GET/POST | `/api/substitutions`，PUT/DELETE `/api/substitutions/{id}` | 替代关系 CRUD |
| GET/POST | `/api/recipes`，PUT/DELETE `/api/recipes/{id}` | 配方版本 CRUD（含配料表整体替换） |
| GET | `/api/recipes/{id}/analyze` | 替代链过敏原递归分析 + 循环检测 |
| POST | `/api/recipes/{id}/safe-plans` | 安全替代方案，body：`{"banned_allergens": ["乳"], "limit": 50}`（limit 可选，钳制 1~50） |
| POST | `/api/compare` | 两版配方差异对比，body：`{"recipe_a":{"code","version"}, "recipe_b":{"code","version"}}`（也支持 `{"id": n}`） |
| GET | `/api/export` | 导出全部数据 JSON |
| POST | `/api/import?mode=merge\|replace` | 导入 JSON；merge 按主键 upsert，replace 清空后导入 |
| POST | `/api/seed` | 载入内置示例数据（幂等） |

### 导入/导出 JSON 格式

```json
{
  "format": "allergen-tracker/v1",
  "ingredients": [
    {"code": "BUTTER", "name": "黄油", "allergens": "乳", "note": ""}
  ],
  "recipes": [
    {
      "code": "COOKIE-001", "version": "1.0", "name": "经典黄油曲奇", "note": "",
      "items": [
        {"ingredient_code": "BUTTER", "quantity": 120, "unit": "g"}
      ]
    }
  ],
  "substitutions": [
    {"source_code": "BUTTER", "target_code": "MARGARINE", "note": "等质量替换"}
  ]
}
```

`allergens` 支持逗号、顿号分隔，服务端自动去空、去重。导入时引用了缺失原料的配方/替代边会被跳过并在返回的 `errors` 中列出。

## 项目结构

```
.
├── app/
│   ├── main.py        # FastAPI 路由（页面 + REST API）
│   ├── graph.py       # 有向图 DFS：替代链过敏原传播 / 循环检测 / 路径标记 / 安全替代方案组合搜索
│   ├── crud.py        # 原料 / 配方 / 替代关系数据操作
│   ├── schemas.py     # Pydantic 模型与过敏原规范化
│   ├── database.py    # SQLite 连接与建表
│   └── seed.py        # 内置示例数据
├── static/
│   ├── index.html     # 单页界面（7 个标签页）
│   ├── style.css
│   └── app.js         # 原生 JS 前端逻辑
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 备注

- 应用仅供研发阶段的配方/过敏原信息管理，产品标签与合规声明仍需以供应商规格书和检测结果为准。
