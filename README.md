# Supply Chain Knowledge Graph + AI Agent (Neo4j · MCP · Claude)

I built a knowledge graph of a multi-tier supply chain in Neo4j and connected it to Claude through a custom **Model Context Protocol (MCP)** server. The result is an AI agent that answers supply chain questions in plain English, such as *"If this supplier shut down tomorrow, how much revenue is at risk?"*, by querying the graph live and explaining the results. Nobody has to write a query.

```
User ──question──> Claude (agent) ──MCP / stdio──> server.py ──read-only Cypher──> Neo4j
          <────────── answer grounded in live query results ──────────
```

**Stack:** Python · Neo4j · Cypher · Model Context Protocol (FastMCP) · Claude · GraphRAG

---

## Why a graph

Most supply chain risk questions are about *paths*. "Which customers are affected if this tier-2 supplier fails?" means walking supplier → supplier → part → product → order → customer. In a relational database that takes five or six joins. In a graph it is one pattern match:

```cypher
MATCH (s:Supplier {id:$id})-[:SUPPLIES]->(p:Part)<-[:HAS_COMPONENT]-(prod:Product)
      <-[:CONTAINS]-(:SalesOrder)<-[:PLACED]-(c:Customer)
RETURN prod.name, count(DISTINCT c) AS customers_affected
```

I wanted to show that pairing a graph with an LLM agent turns these multi-hop questions into a conversation instead of a reporting project.

## What I built

### 1. The knowledge graph
I designed a schema of **12 node types and 16 relationship types** (19 relationship patterns) that runs from raw-material suppliers to end customers. It has about **1,800 nodes and 5,100 relationships**.

```
(Supplier T2)-[:SUPPLIES_TO]->(Supplier T1)-[:SUPPLIES]->(Part)<-[:HAS_COMPONENT]-(Product)
(Plant)-[:PRODUCES]->(Product)        (Plant)-[:SHIPS_TO]->(Warehouse)-[:STOCKS]->(Product)
(Customer)-[:PLACED]->(SalesOrder)-[:CONTAINS]->(Product)
(SalesOrder)-[:FULFILLED_BY]->(Shipment)-[:SHIPPED_FROM]->(Warehouse)    (Shipment)-[:CARRIED_BY]->(Carrier)
(Plant)-[:ISSUED]->(PurchaseOrder)-[:ORDERED_FROM]->(Supplier)           (PurchaseOrder)-[:FOR_PART]->(Part)
(Supplier|Plant|Warehouse)-[:LOCATED_IN]->(Country)<-[:AFFECTS]-(RiskEvent)-[:AFFECTS]->(Supplier)
```

One modeling decision mattered a lot: **facts that belong to a pairing are stored on the relationship**. A part's cost, lead time and on-time rate depend on *who* supplies it, so they live on `SUPPLIES`. Bill-of-materials quantities live on `HAS_COMPONENT`, stock levels on `STOCKS`, and plant capacity on `PRODUCES`. That keeps queries short and the model honest.

The dataset is **synthetic** (`generate_data.py`, seeded for reproducibility): 40 suppliers in 12 countries, 52 parts, 15 products, 4 plants, 6 warehouses, 6 carriers, 50 customers, 600 sales orders and shipments, 400 purchase orders, and 5 risk events. I deliberately seeded realistic problems so there is something real to find: single-sourced critical parts, an unreliable carrier, under-stocked warehouses, and risk events that reach suppliers indirectly through tier 2.

### 2. The MCP server (`server.py`)
I built the server on the Python MCP SDK (FastMCP). It gives the agent two tiers of tools:

| Tier | Tool | Purpose |
|---|---|---|
| Grounding | `get_graph_schema` | Labels, relationship patterns, property types and counts, so generated Cypher uses the real schema instead of guesses |
| Open-ended | `run_cypher_query` | Read-only Cypher the agent writes itself, for anything the templates don't cover |
| Analysis | `find_single_source_parts` | Single points of failure in the supply base |
| Analysis | `supplier_disruption_impact` | Parts, products and 90-day revenue at risk if a supplier (tier 1 or tier 2) fails |
| Analysis | `late_shipment_analysis` | Late rate and average days late by carrier, warehouse or region |
| Analysis | `inventory_below_reorder_point` | Stock shortfalls and the fastest plant to resupply |
| Analysis | `risk_event_exposure` | Suppliers, parts and products exposed to each risk event |
| Analysis | `trace_product_supply` | Full upstream map of one product, including tier-2 suppliers and countries |

The analysis tools are tested Cypher templates behind business-level names. The agent uses them for common questions, which makes those answers fast and reliable. The open-ended tool covers the long tail of everything else.

