import asyncio
from app.db.database import db

async def main():
    tenants = await db.tenants.find().to_list(5)
    for t in tenants:
        if t.get("tenantName") == "Admin 01 Organization":
            tid = t["_id"]
            name = t.get("tenantName")
            sub_id = t.get("subscriptionId")
            stripe_sub = t.get("stripeSubscriptionId")
            print(f"Tenant: _id={tid}, name={name}, subscriptionId={sub_id}, stripeSubId={stripe_sub}")
            if sub_id:
                plan = await db.subscriptionPlans.find_one({"_id": sub_id})
                print(f"Plan name: {plan.get('name')}")

asyncio.run(main())
