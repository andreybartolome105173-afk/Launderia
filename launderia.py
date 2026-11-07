import os
import json
import secrets
import hashlib
from datetime import datetime, date, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "laundry_db.json")
CREDS_FILE = os.path.join(BASE_DIR, "credentials.json")   
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")         
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
CALENDAR_ID = "primary"
TIMEZONE = "Asia/Manila"
MAX_PER_SLOT = 3                                           
SLOTS = [f"{h:02d}:00" for h in range(8, 18)]              
STATUSES = ["Pending", "Confirmed", "Washing", "Drying", "Folding",
            "Ready for Pickup", "Completed", "Cancelled"]
WORKER_STATUSES = ["Washing", "Drying", "Folding", "Ready for Pickup"]

# ---------------------------------------------------------------- database
def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100_000).hex()
    return f"{salt}${h}"

def check_pw(pw, stored):
    return hash_pw(pw, stored.split("$")[0]) == stored

def load():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, encoding="utf-8") as f:
            return json.load(f)

    return {
        "next": {"user": 1, "service": 5, "booking": 1, "payment": 1},
        "users": [
            {"id": 0, "username": "admin", "password": hash_pw("admin123"), "role": "admin",
             "name": "System Administrator", "phone": ""},
            {"id": -1, "username": "owner", "password": hash_pw("owner123"), "role": "owner",
             "name": "Shop Owner", "phone": ""},
            {"id": -2, "username": "worker1", "password": hash_pw("worker123"), "role": "worker",
             "name": "Laundry Worker 1", "phone": ""},
        ],
        "services": [
            {"id": 1, "name": "Wash", "desc": "Machine wash with detergent", "price": 40.0},
            {"id": 2, "name": "Dry", "desc": "Machine dry", "price": 30.0},
            {"id": 3, "name": "Fold", "desc": "Sorting and folding", "price": 15.0},
            {"id": 4, "name": "Clean (Wash+Dry+Fold)", "desc": "Complete package", "price": 75.0},
        ],
        "bookings": [], "payments": [], "calendar_log": [],
    }

def save():
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(DB, f, indent=2)

def new_id(kind):
    n = DB["next"][kind]
    DB["next"][kind] += 1
    return n

DB = load()
save()

def ask(prompt, check=None, error="Invalid input.", allow_blank=False):
    while True:
        v = input(prompt).strip()
        if not v and allow_blank:
            return v
        if v and (check is None or check(v)):
            return v
        print(" !", error)

def is_float(v):
    try:
        return float(v) > 0
    except ValueError:
        return False

def is_date(v):
    try:
        return datetime.strptime(v, "%Y-%m-%d").date() >= date.today()
    except ValueError:
        return False

def is_int_id(v):
    return v.lstrip("-").isdigit()

def money(x):
    return f"PHP {x:,.2f}"

def pause():
    input("\nPress Enter to continue...")

def table(rows, headers):
    if not rows:
        print("  (no records)")
        return
    w = [max(len(str(r[i])) for r in rows + [headers]) for i in range(len(headers))]
    line = " | ".join(h.ljust(w[i]) for i, h in enumerate(headers))
    print(line + "\n" + "-" * len(line))
    for r in rows:
        print(" | ".join(str(c).ljust(w[i]) for i, c in enumerate(r)))

def user_by_id(uid):
    return next((u for u in DB["users"] if u["id"] == uid), None)

def service_by_id(sid):
    return next((s for s in DB["services"] if s["id"] == sid), None)

def booking_by_id(bid):
    return next((b for b in DB["bookings"] if b["id"] == bid), None)

_service = None

def get_service():
    """Log in to Google once (OAuth) and return the Calendar API client, or None if not set up."""
    global _service
    if _service:
        return _service
    if not os.path.exists(CREDS_FILE):
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            creds = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES).run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    _service = build("calendar", "v3", credentials=creds)
    return _service

def event_body(b):
    c = user_by_id(b["customer_id"])
    names = ", ".join(service_by_id(s)["name"] for s in b["services"] if service_by_id(s))
    start = datetime.strptime(f'{b["date"]} {b["slot"]}', "%Y-%m-%d %H:%M")
    return {
        "summary": f'[{b["status"]}] Laundry #{b["id"]} - {c["name"] if c else "?"}',
        "description": f'Services: {names}\nWeight: {b["kilos"]} kg\nTotal: {money(b["total"])}\n'
                       f'Phone: {c["phone"] if c else ""}',
        "start": {"dateTime": start.isoformat(), "timeZone": TIMEZONE},
        "end": {"dateTime": (start + timedelta(hours=1)).isoformat(), "timeZone": TIMEZONE},
    }

