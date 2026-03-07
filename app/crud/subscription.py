# app/crud/subscription.py
from typing import List, Optional
from app.db.database import db
from app.schemas.subscription import Subscription
from bson import ObjectId
from datetime import datetime

# Convert MongoDB _id to string
def convert_id(doc):
    if not doc:
        return None
    if "_id" in doc:
        doc["id"] = str(doc["_id"])
    return doc

# Convert ISO strings to datetime if they come as strings
def parse_datetime(sub_dict: dict):
    expiry = sub_dict.get("expiry_date")
    if isinstance(expiry, str):
        sub_dict["expiry_date"] = datetime.fromisoformat(expiry.replace("Z", "+00:00"))

    for ph in sub_dict.get("payment_history", []):
        d = ph.get("date") # Schema uses 'date'
        if isinstance(d, str):
            ph["date"] = datetime.fromisoformat(d.replace("Z", "+00:00"))

    return sub_dict

async def fetch_subscriptions(tenant_id: str | None = None):
    query = {"tenantId": tenant_id} if tenant_id else {}
    subs = await db.subscriptions.find(query).to_list(100)
    return [convert_id(sub) for sub in subs]

async def fetch_subscription_by_tenant(tenant_id: str):
    sub = await db.subscriptions.find_one({"tenantId": tenant_id})
    return convert_id(sub)

async def create_subscription(sub: Subscription):
    sub_dict = sub.dict()
    sub_dict.pop("id", None)
    sub_dict = parse_datetime(sub_dict)
    
    # Ensure nested datetimes in payment_history are handled
    if sub_dict.get("payment_history"):
        for ph in sub_dict["payment_history"]:
            if isinstance(ph.get("date"), str):
                ph["date"] = datetime.fromisoformat(ph["date"].replace("Z", "+00:00"))

    result = await db.subscriptions.insert_one(sub_dict)
    inserted_sub = await db.subscriptions.find_one({"_id": result.inserted_id})
    return convert_id(inserted_sub)

async def update_subscription(tenant_id: str, sub: Subscription):
    sub_dict = sub.dict(exclude_unset=True)
    sub_dict = parse_datetime(sub_dict)
    
    result = await db.subscriptions.update_one(
        {"tenantId": tenant_id},
        {"$set": sub_dict}
    )
    if result.matched_count == 0:
        return None
    updated_sub = await db.subscriptions.find_one({"tenantId": tenant_id})
    return convert_id(updated_sub)

async def delete_subscription(tenant_id: str):
    result = await db.subscriptions.delete_one({"tenantId": tenant_id})
    return result.deleted_count > 0
