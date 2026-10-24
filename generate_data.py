"""Generate a realistic, clearly synthetic multi-tier supply chain dataset as CSV files.

Output format (the same format load_graph.py accepts for ANY supply chain data):
  data/nodes/<Label>.csv  -> first column "id", other columns are properties
  data/rels/<TYPE>.csv    -> columns from_label, from_id, to_label, to_id, then properties
Columns ending in "_date" are loaded as Neo4j dates; numbers are loaded as numbers.

Usage: python generate_data.py            (writes ./data, seed 42)
"""
import csv, os, random, datetime as dt

random.seed(42)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TODAY = dt.date(2026, 10, 1)

nodes, rels = {}, {}
def node(label, **props): nodes.setdefault(label, []).append(props); return props
def rel(rtype, fl, fid, tl, tid, **props):
    rels.setdefault(rtype, []).append({"from_label": fl, "from_id": fid, "to_label": tl, "to_id": tid, **props})
def d(x): return x.isoformat() if x else ""

# ---------------- locations ----------------
COUNTRIES = [("US", "United States", "North America", 2), ("MX", "Mexico", "North America", 4),
             ("CA", "Canada", "North America", 1), ("CN", "China", "Asia", 6), ("TW", "Taiwan", "Asia", 7),
             ("VN", "Vietnam", "Asia", 5), ("IN", "India", "Asia", 4), ("MY", "Malaysia", "Asia", 4),
             ("DE", "Germany", "Europe", 2), ("PL", "Poland", "Europe", 3), ("BR", "Brazil", "South America", 5),
             ("KR", "South Korea", "Asia", 3)]
for code, name, region, risk in COUNTRIES:
    node("Country", id=code, name=name, region=region, risk_score=risk)

# ---------------- suppliers (tier 1 and tier 2) ----------------
SUP_WORDS = ["Apex", "Summit", "Delta", "Orion", "Pioneer", "Vertex", "Atlas", "Nova", "Harbor", "Crest",
             "Granite", "Beacon", "Keystone", "Meridian", "Sterling", "Cobalt", "Falcon", "Liberty", "Pacific", "Union"]
SUP_KIND = ["Components", "Materials", "Industries", "Manufacturing", "Electronics", "Plastics", "Metals", "Packaging"]
supplier_country_weights = {"CN": 9, "TW": 4, "VN": 4, "MX": 5, "US": 6, "IN": 4, "MY": 3, "DE": 3, "PL": 2, "KR": 3, "BR": 1, "CA": 1}
tier1, tier2 = [], []
for i in range(1, 41):
    tier = 1 if i <= 28 else 2
    c = random.choices(list(supplier_country_weights), weights=supplier_country_weights.values())[0]
    s = node("Supplier", id=f"SUP{i:03d}", name=f"{random.choice(SUP_WORDS)} {random.choice(SUP_KIND)} {i}",
             tier=tier, reliability_score=round(random.uniform(0.72, 0.99), 2), annual_spend_usd=random.randint(150, 4800) * 1000)
    rel("LOCATED_IN", "Supplier", s["id"], "Country", c)
    (tier1 if tier == 1 else tier2).append(s)

# ---------------- parts ----------------
PART_TYPES = [("Electronics", ["Microcontroller", "Power Board", "Sensor Module", "Display Panel", "Wiring Harness", "Battery Pack", "Motor Driver"]),
              ("Mechanical", ["Steel Bracket", "Aluminum Housing", "Drive Shaft", "Bearing Set", "Gear Assembly", "Fastener Kit", "Hinge"]),
              ("Plastics", ["Injection Molded Cover", "Control Knob", "Seal Ring", "Base Tray"]),
              ("Packaging", ["Corrugated Box", "Foam Insert", "Pallet Wrap", "Label Set"]),
              ("Raw Material", ["Copper Coil", "Rubber Gasket", "Glass Panel", "Insulation Foam"])]
parts = []
n = 0
for cat, names in PART_TYPES:
    for nm in names:
        for variant in ("A", "B"):
            n += 1
            parts.append(node("Part", id=f"PRT{n:03d}", name=f"{nm} {variant}", category=cat,
                              unit_cost_usd=round(random.uniform(0.4, 85), 2), critical=cat in ("Electronics", "Raw Material")))

# who supplies each part: about 30% single-sourced (a classic risk), others 2-3 suppliers
for p in parts:
    k = 1 if random.random() < 0.3 else random.choice([2, 2, 3])
    for s in random.sample(tier1, k):
        rel("SUPPLIES", "Supplier", s["id"], "Part", p["id"], unit_cost_usd=round(p["unit_cost_usd"] * random.uniform(0.85, 1.2), 2),
            lead_time_days=random.randint(7, 75), on_time_rate=round(min(0.99, s["reliability_score"] + random.uniform(-0.1, 0.03)), 2))

