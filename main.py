from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
import uuid, json, os, requests
from threading import Lock
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime
import certifi

app = FastAPI()

# ================= MONGODB SETUP =================
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb+srv://wisdomkagyan_db_user:gqbCoXr99sKOcXEw@cluster0.itxqujm.mongodb.net/?appName=Cluster0")
client = MongoClient(MONGODB_URI, tlsCAFile=certifi.where())
db = client.smarteye

# Collections
blinds_collection = db.blinds
guardians_collection = db.guardians

# Create indexes
blinds_collection.create_index("device_id", unique=True)
blinds_collection.create_index("code", unique=True)
guardians_collection.create_index("phone", unique=True)

# ================= ONESIGNAL CONFIG =================
ONESIGNAL_APP_ID = os.getenv("ONESIGNAL_APP_ID")
ONESIGNAL_API_KEY = os.getenv("ONESIGNAL_API_KEY")

# ================= UTIL FUNCTIONS =================

def serialize_doc(doc):
    """Convert MongoDB document to JSON serializable format"""
    if doc and "_id" in doc:
        doc["id"] = str(doc["_id"])
        del doc["_id"]
    return doc

def send_push_notification(user_ids, title, body, data=None):
    if not user_ids:
        print("❌ SKIPPING PUSH: No user_ids provided.")
        return

    url = "https://onesignal.com/api/v1/notifications"
    
    headers = {
        "Authorization": f"Basic {ONESIGNAL_API_KEY}",
        "Content-Type": "application/json",
        "accept": "application/json"
    }

    payload = {
        "app_id": ONESIGNAL_APP_ID,
        "include_aliases": {
            "external_id": user_ids
        },
        "target_channel": "push",
        "headings": {"en": title},
        "contents": {"en": body},
        "data": data or {}
    }

    print(f"🚀 SENDING PUSH TO ALIASES: {user_ids}")
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        print(f"📨 ONESIGNAL RESPONSE: {response.status_code}")
        print(f"📄 RESPONSE BODY: {response.text}")
    except Exception as e:
        print(f"🔥 ERROR SENDING PUSH: {e}")

# =================================================
# ================= BLIND APIs ====================
# =================================================

@app.get("/blind/login")
def blind_login(device_id: str):
    blind = blinds_collection.find_one({"device_id": device_id})
    if blind:
        return {"exists": True, "data": serialize_doc(blind)}
    return {"exists": False}

