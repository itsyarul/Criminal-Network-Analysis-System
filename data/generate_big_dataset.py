"""
Synthetic BIG crime-network dataset generator (~2x, Fresh Network D/E/F/Y/M).

Fresh network: D/E/F cells + Y bridges + M noise, ~78 network + 35 noise = 113 persons.
Row budgets ~2x current: CDRs ~1500, Txns ~320, FIRs ~70, Social ~130, Surveillance ~65, Intel ~60, Criminal History ~110.

Aligned to original generate_dataset.py:
- Same START_DATE, THRESHOLD, LOCATIONS, role distributions, burst/structuring signatures.
- Same two-RNG discipline (main random + alias_rng) so alias variance is eval-only.
- Same headers as backend/loader.py EXPECTED_HEADERS + people_directory alias handling.
- Photos: assigns UUID-orphan mugshots in data/mugshots/ to new network IDs sorted.
- Eval hygiene: ground_truth_flag + alias_map.json + ground_truth_network_big.json kept, never fed to pipeline.

Output: data/synthetic_big_v2/ (isolated, does not overwrite data/*.csv)
"""
import json
import random
import datetime
import os
import csv
import re
from pathlib import Path
from collections import defaultdict, Counter

random.seed(12345)
alias_rng = random.Random(20260716)

OUT_DIR = Path(__file__).resolve().parent / "synthetic_big_v2"
OUT_DIR.mkdir(parents=True, exist_ok=True)

START_DATE = datetime.date(2026, 1, 1)
THRESHOLD = 50000

LOCATIONS = ["Dockside Ward", "Old Market Circle", "Riverside Colony",
             "Industrial Estate Road", "Central Junction", "Eastgate",
             "Hilltop Society", "Station Road", "New Colony",
             "Warehouse District", "North Bypass", "Lakeview Chowk"]

# Roles mirrored
ROLES_D = ["Kingpin", "Lieutenant","Lieutenant","Lieutenant","Associate","Associate","Associate","Associate","Associate","Associate","Associate","Associate","Accountant","Safehouse keeper","Associate","Associate","Associate","Associate","Associate","Associate","Associate","Associate","Associate","Associate"]  # 24
ROLES_E = ["Kingpin","Lieutenant","Lieutenant","Lieutenant","Courier","Courier","Courier","Courier","Courier","Courier","Courier","Courier","Courier","Courier","Financier","Cache keeper","Courier","Courier","Courier","Courier","Courier","Courier","Courier","Courier"] # 24
ROLES_F = ["Kingpin","Lieutenant","Lieutenant","Lieutenant","Enforcer","Enforcer","Enforcer","Enforcer","Enforcer","Enforcer","Enforcer","Enforcer","Enforcer","Enforcer","Accountant","Collection agent","Enforcer","Enforcer","Enforcer","Enforcer","Enforcer","Enforcer","Enforcer","Enforcer"] # 24
ROLES_Y = ["Hawala operator","Weapons supplier","Logistics broker","Corrupt police contact","Cyber coordinator","Arms courier"]

FIRST_NAMES = ["Aarav","Vivaan","Aditya","Vikram","Samar","Arjun","Reyansh","Mohammed","Sai","Krishna","Ishaan","Shaurya","Atharva","Advik","Pranav","Rohan","Kabir","Aryan","Ansh","Dhruv","Naksh","Rudra","Veer","Siddharth","Abhinav","Harsh","Kunal","Nikhil","Varun","Sahil","Manav","Dev","Yash","Aman","Rahul","Aniket","Tushar","Suresh","Rajesh","Sunil","Prakash","Mohan","Gautam","Harshit","Naveen","Sanjay","Deepak","Sameer","Imran","Farhan","Vikas","Ramesh","Anwar","Rajan","Salim","Vinod","Ashok","Yusuf","Santosh","Aslam","Mahesh","Irfan","Vijay","Neha","Meena","Sunil","Dinesh","Javed","Arvind","Priya","Pooja","Kavita","Anita","Sneha","Divya","Kiran","Shalini","Rakesh","Amit","Alok","Vinay","Ajay","Manoj"]
LAST_NAMES = ["Sharma","Verma","Gupta","Jain","Singh","Yadav","Mishra","Pandey","Kumar","Patel","Desai","Reddy","Nair","Menon","Shah","Mehta","Kapoor","Chopra","Bansal","Malhotra","Iyer","Bhatia","Chatterjee","Dutta","Rao","Bansal","Kulkarni","Sinha","Naidu","Deshmukh","Thakur","Rathore","Chauhan","Qureshi","Sheikh","Naik","Pathan","Chavan","Kamble","Gaikwad","Baig","Jadhav","Sawant","Ali","Shaikh","Pandit","Qureshi","Surve","Bhosale","Chougule","Gupta","Salunkhe","Kulkarni","Pillai","Ansari","Malik","Kadam","Bhosale","Pillai"]