### 3. Guardrails
- **Read-only, enforced twice.** Every query runs in a Neo4j `READ_ACCESS` session, *and* a regex rejects write clauses (`CREATE`, `MERGE`, `SET`, `DELETE`, `LOAD CSV`, write-capable APOC calls, and so on) before anything reaches the database.
- **Parameterized queries** (`$params`) to prevent Cypher injection and allow plan caching.
- **Result cap of 200 rows plus a 30-second timeout.** These protect the LLM's context window and push the agent to aggregate in Cypher rather than pulling raw rows.
- **Readable error handling.** Authentication, connection and query errors come back as plain messages the agent can act on, not stack traces.
- **Schema checks.** If the graph doesn't have the labels an analysis tool needs, the tool says so and the agent falls back to writing Cypher itself.

## What the agent found

These are real answers from testing the agent on the graph. Each one came from a single natural-language question:

- **"If supplier SUP007 shut down tomorrow, how much revenue is at risk?"** About **$170K per quarter**, all from the Space Heater, because Insulation Foam B has no backup supplier. The agent then wrote its own follow-up query to show roughly 90 days of finished-goods inventory cover. That turned the answer from "we lose revenue" into "we have a 90-day window to qualify a second source."
- **"Which carrier has the worst on-time rate, and is it getting worse?"** SwiftHaul Logistics, at **73.5% on time** against 84–95% for every other carrier. Its performance is consistently poor rather than steadily declining. The agent also noticed that 16 of 17 undelivered shipments were past their promise date and flagged it as a likely **stale-data issue** rather than blaming the carriers.
- **"Tell me about the risk events."** The agent cross-referenced risk exposure with single-source parts and found **Wiring Harness B**. It is a critical part with one supplier in China, and that supplier depends on a tier-2 supplier in Taiwan. So one part is exposed to **three risk events at once** (tariff, port congestion and typhoon) through a dependency that no single-event view shows.

The third finding is the main point of the project. It needed several queries joined by reasoning, which is where an agent on top of a graph beats a fixed dashboard.

## How it's framed technically

This is **GraphRAG using structured retrieval**: answers are grounded in data retrieved live from the knowledge graph, not in the model's memory. It is not vector search. For relationship-heavy data like a supply network, exact multi-hop traversal is more accurate than similarity search over embeddings. The schema tool grounds text-to-Cypher generation, and the analysis tools act as reliable retrieval templates.

## Run it yourself

**Requirements:** Python 3.10+, a Neo4j database (AuraDB Free or Neo4j Desktop), and the Claude Desktop app.

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # then add your Neo4j URI, username and password

python generate_data.py       # builds the synthetic dataset in data/
python load_graph.py          # loads it into Neo4j (~1,790 nodes, ~5,085 relationships)
python smoke_test.py          # checks the connection and every tool (should print PASS on every line)
```

**Connect Claude:** in Claude Desktop, open Settings → Developer → Edit Config. Paste the block from `claude_desktop_config.example.json` with your real paths to `.venv\Scripts\python.exe` and `server.py`, then fully restart Claude Desktop. To check it worked, ask *"What does my supply chain graph contain?"*

**Bring your own data:** `load_graph.py` loads any folder of CSVs in this format:
- `nodes/<Label>.csv`: an `id` column plus property columns
- `rels/<TYPE>.csv`: `from_label, from_id, to_label, to_id` plus property columns

Numbers, booleans and `*_date` columns are typed automatically. Run it with `python load_graph.py --data my_data --reset`.

## Project structure

| File | Purpose |
|---|---|
| `server.py` | MCP server: 8 tools, read-only enforcement, error handling, schema caching |
| `load_graph.py` | Generic CSV → Neo4j loader with unique-ID constraints and batched writes |
| `generate_data.py` | Seeded generator for the synthetic supply chain dataset |
| `carrier_analysis.py` | Standalone script that recomputes carrier on-time rates and quarterly trends straight from the CSVs, to cross-check the agent's answers |
| `smoke_test.py` | End-to-end check of the database connection and every tool |
| `config.py` | Loads Neo4j settings from `.env` |
| `data/` | Generated node and relationship CSVs |

## What I'd do next

- Add a **Neo4j read-only database user**, so write protection doesn't depend on a regex check.
- Add an **evaluation set** of questions with known answers to measure how accurate the agent's text-to-Cypher is.
- Add **what-if simulation tools**, such as rerouting volume to a backup supplier and recalculating lead times and cost.
- Load a **real public dataset** (for example, a supplier-network dataset) to test the schema beyond synthetic data.

## Troubleshooting

- **The tools don't appear in Claude:** the paths in the config are wrong, or Claude Desktop wasn't fully restarted. Check the server's status under Settings → Developer.
- **"Cannot reach Neo4j":** the database is paused (AuraDB Free pauses when idle) or `NEO4J_URI` is wrong.
- **"Neo4j rejected the username or password":** fix the credentials in `.env`.

---

*Built by [Saketh Dontha](https://www.linkedin.com/in/saketh-dontha-2987a6190/) · [Portfolio](https://sakethdontha.github.io) · [GitHub](https://github.com/sakethdontha)*
