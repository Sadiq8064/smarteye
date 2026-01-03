from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
import uuid, json, os, requests
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime
import certifi
import hashlib
import re
from typing import Optional

app = FastAPI()

# ================= MONGODB SETUP =================
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb+srv://wisdomkagyan_db_user:gqbCoXr99sKOcXEw@cluster0.itxqujm.mongodb.net/?appName=Cluster0")
client = MongoClient(MONGODB_URI, tlsCAFile=certifi.where())
db = client.smarteye

# Collections
blinds_collection = db.blinds
guardians_collection = db.guardians

# Create indexes
blinds_collection.create_index("email", unique=True)
blinds_collection.create_index("code", unique=True)
guardians_collection.create_index("email", unique=True)
guardians_collection.create_index("phone", unique=True)

# ================= ONESIGNAL CONFIG =================
ONESIGNAL_APP_ID = os.getenv("ONESIGNAL_APP_ID")
ONESIGNAL_API_KEY = os.getenv("ONESIGNAL_API_KEY")

# ================= UTIL FUNCTIONS =================

def hash_password(password: str) -> str:
    """Hash password using SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against hash"""
    return hash_password(plain_password) == hashed_password

def is_valid_email(email: str) -> bool:
    """Basic email validation"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def is_valid_phone(phone: str) -> bool:
    """Basic phone validation (10-15 digits)"""
    pattern = r'^\+?[1-9]\d{9,14}$'
    return re.match(pattern, phone) is not None

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
# ================= USER AUTH APIs ================
# =================================================

@app.post("/auth/login")
def login(email: str, password: str, user_type: str = "blind"):
    """Login for both blind users and guardians"""
    
    if not is_valid_email(email):
        raise HTTPException(status_code=400, detail="Invalid email format")
    
    if user_type == "blind":
        collection = blinds_collection
    elif user_type == "guardian":
        collection = guardians_collection
    else:
        raise HTTPException(status_code=400, detail="Invalid user type. Use 'blind' or 'guardian'")
    
    # Find user by email
    user = collection.find_one({"email": email})
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Verify password
    if not verify_password(password, user["password"]):
        raise HTTPException(status_code=401, detail="Incorrect password")
    
    # Remove password from response
    user_data = serialize_doc(user)
    user_data.pop("password", None)
    
    return {
        "success": True,
        "message": "Login successful",
        "data": user_data
    }

# =================================================
# ================= BLIND APIs ====================
# =================================================

@app.post("/blind/register")
def blind_register(
    name: str, 
    email: str, 
    password: str,
    latitude: float = 0.0,
    longitude: float = 0.0
):
    """Register a new blind user with email, password, name, and location"""
    
    # Validate inputs
    if not is_valid_email(email):
        raise HTTPException(status_code=400, detail="Invalid email format")
    
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    
    # Check if email already exists
    existing = blinds_collection.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    blind_id = str(uuid.uuid4())
    blind_code = str(uuid.uuid4())
    
    blind_data = {
        "name": name,
        "email": email,
        "password": hash_password(password),
        "code": blind_code,
        "latitude": latitude,
        "longitude": longitude,
        "active": False,
        "guardians": [],  # List of guardian IDs
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    result = blinds_collection.insert_one(blind_data)
    
    return {
        "success": True,
        "blind_id": str(result.inserted_id),
        "blind_code": blind_code,
        "message": "Registration successful"
    }

@app.get("/blind/profile")
def get_blind_profile(blind_id: str):
    """Get blind user profile"""
    try:
        blind_obj_id = ObjectId(blind_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid blind ID format")
    
    blind = blinds_collection.find_one({"_id": blind_obj_id})
    if not blind:
        raise HTTPException(status_code=404, detail="Blind user not found")
    
    # Remove sensitive data
    blind_data = serialize_doc(blind)
    blind_data.pop("password", None)
    
    return {"success": True, "data": blind_data}

@app.put("/blind/update")
def update_blind_profile(
    blind_id: str,
    name: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None
):
    """Update blind user profile"""
    try:
        blind_obj_id = ObjectId(blind_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid blind ID format")
    
    # Build update data
    update_data = {"updated_at": datetime.utcnow()}
    
    if name:
        update_data["name"] = name
    if latitude is not None:
        update_data["latitude"] = latitude
    if longitude is not None:
        update_data["longitude"] = longitude
    
    result = blinds_collection.update_one(
        {"_id": blind_obj_id},
        {"$set": update_data}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Blind user not found or no changes made")
    
    return {"success": True, "message": "Profile updated successfully"}

@app.get("/blind/guardians")
def get_blind_guardians(blind_id: str):
    """Get all guardians associated with a blind user"""
    try:
        blind_obj_id = ObjectId(blind_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid blind ID format")
    
    blind = blinds_collection.find_one({"_id": blind_obj_id})
    if not blind:
        raise HTTPException(status_code=404, detail="Blind user not found")
    
    guardian_ids = [ObjectId(gid) for gid in blind.get("guardians", [])]
    guardians = list(guardians_collection.find({"_id": {"$in": guardian_ids}}))
    
    # Remove sensitive data
    guardian_list = []
    for guardian in guardians:
        guardian_data = serialize_doc(guardian)
        guardian_data.pop("password", None)
        guardian_list.append({
            "id": guardian_data["id"],
            "name": guardian_data["name"],
            "email": guardian_data["email"],
            "phone": guardian_data["phone"]
        })
    
    return guardian_list

@app.get("/blind/remove-guardian")
def blind_remove_guardian(blind_id: str, guardian_id: str):
    """Remove a guardian from blind user's guardians list"""
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
    
    return {"success": True, "message": "Guardian removed successfully"}

