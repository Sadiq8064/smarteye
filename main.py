from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
import uuid, json, os, requests, jwt, datetime
from threading import Lock

app = FastAPI()

# ================= FILE SETUP =================
DATA_DIR = "data"
BLINDS_FILE = f"{DATA_DIR}/blinds.json"
GUARDIANS_FILE = f"{DATA_DIR}/guardians.json"

os.makedirs(DATA_DIR, exist_ok=True)
file_lock = Lock()
active_connections = {}

# ================= COURIER CONFIG =================
COURIER_AUTH_TOKEN = "pk_test_TN18M50G4S4H77JB1XW3ZR9DMB8W"
JWT_SECRET = "super_secret_jwt_key_change_later_please_12345"

# ================= UTIL FUNCTIONS =================

def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)

def save_json(path, data):
    with file_lock:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

def generate_courier_jwt(user_id: str):
    payload = {
        "sub": user_id,
        "iat": datetime.datetime.utcnow(),
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1),
        "scope": "inbox:read inbox:write"
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def send_courier_notification(user_ids, title, body, data=None):
    if not user_ids:
        return

    url = "https://api.courier.com/send"
    headers = {
        "Authorization": f"Bearer {COURIER_AUTH_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "message": {
            "to": {"user_ids": user_ids},
            "content": {
                "title": title,
                "body": body
            },
            "data": data or {}
        }
    }

    requests.post(url, headers=headers, json=payload)

# =================================================
# ================= BLIND APIs ====================
# =================================================

@app.get("/blind/login")
def blind_login(device_id: str):
    blinds = load_json(BLINDS_FILE)
    for blind in blinds.values():
        if blind["device_id"] == device_id:
            return {"exists": True, "data": blind}
    return {"exists": False}

@app.get("/blind/register")
def blind_register(name: str, device_id: str, latitude: float, longitude: float):
    blinds = load_json(BLINDS_FILE)

    blind_id = str(uuid.uuid4())
    blind_code = str(uuid.uuid4())

    blinds[blind_id] = {
        "id": blind_id,
        "code": blind_code,
        "name": name,
        "device_id": device_id,
        "latitude": latitude,
        "longitude": longitude,
        "active": False,
        "guardians": []
    }

    save_json(BLINDS_FILE, blinds)
    return {"success": True, "blind_id": blind_id, "blind_code": blind_code}

@app.get("/blind/guardians")
def get_blind_guardians(blind_id: str):
    blinds = load_json(BLINDS_FILE)
    guardians = load_json(GUARDIANS_FILE)

    if blind_id not in blinds:
        return {"error": "Blind not found"}

    return [
        {
            "id": guardians[g]["id"],
            "name": guardians[g]["name"],
            "phone": guardians[g]["phone"]
        }
        for g in blinds[blind_id]["guardians"]
        if g in guardians
    ]

@app.get("/blind/remove-guardian")
def blind_remove_guardian(blind_id: str, guardian_id: str):
    blinds = load_json(BLINDS_FILE)
    guardians = load_json(GUARDIANS_FILE)

    if blind_id not in blinds or guardian_id not in guardians:
        return {"error": "Invalid ID"}

    blinds[blind_id]["guardians"].remove(guardian_id)
    guardians[guardian_id]["blind_persons"].remove(blind_id)

    save_json(BLINDS_FILE, blinds)
    save_json(GUARDIANS_FILE, guardians)
    return {"success": True}

@app.get("/blind/delete")
def delete_blind(blind_id: str):
    blinds = load_json(BLINDS_FILE)
    guardians = load_json(GUARDIANS_FILE)

    for g in guardians.values():
        if blind_id in g["blind_persons"]:
            g["blind_persons"].remove(blind_id)

    blinds.pop(blind_id, None)

    save_json(BLINDS_FILE, blinds)
    save_json(GUARDIANS_FILE, guardians)
    return {"success": True}

