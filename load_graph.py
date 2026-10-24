"""Load supply chain CSVs into Neo4j.

Works with the generated sample data or ANY supply chain data in the same simple format:
  <folder>/nodes/<Label>.csv  -> "id" column + property columns
  <folder>/rels/<TYPE>.csv    -> from_label, from_id, to_label, to_id + property columns
Numbers become numbers, true/false become booleans, columns ending in "_date" become Neo4j dates,
empty cells are left out.

Usage:
  python load_graph.py                 # load ./data
  python load_graph.py --data my_data  # load your own folder
  python load_graph.py --reset         # delete everything in the database first
Connection settings come from the .env file (NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE).
"""
import argparse, csv, datetime as dt, glob, os, re
from neo4j import GraphDatabase
from config import settings

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
BATCH = 1000

def safe(name):
    if not NAME.fullmatch(name):
        raise SystemExit(f"'{name}' is not a valid label, type, or property name (letters, digits, underscores only)")
    return name

def convert(col, val):
    v = val.strip()
    if v == "":
        return None
    if col.endswith("_date"):
        return dt.date.fromisoformat(v[:10])
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    if re.fullmatch(r"-?\d+", v) and not (len(v) > 1 and v.startswith("0")):
        return int(v)
    if re.fullmatch(r"-?\d*\.\d+", v):
        return float(v)
    return v

def rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            yield {k.strip(): convert(k.strip(), v or "") for k, v in r.items() if k}

def batches(items):
    buf = []
    for it in items:
        buf.append(it)
        if len(buf) == BATCH:
            yield buf; buf = []
    if buf:
        yield buf

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
    ap.add_argument("--reset", action="store_true", help="delete all nodes and relationships first")
    a = ap.parse_args()
    s = settings()
    driver = GraphDatabase.driver(s["uri"], auth=(s["user"], s["password"]))
    driver.verify_connectivity()
    db = s["database"]

    if a.reset:
        while driver.execute_query("MATCH (n) WITH n LIMIT 5000 DETACH DELETE n RETURN count(*) AS c",
                                   database_=db).records[0]["c"]:
            pass
        print("database cleared")

    node_files = sorted(glob.glob(os.path.join(a.data, "nodes", "*.csv")))
    rel_files = sorted(glob.glob(os.path.join(a.data, "rels", "*.csv")))
    if not node_files:
        raise SystemExit(f"no node files found in {a.data}/nodes")

    for path in node_files:
        label = safe(os.path.splitext(os.path.basename(path))[0])
        driver.execute_query(f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE", database_=db)
        count = 0
        for b in batches(rows(path)):
            data = []
            for r in b:
                if r.get("id") is None:
                    raise SystemExit(f"{path}: every row needs an id")
                for k in r: safe(k)
                data.append({"id": r["id"], "props": {k: v for k, v in r.items() if k != "id" and v is not None}})
            driver.execute_query(f"UNWIND $rows AS row MERGE (n:{label} {{id: row.id}}) SET n += row.props", rows=data, database_=db)
            count += len(data)
        print(f"  {label}: {count} nodes")

    for path in rel_files:
        rtype = safe(os.path.splitext(os.path.basename(path))[0])
        groups = {}
        for r in rows(path):
            key = (safe(r.pop("from_label")), safe(r.pop("to_label")))
            for k in r: safe(k)
            groups.setdefault(key, []).append({"from": r.pop("from_id"), "to": r.pop("to_id"),
                                               "props": {k: v for k, v in r.items() if v is not None}})
        total = skipped = 0
        for (fl, tl), items in groups.items():
            for b in batches(items):
                linked = driver.execute_query(
                    f"UNWIND $rows AS row MATCH (a:{fl} {{id: row.from}}) MATCH (b:{tl} {{id: row.to}}) "
                    f"MERGE (a)-[r:{rtype}]->(b) SET r += row.props RETURN count(r) AS c", rows=b, database_=db).records[0]["c"]
                total += linked; skipped += len(b) - linked
        print(f"  {rtype}: {total} relationships" + (f" ({skipped} skipped: from_id or to_id not found)" if skipped else ""))

    rec = driver.execute_query("MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels", database_=db).records[0]
    print(f"done: {rec['nodes']} nodes, {rec['rels']} relationships in the database")
    driver.close()

if __name__ == "__main__":
    main()
