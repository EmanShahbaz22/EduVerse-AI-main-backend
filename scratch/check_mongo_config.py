import motor.motor_asyncio
import asyncio
import json
import os
from dotenv import load_dotenv

async def check_mongo():
    load_dotenv()
    mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    db_name = os.getenv("MONGO_DB", "eduverse")
    
    client = motor.motor_asyncio.AsyncIOMotorClient(mongo_uri)
    db = client[db_name]
    config_col = db["config"]
    
    doc = await config_col.find_one({"_id": "active_worker_model"})
    print(f"Config Document: {json.dumps(doc, default=str)}")
    
    if not doc:
        print("MISSING: active_worker_model document in config collection.")
    else:
        print(f"Value: {doc.get('value')}")

if __name__ == "__main__":
    asyncio.run(check_mongo())