@app.get("/blind/delete")
def delete_blind(blind_id: str):
    """Delete blind user account"""
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
    
    return {"success": True, "message": "Account deleted successfully"}

@app.get("/blind/helper")
def blind_helper(blind_id: str):
    """Send SOS notification to all guardians"""
    try:
        blind_obj_id = ObjectId(blind_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid blind ID format")
    
    blind = blinds_collection.find_one({"_id": blind_obj_id})
    if not blind:
        raise HTTPException(status_code=404, detail="Blind user not found")
    
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

    return {"success": True, "notified": len(guardian_users), "message": "SOS sent to guardians"}

@app.websocket("/ws/blind/{blind_id}")
async def blind_ws(ws: WebSocket, blind_id: str):
    """WebSocket for real-time location updates"""
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

@app.post("/guardian/register")
def guardian_register(
    name: str, 
    email: str, 
    password: str,
    phone: str
):
    """Register a new guardian with name, email, password, and phone"""
    
    # Validate inputs
    if not is_valid_email(email):
        raise HTTPException(status_code=400, detail="Invalid email format")
    
    if not is_valid_phone(phone):
        raise HTTPException(status_code=400, detail="Invalid phone number format")
    
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    
    # Check if email already exists
    existing = guardians_collection.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Check if phone already exists
    existing_phone = guardians_collection.find_one({"phone": phone})
    if existing_phone:
        raise HTTPException(status_code=400, detail="Phone number already registered")
    
    guardian_data = {
        "name": name,
        "email": email,
        "password": hash_password(password),
        "phone": phone,
        "blind_persons": [],  # List of blind user IDs
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    result = guardians_collection.insert_one(guardian_data)
    
    return {
        "success": True,
        "guardian_id": str(result.inserted_id),
        "message": "Registration successful"
    }

@app.get("/guardian/profile")
def get_guardian_profile(guardian_id: str):
    """Get guardian profile"""
    try:
        guardian_obj_id = ObjectId(guardian_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid guardian ID format")
    
    guardian = guardians_collection.find_one({"_id": guardian_obj_id})
    if not guardian:
        raise HTTPException(status_code=404, detail="Guardian not found")
    
    # Remove sensitive data
    guardian_data = serialize_doc(guardian)
    guardian_data.pop("password", None)
    
    return {"success": True, "data": guardian_data}

@app.put("/guardian/update")
def update_guardian_profile(
    guardian_id: str,
    name: Optional[str] = None,
    phone: Optional[str] = None
):
    """Update guardian profile"""
    try:
        guardian_obj_id = ObjectId(guardian_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid guardian ID format")
    
    # Build update data
    update_data = {"updated_at": datetime.utcnow()}
    
    if name:
        update_data["name"] = name
    if phone:
        if not is_valid_phone(phone):
            raise HTTPException(status_code=400, detail="Invalid phone number format")
        # Check if phone already exists (excluding current user)
        existing = guardians_collection.find_one({
            "phone": phone,
            "_id": {"$ne": guardian_obj_id}
        })
        if existing:
            raise HTTPException(status_code=400, detail="Phone number already in use")
        update_data["phone"] = phone
    
    result = guardians_collection.update_one(
        {"_id": guardian_obj_id},
        {"$set": update_data}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Guardian not found or no changes made")
    
    return {"success": True, "message": "Profile updated successfully"}

@app.get("/guardian/blinds")
def get_guardian_blinds(guardian_id: str):
    """Get all blind users associated with a guardian"""
    try:
        guardian_obj_id = ObjectId(guardian_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid guardian ID format")
    
    guardian = guardians_collection.find_one({"_id": guardian_obj_id})
    if not guardian:
        raise HTTPException(status_code=404, detail="Guardian not found")
    
    blind_ids = [ObjectId(bid) for bid in guardian.get("blind_persons", [])]
    blinds = list(blinds_collection.find({"_id": {"$in": blind_ids}}))
    
    # Remove sensitive data and format response
    blind_list = []
    for blind in blinds:
        blind_data = serialize_doc(blind)
        blind_data.pop("password", None)
        blind_list.append({
            "id": blind_data["id"],
            "name": blind_data["name"],
            "email": blind_data["email"],
            "active": blind_data.get("active", False),
            "latitude": blind_data["latitude"],
            "longitude": blind_data["longitude"],
            "last_updated": blind_data.get("updated_at"),
            "blind_code": blind_data.get("code")  # Include code for reference
        })
    
    return blind_list

@app.get("/guardian/add-blind")
def guardian_add_blind(guardian_id: str, blind_code: str):
    """Add a blind user using blind code"""
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
        raise HTTPException(status_code=400, detail="Blind user already added")
    
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
    
    return {
        "success": True, 
        "message": "Blind user added successfully",
        "blind_id": blind_id,
        "blind_name": blind["name"]
    }

@app.get("/guardian/remove-blind")
def guardian_remove_blind(guardian_id: str, blind_id: str):
    """Remove a blind user from guardian's list"""
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
    
    return {"success": True, "message": "Blind user removed successfully"}

@app.get("/guardian/delete")
def delete_guardian(guardian_id: str):
    """Delete guardian account"""
    try:
        guardian_obj_id = ObjectId(guardian_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid guardian ID format")
    
    # Get guardian to find their blind users
    guardian = guardians_collection.find_one({"_id": guardian_obj_id})
    if guardian:
        # Remove this guardian from all blind users
        blind_ids = [ObjectId(bid) for bid in guardian.get("blind_persons", [])]
        blinds_collection.update_many(
            {"_id": {"$in": blind_ids}},
            {"$pull": {"guardians": guardian_id}}
        )
    
    # Delete the guardian
    guardians_collection.delete_one({"_id": guardian_obj_id})
    
    return {"success": True, "message": "Account deleted successfully"}

@app.websocket("/ws/guardian/track/{blind_id}")
async def guardian_track(ws: WebSocket, blind_id: str):
    """WebSocket for real-time tracking of blind user"""
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
                blind_data = serialize_doc(blind)
                blind_data.pop("password", None)
                await ws.send_json(blind_data)
            else:
                await ws.send_json({"error": "Blind user not found"})
            
            # Wait for acknowledgment (ping)
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"Tracking WebSocket error: {e}")

# =================================================
# ================= PASSWORD RESET ================
# =================================================

@app.post("/auth/reset-password")
def reset_password(email: str, new_password: str, user_type: str = "blind"):
    """Reset password for both blind users and guardians"""
    
    if not is_valid_email(email):
        raise HTTPException(status_code=400, detail="Invalid email format")
    
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    
    if user_type == "blind":
        collection = blinds_collection
    elif user_type == "guardian":
        collection = guardians_collection
    else:
        raise HTTPException(status_code=400, detail="Invalid user type. Use 'blind' or 'guardian'")
    
    # Find user by email
    user = collection.find_one({"email": email})
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Update password
    hashed_password = hash_password(new_password)
    
    collection.update_one(
        {"_id": user["_id"]},
        {"$set": {"password": hashed_password, "updated_at": datetime.utcnow()}}
    )
    
    return {"success": True, "message": "Password reset successfully"}

# =================================================
# ============ FULL SYSTEM CLEANUP API ============
# =================================================

@app.get("/system/cleanup")
def system_cleanup():
    """Clean all data from the system"""
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
    """Health check endpoint"""
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