def calendar_sync(b, remove=False):
    """Create / update / delete the booking's Google Calendar event. Always logs the action."""
    if remove or b["status"] == "Cancelled":
        action = "delete"
    else:
        action = "update" if b.get("event_id") else "create"
    synced = False
    try:
        svc = get_service()
        if svc:
            if action == "delete":
                if b.get("event_id"):
                    svc.events().delete(calendarId=CALENDAR_ID, eventId=b["event_id"]).execute()
                    b["event_id"] = ""
            elif action == "update":
                svc.events().update(calendarId=CALENDAR_ID, eventId=b["event_id"],
                                    body=event_body(b)).execute()
            else:
                b["event_id"] = svc.events().insert(calendarId=CALENDAR_ID,
                                                    body=event_body(b)).execute()["id"]
            synced = True
    except Exception as e:
        print(f"  (Google Calendar error: {str(e)[:80]})")
    DB.setdefault("calendar_log", []).append({
        "time": datetime.now().isoformat(timespec="seconds"), "action": action,
        "booking": b["id"], "synced": synced, "title": event_body(b)["summary"]})
    save()

def booking_row(b):
    c = user_by_id(b["customer_id"])
    names = ", ".join(service_by_id(s)["name"] if service_by_id(s) else "?" for s in b["services"])
    return [b["id"], c["name"] if c else "?", names, f'{b["kilos"]}kg',
            f'{b["date"]} {b["slot"]}', b["status"], money(b["total"])]

BK_HEAD = ["ID", "Customer", "Services", "Weight", "Schedule", "Status", "Total"]

def slot_count(d, slot, ignore_id=None):
    return sum(1 for b in DB["bookings"] if b["date"] == d and b["slot"] == slot
               and b["status"] != "Cancelled" and b["id"] != ignore_id)

def pick_booking(rows_source):
    bid = ask("Booking ID: ", str.isdigit, "Enter a number.")
    b = booking_by_id(int(bid))
    if b is None or b not in rows_source:
        print(" ! Booking not found or not allowed.")
        return None
    return b

def register():
    print("\n--- Customer Registration ---")
    username = ask("Username: ", lambda v: v.isalnum() and len(v) >= 4,
                   "Letters/numbers only, at least 4 characters.")
    if any(u["username"] == username for u in DB["users"]):
        print(" ! Username already taken.")
        return
    name = ask("Full name: ", lambda v: len(v) >= 3, "Name too short.")
    phone = ask("Mobile number (09XXXXXXXXX): ",
                lambda v: v.isdigit() and len(v) == 11 and v.startswith("09"),
                "Must be 11 digits starting with 09.")
    pw = ask("Password (min 6 chars): ", lambda v: len(v) >= 6, "Too short.")
    DB["users"].append({"id": new_id("user"), "username": username, "password": hash_pw(pw),
                        "role": "customer", "name": name, "phone": phone})
    save()
    print("Registered! You can now log in.")

def login():
    print("\n--- Login ---")
    u = input("Username: ").strip()
    p = input("Password: ")
    for user in DB["users"]:
        if user["username"] == u and check_pw(p, user["password"]):
            return user
    print(" ! Wrong username or password.")
    return None

def show_services():
    print("\n--- Laundry Services (price per kilo) ---")
    table([[s["id"], s["name"], s["desc"], money(s["price"])] for s in DB["services"]],
          ["ID", "Service", "Description", "Price/kg"])

def add_service():
    name = ask("Service name: ")
    desc = ask("Description: ")
    price = float(ask("Price per kilo: ", is_float, "Enter a positive number."))
    DB["services"].append({"id": new_id("service"), "name": name, "desc": desc, "price": price})
    save()
    print("Service added.")

def update_service():
    show_services()
    s = service_by_id(int(ask("Service ID to update: ", str.isdigit, "Enter a number.")))
    if not s:
        print(" ! Not found.")
        return
    s["name"] = ask(f"Name [{s['name']}]: ", allow_blank=True) or s["name"]
    s["desc"] = ask(f"Description [{s['desc']}]: ", allow_blank=True) or s["desc"]
    p = ask(f"Price [{s['price']}]: ", is_float, "Positive number.", allow_blank=True)
    s["price"] = float(p) if p else s["price"]
    save()
    print("Service updated.")