# Build fresh network nodes
def unique_names(n):
    used=set()
    out=[]
    attempts=0
    while len(out)<n and attempts<10000:
        fn=random.choice(FIRST_NAMES)
        ln=random.choice(LAST_NAMES)
        name=f"{fn} {ln}"
        if name not in used and name not in ["Anwar Sheikh","Suresh Rane","Farhan Qureshi","Vikas Chauhan","Ganesh Pawar","Iqbal Khan","Karan Mehra","Ramesh Yadav","Sanjay More","Tariq Ansari","Faisal Shah","Meena Joshi","Rohit Bhatt","Rajan Naik","Salim Pathan","Vinod Chavan","Nitin Kamble","Ashok Gaikwad","Imran Baig","Sameer Jadhav","Deepak Sawant","Waseem Ali","Rafiq Shaikh","Ganesh Pandit","Kavita Desai","Mohsin Qureshi","Prakash Shetty","Nasir Malik","Ravi Kadam","Baban Surve","Yusuf Ali","Santosh Bhosale","Ramesh Chougule","Aslam Pathan","Mahesh Gupta","Irfan Sheikh","Vijay Salunkhe","Neha Kulkarni","Sunil Pillai","Dinesh Mehta","Javed Ansari","Arvind Kapoor","R.K. Verma"]:
            used.add(name)
            out.append(name)
        attempts+=1
    return out

names_pool = unique_names(120)
idx=0
nodes=[]
for cell, roles, prefix in [("D", ROLES_D, "D"), ("E", ROLES_E, "E"), ("F", ROLES_F, "F")]:
    for i, role in enumerate(roles, start=1):
        nid = f"{prefix}{i}" if prefix!="F" or i<=12 else f"{prefix}{i}"  # keep simple sequential
        # Ensure IDs are D1..D24, E1..E24, F1..F24
        nid = f"{prefix}{i}"
        nodes.append({"id": nid, "name": names_pool[idx], "role": role, "cell": cell})
        idx+=1

# Bridges Y1..Y6
for i, role in enumerate(ROLES_Y, start=1):
    nid=f"Y{i}"
    nodes.append({"id": nid, "name": names_pool[idx], "role": role, "cell": "Bridge"})
    idx+=1

# Noise M1..M35 (fresh, distinct from N)
NOISE_FIRST = ["Rakesh", "Priya", "Manoj", "Sunita", "Ajay", "Pooja", "Deepa","Vinay", "Anita", "Kiran", "Sneha", "Arjun", "Divya", "Naveen","Shalini", "Rohan", "Kavya", "Amit", "Poonam", "Alok","Harsh","Tanya","Neha","Ritu","Anjali","Komal","Sonia","Megha","Shruti","Ishita","Nisha","Pallavi","Swati","Bhavna","Karishma"]
NOISE_LAST = ["Mishra", "Reddy", "Naidu", "Kulkarni", "Bhatia", "Iyer", "Menon","Chatterjee", "Dutta", "Rao", "Nair", "Sinha", "Malhotra", "Bansal","Gupta","Sharma","Jain","Patel","Desai","Kumar"]
noise_people=[]
used_names=set(names_pool[:idx])
for i in range(35):
    while True:
        name=f"{random.choice(NOISE_FIRST)} {random.choice(NOISE_LAST)}"
        if name not in used_names:
            used_names.add(name)
            break
    pid=f"M{i+1}"
    noise_people.append({"id": pid, "name": name, "role": "Unrelated/background", "cell": "Noise", "phone": "", "account": ""})
    # phone/account will be assigned below

# Assign phones/accounts in fresh block 70100xxxxx to avoid collision with 70000
def make_phone_fresh(i):
    return f"70100{i:05d}"
def make_account_fresh(i):
    return f"AC0010{i:06d}"

all_people = nodes + noise_people
# sequential indices
for i, p in enumerate(all_people, start=1):
    p["phone"] = make_phone_fresh(i)
    p["account"] = make_account_fresh(i)

id_to_person={p["id"]: p for p in all_people}
noise_ids=[p["id"] for p in noise_people]

# Photo mapping: use UUID orphans first
DATA_DIR = Path(__file__).resolve().parent
MUGSHOTS_DIR = DATA_DIR / "mugshots"
# Collect UUID orphans: files that are not canonical A/B/C/X/N*.jpg and not random-person.jpeg
canonical_ids = set([f"A{i}" for i in range(1,14)] + [f"B{i}" for i in range(1,14)] + [f"C{i}" for i in range(1,14)] + ["X1","X2","X3","X4"] + [f"N{i}" for i in range(1,21)])
# Actually our fresh will map to D/E/F/Y/M, so any UUID is orphan
uuid_files = sorted([f.name for f in MUGSHOTS_DIR.iterdir() if re.match(r"^[0-9a-f]{8}-", f.name.lower())])
# also include random-person.jpeg is not counted but we keep it as not assigned
# Sort nodes sorted by id for deterministic mapping
sorted_network_ids = sorted([n["id"] for n in nodes], key=lambda x: (x[0], int(re.search(r"\d+", x).group())))
photo_manifest={}
for nid, fname in zip(sorted_network_ids, uuid_files):
    photo_manifest[nid]=f"/mugshots/{fname}"
    # update person entry
    id_to_person[nid]["photo"]=f"/mugshots/{fname}"