# tier 2 suppliers feed tier 1 suppliers (multi-tier visibility)
for s2 in tier2:
    for s1 in random.sample(tier1, random.randint(1, 4)):
        rel("SUPPLIES_TO", "Supplier", s2["id"], "Supplier", s1["id"], material=random.choice(["Resin", "Copper", "Steel Coil", "Silicon Wafers", "Lithium Cells", "Paper Pulp", "Rare Earth Magnets"]))

# ---------------- products and bill of materials ----------------
PRODUCTS = [("Smart Thermostat", "Home Electronics", 149), ("Air Purifier", "Home Appliances", 229), ("Robot Vacuum", "Home Appliances", 399),
            ("Electric Kettle", "Kitchen", 59), ("Countertop Blender", "Kitchen", 119), ("Space Heater", "Home Appliances", 89),
            ("Security Camera", "Home Electronics", 129), ("Video Doorbell", "Home Electronics", 179), ("Dehumidifier", "Home Appliances", 249),
            ("Stand Mixer", "Kitchen", 329), ("Ceiling Fan", "Home Appliances", 199), ("Smart Plug 4-Pack", "Home Electronics", 39),
            ("Water Filter Pitcher", "Kitchen", 34), ("Cordless Drill", "Tools", 139), ("Pressure Washer", "Tools", 289)]
products = []
for i, (nm, cat, price) in enumerate(PRODUCTS, 1):
    p = node("Product", id=f"PRD{i:03d}", name=nm, category=cat, unit_price_usd=price)
    products.append(p)
    for part in random.sample(parts, random.randint(5, 9)):
        rel("HAS_COMPONENT", "Product", p["id"], "Part", part["id"], quantity=random.choice([1, 1, 1, 2, 2, 4]))

# ---------------- plants and warehouses ----------------
PLANTS = [("PLT01", "Memphis Assembly", "US"), ("PLT02", "Monterrey Assembly", "MX"), ("PLT03", "Penang Assembly", "MY"), ("PLT04", "Wroclaw Assembly", "PL")]
for pid, nm, c in PLANTS:
    node("Plant", id=pid, name=nm)
    rel("LOCATED_IN", "Plant", pid, "Country", c)
for p in products:
    for pid, _, _ in random.sample(PLANTS, random.choice([1, 1, 2])):
        rel("PRODUCES", "Plant", pid, "Product", p["id"], capacity_units_per_week=random.randint(300, 4000))

WAREHOUSES = [("DC01", "Memphis DC", "Memphis", "TN"), ("DC02", "Dallas DC", "Dallas", "TX"), ("DC03", "Columbus DC", "Columbus", "OH"),
              ("DC04", "Reno DC", "Reno", "NV"), ("DC05", "Atlanta DC", "Atlanta", "GA"), ("DC06", "Allentown DC", "Allentown", "PA")]
for wid, nm, city, st in WAREHOUSES:
    node("Warehouse", id=wid, name=nm, city=city, state=st)
    rel("LOCATED_IN", "Warehouse", wid, "Country", "US")
transit_base = {"PLT01": 2, "PLT02": 4, "PLT03": 28, "PLT04": 24}
for pid, _, _ in PLANTS:
    for wid, *_ in WAREHOUSES:
        rel("SHIPS_TO", "Plant", pid, "Warehouse", wid, transit_days=transit_base[pid] + random.randint(0, 3),
            cost_per_unit_usd=round(random.uniform(0.6, 4.5) * (3 if transit_base[pid] > 20 else 1), 2))

for wid, *_ in WAREHOUSES:
    for p in products:
        rop = random.randint(80, 400)
        low = random.random() < 0.15
        rel("STOCKS", "Warehouse", wid, "Product", p["id"], on_hand=random.randint(0, rop - 1) if low else random.randint(rop, rop * 4),
            reorder_point=rop, safety_stock=int(rop * 0.4))

# ---------------- carriers, customers, orders, shipments ----------------
CARRIERS = [("CAR01", "Interstate Freight", "Truckload", 0.06), ("CAR02", "RapidParcel", "Parcel", 0.08), ("CAR03", "BlueLine LTL", "LTL", 0.12),
            ("CAR04", "SwiftHaul Logistics", "Truckload", 0.27), ("CAR05", "Gulf Intermodal", "Intermodal", 0.15), ("CAR06", "MetroExpress", "Parcel", 0.05)]
for cid, nm, mode, _ in CARRIERS:
    node("Carrier", id=cid, name=nm, mode=mode)
late_prob = {cid: lp for cid, _, _, lp in CARRIERS}

SEGMENTS = ["Big Box Retail", "E-Commerce", "Home Improvement", "Regional Distributor", "Specialty Retail"]
REGIONS = ["Southeast", "Midwest", "Northeast", "Southwest", "West"]
dc_for_region = {"Southeast": ["DC01", "DC05"], "Midwest": ["DC01", "DC03"], "Northeast": ["DC06", "DC03"], "Southwest": ["DC02", "DC04"], "West": ["DC04"]}
customers = []
for i in range(1, 51):
    c = node("Customer", id=f"CUS{i:03d}", name=f"Customer {i:03d}", segment=random.choice(SEGMENTS), region=random.choice(REGIONS))
    customers.append(c)