def delete_service():
    show_services()
    s = service_by_id(int(ask("Service ID to delete: ", str.isdigit, "Enter a number.")))
    if not s:
        print(" ! Not found.")
        return
    if any(s["id"] in b["services"] and b["status"] not in ("Completed", "Cancelled")
           for b in DB["bookings"]):
        print(" ! Service is used by an active booking. Cannot delete.")
        return
    DB["services"].remove(s)
    save()
    print("Service deleted.")

def create_booking(customer):
    show_services()
    ids = ask("Service ID(s), comma-separated: ",
              lambda v: all(x.strip().isdigit() and service_by_id(int(x)) for x in v.split(",")),
              "Choose valid service IDs.")
    sel = sorted({int(x) for x in ids.split(",")})
    kilos = float(ask("Estimated weight (kg): ", is_float, "Enter a positive number."))
    d = ask("Date (YYYY-MM-DD): ", is_date, "Use YYYY-MM-DD, today or later.")
    print("Slots:", "  ".join(f"{s}({MAX_PER_SLOT - slot_count(d, s)} left)" for s in SLOTS))
    slot = ask("Time slot (HH:00): ", lambda v: v in SLOTS, "Pick one of the listed slots.")
    if slot_count(d, slot) >= MAX_PER_SLOT:
        print(" ! That slot is full. Please choose another.")
        return
      total = round(kilos * sum(service_by_id(s)["price"] for s in sel), 2)
    b = {"id": new_id("booking"), "customer_id": customer["id"], "services": sel, "kilos": kilos,
         "date": d, "slot": slot, "status": "Pending", "total": total, "event_id": "",
         "created": datetime.now().isoformat(timespec="seconds")}
    DB["bookings"].append(b)
    save()
    print(f"Booking #{b['id']} created. Estimated total: {money(total)}")
    calendar_sync(b)

def list_bookings(rows, title):
    print(f"\n--- {title} ---")
    table([booking_row(b) for b in rows], BK_HEAD)

def update_booking_customer(customer):
    mine = [b for b in DB["bookings"] if b["customer_id"] == customer["id"] and b["status"] == "Pending"]
    list_bookings(mine, "Your pending bookings")
    if not mine:
        return
    b = pick_booking(mine)
    if not b:
        return
    d = ask(f"New date [{b['date']}]: ", is_date, "YYYY-MM-DD, today or later.", allow_blank=True) or b["date"]
    slot = ask(f"New slot [{b['slot']}]: ", lambda v: v in SLOTS, "Invalid slot.", allow_blank=True) or b["slot"]
    if slot_count(d, slot, b["id"]) >= MAX_PER_SLOT:
        print(" ! That slot is full.")
        return
    b["date"], b["slot"] = d, slot
    save()
    calendar_sync(b)
    print("Booking updated.")

def cancel_booking_customer(customer):
    mine = [b for b in DB["bookings"] if b["customer_id"] == customer["id"] and b["status"] == "Pending"]
    list_bookings(mine, "Your pending bookings")
    if not mine:
        return
    b = pick_booking(mine)
    if b and ask("Delete this booking? (y/n): ", lambda v: v.lower() in ("y", "n")).lower() == "y":
        calendar_sync(b, remove=True)
        DB["bookings"].remove(b)
        save()
        print("Booking deleted.")

def search_bookings(source=None):
    source = DB["bookings"] if source is None else source
    q = ask("Search (customer name, service, status, date or ID): ").lower()
    res = [b for b in source if q in " ".join(map(str, booking_row(b))).lower()]
    list_bookings(res, f"Results for '{q}'")

def sort_bookings(source=None):
    source = DB["bookings"] if source is None else source
    key = ask("Sort by: 1) Date  2) Status  3) Total (high-low)  4) Customer: ",
              lambda v: v in ("1", "2", "3", "4"), "Choose 1-4.")
    keyf = {"1": lambda b: (b["date"], b["slot"]),
            "2": lambda b: STATUSES.index(b["status"]),
            "3": lambda b: -b["total"],
            "4": lambda b: (user_by_id(b["customer_id"]) or {"name": ""})["name"].lower()}[key]
    list_bookings(sorted(source, key=keyf), "Sorted bookings")