# If network > uuid count, fallback to N*.jpg reusing (not copying, just path)
# Our network is 78, uuid count is ~62, so 16 overflow
if len(sorted_network_ids) > len(uuid_files):
    overflow = sorted_network_ids[len(uuid_files):]
    # reuse canonical N photos sorted
    n_photos = sorted([f.name for f in MUGSHOTS_DIR.iterdir() if re.match(r"^N\d+\.jpg$", f.name)])
    for nid, fname in zip(overflow, n_photos*2):
        photo_manifest[nid]=f"/mugshots/{fname}"
        id_to_person[nid]["photo"]=f"/mugshots/{fname}"
# Noise M photos: assign remaining UUIDs if any else canonical random
remaining_uuid = uuid_files[len(sorted_network_ids):]
sorted_noise = sorted(noise_ids, key=lambda x: int(x[1:]))
for nid, fname in zip(sorted_noise, remaining_uuid):
    photo_manifest[nid]=f"/mugshots/{fname}"
    id_to_person[nid]["photo"]=f"/mugshots/{fname}"
# remaining noise get ui-avatars fallback (no photo field, frontend will fallback)

# Build edges: mirror original topology but expanded
edges=[]
# Within D: Kingpin D1 -> Lieutenants D2,D3,D4 ; Lieutenants -> Associates
for e in [("D1","D2"),("D1","D3"),("D1","D4")]:
    edges.append({"source": e[0], "target": e[1], "relation": "commands"})
# D lieutenants to associates (6 associates D5-D10 plus D14-D17 etc)
d_associates=[f"D{i}" for i in [5,6,7,8,9,10,13,14,15,16,17,18,19,20,21,22,23,24]][:12]
# map: D2 -> 3 associates, D3 -> 3, D4 ->3, remaining cross
for a, b in [("D2","D5"),("D2","D6"),("D2","D13"),("D3","D7"),("D3","D8"),("D3","D14"),("D4","D9"),("D4","D10"),("D4","D15")]:
    edges.append({"source": a, "target": b, "relation": "commands"})
# extra associate mesh
for a,b in [("D5","D6"),("D7","D8"),("D11","D5"),("D12","D7")]:
    if a in id_to_person and b in id_to_person:
        edges.append({"source": a, "target": b, "relation": "coordinates"})

# Within E: similar but B-style has many couriers
for e in [("E1","E2"),("E1","E3"),("E1","E4")]:
    edges.append({"source": e[0], "target": e[1], "relation": "commands"})
for a,b in [("E2","E5"),("E2","E6"),("E2","E13"),("E3","E7"),("E3","E8"),("E3","E14"),("E4","E9"),("E4","E10"),("E4","E15")]:
    edges.append({"source": a, "target": b, "relation": "commands"})
for a,b in [("E5","E6"),("E7","E8"),("E11","E5"),("E12","E6")]:
    if a in id_to_person and b in id_to_person:
        edges.append({"source": a, "target": b, "relation": "coordinates"})

# Within F: Enforcers
for e in [("F1","F2"),("F1","F3"),("F1","F4")]:
    edges.append({"source": e[0], "target": e[1], "relation": "commands"})
for a,b in [("F2","F5"),("F2","F6"),("F2","F13"),("F3","F7"),("F3","F8"),("F3","F14"),("F4","F9"),("F4","F10"),("F4","F15")]:
    edges.append({"source": a, "target": b, "relation": "commands"})
for a,b in [("F5","F6"),("F7","F8"),("F11","F5"),("F12","F6")]:
    if a in id_to_person and b in id_to_person:
        edges.append({"source": a, "target": b, "relation": "coordinates"})

# Bridge Y edges: mirror X1-X4 logic but expanded to 6 bridges
# Y1 hawala -> accountants/financiers
for tgt in ["D11","E15","F11"]:
    if tgt in id_to_person:
        edges.append({"source": "Y1", "target": tgt, "relation": "launders_for"})
# Y2 weapons -> Kingpins
for tgt in ["E1","F1","D1"]:
    edges.append({"source": "Y2", "target": tgt, "relation": "supplies_weapons"})
# Y3 logistics -> Kingpins
for tgt in ["D1","E1","F1"]:
    edges.append({"source": "Y3", "target": tgt, "relation": "provides_logistics"})
# Y4 corrupt police -> Lieutenants
for tgt in ["D2","E2","F2"]:
    edges.append({"source": "Y4", "target": tgt, "relation": "tips_off"})
# Y5 cyber coordinator -> associates
for tgt in ["D13","E13","F13"]:
    edges.append({"source": "Y5", "target": tgt, "relation": "coordinates_cyber"})
# Y6 arms courier -> couriers/enforcers
for tgt in ["E5","F5","D5"]:
    edges.append({"source": "Y6", "target": tgt, "relation": "courier_for"})

# Additional random intra-cell edges to increase density to ~80 edges
random.seed(999)
for cell_prefix in ["D","E","F"]:
    members=[n["id"] for n in nodes if n["cell"]==cell_prefix]
    for _ in range(8):
        a,b = random.sample(members,2)
        if not any(e["source"]==a and e["target"]==b for e in edges):
            edges.append({"source": a, "target": b, "relation": "associates"})