for i in range(1, 601):
    cust = random.choice(customers)
    od = TODAY - dt.timedelta(days=random.randint(0, 180))
    so = node("SalesOrder", id=f"SO{i:05d}", order_date=d(od), channel=random.choice(["EDI", "Web", "Sales Rep"]))
    rel("PLACED", "Customer", cust["id"], "SalesOrder", so["id"])
    total = 0
    for p in random.sample(products, random.choice([1, 1, 2, 3])):
        q = random.randint(5, 120)
        total += q * p["unit_price_usd"]
        rel("CONTAINS", "SalesOrder", so["id"], "Product", p["id"], quantity=q)
    so["order_value_usd"] = round(total, 2)
    wid = random.choice(dc_for_region[cust["region"]])
    cid = random.choice(CARRIERS)[0]
    ship = od + dt.timedelta(days=random.randint(1, 3))
    promised = ship + dt.timedelta(days=random.randint(2, 6))
    if ship > TODAY:
        status, delivered = "Pending", None
    else:
        late = random.random() < late_prob[cid] + (0.08 if wid == "DC02" else 0)
        delivered = promised + dt.timedelta(days=random.randint(1, 6)) if late else promised - dt.timedelta(days=random.randint(0, 1))
        status = "In Transit" if delivered > TODAY else ("Delivered Late" if late else "Delivered On Time")
        if delivered > TODAY: delivered = None
    sh = node("Shipment", id=f"SH{i:05d}", ship_date=d(ship), promised_date=d(promised), delivered_date=d(delivered), status=status,
              freight_cost_usd=round(random.uniform(80, 1400), 2),
              days_late=max(0, (delivered - promised).days) if delivered else 0)
    rel("FULFILLED_BY", "SalesOrder", so["id"], "Shipment", sh["id"])
    rel("SHIPPED_FROM", "Shipment", sh["id"], "Warehouse", wid)
    rel("CARRIED_BY", "Shipment", sh["id"], "Carrier", cid)

# ---------------- purchase orders ----------------
supplies = rels["SUPPLIES"]
for i in range(1, 401):
    s = random.choice(supplies)
    od = TODAY - dt.timedelta(days=random.randint(0, 200))
    promised = od + dt.timedelta(days=s["lead_time_days"])
    on_time = random.random() < s["on_time_rate"]
    received = promised - dt.timedelta(days=random.randint(0, 2)) if on_time else promised + dt.timedelta(days=random.randint(2, 20))
    status = "Open" if received > TODAY else ("Received Late" if not on_time else "Received On Time")
    po = node("PurchaseOrder", id=f"PO{i:05d}", order_date=d(od), promised_date=d(promised), received_date=d(received if received <= TODAY else None),
              status=status, quantity=random.randint(200, 20000))
    po["value_usd"] = round(po["quantity"] * s["unit_cost_usd"], 2)
    rel("ISSUED", "Plant", random.choice(PLANTS)[0], "PurchaseOrder", po["id"])
    rel("ORDERED_FROM", "PurchaseOrder", po["id"], "Supplier", s["from_id"])
    rel("FOR_PART", "PurchaseOrder", po["id"], "Part", s["to_id"])

# ---------------- risk events ----------------
EVENTS = [("RSK01", "Typhoon", "Port closures and factory shutdowns", 4, dt.date(2026, 9, 18), ["TW"]),
          ("RSK02", "Port Congestion", "Vessel backlog at major export ports", 3, dt.date(2026, 9, 2), ["CN", "VN"]),
          ("RSK03", "Tariff Increase", "New import tariff on electronic components", 3, dt.date(2026, 8, 15), ["CN"]),
          ("RSK04", "Labor Strike", "Rail and trucking strike", 2, dt.date(2026, 9, 25), ["DE"]),
          ("RSK05", "Supplier Bankruptcy Watch", "Credit downgrade of a key supplier", 4, dt.date(2026, 9, 28), [])]
for eid, typ, desc, sev, date, countries in EVENTS:
    node("RiskEvent", id=eid, type=typ, description=desc, severity=sev, start_date=d(date))
    for c in countries:
        rel("AFFECTS", "RiskEvent", eid, "Country", c)
weak = min(tier1, key=lambda s: s["reliability_score"])
rel("AFFECTS", "RiskEvent", "RSK05", "Supplier", weak["id"])

# ---------------- write ----------------
for sub in ("nodes", "rels"):
    os.makedirs(os.path.join(OUT, sub), exist_ok=True)
for label, rows in nodes.items():
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(os.path.join(OUT, "nodes", f"{label}.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, keys); w.writeheader(); w.writerows(rows)
for rtype, rows in rels.items():
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(os.path.join(OUT, "rels", f"{rtype}.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, keys); w.writeheader(); w.writerows(rows)
print(f"nodes: {sum(len(v) for v in nodes.values())} in {len(nodes)} labels; "
      f"relationships: {sum(len(v) for v in rels.values())} in {len(rels)} types -> {OUT}")
