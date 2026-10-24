"""Check the setup before using Claude: connects to Neo4j and runs every analysis tool once.
Usage: python smoke_test.py"""
import json
import server

checks = [("schema", server.get_graph_schema), ("single-source parts", server.find_single_source_parts),
          ("late shipments by carrier", lambda: server.late_shipment_analysis("carrier")),
          ("inventory below reorder point", server.inventory_below_reorder_point),
          ("risk event exposure", server.risk_event_exposure),
          ("trace product", lambda: server.trace_product_supply("PRD001")),
          ("supplier impact", lambda: server.supplier_disruption_impact("SUP001")),
          ("read-only guard", lambda: server.run_cypher_query("MATCH (n) DETACH DELETE n"))]
ok = True
for name, fn in checks:
    out = json.loads(fn())
    err = out.get("error")
    expected_error = name == "read-only guard"
    good = bool(err) == expected_error
    ok &= good
    size = out.get("row_count", len(out.get("node_labels", out.get("components", out.get("parts_affected", [])))))
    print(f"{'PASS' if good else 'FAIL'}  {name}: {err if err else f'{size} results'}")
print("\nAll good. Open Claude Desktop and ask a question." if ok else "\nFix the FAIL lines above first.")