# Events: 3 events like original but for D/E/F
events=[
    {"id": "EVENT_D", "cell": "D", "type": "drug_shipment", "day": 58, "core_participants": ["D1","D2","D3","D4","D11","D12"]},
    {"id": "EVENT_E", "cell": "E", "type": "arms_shipment", "day": 61, "core_participants": ["E1","E2","E3","E4","E15","E16"]},
    {"id": "EVENT_F", "cell": "F", "type": "extortion_collection", "day": 64, "core_participants": ["F1","F2","F3","F4","F11","F12"]},
]

gt_big={"nodes": nodes, "edges": edges, "events": events}
with open(OUT_DIR / "ground_truth_network_big.json","w") as f:
    json.dump(gt_big,f,indent=2)

# Now generate CSVs similar to original but 2x
# Reset random for generation reproducibility (different from edge gen)
random.seed(23456)
alias_rng = random.Random(20260717)
START_DATE = datetime.date(2026,1,1)
node_by_id={n["id"]:n for n in nodes}

# CDR generation
call_counter=0
cdr_rows=[]
def add_call(a_id,b_id,day,duration_range=(20,600)):
    global call_counter
    if a_id==b_id: return
    day=max(1,min(90,day))
    call_counter+=1
    t=START_DATE+datetime.timedelta(days=day-1)
    ts=datetime.datetime.combine(t,datetime.time(random.randint(7,23), random.randint(0,59)))
    call_type=random.choices(["voice","sms"],weights=[0.75,0.25])[0]
    a,b=id_to_person[a_id],id_to_person[b_id]
    cdr_rows.append({"call_id":f"CDR{call_counter:05d}","caller_id":a_id,"caller_name":a["name"],"caller_phone":a["phone"],"callee_id":b_id,"callee_name":b["name"],"callee_phone":b["phone"],"timestamp":ts.isoformat(sep=" "),"day":day,"call_type":call_type,"duration_sec":random.randint(*duration_range) if call_type=="voice" else 0,"cell_tower_location":random.choice(LOCATIONS)})

# Baseline along edges
for e in edges:
    n_calls=random.randint(8,16)  # slightly higher than original 6-14
    for d in sorted(random.sample(range(1,91), n_calls)):
        add_call(e["source"], e["target"], d)

# Event bursts
CELL_NOISE_MULT={"D":1.0,"E":1.8,"F":1.0}
for ev in events:
    day_ev=ev["day"]
    core=ev["core_participants"]
    mult=CELL_NOISE_MULT[ev["cell"]]
    window=list(range(day_ev-6, day_ev))
    n_burst=int(random.randint(14,24)*mult)
    for _ in range(n_burst):
        a,b=random.sample(core,2)
        dur=(30,900) if mult<=1.2 else (10,900)
        add_call(a,b,random.choice(window),duration_range=dur)
    n_decoy=int(random.randint(6,12)*mult)
    cell_members=[n["id"] for n in nodes if n["cell"]==ev["cell"]]
    for _ in range(n_decoy):
        add_call(random.choice(cell_members), random.choice(noise_ids), random.choice(window+[day_ev,day_ev+1]), duration_range=(5,60))

# Bridge bump
bridge_relevance={"Y1":["D","E","F"],"Y2":["E","F","D"],"Y3":["D","E","F"],"Y4":["D","E","F"],"Y5":["D","E","F"],"Y6":["D","E","F"]}
for ev in events:
    for bridge_id,cells in bridge_relevance.items():
        if ev["cell"] not in cells: continue
        targets=[e["target"] for e in edges if e["source"]==bridge_id and node_by_id.get(e["target"],{}).get("cell")==ev["cell"]]
        for tgt in targets:
            for _ in range(random.randint(2,4)):
                add_call(bridge_id,tgt,random.randint(ev["day"]-5, ev["day"]-1),duration_range=(20,300))

# Background noise 320
for _ in range(320):
    a,b=random.sample(noise_ids,2)
    add_call(a,b,random.randint(1,90),duration_range=(10,400))

# Financial transactions
txn_counter=0
txn_rows=[]
def add_txn(sender_id,receiver_id,amount,day,txn_type="Bank Transfer",flag="none"):
    global txn_counter
    day=max(1,min(90,day))
    txn_counter+=1
    t=START_DATE+datetime.timedelta(days=day-1)
    ts=datetime.datetime.combine(t,datetime.time(random.randint(8,22),random.randint(0,59)))
    s,r=id_to_person[sender_id],id_to_person[receiver_id]
    txn_rows.append({"txn_id":f"TXN{txn_counter:05d}","sender_id":sender_id,"sender_name":s["name"],"sender_account":s["account"],"receiver_id":receiver_id,"receiver_name":r["name"],"receiver_account":r["account"],"amount_inr":amount,"timestamp":ts.isoformat(sep=" "),"day":day,"txn_type":txn_type,"ground_truth_flag":flag})

finance_roles={"Accountant","Financier","Cache keeper","Safehouse keeper","Collection agent","Kingpin","Hawala operator","Cyber coordinator"}
finance_people=[n for n in nodes if n["role"] in finance_roles]
for _ in range(280):
    s,r=random.sample(finance_people,2)
    if s["cell"]!=r["cell"] and random.random()<0.7: continue
    add_txn(s["id"],r["id"],random.randint(2000,25000),random.randint(1,90),txn_type=random.choice(["UPI","Bank Transfer"]))