def set_status(allowed):
    active = [b for b in DB["bookings"] if b["status"] not in ("Completed", "Cancelled")]
    list_bookings(active, "Active bookings")
    if not active:
        return
    b = pick_booking(active)
    if not b:
        return
    for i, s in enumerate(allowed, 1):
        print(f" {i}. {s}")
    n = ask("New status #: ", lambda v: v.isdigit() and 1 <= int(v) <= len(allowed), "Invalid choice.")
    b["status"] = allowed[int(n) - 1]
    save()
    print("Status updated.")
    calendar_sync(b)

def record_payment():
    ready = [b for b in DB["bookings"] if b["status"] == "Ready for Pickup"]
    list_bookings(ready, "Ready for pickup (awaiting payment)")
    if not ready:
        return
    b = pick_booking(ready)
    if not b:
        return
    print(f"Amount due: {money(b['total'])}")
    amt = float(ask("Amount received: ", is_float, "Positive number."))
    if amt < b["total"]:
        print(" ! Amount is less than total due.")
        return
    DB["payments"].append({"id": new_id("payment"), "booking_id": b["id"], "amount": b["total"],
                           "method": "cash", "date": date.today().isoformat()})  
    b["status"] = "Completed"
    save()
    c = user_by_id(b["customer_id"])
    print("\n------------- OFFICIAL RECEIPT -------------")
    print(f" Booking #{b['id']}   Customer: {c['name'] if c else '?'}")
    print(f" Services : {', '.join(service_by_id(s)['name'] for s in b['services'] if service_by_id(s))}")
    print(f" Weight   : {b['kilos']} kg")
    print(f" Total    : {money(b['total'])}   (Cash)")
    print(f" Received : {money(amt)}   Change: {money(amt - b['total'])}")
    print("--------------------------------------------")
    calendar_sync(b)

def sales_report():
    pays = DB["payments"]
    print("\n--- Sales Report ---")
    print("Today's income      :", money(sum(p["amount"] for p in pays if p["date"] == date.today().isoformat())))
    month = date.today().strftime("%Y-%m")
    print("This month's income :", money(sum(p["amount"] for p in pays if p["date"].startswith(month))))
    print("Total income        :", money(sum(p["amount"] for p in pays)))
    counts = {}
    for p in pays:
        b = booking_by_id(p["booking_id"])
        for s in (b["services"] if b else []):
            sv = service_by_id(s)
            if sv:
                counts[sv["name"]] = counts.get(sv["name"], 0) + 1
    print("\nBookings by status:")
    table([[st, sum(1 for b in DB["bookings"] if b["status"] == st)] for st in STATUSES],
          ["Status", "Count"])
    print("\nMost requested services:")
    table(sorted(([k, v] for k, v in counts.items()), key=lambda r: -r[1]), ["Service", "Times availed"])
    print("\nTransactions:")
    table([[p["id"], p["booking_id"], p["date"], p["method"], money(p["amount"])]
           for p in sorted(pays, key=lambda p: p["date"], reverse=True)],
          ["Pay#", "Booking", "Date", "Method", "Amount"])

def list_users():
    table([[u["id"], u["username"], u["name"], u["role"], u["phone"]] for u in DB["users"]],
          ["ID", "Username", "Name", "Role", "Phone"])

def add_staff():
    username = ask("Username: ", lambda v: v.isalnum() and len(v) >= 4, "Letters/numbers, min 4.")
    if any(u["username"] == username for u in DB["users"]):
        print(" ! Username taken.")
        return
    role = ask("Role (worker/admin/owner): ", lambda v: v in ("worker", "admin", "owner"), "Invalid role.")
    name = ask("Full name: ")
    pw = ask("Password (min 6): ", lambda v: len(v) >= 6, "Too short.")
    DB["users"].append({"id": new_id("user"), "username": username, "password": hash_pw(pw),
                        "role": role, "name": name, "phone": ""})
    save()
    print("Account created.")

def update_user():
    list_users()
    u = user_by_id(int(ask("User ID: ", is_int_id, "Enter a number.")))
    if not u:
        print(" ! Not found.")
        return
    u["name"] = ask(f"Name [{u['name']}]: ", allow_blank=True) or u["name"]
    ph = ask(f"Phone [{u['phone']}]: ", lambda v: v.isdigit() and len(v) == 11, "11 digits.", allow_blank=True)
    u["phone"] = ph or u["phone"]
    pw = ask("New password (Enter to keep): ", lambda v: len(v) >= 6, "Min 6.", allow_blank=True)
    if pw:
        u["password"] = hash_pw(pw)
    save()
    print("User updated.")

