"""MCP server that lets Claude explore and question a supply chain knowledge graph in Neo4j.

Claude gets two kinds of tools:
  * general tools that work on ANY graph: get_graph_schema, run_cypher_query (read-only)
  * supply chain analysis tools for the standard model (see README): single-source risk,
    supplier disruption impact, late shipments, low inventory, risk event exposure, product tracing

Everything is read-only: queries run in Neo4j READ transactions, and write clauses are rejected.
Run by Claude Desktop over stdio (see README); you do not start it yourself.
"""
import functools, json, re
import neo4j
from neo4j import GraphDatabase, READ_ACCESS
from mcp.server.fastmcp import FastMCP
from config import settings

MAX_ROWS = 200
TIMEOUT_S = 30

mcp = FastMCP("supply-chain-graph", instructions=(
    "Tools for a supply chain knowledge graph stored in Neo4j. Call get_graph_schema first to learn the "
    "node labels, relationship types, and properties. Prefer the specific analysis tools when they fit the question; "
    "otherwise write a read-only Cypher query for run_cypher_query. Base every answer on query results, "
    "show the key numbers, and say when the data cannot answer a question."))

_driver = None
_schema_cache = None

def driver():
    global _driver
    if _driver is None:
        s = settings()
        _driver = GraphDatabase.driver(s["uri"], auth=(s["user"], s["password"]), max_transaction_retry_time=5)
        _driver.database_name = s["database"]
    return _driver

# ---------------- helpers ----------------
def tool(fn):
    """Register an MCP tool that returns a clear message instead of a stack trace when something fails."""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except neo4j.exceptions.AuthError:
            return json.dumps({"error": "Neo4j rejected the username or password. Check NEO4J_USERNAME and NEO4J_PASSWORD in .env."})
        except (neo4j.exceptions.ServiceUnavailable, neo4j.exceptions.SessionExpired, OSError):
            return json.dumps({"error": "Cannot reach Neo4j. Check that the database is running and NEO4J_URI in .env is correct."})
        except neo4j.exceptions.Neo4jError as e:
            return json.dumps({"error": e.message, "code": e.code})
        except SystemExit as e:
            return json.dumps({"error": str(e)})
    return mcp.tool()(wrapper)

WRITE_CLAUSES = re.compile(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH|LOAD\s+CSV|IN\s+TRANSACTIONS)\b|\bapoc\.(create|merge|refactor|periodic|load|import|export)", re.I)

def _clean(v):
    """Make Neo4j values JSON friendly."""
    if isinstance(v, neo4j.graph.Node):
        return {"labels": sorted(v.labels), **{k: _clean(x) for k, x in v.items()}}
    if isinstance(v, neo4j.graph.Relationship):
        return {"type": v.type, **{k: _clean(x) for k, x in v.items()}}
    if isinstance(v, neo4j.graph.Path):
        return {"nodes": [_clean(n) for n in v.nodes], "relationships": [_clean(r) for r in v.relationships]}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)  # dates, datetimes, durations, points

def _read(query, params=None, limit=MAX_ROWS):
    """Run a query in a READ transaction and return up to `limit` rows."""
    @neo4j.unit_of_work(timeout=TIMEOUT_S)
    def work(tx):
        result = tx.run(query, params or {})
        out = []
        for rec in result:
            if len(out) == limit:
                return out, True
            out.append({k: _clean(rec[k]) for k in rec.keys()})
        return out, False
    d = driver()
    with d.session(database=d.database_name, default_access_mode=READ_ACCESS) as session:
        return session.execute_read(work)

def _reply(rows, truncated=False, note=None):
    body = {"row_count": len(rows), "rows": rows}
    if truncated:
        body["note"] = f"Only the first {len(rows)} rows are shown. Add aggregation or a LIMIT for a complete answer."
    if note:
        body["note"] = note
    return json.dumps(body, indent=1, default=str)

def _requires(*labels):
    have = set(_schema()["node_labels"])
    missing = [l for l in labels if l not in have]
    if missing:
        return (f"This tool needs node labels {missing}, which are not in this graph. "
                "Use get_graph_schema and run_cypher_query instead.")
    return None