for _ in range(180):
    s,r=random.sample(noise_people,2)
    add_txn(s["id"],r["id"],random.randint(500,15000),random.randint(1,90),txn_type=random.choice(["UPI","Bank Transfer","Cash"]))
# Lump sums for D and E
lump_sum_chains={
    "D":{"chain":[("D1","D11"),("D11","Y3")],"amount_range":(150000,450000)},
    "E":{"chain":[("E1","E15"),("E15","Y2"),("E15","Y3")],"amount_range":(200000,600000)},
}
for ev in events:
    cfg=lump_sum_chains.get(ev["cell"])
    if not cfg: continue
    for s,r in cfg["chain"]:
        add_txn(s,r,random.randint(*cfg["amount_range"]),random.randint(ev["day"]-4,ev["day"]-1),txn_type=random.choice(["Bank Transfer","Hawala"]),flag=f"suspicious_lump_{ev['cell']}")
# Structuring for F
ev_c=next(e for e in events if e["cell"]=="F")
struct_days=list(range(ev_c["day"]-12, ev_c["day"]-1))
for _ in range(random.randint(35,45)):
    payer=random.choice(noise_people)["id"]
    add_txn(payer,"F12",random.randint(35000,49500),random.choice(struct_days),txn_type="Cash",flag="structuring_component")
for _ in range(4):
    add_txn("F12","F11",random.randint(250000,400000),random.randint(ev_c["day"]-6,ev_c["day"]-1),txn_type="Cash",flag="structuring_consolidation")
for s,r in [("F11","Y1"),("F11","F1")]:
    add_txn(s,r,random.randint(200000,500000),random.randint(ev_c["day"]-3,ev_c["day"]-1),txn_type="Hawala",flag="suspicious_lump_F")

# Alias variance (D8)
P_ALIAS_FIR=0.35
P_ALIAS_SOCIAL=0.25
NICKNAMES_D={
    "Aarav Sharma":["Aarav Bhai"],
    names_pool[0]:[names_pool[0].split()[0]],
}
# reuse original nicknames logic but for fresh names generate nicknames as first name alone
# For simplicity, use first-name nickname for 8 random fresh persons
fresh_nick={names_pool[i]:[names_pool[i].split()[0]] for i in random.sample(range(len(names_pool)),8)}
NICKNAMES={**fresh_nick}
SURNAME_ALT={"Sharma":["Sharma"],"Verma":["Verma"]}
VOWELS=set("aeiouAEIOU")
alias_map={}
def typo_variant(name):
    if len(name)<4: return name
    if alias_rng.random()<0.5:
        cand=[i for i in range(1,len(name)) if name[i] in VOWELS and name[i-1]!=" "]
        if cand:
            i=alias_rng.choice(cand)
            return name[:i]+name[i+1:]
        return name
    i=alias_rng.randrange(1,len(name)-1)
    if name[i]==" " or name[i-1]==" ": return name
    return name[:i-1]+name[i]+name[i-1]+name[i+1:]
def alias_form(name):
    r=alias_rng.random()
    if name in NICKNAMES and r<0.60:
        return alias_rng.choice(NICKNAMES[name])
    parts=name.split()
    if len(parts)>1 and parts[-1] in SURNAME_ALT and r<0.75:
        return " ".join(parts[:-1]+[alias_rng.choice(SURNAME_ALT[parts[-1]])])
    return typo_variant(name)
def maybe_alias(person):
    if alias_rng.random()<P_ALIAS_FIR:
        txt=alias_form(person["name"])
        if txt!=person["name"]:
            alias_map[txt]=person["name"]
        return txt
    return person["name"]