def delete_user(me):
    list_users()
    u = user_by_id(int(ask("User ID to delete: ", is_int_id, "Enter a number.")))
    if not u or u["id"] == me["id"]:
        print(" ! Not found / cannot delete yourself.")
        return
    if any(b["customer_id"] == u["id"] and b["status"] not in ("Completed", "Cancelled")
           for b in DB["bookings"]):
        print(" ! User has active bookings.")
        return
    DB["users"].remove(u)
    save()
    print("User deleted.")

def admin_cancel_booking():
    list_bookings(DB["bookings"], "All bookings")
    b = pick_booking(DB["bookings"])
    if not b:
        return
    if b["status"] == "Completed":
        print(" ! Completed bookings are sales records and cannot be changed/deleted.")
        return
    b["status"] = "Cancelled"
    save()
    calendar_sync(b)
    print("Booking cancelled.")

def view_calendar_log():
    table([[m["time"], m["action"], m["booking"], "synced" if m["synced"] else "saved only", m["title"][:40]]
           for m in DB.get("calendar_log", [])[-15:]],
          ["Time", "Action", "Booking", "Google Calendar", "Event"])

def run_menu(title, items):
    while True:
        print(f"\n===== {title} =====")
        for i, (label, _) in enumerate(items, 1):
            print(f" {i}. {label}")
        print(" 0. Logout")
        c = input("Choose: ").strip()
        if c == "0":
            return
        if c.isdigit() and 1 <= int(c) <= len(items):
            items[int(c) - 1][1]()
            pause()
        else:
            print(" ! Invalid choice.")

def customer_menu(me):
    mine = lambda: [b for b in DB["bookings"] if b["customer_id"] == me["id"]]
    run_menu(f"CUSTOMER - {me['name']}", [
        ("View services and prices", show_services),
        ("Make a reservation", lambda: create_booking(me)),
        ("View my bookings / track status", lambda: list_bookings(mine(), "My bookings")),
        ("Search my bookings", lambda: search_bookings(mine())),
        ("Sort my bookings", lambda: sort_bookings(mine())),
        ("Reschedule a pending booking", lambda: update_booking_customer(me)),
        ("Delete a pending booking", lambda: cancel_booking_customer(me)),
    ])

def worker_menu(me):
    run_menu(f"WORKER - {me['name']}", [
        ("View all orders", lambda: list_bookings(DB["bookings"], "All orders")),
        ("Search orders", search_bookings),
        ("Sort orders", sort_bookings),
        ("Update order progress", lambda: set_status(WORKER_STATUSES)),
        ("Record payment (complete order)", record_payment),
    ])

def owner_menu(me):
    run_menu(f"OWNER - {me['name']}", [
        ("Sales report", sales_report),
        ("View all bookings", lambda: list_bookings(DB["bookings"], "All bookings")),
        ("Search bookings", search_bookings),
        ("Sort bookings", sort_bookings),
        ("View services", show_services),
    ])

def admin_menu(me):
    run_menu(f"ADMIN - {me['name']}", [
        ("View all bookings", lambda: list_bookings(DB["bookings"], "All bookings")),
        ("Search bookings", search_bookings),
        ("Sort bookings", sort_bookings),
        ("Confirm / update booking status", lambda: set_status(STATUSES[1:6])),
        ("Cancel a booking", admin_cancel_booking),
        ("Record payment", record_payment),
        ("Sales report", sales_report),
        ("View services", show_services),
        ("Add service", add_service),
        ("Update service / price", update_service),
        ("Delete service", delete_service),
        ("View users", list_users),
        ("Add staff account", add_staff),
        ("Update user", update_user),
        ("Delete user", lambda: delete_user(me)),
        ("View Google Calendar sync log", view_calendar_log),
    ])

MENUS = {"customer": customer_menu, "worker": worker_menu, "owner": owner_menu, "admin": admin_menu}

def main():
    while True:
        print("\n=== LAUNDERIA EXPRESS LAUNDRY ===\nBurgos Ave., Cabanatuan City, Nueva Ecija")
        print(" 1. Login\n 2. Register (customer)\n 3. View services and prices\n 0. Exit")
        c = input("Choose: ").strip()
        if c == "1":
            u = login()
            if u:
                MENUS[u["role"]](u)
        elif c == "2":
            register()
        elif c == "3":
            show_services()
            pause()
        elif c == "0":
            break
        else:
            print(" ! Invalid choice.")

if __name__ == "__main__":
    main()
