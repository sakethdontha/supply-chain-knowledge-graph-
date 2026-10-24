import csv
from collections import defaultdict
from datetime import datetime

shipments = {}
with open("data/nodes/Shipment.csv", newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        shipments[row["id"]] = row

ship_to_carrier = {}
with open("data/rels/CARRIED_BY.csv", newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        ship_to_carrier[row["from_id"]] = row["to_id"]

carriers = {}
with open("data/nodes/Carrier.csv", newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        carriers[row["id"]] = row["name"]

# attach carrier + parse ship_date
recs = []
for sid, s in shipments.items():
    cid = ship_to_carrier.get(sid)
    if not cid:
        continue
    ship_date = datetime.strptime(s["ship_date"], "%Y-%m-%d")
    late = s["status"] != "Delivered On Time"
    days_late = float(s["days_late"]) if s["days_late"] else 0.0
    recs.append((cid, ship_date, late, days_late, s["status"]))

recs.sort(key=lambda r: r[1])

# Overall late rate per carrier
by_carrier = defaultdict(list)
for cid, d, late, dl, status in recs:
    by_carrier[cid].append((d, late, dl, status))

print("=== Overall on-time performance by carrier ===")
overall = []
for cid, items in by_carrier.items():
    n = len(items)
    n_late = sum(1 for _, late, _, _ in items if late)
    avg_days_late = sum(dl for _, _, dl, _ in items if dl > 0) / max(1, sum(1 for _,_,dl,_ in items if dl>0))
    late_rate = n_late / n * 100
    overall.append((cid, carriers.get(cid, cid), n, n_late, late_rate, avg_days_late))

overall.sort(key=lambda x: -x[4])
for cid, name, n, n_late, late_rate, avg_dl in overall:
    print(f"{cid:6} {name:22} shipments={n:4} late={n_late:4} late_rate={late_rate:5.1f}%  avg_days_late(when late)={avg_dl:4.2f}")

print()
print("=== Trend: late rate by half (first half of date range vs second half), per carrier ===")
all_dates = [d for _, d, _, _, _ in recs]
dmin, dmax = min(all_dates), max(all_dates)
mid = dmin + (dmax - dmin) / 2
print(f"date range: {dmin.date()} to {dmax.date()}, midpoint {mid.date()}")

for cid, items in by_carrier.items():
    first = [it for it in items if it[0] <= mid]
    second = [it for it in items if it[0] > mid]
    def rate(lst):
        if not lst:
            return None
        return sum(1 for _, late, _, _ in lst if late) / len(lst) * 100
    r1, r2 = rate(first), rate(second)
    name = carriers.get(cid, cid)
    r1s = f"{r1:5.1f}%" if r1 is not None else "  n/a"
    r2s = f"{r2:5.1f}%" if r2 is not None else "  n/a"
    delta = (r2 - r1) if (r1 is not None and r2 is not None) else None
    deltas = f"{delta:+5.1f}pp" if delta is not None else "   n/a"
    print(f"{cid:6} {name:22} n1={len(first):4} rate1={r1s}   n2={len(second):4} rate2={r2s}   delta={deltas}")

print()
print("=== Trend: late rate by quarter, per carrier ===")
def quarter(d):
    return f"{d.year}-Q{(d.month-1)//3 + 1}"

by_carrier_quarter = defaultdict(lambda: defaultdict(lambda: [0,0]))
for cid, d, late, dl, status in recs:
    q = quarter(d)
    by_carrier_quarter[cid][q][0] += 1
    if late:
        by_carrier_quarter[cid][q][1] += 1

quarters_sorted = sorted({quarter(d) for d in all_dates})
header = "carrier".ljust(22) + "".join(q.rjust(10) for q in quarters_sorted)
print(header)
for cid in by_carrier:
    name = carriers.get(cid, cid)
    row = name.ljust(22)
    for q in quarters_sorted:
        n, nlate = by_carrier_quarter[cid].get(q, [0,0])
        if n == 0:
            row += "n/a".rjust(10)
        else:
            row += f"{nlate/n*100:.0f}%({n})".rjust(10)
    print(row)