# FIR narratives
fir_rows=[]
fir_counter=0
IPC_BY_CELL={"D":["NDPS Act Section 8/20 (possession & trafficking)","IPC 120B (criminal conspiracy)"],"E":["Arms Act Section 25","IPC 120B (criminal conspiracy)"],"F":["IPC 384 (extortion)","IPC 506 (criminal intimidation)"]}
REL_TEMPLATES=[
    "Acting on a tip-off, a patrol team intercepted {a} near {loc} on suspicion of involvement with a network linked to {kp}. {a} was found in possession of a mobile phone with frequent contact to {b}. Case registered under {ipc}.",
    "Complainant reported suspicious movement of unknown persons believed to include {a} and {b} around {loc} in the days preceding {evtype}. Surveillance recommended.",
    "Informant states that {a}, associated with {kp}'s network, was seen meeting {b} at {loc}. Nature of meeting unclear; flagged for financial-record cross-check.",
    "FIR registered after a scuffle involving {a} at {loc}. Preliminary questioning suggests a possible link to {b} through a prior known association. Case under {ipc}.",
    "Source intelligence indicates {kp}'s group may be preparing for an upcoming operation. {a} and {b} were observed conferring near {loc} multiple times this week.",
]
NOISE_FIR_TEMPLATES=[
    "Complaint of two-wheeler theft reported outside {loc}. No suspects identified. Case under IPC 379.",
    "Minor road-rage altercation reported near {loc}; both parties counselled and released.",
    "Shopkeeper at {loc} reported a break-in overnight; petty cash and goods stolen. Case under IPC 380.",
    "Noise complaint received regarding a private function at {loc}; resolved on-site.",
    "Missing-person report filed for a local resident last seen near {loc}; family contacted, resolved.",
]
for ev in events:
    kp=node_by_id[ev["core_participants"][0]]["name"]
    cell_members=[n for n in nodes if n["cell"]==ev["cell"] and n["id"]!=ev["core_participants"][0]]
    for _ in range(random.randint(14,18)):
        fir_counter+=1
        a,b=random.sample(cell_members,2) if len(cell_members)>=2 else (cell_members[0],cell_members[0])
        template=random.choice(REL_TEMPLATES)
        loc=random.choice(LOCATIONS)
        ipc=random.choice(IPC_BY_CELL[ev["cell"]])
        evtype=ev["type"].replace("_"," ")
        narrative=template.format(a=maybe_alias(a),b=maybe_alias(b),kp=kp,loc=loc,ipc=ipc,evtype=evtype)
        fir_day=random.randint(max(1,ev["day"]-20), ev["day"]-1)
        fir_rows.append({"fir_id":f"FIR{fir_counter:04d}","date":(START_DATE+datetime.timedelta(days=fir_day-1)).isoformat(),"day":fir_day,"station":f"{loc} Police Station","location":loc,"ipc_sections":ipc,"narrative":narrative,"ground_truth_flag":f"relevant_cell_{ev['cell']}"})
for _ in range(24):
    fir_counter+=1
    loc=random.choice(LOCATIONS)
    fir_day=random.randint(1,90)
    fir_rows.append({"fir_id":f"FIR{fir_counter:04d}","date":(START_DATE+datetime.timedelta(days=fir_day-1)).isoformat(),"day":fir_day,"station":f"{loc} Police Station","location":loc,"ipc_sections":"IPC 379/380 (petty offence)","narrative":random.choice(NOISE_FIR_TEMPLATES).format(loc=loc),"ground_truth_flag":"noise"})
random.shuffle(fir_rows)

# Social posts
social_rows=[]
social_counter=0
CODED_TEMPLATES=["Big delivery coming through {loc} soon, everyone ready? #hustle","Meeting the usual crew near {loc} tonight, don't be late.","Package count almost done, moving it by this weekend near {loc}.","Collections running slow this week around {loc}, need to speed up.","New shipment landing soon, {loc} side clear for now.",]
CODED_ALIAS_TEMPLATES=["Met {alias} near {loc} last night, keep it quiet. #hustle","Told {alias} the drop moves to {loc}, pass it on.","{alias} collecting around {loc}, wrap up fast. #grind",]
NOISE_SOCIAL_TEMPLATES=["Great biryani at the new place near {loc} today! 10/10 #foodie","Traffic near {loc} is unbearable this morning, avoid if you can.","Watching the match tonight, who's up for it? #cricket","Beautiful sunset from {loc} today, needed this.","Anyone recommend a good electrician near {loc}?",]
HASHTAGS=["#hustle","#grind","#weekend","#business","#family","#blessed"]
def handle_for(person):
    base=person["name"].split()[0].lower()
    return f"@{base}{random.randint(10,99)}"
for ev in events:
    core_ids=ev["core_participants"]
    for _ in range(random.randint(24,32)):
        social_counter+=1
        pid=random.choice(core_ids)
        p=id_to_person[pid]
        loc=random.choice(LOCATIONS)
        post_day=random.randint(ev["day"]-8, ev["day"]-1)
        if alias_rng.random()<P_ALIAS_SOCIAL:
            others=[c for c in core_ids if c!=pid]
            mentioned=id_to_person[alias_rng.choice(others)] if others else p
            alias_txt=alias_form(mentioned["name"])
            if alias_txt!=mentioned["name"]:
                alias_map[alias_txt]=mentioned["name"]
            post_text=alias_rng.choice(CODED_ALIAS_TEMPLATES).format(alias=alias_txt,loc=loc)
        else:
            post_text=random.choice(CODED_TEMPLATES).format(loc=loc)
        social_rows.append({"post_id":f"SOC{social_counter:04d}","handle":handle_for(p),"person_id":pid,"timestamp":(START_DATE+datetime.timedelta(days=post_day-1)).isoformat(),"day":post_day,"location_tag":loc,"post_text":post_text,"hashtags":random.choice(HASHTAGS),"ground_truth_flag":f"relevant_cell_{ev['cell']}"})
for _ in range(50):
    social_counter+=1
    p=random.choice(noise_people)
    loc=random.choice(LOCATIONS)
    post_day=random.randint(1,90)
    social_rows.append({"post_id":f"SOC{social_counter:04d}","handle":handle_for(p),"person_id":p["id"],"timestamp":(START_DATE+datetime.timedelta(days=post_day-1)).isoformat(),"day":post_day,"location_tag":loc,"post_text":random.choice(NOISE_SOCIAL_TEMPLATES).format(loc=loc),"hashtags":random.choice(HASHTAGS),"ground_truth_flag":"noise"})