def _schema():
    global _schema_cache
    if _schema_cache is None:
        labels = _read("MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS count ORDER BY label", limit=1000)[0]
        node_props = _read("CALL db.schema.nodeTypeProperties() YIELD nodeLabels, propertyName, propertyTypes "
                           "RETURN nodeLabels, propertyName, propertyTypes", limit=5000)[0]
        rel_props = _read("CALL db.schema.relTypeProperties() YIELD relType, propertyName, propertyTypes "
                          "RETURN relType, propertyName, propertyTypes", limit=5000)[0]
        patterns = _read("MATCH (a)-[r]->(b) RETURN labels(a)[0] AS from_label, type(r) AS type, labels(b)[0] AS to_label, "
                         "count(*) AS count ORDER BY type, from_label", limit=1000)[0]
        nodes = {}
        for r in node_props:
            for lab in r["nodeLabels"]:
                if r["propertyName"]:
                    nodes.setdefault(lab, {})[r["propertyName"]] = "/".join(r["propertyTypes"] or [])
        rels = {}
        for r in rel_props:
            t = r["relType"].strip(":`")
            if r["propertyName"]:
                rels.setdefault(t, {})[r["propertyName"]] = "/".join(r["propertyTypes"] or [])
        _schema_cache = {
            "node_labels": {l["label"]: {"count": l["count"], "properties": nodes.get(l["label"], {})} for l in labels},
            "relationships": [{"pattern": f"(:{p['from_label']})-[:{p['type']}]->(:{p['to_label']})", "count": p["count"],
                               "properties": rels.get(p["type"], {})} for p in patterns],
        }
    return _schema_cache

# ---------------- general tools ----------------
@tool
def get_graph_schema() -> str:
    """Describe the graph: every node label with its count and properties, and every relationship pattern
    (which labels it connects) with its count and properties. Call this before writing Cypher."""
    return json.dumps(_schema(), indent=1)

@tool
def run_cypher_query(query: str, params: dict | None = None) -> str:
    """Run a READ-ONLY Cypher query and return the rows as JSON (at most 200 rows).
    Use $parameters for values. Write clauses (CREATE, MERGE, SET, DELETE, ...) are rejected.
    Dates are Neo4j date values: compare with date('2026-09-01'), and use duration.between(a, b).days for gaps."""
    if WRITE_CLAUSES.search(query):
        return json.dumps({"error": "Only read queries are allowed (no CREATE, MERGE, SET, DELETE, REMOVE, DROP, LOAD CSV)."})
    try:
        rows, truncated = _read(query, params)
        return _reply(rows, truncated)
    except neo4j.exceptions.Neo4jError as e:
        return json.dumps({"error": e.message, "code": e.code, "hint": "Check labels and property names with get_graph_schema."})

# ---------------- supply chain analysis tools ----------------
@tool
def find_single_source_parts() -> str:
    """Parts that only ONE supplier provides (single points of failure), with the supplier, its country risk,
    lead time, and the products that use each part. Critical parts and high-risk countries come first."""
    if (m := _requires("Part", "Supplier")): return m
    rows, t = _read("""
        MATCH (p:Part)<-[s:SUPPLIES]-(sup:Supplier)
        WITH p, collect({sup: sup, rel: s}) AS sources
        WHERE size(sources) = 1
        WITH p, sources[0].sup AS sup, sources[0].rel AS r
        OPTIONAL MATCH (sup)-[:LOCATED_IN]->(c:Country)
        OPTIONAL MATCH (prod:Product)-[:HAS_COMPONENT]->(p)
        RETURN p.id AS part_id, p.name AS part, p.category AS category, p.critical AS critical,
               sup.name AS only_supplier, c.name AS supplier_country, c.risk_score AS country_risk_1_to_7,
               r.lead_time_days AS lead_time_days, r.on_time_rate AS on_time_rate,
               collect(DISTINCT prod.name) AS used_in_products
        ORDER BY critical DESC, country_risk_1_to_7 DESC, size(used_in_products) DESC""")
    return _reply(rows, t)

