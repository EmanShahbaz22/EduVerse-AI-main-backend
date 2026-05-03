import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from app.utils.security import get_password_hash
from bson import ObjectId

async def create_test_teacher():
    client = AsyncIOMotorClient("mongodb://localhost:27017")
    db = client["eduverse"]
    
    # Check if exists
    user = await db.users.find_one({"email": "test_teacher_rag@example.com"})
    if not user:
        user_id = ObjectId()
        await db.users.insert_one({
            "_id": user_id,
            "fullName": "Test Teacher",
            "email": "test_teacher_rag@example.com",
            "password": get_password_hash("password123"),
            "role": "teacher",
            "status": "active"
        })
        print("Created test teacher in DB.")
    else:
        # Update password just in case
        await db.users.update_one(
            {"_id": user["_id"]},
            {"$set": {"password": get_password_hash("password123")}}
        )
        print("Updated test teacher in DB.")

if __name__ == "__main__":
    asyncio.run(create_test_teacher())