random.shuffle(social_rows)

# Surveillance reports
surv_rows=[]
surv_counter=0
SURV_TEAMS=["Field Unit 3","Surveillance Team Bravo","Surveillance Team Alpha","Field Unit 7","Alpha Recon Squad","Bravo Watch"]
for ev in events:
    core=ev["core_participants"]
    for _ in range(random.randint(12,16)):
        surv_counter+=1
        a,b=random.sample(core,2)
        loc=random.choice(LOCATIONS)
        day=random.randint(ev["day"]-10, ev["day"]-1)
        team=random.choice(SURV_TEAMS)
        conf=random.choice(["High","Medium","Low"])
        note=f"{random.randint(6,22):02d}{random.randint(0,59):02d} hrs - Surveillance Team reports {maybe_alias(id_to_person[a])} entering premises at {loc} with {maybe_alias(id_to_person[b])}. Vehicle reg MH-DEMO-{random.randint(1000,9999)} parked outside."
        surv_rows.append({"report_id":f"SURV{surv_counter:04d}","date":(START_DATE+datetime.timedelta(days=day-1)).isoformat(),"day":day,"team":team,"location":loc,"confidence":conf,"activity_notes":note,"ground_truth_flag":f"relevant_cell_{ev['cell']}"})
for _ in range(28):
    surv_counter+=1
    loc=random.choice(LOCATIONS)
    day=random.randint(1,90)
    team=random.choice(SURV_TEAMS)
    conf=random.choice(["Low","Medium"])
    note=random.choice(["Routine patrol near {loc}; no suspicious activity observed.","Responded to public disturbance call near {loc}; resolved on-site.","Logged stationary vehicle near {loc}; owner traced, no concern."]).format(loc=loc)
    note=f"{random.randint(6,22):02d}{random.randint(0,59):02d} hrs - {note}"
    surv_rows.append({"report_id":f"SURV{surv_counter:04d}","date":(START_DATE+datetime.timedelta(days=day-1)).isoformat(),"day":day,"team":team,"location":loc,"confidence":conf,"activity_notes":note,"ground_truth_flag":"noise"})
random.shuffle(surv_rows)

# Intelligence reports
intel_rows=[]
intel_counter=0
RELIABILITY=["A1 (High)","B2 (Usually reliable / Probably true)","C3 (Fairly reliable / Possibly true)","D4 (Not usually reliable / Doubtful)","F6 (Cannot be judged)"]
for ev in events:
    for _ in range(random.randint(10,14)):
        intel_counter+=1
        kp=node_by_id[ev["core_participants"][0]]["name"]
        day=random.randint(ev["day"]-12, ev["day"]-1)
        rel=random.choice(RELIABILITY)
        mentioned=random.choice(ev["core_participants"])
        narrative=f"Source indicates {kp}'s network {random.choice(['may be preparing for upcoming operation','has been in unusual contact with associates outside its usual circle','shows increased compartmentalization'])} around {random.choice(LOCATIONS)}."
        intel_rows.append({"report_id":f"INTEL{intel_counter:04d}","date":(START_DATE+datetime.timedelta(days=day-1)).isoformat(),"day":day,"source_reliability":rel,"narrative":narrative,"mentioned_entity_ids":mentioned,"ground_truth_flag":f"relevant_cell_{ev['cell']}"})
for _ in range(26):
    intel_counter+=1
    day=random.randint(1,90)
    rel=random.choice(RELIABILITY)
    narrative=random.choice(["Source reports minor unrest expected around local event; no criminal linkage identified.","Unconfirmed rumor of burglary ring near Eastgate, unrelated to networks under investigation.","Informant claims shopkeeper dispute may escalate; no organized crime linkage suspected."])
    intel_rows.append({"report_id":f"INTEL{intel_counter:04d}","date":(START_DATE+datetime.timedelta(days=day-1)).isoformat(),"day":day,"source_reliability":rel,"narrative":narrative,"mentioned_entity_ids":"","ground_truth_flag":"noise"})
random.shuffle(intel_rows)

# Criminal history
crim_rows=[]
crim_counter=0
def random_dob():
    y=random.randint(1970,2005); m=random.randint(1,12); d=random.randint(1,28)
    return f"{y}-{m:02d}-{d:02d}"
GANG_MAP={"D":"D-Cell Syndicate","E":"E-Smuggling Outfit","F":"F-Extortion Crew","Bridge":"Bridge Network","Noise":"Unaffiliated"}
for p in nodes:
    crim_counter+=1
    alias=""
    if random.random()<0.3:
        alias=p["name"].split()[0]
    priors=[]
    for _ in range(random.randint(1,3)):
        yr=random.randint(2015,2025)
        ipc=random.choice(["IPC 384 (Extortion)","IPC 506 (Criminal intimidation)","IPC 379 (Theft)","Arms Act Section 25","NDPS Act Section 8/20","IPC 120B (criminal conspiracy)","Excise Act violation"])
        priors.append(f"{yr} - {ipc}")
    crim_rows.append({"record_id":f"CRIM{crim_counter:04d}","person_id":p["id"],"name":p["name"],"alias":alias,"dob":random_dob(),"prior_offences":"; ".join(priors),"gang_affiliation":GANG_MAP.get(p["cell"],GANG_MAP["Bridge"]),"known_address":f"{random.randint(10,200)}, {random.choice(LOCATIONS)}","ground_truth_flag":f"network_{p['cell']}"})