@tool
def supplier_disruption_impact(supplier: str) -> str:
    """What happens if a supplier stops delivering. `supplier` is a supplier id (e.g. SUP007) or part of its name.
    Works for tier-2 suppliers too (follows them to the tier-1 suppliers they feed). Returns each affected part,
    how many alternative suppliers it has, the products that use it, and the recent sales at risk for products
    whose parts have no alternative supplier."""
    if (m := _requires("Supplier", "Part")): return m
    found, _ = _read("MATCH (s:Supplier) WHERE s.id = $q OR toLower(s.name) CONTAINS toLower($q) "
                     "OPTIONAL MATCH (s)-[:LOCATED_IN]->(c:Country) "
                     "RETURN s.id AS id, s.name AS name, s.tier AS tier, c.name AS country ORDER BY s.id", {"q": supplier}, limit=10)
    if not found:
        return json.dumps({"error": f"No supplier matches '{supplier}'."})
    if len(found) > 1:
        return json.dumps({"note": "Several suppliers match; call again with one id.", "matches": found})
    sid = found[0]["id"]
    parts, _ = _read("""
        MATCH (s:Supplier {id: $sid})-[:SUPPLIES_TO*0..1]->(t1:Supplier)-[:SUPPLIES]->(p:Part)
        WITH p, collect(DISTINCT t1) AS via
        OPTIONAL MATCH (alt:Supplier)-[:SUPPLIES]->(p) WHERE NOT alt IN via
        WITH p, via, count(DISTINCT alt) AS alternative_suppliers
        OPTIONAL MATCH (prod:Product)-[:HAS_COMPONENT]->(p)
        RETURN p.id AS part_id, p.name AS part, p.critical AS critical, [v IN via | v.name] AS supplied_through,
               alternative_suppliers, collect(DISTINCT prod.id) AS product_ids, collect(DISTINCT prod.name) AS products
        ORDER BY alternative_suppliers, critical DESC""", {"sid": sid}, limit=500)
    exposed = sorted({pid for p in parts if p["alternative_suppliers"] == 0 for pid in p["product_ids"]})
    at_risk = []
    if exposed and "SalesOrder" in _schema()["node_labels"]:
        at_risk, _ = _read("""
            MATCH (x:SalesOrder) WITH max(x.order_date) AS last
            MATCH (c:Customer)-[:PLACED]->(so:SalesOrder)-[k:CONTAINS]->(prod:Product)
            WHERE prod.id IN $ids AND so.order_date > last - duration('P90D')
            RETURN prod.name AS product, sum(k.quantity) AS units_last_90_days,
                   round(sum(k.quantity * prod.unit_price_usd)) AS revenue_last_90_days_usd,
                   count(DISTINCT c) AS customers
            ORDER BY revenue_last_90_days_usd DESC""", {"ids": exposed})
    for p in parts:
        p.pop("product_ids", None)
    return json.dumps({"supplier": found[0], "parts_affected": parts,
                       "products_with_no_alternative_source": at_risk}, indent=1, default=str)

@tool
def late_shipment_analysis(group_by: str = "carrier") -> str:
    """On-time delivery performance for delivered shipments. group_by: 'carrier', 'warehouse', or 'customer_region'.
    Returns delivered count, late count, late rate %, and average days late (late shipments only)."""
    match = {"carrier": "MATCH (sh:Shipment)-[:CARRIED_BY]->(g:Carrier) WITH sh, g.name AS grp",
             "warehouse": "MATCH (sh:Shipment)-[:SHIPPED_FROM]->(g:Warehouse) WITH sh, g.name AS grp",
             "customer_region": "MATCH (c:Customer)-[:PLACED]->(:SalesOrder)-[:FULFILLED_BY]->(sh:Shipment) WITH sh, c.region AS grp"}
    if group_by not in match:
        return json.dumps({"error": "group_by must be carrier, warehouse, or customer_region"})
    if (m := _requires("Shipment")): return m
    rows, t = _read(match[group_by] + """
        WHERE sh.delivered_date IS NOT NULL
        WITH grp, count(sh) AS delivered, sum(CASE WHEN sh.days_late > 0 THEN 1 ELSE 0 END) AS late,
             avg(CASE WHEN sh.days_late > 0 THEN sh.days_late END) AS avg_late
        RETURN grp AS """ + group_by + """, delivered, late, round(100.0 * late / delivered, 1) AS late_rate_pct,
               round(avg_late, 1) AS avg_days_late
        ORDER BY late_rate_pct DESC""")
    return _reply(rows, t)

@tool
def inventory_below_reorder_point() -> str:
    """Warehouse and product pairs where stock on hand is below the reorder point, with the shortfall,
    whether it is also below safety stock, and the plant that can resupply that warehouse fastest."""
    if (m := _requires("Warehouse", "Product")): return m
    rows, t = _read("""
        MATCH (w:Warehouse)-[s:STOCKS]->(p:Product)
        WHERE s.on_hand < s.reorder_point
        OPTIONAL MATCH (pl:Plant)-[:PRODUCES]->(p)
        OPTIONAL MATCH (pl)-[lane:SHIPS_TO]->(w)
        WITH w, p, s, pl, lane ORDER BY lane.transit_days
        WITH w, p, s, collect(CASE WHEN pl IS NULL THEN null ELSE {plant: pl.name, transit_days: lane.transit_days} END) AS options
        RETURN w.name AS warehouse, p.name AS product, s.on_hand AS on_hand, s.reorder_point AS reorder_point,
               s.reorder_point - s.on_hand AS shortfall_units, s.on_hand < s.safety_stock AS below_safety_stock,
               options[0] AS fastest_resupply
        ORDER BY below_safety_stock DESC, shortfall_units DESC""")
    return _reply(rows, t)