# ---------- Blind Helper (SEND NOTIFICATION) ----------
@app.get("/blind/helper")
def blind_helper(blind_id: str):
    blinds = load_json(BLINDS_FILE)
    guardians = load_json(GUARDIANS_FILE)

    if blind_id not in blinds:
        return {"error": "Blind not found"}

    guardian_users = [
        f"guardian_{gid}"
        for gid in blinds[blind_id]["guardians"]
        if gid in guardians
    ]

    send_courier_notification(
        guardian_users,
        title="🚨 Help Needed",
        body=f"{blinds[blind_id]['name']} needs your help!",
        data={
            "blind_id": blind_id,
            "latitude": blinds[blind_id]["latitude"],
            "longitude": blinds[blind_id]["longitude"]
        }
    )

    return {"success": True, "notified": len(guardian_users)}

@app.websocket("/ws/blind/{blind_id}")
async def blind_ws(ws: WebSocket, blind_id: str):
    await ws.accept()
    active_connections[blind_id] = ws

    blinds = load_json(BLINDS_FILE)
    blinds[blind_id]["active"] = True
    save_json(BLINDS_FILE, blinds)

    try:
        while True:
            data = await ws.receive_json()
            blinds[blind_id]["latitude"] = data["latitude"]
            blinds[blind_id]["longitude"] = data["longitude"]
            save_json(BLINDS_FILE, blinds)
    except WebSocketDisconnect:
        blinds[blind_id]["active"] = False
        save_json(BLINDS_FILE, blinds)

# =================================================
# ================= GUARDIAN APIs =================
# =================================================

@app.get("/guardian/login")
def guardian_login(phone: str):
    guardians = load_json(GUARDIANS_FILE)
    for guardian in guardians.values():
        if guardian["phone"] == phone:
            return {"exists": True, "data": guardian}
    return {"exists": False}

@app.get("/guardian/register")
def guardian_register(name: str, phone: str):
    guardians = load_json(GUARDIANS_FILE)

    guardian_id = str(uuid.uuid4())
    guardians[guardian_id] = {
        "id": guardian_id,
        "name": name,
        "phone": phone,
        "blind_persons": []
    }

    save_json(GUARDIANS_FILE, guardians)
    return {"success": True, "guardian_id": guardian_id}

@app.get("/guardian/blinds")
def get_guardian_blinds(guardian_id: str):
    guardians = load_json(GUARDIANS_FILE)
    blinds = load_json(BLINDS_FILE)

    return [
        {
            "id": blinds[b]["id"],
            "name": blinds[b]["name"],
            "active": blinds[b]["active"],
            "latitude": blinds[b]["latitude"],
            "longitude": blinds[b]["longitude"]
        }
        for b in guardians.get(guardian_id, {}).get("blind_persons", [])
        if b in blinds
    ]

@app.get("/guardian/add-blind")
def guardian_add_blind(guardian_id: str, blind_code: str):
    blinds = load_json(BLINDS_FILE)
    guardians = load_json(GUARDIANS_FILE)

    blind = next((b for b in blinds.values() if b["code"] == blind_code), None)
    if not blind:
        return {"error": "Invalid blind code"}

    blind["guardians"].append(guardian_id)
    guardians[guardian_id]["blind_persons"].append(blind["id"])

    save_json(BLINDS_FILE, blinds)
    save_json(GUARDIANS_FILE, guardians)
    return {"success": True}

@app.get("/guardian/remove-blind")
def guardian_remove_blind(guardian_id: str, blind_id: str):
    blinds = load_json(BLINDS_FILE)
    guardians = load_json(GUARDIANS_FILE)

    guardians[guardian_id]["blind_persons"].remove(blind_id)
    blinds[blind_id]["guardians"].remove(guardian_id)

    save_json(BLINDS_FILE, blinds)
    save_json(GUARDIANS_FILE, guardians)
    return {"success": True}

@app.get("/guardian/delete")
def delete_guardian(guardian_id: str):
    blinds = load_json(BLINDS_FILE)
    guardians = load_json(GUARDIANS_FILE)

    for b in blinds.values():
        if guardian_id in b["guardians"]:
            b["guardians"].remove(guardian_id)

    guardians.pop(guardian_id, None)

    save_json(BLINDS_FILE, blinds)
    save_json(GUARDIANS_FILE, guardians)
    return {"success": True}

@app.websocket("/ws/guardian/track/{blind_id}")
async def guardian_track(ws: WebSocket, blind_id: str):
    await ws.accept()
    try:
        while True:
            blinds = load_json(BLINDS_FILE)
            await ws.send_json(blinds.get(blind_id))
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