for p in random.sample(noise_people, 18):
    crim_counter+=1
    crim_rows.append({"record_id":f"CRIM{crim_counter:04d}","person_id":p["id"],"name":p["name"],"alias":"","dob":random_dob(),"prior_offences":random.choice(["2023 - IPC 506 (Criminal intimidation)","2022 - IPC 379 (Theft)","2021 - Excise Act violation"]),"gang_affiliation":"Unaffiliated","known_address":f"{random.randint(10,200)}, {random.choice(LOCATIONS)}","ground_truth_flag":"noise"})
random.shuffle(crim_rows)

# Write
def write_csv(path, rows):
    if not rows: return
    with open(path,"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

write_csv(OUT_DIR / "cdrs.csv", cdr_rows)
write_csv(OUT_DIR / "transactions.csv", txn_rows)
write_csv(OUT_DIR / "firs.csv", fir_rows)
write_csv(OUT_DIR / "social_posts.csv", social_rows)
write_csv(OUT_DIR / "surveillance_reports.csv", surv_rows)
write_csv(OUT_DIR / "intelligence_reports.csv", intel_rows)
write_csv(OUT_DIR / "criminal_history.csv", crim_rows)
with open(OUT_DIR / "people_directory.json","w") as f:
    json.dump({"network_people": nodes, "noise_people": noise_people}, f, indent=2)
with open(OUT_DIR / "alias_map.json","w") as f:
    json.dump(alias_map,f,indent=2,sort_keys=True)
with open(OUT_DIR / "photo_manifest.json","w") as f:
    json.dump(photo_manifest,f,indent=2,sort_keys=True)
# rebuild face embeddings for big? No, original embeddings already cover UUIDs; big uses same files so same embeddings will work via fallback. But we note mapping.
with open(OUT_DIR / "README.md","w") as f:
    f.write(f"""# Synthetic Big v2 — Fresh Network D/E/F/Y/M (~2x)
Generated {datetime.datetime.now().isoformat()}
Network: {len(nodes)} (D/E/F/Y) + Noise: {len(noise_people)} = {len(nodes)+len(noise_people)}
Photos: {len(photo_manifest)} mapped (UUID orphans first)
CDRs: {len(cdr_rows)}
Transactions: {len(txn_rows)} (flagged: {sum(1 for t in txn_rows if t['ground_truth_flag']!='none')})
FIRs: {len(fir_rows)} (noise: {sum(1 for f in fir_rows if f['ground_truth_flag']=='noise')})
Social: {len(social_rows)} (noise: {sum(1 for s in social_rows if s['ground_truth_flag']=='noise')})
Surveillance: {len(surv_rows)} (noise: {sum(1 for s in surv_rows if s['ground_truth_flag']=='noise')})
Intel: {len(intel_rows)} (noise: {sum(1 for s in intel_rows if s['ground_truth_flag']=='noise')})
Criminal History: {len(crim_rows)} (noise: {sum(1 for s in crim_rows if s['ground_truth_flag']=='noise')})
Aliases: {len(alias_map)}
Ground truth: ground_truth_network_big.json with {len(edges)} edges
IDs: D1-D24, E1-E24, F1-F24, Y1-Y6, M1-M35 (fresh, no collision with A/B/C/X/N)
Phones: 70100xxxx, Accounts: AC0010xxxx (fresh block)
To test: python -m backend.loader --data-dir data/synthetic_big_v2 --out-dir output_big ; python -m backend.pipeline --data-dir data/synthetic_big_v2
Or copy: cp data/synthetic_big_v2/*.csv data/ ; cp data/synthetic_big_v2/people_directory.json data/
""")

print(f"People (network): {len(nodes)}   Noise people: {len(noise_people)}")
print(f"CDRs: {len(cdr_rows)}")
print(f"Transactions: {len(txn_rows)}  (suspicious/structuring: {sum(1 for t in txn_rows if t['ground_truth_flag']!='none')})")
print(f"FIRs: {len(fir_rows)}  (noise: {sum(1 for f in fir_rows if f['ground_truth_flag']=='noise')})")
print(f"Social posts: {len(social_rows)}  (noise: {sum(1 for s in social_rows if s['ground_truth_flag']=='noise')})")
print(f"Surveillance: {len(surv_rows)}  (noise: {sum(1 for s in surv_rows if s['ground_truth_flag']=='noise')})")
print(f"Intel: {len(intel_rows)}  (noise: {sum(1 for s in intel_rows if s['ground_truth_flag']=='noise')})")
print(f"Criminal History: {len(crim_rows)}  (noise: {sum(1 for s in crim_rows if s['ground_truth_flag']=='noise')})")
print(f"Aliases: {len(alias_map)}")
print(f"Photo manifest: {len(photo_manifest)}")
