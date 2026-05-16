import logging
from datetime import datetime, timedelta
from bson import ObjectId
from app.db.database import db

logger = logging.getLogger(__name__)

async def process_tenant_upgrade(tenant_id: str, plan_id: str, stripe_sub_id: str | None, session_id: str, amount_total: int, currency: str, metadata: dict | None = None):
    """
    Shared helper to process a tenant subscription upgrade.
    Used by BOTH the Webhook and the manual Verify endpoint.
    """
    try:
        tenant_oid = ObjectId(tenant_id)
        plan_oid = ObjectId(plan_id)
        
        # 1. Fetch the plan
        plan_doc = await db.subscriptionPlans.find_one({"_id": plan_oid})
        if not plan_doc:
            logger.error(f"[StripeUpgrade] Plan {plan_id} not found")
            return {"success": False, "message": "Subscription plan not found"}

        # 2. Calculate expiry
        now = datetime.utcnow()
        billing_cycle = plan_doc.get("billingCycle", "monthly").lower()
        expiry = now + timedelta(days=365) if billing_cycle == "yearly" else now + timedelta(days=30)

        # 3. Update the tenant
        logger.info(f"[StripeUpgrade] Upgrading tenant {tenant_id} to plan {plan_doc.get('name')}")
        await db.tenants.update_one(
            {"_id": tenant_oid},
            {"$set": {
                "subscriptionId": plan_oid,
                "subscriptionPlan": plan_doc.get("name"),
                "subscriptionCategory": plan_doc.get("category", "paid"),
                "stripeSubscriptionId": stripe_sub_id,
                "subscriptionStartDate": now,
                "subscriptionExpiryDate": expiry,
                "updatedAt": now
            }}
        )

        # 4. Record the payment for history
        from app.crud.payments import create_payment
        await create_payment({
            "tenantId": tenant_id,
            "paymentType": "subscription",
            "amount": amount_total / 100 if amount_total else 0,
            "currency": currency or "usd",
            "status": "completed",
            "stripeSessionId": session_id,
            "metadata": metadata or {}
        })

        return {"success": True, "message": "Subscription upgraded successfully!"}
    except Exception as e:
        logger.exception(f"[StripeUpgrade] Error processing upgrade for tenant {tenant_id}: {e}")
        return {"success": False, "message": str(e)}