@app.get("/blind/register")
def blind_register(name: str, device_id: str, latitude: float, longitude: float):
    # Check if device already registered
    existing = blinds_collection.find_one({"device_id": device_id})
    if existing:
        raise HTTPException(status_code=400, detail="Device already registered")

    blind_id = str(uuid.uuid4())
    blind_code = str(uuid.uuid4())
    
    blind_data = {
        "name": name,
        "device_id": device_id,
        "code": blind_code,
        "latitude": latitude,
        "longitude": longitude,
        "active": False,
        "guardians": [],
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    result = blinds_collection.insert_one(blind_data)
    
    return {
        "success": True,
        "blind_id": str(result.inserted_id),
        "blind_code": blind_code
    }

@app.get("/blind/guardians")
def get_blind_guardians(blind_id: str):
    try:
        blind_obj_id = ObjectId(blind_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid blind ID format")
    
    blind = blinds_collection.find_one({"_id": blind_obj_id})
    if not blind:
        raise HTTPException(status_code=404, detail="Blind not found")
    
    guardian_ids = [ObjectId(gid) for gid in blind.get("guardians", [])]
    guardians = list(guardians_collection.find({"_id": {"$in": guardian_ids}}))
    
    return [
        {
            "id": str(g["_id"]),
            "name": g["name"],
            "phone": g["phone"]
        }
        for g in guardians
    ]

@app.get("/blind/remove-guardian")
def blind_remove_guardian(blind_id: str, guardian_id: str):
    try:
        blind_obj_id = ObjectId(blind_id)
        guardian_obj_id = ObjectId(guardian_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid ID format")
    
    # Remove guardian from blind
    blinds_collection.update_one(
        {"_id": blind_obj_id},
        {"$pull": {"guardians": guardian_id}}
    )
    
    # Remove blind from guardian
    guardians_collection.update_one(
        {"_id": guardian_obj_id},
        {"$pull": {"blind_persons": blind_id}}
    )
    
    return {"success": True}

@app.get("/blind/delete")
def delete_blind(blind_id: str):
    try:
        blind_obj_id = ObjectId(blind_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid blind ID format")
    
    # Get blind to find its guardians
    blind = blinds_collection.find_one({"_id": blind_obj_id})
    if blind:
        # Remove this blind from all guardians
        guardians_collection.update_many(
            {"_id": {"$in": [ObjectId(gid) for gid in blind.get("guardians", [])]}},
            {"$pull": {"blind_persons": blind_id}}
        )
    
    # Delete the blind
    blinds_collection.delete_one({"_id": blind_obj_id})
    
    return {"success": True}

@app.get("/blind/helper")
def blind_helper(blind_id: str):
    try:
        blind_obj_id = ObjectId(blind_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid blind ID format")
    
    blind = blinds_collection.find_one({"_id": blind_obj_id})
    if not blind:
        raise HTTPException(status_code=404, detail="Blind not found")
    
    # Prepare guardian user IDs for push notification
    guardian_users = [
        f"guardian_{gid}"
        for gid in blind.get("guardians", [])
    ]

    send_push_notification(
        guardian_users,
        title="🚨 Help Needed",
        body=f"{blind['name']} needs your help!",
        data={
            "blind_id": blind_id,
            "latitude": blind["latitude"],
            "longitude": blind["longitude"]
        }
    )

    return {"success": True, "notified": len(guardian_users)}

@app.websocket("/ws/blind/{blind_id}")
async def blind_ws(ws: WebSocket, blind_id: str):
    await ws.accept()
    
    try:
        blind_obj_id = ObjectId(blind_id)
    except:
        await ws.close(code=1008, reason="Invalid blind ID")
        return
    
    # Update blind as active
    blinds_collection.update_one(
        {"_id": blind_obj_id},
        {"$set": {"active": True, "updated_at": datetime.utcnow()}}
    )
    
    try:
        while True:
            data = await ws.receive_json()
            # Update location
            blinds_collection.update_one(
                {"_id": blind_obj_id},
                {"$set": {
                    "latitude": data["latitude"],
                    "longitude": data["longitude"],
                    "updated_at": datetime.utcnow()
                }}
            )
    except WebSocketDisconnect:
        # Set blind as inactive
        blinds_collection.update_one(
            {"_id": blind_obj_id},
            {"$set": {"active": False, "updated_at": datetime.utcnow()}}
        )
    except Exception as e:
        print(f"WebSocket error: {e}")

# =================================================
# ================= GUARDIAN APIs =================
# =================================================

@app.get("/guardian/login")
def guardian_login(phone: str):
    guardian = guardians_collection.find_one({"phone": phone})
    if guardian:
        return {"exists": True, "data": serialize_doc(guardian)}
    return {"exists": False}

@app.get("/guardian/register")
def guardian_register(name: str, phone: str):
    # Check if phone already registered
    existing = guardians_collection.find_one({"phone": phone})
    if existing:
        raise HTTPException(status_code=400, detail="Phone number already registered")

    guardian_data = {
        "name": name,
        "phone": phone,
        "blind_persons": [],
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    result = guardians_collection.insert_one(guardian_data)
    
    return {
        "success": True,
        "guardian_id": str(result.inserted_id)
    }

@app.get("/guardian/blinds")
def get_guardian_blinds(guardian_id: str):
    try:
        guardian_obj_id = ObjectId(guardian_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid guardian ID format")
    
    guardian = guardians_collection.find_one({"_id": guardian_obj_id})
    if not guardian:
        raise HTTPException(status_code=404, detail="Guardian not found")
    
    blind_ids = [ObjectId(bid) for bid in guardian.get("blind_persons", [])]
    blinds = list(blinds_collection.find({"_id": {"$in": blind_ids}}))
    
    return [
        {
            "id": str(b["_id"]),
            "name": b["name"],
            "active": b.get("active", False),
            "latitude": b["latitude"],
            "longitude": b["longitude"],
            "last_updated": b.get("updated_at")
        }
        for b in blinds
    ]

@app.get("/guardian/add-blind")
def guardian_add_blind(guardian_id: str, blind_code: str):
    try:
        guardian_obj_id = ObjectId(guardian_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid guardian ID format")
    
    # Find blind by code
    blind = blinds_collection.find_one({"code": blind_code})
    if not blind:
        raise HTTPException(status_code=404, detail="Invalid blind code")
    
    blind_id = str(blind["_id"])
    
    # Check if already added
    guardian = guardians_collection.find_one({"_id": guardian_obj_id})
    if guardian and blind_id in guardian.get("blind_persons", []):
        raise HTTPException(status_code=400, detail="Blind already added")
    
    # Add guardian to blind
    blinds_collection.update_one(
        {"_id": blind["_id"]},
        {"$addToSet": {"guardians": guardian_id}}
    )
    
    # Add blind to guardian
    guardians_collection.update_one(
        {"_id": guardian_obj_id},
        {"$addToSet": {"blind_persons": blind_id}}
    )
    
    return {"success": True}

@app.get("/guardian/remove-blind")
def guardian_remove_blind(guardian_id: str, blind_id: str):
    try:
        guardian_obj_id = ObjectId(guardian_id)
        blind_obj_id = ObjectId(blind_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid ID format")
    
    # Remove blind from guardian
    guardians_collection.update_one(
        {"_id": guardian_obj_id},
        {"$pull": {"blind_persons": blind_id}}
    )
    
    # Remove guardian from blind
    blinds_collection.update_one(
        {"_id": blind_obj_id},
        {"$pull": {"guardians": guardian_id}}
    )
    
    return {"success": True}

@app.get("/guardian/delete")
def delete_guardian(guardian_id: str):
    try:
        guardian_obj_id = ObjectId(guardian_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid guardian ID format")
    
    # Get guardian to find their blinds
    guardian = guardians_collection.find_one({"_id": guardian_obj_id})
    if guardian:
        # Remove this guardian from all blinds
        blind_ids = [ObjectId(bid) for bid in guardian.get("blind_persons", [])]
        blinds_collection.update_many(
            {"_id": {"$in": blind_ids}},
            {"$pull": {"guardians": guardian_id}}
        )
    
    # Delete the guardian
    guardians_collection.delete_one({"_id": guardian_obj_id})
    
    return {"success": True}

@app.websocket("/ws/guardian/track/{blind_id}")
async def guardian_track(ws: WebSocket, blind_id: str):
    await ws.accept()
    
    try:
        blind_obj_id = ObjectId(blind_id)
    except:
        await ws.close(code=1008, reason="Invalid blind ID")
        return
    
    try:
        while True:
            blind = blinds_collection.find_one({"_id": blind_obj_id})
            if blind:
                await ws.send_json(serialize_doc(blind))
            else:
                await ws.send_json({"error": "Blind not found"})
            
            # Wait for acknowledgment (ping)
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"Tracking WebSocket error: {e}")

# =================================================
# ============ FULL SYSTEM CLEANUP API ============
# =================================================

@app.get("/system/cleanup")
def system_cleanup():
    blinds_collection.delete_many({})
    guardians_collection.delete_many({})
    
    return {
        "success": True,
        "message": "⚠️ ALL DATA CLEANED. System reset completed."
    }

# =================================================
# ============ HEALTH CHECK API ============
# =================================================

@app.get("/health")
def health_check():
    try:
        # Test database connection
        db.command("ping")
        return {
            "status": "healthy",
            "database": "connected",
            "blinds_count": blinds_collection.count_documents({}),
            "guardians_count": guardians_collection.count_documents({})
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "database": "disconnected",
            "error": str(e)
        }