@tool
def risk_event_exposure() -> str:
    """For each risk event (storms, port congestion, tariffs, strikes, supplier distress): the suppliers it affects
    (directly, through their country, and tier-2 suppliers through the tier-1 suppliers they feed),
    the parts and products exposed, and how many exposed parts are single-sourced."""
    if (m := _requires("RiskEvent", "Supplier")): return m
    rows, t = _read("""
        MATCH (e:RiskEvent)
        OPTIONAL MATCH (e)-[:AFFECTS]->(x)
        OPTIONAL MATCH (x)<-[:LOCATED_IN]-(local:Supplier)
        WITH e, x, collect(local) AS locals
        WITH e, collect(CASE WHEN x:Supplier THEN [x] ELSE locals END) AS lists
        WITH e, reduce(acc = [], l IN lists | acc + l) AS hit
        UNWIND CASE WHEN size(hit) = 0 THEN [null] ELSE hit END AS s
        OPTIONAL MATCH (s)-[:SUPPLIES_TO*0..1]->(:Supplier)-[:SUPPLIES]->(p:Part)
        OPTIONAL MATCH (p)<-[:SUPPLIES]-(src:Supplier)
        WITH e, s, p, count(DISTINCT src) AS sources
        OPTIONAL MATCH (prod:Product)-[:HAS_COMPONENT]->(p)
        RETURN e.id AS event_id, e.type AS event, e.severity AS severity, e.start_date AS start_date,
               count(DISTINCT s) AS suppliers_affected, count(DISTINCT p) AS parts_exposed,
               count(DISTINCT CASE WHEN sources = 1 THEN p END) AS single_sourced_parts_exposed,
               collect(DISTINCT prod.name) AS products_exposed
        ORDER BY severity DESC, parts_exposed DESC""")
    return _reply(rows, t)

@tool
def trace_product_supply(product: str) -> str:
    """Full upstream supply map for one product (id like PRD003 or part of its name): the plants that make it,
    each component part and quantity, every supplier of each part with country, lead time, and on-time rate,
    and the tier-2 suppliers behind them."""
    if (m := _requires("Product", "Part")): return m
    found, _ = _read("MATCH (p:Product) WHERE p.id = $q OR toLower(p.name) CONTAINS toLower($q) "
                     "RETURN p.id AS id, p.name AS name ORDER BY p.id", {"q": product}, limit=10)
    if not found:
        return json.dumps({"error": f"No product matches '{product}'."})
    if len(found) > 1:
        return json.dumps({"note": "Several products match; call again with one id.", "matches": found})
    pid = found[0]["id"]
    plants, _ = _read("MATCH (pl:Plant)-[r:PRODUCES]->(:Product {id: $id}) OPTIONAL MATCH (pl)-[:LOCATED_IN]->(c:Country) "
                      "RETURN pl.name AS plant, c.name AS country, r.capacity_units_per_week AS capacity_units_per_week", {"id": pid})
    bom, _ = _read("""
        MATCH (:Product {id: $id})-[hc:HAS_COMPONENT]->(p:Part)
        OPTIONAL MATCH (p)<-[s:SUPPLIES]-(sup:Supplier)
        OPTIONAL MATCH (sup)-[:LOCATED_IN]->(c:Country)
        OPTIONAL MATCH (t2:Supplier)-[st:SUPPLIES_TO]->(sup)
        OPTIONAL MATCH (t2)-[:LOCATED_IN]->(c2:Country)
        WITH p, hc, sup, s, c, [x IN collect(DISTINCT {supplier: t2.name, material: st.material, country: c2.name})
                                WHERE x.supplier IS NOT NULL] AS tier2
        WITH p, hc, collect(CASE WHEN sup IS NULL THEN null ELSE {supplier: sup.name, country: c.name,
             lead_time_days: s.lead_time_days, on_time_rate: s.on_time_rate, tier2_suppliers: tier2} END) AS suppliers
        RETURN p.id AS part_id, p.name AS part, hc.quantity AS quantity_per_unit, p.critical AS critical,
               size(suppliers) AS supplier_count, suppliers
        ORDER BY supplier_count, part""", {"id": pid}, limit=500)
    return json.dumps({"product": found[0], "made_at": plants, "components": bom}, indent=1, default=str)

if __name__ == "__main__":
    mcp.run()