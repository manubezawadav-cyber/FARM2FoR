
from flask import Flask, jsonify, request, render_template_string
import sqlite3
from pathlib import Path
from datetime import datetime

app = Flask(__name__)
DB = Path(__file__).with_name("farm2for.db")


def connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def rows(sql, args=()):
    with connect() as db:
        return [dict(r) for r in db.execute(sql, args).fetchall()]


def one(sql, args=()):
    result = rows(sql, args)
    return result[0] if result else None


def execute(sql, args=()):
    with connect() as db:
        cur = db.execute(sql, args)
        return cur.lastrowid


def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS crops(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          farmer TEXT NOT NULL, village TEXT NOT NULL,
          name TEXT NOT NULL, qty REAL NOT NULL CHECK(qty >= 0),
          price REAL NOT NULL CHECK(price >= 0),
          harvest_date TEXT, expiry_date TEXT,
          status TEXT DEFAULT 'Available'
        );
        CREATE TABLE IF NOT EXISTS orders(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          crop_id INTEGER NOT NULL, buyer TEXT NOT NULL,
          qty REAL NOT NULL, total REAL NOT NULL,
          status TEXT DEFAULT 'Placed', created_at TEXT,
          FOREIGN KEY(crop_id) REFERENCES crops(id)
        );
        CREATE TABLE IF NOT EXISTS storage(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL, district TEXT NOT NULL,
          capacity REAL NOT NULL, used REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS branches(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL, district TEXT NOT NULL,
          type TEXT NOT NULL, manager TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS fund(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          entry_type TEXT NOT NULL,
          amount REAL NOT NULL CHECK(amount > 0),
          note TEXT NOT NULL, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS trace_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          crop_id INTEGER NOT NULL, event TEXT NOT NULL,
          location TEXT NOT NULL, note TEXT NOT NULL,
          created_at TEXT,
          FOREIGN KEY(crop_id) REFERENCES crops(id)
        );
        """)

        if db.execute("SELECT COUNT(*) FROM crops").fetchone()[0] == 0:
            db.executemany("""
              INSERT INTO crops
              (farmer,village,name,qty,price,harvest_date,expiry_date)
              VALUES(?,?,?,?,?,?,?)
            """, [
              ("Ramu", "Tagarapuvalasa", "Tomatoes", 100, 24,
               "2026-10-08", "2026-10-15"),
              ("Lakshmi", "Vizianagaram", "Rice", 500, 42,
               "2026-10-01", "2027-04-01"),
              ("Suresh", "Tadepalligudem", "Chilli", 80, 95,
               "2026-10-06", "2027-01-06"),
            ])
            db.execute("""
              INSERT INTO trace_events(crop_id,event,location,note,created_at)
              VALUES(1,'Crop listed','Tagarapuvalasa',
                     'Farmer listed tomatoes on FARM2FOR',?)
            """, (datetime.now().isoformat(timespec="minutes"),))

        if db.execute("SELECT COUNT(*) FROM storage").fetchone()[0] == 0:
            db.executemany("""
              INSERT INTO storage(name,district,capacity,used)
              VALUES(?,?,?,?)
            """, [
              ("Green Valley Cold Storage", "Vizianagaram", 1000, 250),
              ("Village Fresh Hub", "West Godavari", 500, 100),
            ])

        if db.execute("SELECT COUNT(*) FROM branches").fetchone()[0] == 0:
            db.executemany("""
              INSERT INTO branches(name,district,type,manager)
              VALUES(?,?,?,?)
            """, [
              ("Vizianagaram Main Branch", "Vizianagaram",
               "District Main Branch", "Branch Manager"),
              ("Tagarapuvalasa Sub-Branch", "Vizianagaram",
               "Village Sub-Branch", "Village Coordinator"),
              ("Tadepalligudem Main Branch", "West Godavari",
               "District Main Branch", "Branch Manager"),
            ])


@app.get("/")
def home():
    return render_template_string(HTML)


@app.get("/health")
def health():
    return jsonify(status="ok", app="FARM2FOR")


@app.get("/api/summary")
def summary():
    return jsonify({
      "farmers": one("SELECT COUNT(DISTINCT farmer) n FROM crops")["n"],
      "crops": one("SELECT COUNT(*) n FROM crops")["n"],
      "available_kg": one("SELECT COALESCE(SUM(qty),0) n FROM crops")["n"],
      "orders": one("SELECT COUNT(*) n FROM orders")["n"],
      "sales_value": one("SELECT COALESCE(SUM(total),0) n FROM orders")["n"],
      "storage_capacity": one("SELECT COALESCE(SUM(capacity),0) n FROM storage")["n"],
      "storage_used": one("SELECT COALESCE(SUM(used),0) n FROM storage")["n"],
      "fund_balance": (
        one("""SELECT COALESCE(SUM(CASE
          WHEN entry_type='Contribution' THEN amount
          WHEN entry_type='Allocation' THEN -amount ELSE 0 END),0) n
          FROM fund""")["n"]
      )
    })


@app.route("/api/crops", methods=["GET", "POST"])
def crops():
    if request.method == "GET":
        return jsonify(rows("SELECT * FROM crops ORDER BY id DESC"))
    d = request.get_json(silent=True) or {}
    required = ["farmer", "village", "name", "qty", "price"]
    if any(not str(d.get(k, "")).strip() for k in required):
        return jsonify(error="Please fill in all required crop fields."), 400
    try:
        qty, price = float(d["qty"]), float(d["price"])
        if qty <= 0 or price < 0:
            raise ValueError()
    except (TypeError, ValueError):
        return jsonify(error="Quantity must be positive and price cannot be negative."), 400

    cid = execute("""INSERT INTO crops
      (farmer,village,name,qty,price,harvest_date,expiry_date)
      VALUES(?,?,?,?,?,?,?)""",
      (d["farmer"].strip(), d["village"].strip(), d["name"].strip(),
       qty, price, d.get("harvest_date") or None,
       d.get("expiry_date") or None))
    execute("""INSERT INTO trace_events
      (crop_id,event,location,note,created_at)
      VALUES(?,?,?,?,?)""",
      (cid, "Crop listed", d["village"].strip(),
       "Crop added to FARM2FOR marketplace", datetime.now().isoformat(timespec="minutes")))
    return jsonify(id=cid, message="Crop listed successfully"), 201


@app.route("/api/orders", methods=["GET", "POST"])
def orders():
    if request.method == "GET":
        return jsonify(rows("""SELECT o.*,c.name crop_name,c.farmer,c.village
          FROM orders o JOIN crops c ON c.id=o.crop_id ORDER BY o.id DESC"""))
    d = request.get_json(silent=True) or {}
    try:
        cid, qty = int(d["crop_id"]), float(d["qty"])
        buyer = str(d["buyer"]).strip()
        if not buyer or qty <= 0:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        return jsonify(error="Enter a buyer, crop and positive quantity."), 400

    with connect() as db:
        crop = db.execute("SELECT * FROM crops WHERE id=?", (cid,)).fetchone()
        if not crop:
            return jsonify(error="Crop not found."), 404
        if crop["qty"] < qty:
            return jsonify(error="Not enough crop stock available."), 400

        total = round(qty * crop["price"], 2)
        now = datetime.now().isoformat(timespec="minutes")
        cur = db.execute("""INSERT INTO orders
          (crop_id,buyer,qty,total,status,created_at)
          VALUES(?,?,?,?,?,?)""",
          (cid, buyer, qty, total, "Placed", now))
        db.execute("UPDATE crops SET qty=qty-? WHERE id=?", (qty, cid))
        db.execute("""INSERT INTO trace_events
          (crop_id,event,location,note,created_at) VALUES(?,?,?,?,?)""",
          (cid, "Order placed", crop["village"],
           f"Order #{cur.lastrowid}: {qty:g} kg booked by {buyer}", now))
        db.execute("""UPDATE crops SET status=
          CASE WHEN qty<=0 THEN 'Out of stock' ELSE 'Available' END
          WHERE id=?""", (cid,))
        oid = cur.lastrowid
    return jsonify(id=oid, total=total, message="Order placed"), 201


@app.post("/api/orders/<int:oid>/status")
def update_order(oid):
    d = request.get_json(silent=True) or {}
    allowed = ["Placed", "Confirmed", "Collected", "In Storage",
               "Out for Delivery", "Delivered", "Cancelled"]
    status = d.get("status")
    if status not in allowed:
        return jsonify(error="Invalid order status."), 400
    if not one("SELECT id FROM orders WHERE id=?", (oid,)):
        return jsonify(error="Order not found."), 404
    execute("UPDATE orders SET status=? WHERE id=?", (status, oid))
    return jsonify(message="Order status updated")


@app.get("/api/storage")
def get_storage():
    return jsonify(rows("SELECT * FROM storage ORDER BY id"))


@app.post("/api/storage/<int:sid>/receive")
def receive_storage(sid):
    d = request.get_json(silent=True) or {}
    try:
        qty = float(d["qty"])
        if qty <= 0:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        return jsonify(error="Enter a positive quantity."), 400
    with connect() as db:
        s = db.execute("SELECT * FROM storage WHERE id=?", (sid,)).fetchone()
        if not s:
            return jsonify(error="Storage facility not found."), 404
        if s["used"] + qty > s["capacity"]:
            return jsonify(error="Insufficient free storage capacity."), 400
        db.execute("UPDATE storage SET used=used+? WHERE id=?", (qty, sid))
    return jsonify(message="Storage stock updated")


@app.get("/api/branches")
def get_branches():
    return jsonify(rows("SELECT * FROM branches ORDER BY district,name"))


@app.route("/api/fund", methods=["GET", "POST"])
def fund():
    if request.method == "GET":
        return jsonify(rows("SELECT * FROM fund ORDER BY id DESC"))
    d = request.get_json(silent=True) or {}
    typ = d.get("entry_type")
    note = str(d.get("note", "")).strip()
    try:
        amount = float(d["amount"])
        if typ not in ["Contribution", "Allocation"] or amount <= 0 or not note:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        return jsonify(error="Enter a valid type, positive amount and note."), 400

    balance = one("""SELECT COALESCE(SUM(CASE
      WHEN entry_type='Contribution' THEN amount
      WHEN entry_type='Allocation' THEN -amount ELSE 0 END),0) n
      FROM fund""")["n"]
    if typ == "Allocation" and amount > balance:
        return jsonify(error="Allocation exceeds the current fund balance."), 400
    execute("""INSERT INTO fund(entry_type,amount,note,created_at)
      VALUES(?,?,?,?)""",
      (typ, amount, note, datetime.now().isoformat(timespec="minutes")))
    return jsonify(message="Fund entry recorded"), 201


@app.get("/api/forecast")
def forecast():
    # Baseline supply indicators, not a trained AI prediction.
    return jsonify(rows("""SELECT name,
      ROUND(SUM(qty),1) available_kg,
      ROUND(AVG(price),2) average_price,
      COUNT(*) listings
      FROM crops GROUP BY name ORDER BY available_kg DESC"""))


@app.get("/api/trace/<int:cid>")
def trace(cid):
    crop = one("SELECT * FROM crops WHERE id=?", (cid,))
    if not crop:
        return jsonify(error="Crop ID not found."), 404
    events = rows("""SELECT * FROM trace_events
      WHERE crop_id=? ORDER BY id DESC""", (cid,))
    return jsonify(crop=crop, events=events)


HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FARM2FOR | Agriculture Network</title>
<style>
:root{--green:#176b42;--dark:#103b2b;--lime:#d9efb0;--bg:#f4f7f2;
--white:#fff;--muted:#64746a;--line:#e0e9df}
*{box-sizing:border-box}body{margin:0;font:15px Arial,sans-serif;background:var(--bg);color:#20352a}
header{background:var(--dark);color:white;padding:20px 5%;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.brand{font-size:24px;font-weight:800;letter-spacing:1px}.brand span{color:var(--lime)}
header p{margin:6px 0 0;color:#d8e9db;font-size:13px}
main{max-width:1250px;margin:25px auto;padding:0 18px}
nav{display:flex;gap:8px;overflow:auto;padding:4px 0 15px}
nav button{border:1px solid var(--line);background:white;color:var(--dark);border-radius:10px;padding:11px 14px;white-space:nowrap;cursor:pointer}
nav button.active,button.primary{background:var(--green);color:white;border-color:var(--green)}
h1{font-size:27px;margin:12px 0 6px}h2{font-size:19px;margin:0 0 14px}
.sub{color:var(--muted);margin:0 0 20px}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}
.card,.panel{background:white;border:1px solid var(--line);border-radius:15px;padding:18px;margin-bottom:16px}
.metric{font-size:26px;font-weight:800;color:var(--green);margin:9px 0}
.metric-label{font-size:13px;color:var(--muted)}
.two{display:grid;grid-template-columns:1.1fr .9fr;gap:16px}
button{cursor:pointer;border:1px solid var(--line);border-radius:9px;background:white;padding:10px 12px;color:var(--dark)}
input,select{padding:11px;border:1px solid #d4dfd3;border-radius:9px;width:100%;background:white;min-width:0}
label{font-size:12px;color:var(--muted);display:block;margin-bottom:5px}
form .fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
form .wide{grid-column:1/-1}
form button{margin-top:12px}
.tablewrap{overflow:auto}table{border-collapse:collapse;width:100%;min-width:560px}
th,td{text-align:left;padding:11px;border-bottom:1px solid var(--line);font-size:13px}
th{color:var(--muted);font-weight:600;background:#f8faf7}
.pill{background:#edf5e8;color:#23633c;border-radius:20px;padding:5px 8px;font-size:12px;white-space:nowrap}
.notice{padding:12px;border-radius:10px;background:#edf5e8;color:#245a38;margin:12px 0}
.error{background:#fff0ed;color:#8d3021}
.small{font-size:12px;color:var(--muted);line-height:1.6}
.hidden{display:none!important}
.progress{height:9px;background:#e7eee4;border-radius:8px;overflow:hidden;margin:10px 0}
.progress span{height:100%;display:block;background:#63a35d}
footer{text-align:center;padding:25px;color:var(--muted);font-size:12px}
@media(max-width:800px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.two{grid-template-columns:1fr}}
@media(max-width:480px){.grid{grid-template-columns:1fr}form .fields{grid-template-columns:1fr}h1{font-size:23px}}
</style>
</head>
<body>
<header>
  <div><div class="brand">FARM<span>2</span>FOR</div>
  <p>Connecting farmers, markets and communities</p></div>
  <div class="pill">HACKATHON PROTOTYPE</div>
</header>
<main>
<nav id="nav">
<button class="active" data-page="overview">Overview</button>
<button data-page="market">Marketplace</button>
<button data-page="orders">Orders</button>
<button data-page="storage">Cold Storage</button>
<button data-page="branches">Branches</button>
<button data-page="insights">Crop Insights</button>
<button data-page="fund">Support Fund</button>
<button data-page="trace">Traceability</button>
</nav>
<section id="overview" class="page">
<h1>Headquarters Dashboard</h1><p class="sub">A live view of records stored in your FARM2FOR database.</p>
<div class="grid" id="metrics"></div>
<div class="two">
<div class="panel"><h2>How FARM2FOR works</h2>
<p>🌱 Farmer lists available crops</p><p>🛒 Customer places an order</p>
<p>🚚 Collection and order status are updated</p><p>❄️ Cold storage records stock received</p>
<p>🔎 Traceability records help follow crop history</p></div>
<div class="panel"><h2>Network summary</h2><div id="overview-extra"></div>
<p class="small">Sample records are included for demonstration. Add your own records using the tabs above.</p></div>
</div></section>

<section id="market" class="page hidden">
<h1>Farmer Marketplace</h1><p class="sub">Browse crop listings or register produce from a farmer.</p>
<div class="two"><div class="panel"><h2>Available crops</h2><div class="tablewrap"><table><thead><tr><th>Crop</th><th>Farmer / Village</th><th>Stock</th><th>Price/kg</th><th>Order</th></tr></thead><tbody id="crop-list"></tbody></table></div></div>
<div class="panel"><h2>List a crop</h2><form id="crop-form"><div class="fields">
<div><label>Farmer name</label><input name="farmer" required></div>
<div><label>Village</label><input name="village" required></div>
<div><label>Crop name</label><input name="name" required></div>
<div><label>Quantity (kg)</label><input name="qty" type="number" min=".1" step=".1" required></div>
<div><label>Price per kg (₹)</label><input name="price" type="number" min="0" step=".01" required></div>
<div><label>Harvest date</label><input name="harvest_date" type="date"></div>
<div><label>Expiry date (if applicable)</label><input name="expiry_date" type="date"></div>
</div><button class="primary">Add crop listing</button></form><div id="crop-msg"></div></div></div>
</section>

<section id="orders" class="page hidden">
<h1>Order Management</h1><p class="sub">Place orders from the marketplace and update progress.</p>
<div class="panel"><div class="tablewrap"><table><thead><tr><th>Order</th><th>Buyer</th><th>Crop</th><th>Quantity</th><th>Total</th><th>Status</th><th>Update</th></tr></thead><tbody id="order-list"></tbody></table></div></div>
</section>

<section id="storage" class="page hidden">
<h1>Cold Storage Network</h1><p class="sub">Monitor facility capacity and record incoming stock.</p>
<div class="grid" id="storage-list"></div>
<div class="panel"><h2>Receive produce</h2><form id="storage-form"><div class="fields">
<div><label>Storage facility</label><select name="storage_id" id="storage-select" required></select></div>
<div><label>Quantity received (kg)</label><input name="qty" type="number" min=".1" step=".1" required></div>
</div><button class="primary">Record incoming stock</button></form><div id="storage-msg"></div></div>
</section>

<section id="branches" class="page hidden">
<h1>District & Village Branches</h1><p class="sub">View the FARM2FOR branch network.</p>
<div class="panel"><div class="tablewrap"><table><thead><tr><th>Branch</th><th>District</th><th>Type</th><th>Coordinator / Manager</th></tr></thead><tbody id="branch-list"></tbody></table></div></div>
<p class="small">This prototype displays branch records. Branch creation and permission management can be added as a next development step.</p>
</section>

<section id="insights" class="page hidden">
<h1>Crop Supply Insights</h1><p class="sub">Current listing totals and average prices—not a trained forecasting model.</p>
<div class="panel"><div class="tablewrap"><table><thead><tr><th>Crop</th><th>Total available (kg)</th><th>Average price/kg</th><th>Listings</th><th>Supply indicator</th></tr></thead><tbody id="forecast-list"></tbody></table></div></div>
<div class="notice">Next AI milestone: collect dated orders, prices, harvest seasons and local demand before training and evaluating a crop-demand model.</div>
</section>

<section id="fund" class="page hidden">
<h1>Proposed Farmer Support Fund</h1><p class="sub">A transparent ledger concept for proposed FARM2FOR contributions and farmer-support allocations.</p>
<div class="grid" id="fund-metrics"></div>
<div class="two"><div class="panel"><h2>Record a ledger entry</h2>
<form id="fund-form"><div class="fields">
<div><label>Entry type</label><select name="entry_type"><option>Contribution</option><option>Allocation</option></select></div>
<div><label>Amount (₹)</label><input name="amount" type="number" min=".01" step=".01" required></div>
<div class="wide"><label>Purpose / description</label><input name="note" required placeholder="e.g. Proposed revenue contribution"></div>
</div><button class="primary">Save entry</button></form><div id="fund-msg"></div></div>
<div class="panel"><h2>Fund principles</h2><p>• Contributions and allocations are recorded separately.</p><p>• Allocations cannot exceed the recorded balance.</p><p>• Actual funding rules and approvals must be established before real use.</p></div></div>
<div class="panel"><h2>Ledger history</h2><div class="tablewrap"><table><thead><tr><th>Date</th><th>Type</th><th>Amount</th><th>Purpose</th></tr></thead><tbody id="fund-list"></tbody></table></div></div>
</section>

<section id="trace" class="page hidden">
<h1>Crop Traceability</h1><p class="sub">Look up a crop batch by its database ID.</p>
<div class="panel"><form id="trace-form"><div class="fields"><div><label>Crop ID</label><input name="id" type="number" min="1" value="1" required></div><div style="align-self:end"><button class="primary">Find crop history</button></div></div></form><div id="trace-result"></div></div>
<p class="small">This demo uses a crop ID for lookup. A printable QR label and verified identity checks are future features.</p>
</section>
</main>
<footer>FARM2FOR • Agriculture technology prototype • Demo data is not a live commercial service</footer>

<script>
const $ = s => document.querySelector(s);
const money = n => '₹' + Number(n || 0).toLocaleString('en-IN',{maximumFractionDigits:2});
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(url, options={}) {
  const r = await fetch(url,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});
  const data = await r.json();
  if(!r.ok) throw new Error(data.error || 'Request failed');
  return data;
}
function msg(id,text,error=false){
  $(id).innerHTML = `<div class="notice ${error?'error':''}">${esc(text)}</div>`;
}
function formData(form){return Object.fromEntries(new FormData(form).entries());}
async function loadSummary(){
 const d=await api('/api/summary');
 const cards=[['Farmers represented',d.farmers],['Crop listings',d.crops],
 ['Available produce',Number(d.available_kg).toLocaleString()+' kg'],
 ['Orders placed',d.orders],['Order value',money(d.sales_value)],
 ['Storage capacity',d.storage_capacity+' kg'],['Storage used',d.storage_used+' kg'],
 ['Support fund balance',money(d.fund_balance)]];
 $('#metrics').innerHTML=cards.map(c=>`<div class="card"><div class="metric-label">${c[0]}</div><div class="metric">${c[1]}</div></div>`).join('');
 $('#overview-extra').innerHTML=`<p><b>Produce available:</b> ${d.available_kg} kg</p><p><b>Storage occupancy:</b> ${d.storage_capacity ? Math.round(d.storage_used/d.storage_capacity*100):0}%</p><div class="progress"><span style="width:${d.storage_capacity?Math.min(100,d.storage_used/d.storage_capacity*100):0}%"></span></div><p><b>Recorded order value:</b> ${money(d.sales_value)}</p>`;
 $('#fund-metrics').innerHTML=[['Current balance',money(d.fund_balance)],['Total storage',d.storage_capacity+' kg'],['Placed orders',d.orders]].map(c=>`<div class="card"><div class="metric-label">${c[0]}</div><div class="metric">${c[1]}</div></div>`).join('');
}
async function loadCrops(){
 const data=await api('/api/crops');
 $('#crop-list').innerHTML=data.map(c=>`<tr><td><b>${esc(c.name)}</b><br><span class="small">ID ${c.id}</span></td><td>${esc(c.farmer)}<br>${esc(c.village)}</td><td>${c.qty} kg</td><td>${money(c.price)}</td><td><button ${c.qty<=0?'disabled':''} onclick="placeOrder(${c.id},'${esc(c.name).replace(/'/g,"\\'")}')">Order</button></td></tr>`).join('')||'<tr><td colspan="5">No crop listings yet.</td></tr>';
}
async function placeOrder(id,name){
 const buyer=prompt('Customer name for '+name+':');
 if(!buyer || !buyer.trim()) return;
 const qty=prompt('Quantity in kg:');
 if(qty===null) return;
 try{await api('/api/orders',{method:'POST',body:JSON.stringify({crop_id:id,buyer:buyer.trim(),qty:Number(qty)})});alert('Order placed successfully');await refresh();}
 catch(e){alert(e.message);}
}
async function loadOrders(){
 const data=await api('/api/orders');
 const statuses=['Placed','Confirmed','Collected','In Storage','Out for Delivery','Delivered','Cancelled'];
 $('#order-list').innerHTML=data.map(o=>`<tr><td>#${o.id}</td><td>${esc(o.buyer)}</td><td>${esc(o.crop_name)}</td><td>${o.qty} kg</td><td>${money(o.total)}</td><td><span class="pill">${esc(o.status)}</span></td><td><select onchange="updateStatus(${o.id},this.value)">${statuses.map(s=>`<option ${s===o.status?'selected':''}>${s}</option>`).join('')}</select></td></tr>`).join('')||'<tr><td colspan="7">No orders yet. Place an order from Marketplace.</td></tr>';
}
async function updateStatus(id,status){try{await api('/api/orders/'+id+'/status',{method:'POST',body:JSON.stringify({status})});await refresh();}catch(e){alert(e.message);}}
async function loadStorage(){
 const data=await api('/api/storage');
 $('#storage-list').innerHTML=data.map(s=>`<div class="card"><h2>${esc(s.name)}</h2><p class="small">${esc(s.district)}</p><div class="metric">${s.used} / ${s.capacity} kg</div><div class="progress"><span style="width:${s.capacity?Math.min(100,s.used/s.capacity*100):0}%"></span></div><span class="pill">${Math.max(0,s.capacity-s.used)} kg free</span></div>`).join('');
 $('#storage-select').innerHTML=data.map(s=>`<option value="${s.id}">${esc(s.name)} — ${esc(s.district)}</option>`).join('');
}
async function loadBranches(){
 const data=await api('/api/branches');
 $('#branch-list').innerHTML=data.map(b=>`<tr><td>${esc(b.name)}</td><td>${esc(b.district)}</td><td>${esc(b.type)}</td><td>${esc(b.manager)}</td></tr>`).join('');
}
async function loadForecast(){
 const data=await api('/api/forecast');
 $('#forecast-list').innerHTML=data.map(c=>`<tr><td>${esc(c.name)}</td><td>${c.available_kg}</td><td>${money(c.average_price)}</td><td>${c.listings}</td><td><span class="pill">${c.available_kg<100?'Low listed supply':c.available_kg<300?'Moderate listed supply':'Higher listed supply'}</span></td></tr>`).join('')||'<tr><td colspan="5">No crop data available.</td></tr>';
}
async function loadFund(){
 const data=await api('/api/fund');
 $('#fund-list').innerHTML=data.map(f=>`<tr><td>${esc(f.created_at)}</td><td>${esc(f.entry_type)}</td><td>${money(f.amount)}</td><td>${esc(f.note)}</td></tr>`).join('')||'<tr><td colspan="4">No ledger entries yet.</td></tr>';
}
async function refresh(){
 await Promise.all([loadSummary(),loadCrops(),loadOrders(),loadStorage(),loadBranches(),loadForecast(),loadFund()]);
}
$('#nav').addEventListener('click',e=>{
 const b=e.target.closest('button[data-page]');if(!b)return;
 document.querySelectorAll('nav button').forEach(x=>x.classList.toggle('active',x===b));
 document.querySelectorAll('.page').forEach(x=>x.classList.toggle('hidden',x.id!==b.dataset.page));
});
$('#crop-form').addEventListener('submit',async e=>{
 e.preventDefault();const d=formData(e.target);
 ['qty','price'].forEach(k=>d[k]=Number(d[k]));
 try{await api('/api/crops',{method:'POST',body:JSON.stringify(d)});e.target.reset();msg('#crop-msg','Crop listing saved.');await refresh();}
 catch(err){msg('#crop-msg',err.message,true);}
});
$('#storage-form').addEventListener('submit',async e=>{
 e.preventDefault();const d=formData(e.target);
 try{await api('/api/storage/'+d.storage_id+'/receive',{method:'POST',body:JSON.stringify({qty:Number(d.qty)})});e.target.reset();msg('#storage-msg','Storage receipt recorded.');await refresh();}
 catch(err){msg('#storage-msg',err.message,true);}
});
$('#fund-form').addEventListener('submit',async e=>{
 e.preventDefault();const d=formData(e.target);d.amount=Number(d.amount);
 try{await api('/api/fund',{method:'POST',body:JSON.stringify(d)});e.target.reset();msg('#fund-msg','Fund ledger entry saved.');await refresh();}
 catch(err){msg('#fund-msg',err.message,true);}
});
$('#trace-form').addEventListener('submit',async e=>{
 e.preventDefault();const d=formData(e.target);
 try{const r=await api('/api/trace/'+Number(d.id));
 $('#trace-result').innerHTML=`<div class="notice"><b>${esc(r.crop.name)}</b> — Batch ID ${r.crop.id}<br>Farmer: ${esc(r.crop.farmer)}<br>Village: ${esc(r.crop.village)}<br>Harvest date: ${esc(r.crop.harvest_date||'Not recorded')}<br>Expiry date: ${esc(r.crop.expiry_date||'Not recorded')}<br>Available stock: ${r.crop.qty} kg</div><h2>History</h2>${r.events.map(x=>`<p><span class="pill">${esc(x.event)}</span> ${esc(x.location)}<br>${esc(x.note)}<br><span class="small">${esc(x.created_at)}</span></p>`).join('')||'No events recorded.'}`;
 }catch(err){msg('#trace-result',err.message,true);}
});
async function start(){try{await refresh();}catch(e){console.error(e);alert('Could not load the database. Check that the Flask server is running.');}}
start();
</script>
</body>
</html>
"""

init_db()

if __name__ == "__main__":
    app.run(debug=True)
