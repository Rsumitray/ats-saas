import os
import datetime
import razorpay

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")

PLAN_PRICE_INR = float(os.getenv("PLAN_PRICE_INR", "350"))
PLAN_VALIDITY_DAYS = int(os.getenv("PLAN_VALIDITY_DAYS", "30"))
PLAN_MODIFY_QUOTA = int(os.getenv("PLAN_MODIFY_QUOTA", "100"))
PLAN_DOWNLOAD_QUOTA = int(os.getenv("PLAN_DOWNLOAD_QUOTA", "20"))

TOPUP_PRICE_INR = float(os.getenv("TOPUP_PRICE_INR", "150"))
TOPUP_MODIFY_QUOTA = int(os.getenv("TOPUP_MODIFY_QUOTA", "50"))
TOPUP_DOWNLOAD_QUOTA = int(os.getenv("TOPUP_DOWNLOAD_QUOTA", "10"))

# --- International pricing (USD) ---
# These are independent price points, not a currency conversion of the INR
# price — set them to whatever you actually want to charge internationally.
# IMPORTANT: creating a non-INR order only works once your Razorpay account
# has "International Payments" / multi-currency enabled, which is a separate
# approval Razorpay grants after you complete Business + KYC details (the
# ones you clicked "Add later" on). Until then, a USD order will fail with a
# clear error from Razorpay's API — see create_order() below, which surfaces
# that error rather than hiding it.
ENABLE_USD = os.getenv("ENABLE_USD", "false").lower() == "true"
PLAN_PRICE_USD = float(os.getenv("PLAN_PRICE_USD", "5"))
TOPUP_PRICE_USD = float(os.getenv("TOPUP_PRICE_USD", "2"))

SUPPORTED_CURRENCIES = ("INR", "USD")

_client = None


def client():
    global _client
    if _client is None:
        if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
            raise RuntimeError("Razorpay is not configured on the server.")
        _client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    return _client


def price_for(kind: str, currency: str = "INR") -> float:
    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError(f"Unsupported currency: {currency}")
    if kind == "plan":
        return PLAN_PRICE_USD if currency == "USD" else PLAN_PRICE_INR
    if kind == "topup":
        return TOPUP_PRICE_USD if currency == "USD" else TOPUP_PRICE_INR
    raise ValueError("kind must be 'plan' or 'topup'")


def create_order(kind: str, receipt: str, currency: str = "INR") -> dict:
    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError(f"Unsupported currency: {currency}")
    if currency == "USD" and not ENABLE_USD:
        raise RuntimeError(
            "USD pricing isn't turned on yet. Set ENABLE_USD=true in backend/.env once your Razorpay "
            "account has International Payments enabled (Settings -> Payment Methods -> International "
            "Cards in the Razorpay dashboard, which requires Business + KYC details to be completed first)."
        )
    amount_minor = int(round(price_for(kind, currency) * 100))  # paise for INR, cents for USD
    order = client().order.create(
        {
            "amount": amount_minor,
            "currency": currency,
            "receipt": receipt,
            "payment_capture": 1,
            "notes": {"kind": kind, "currency": currency},
        }
    )
    return order


def verify_signature(order_id: str, payment_id: str, signature: str) -> bool:
    try:
        client().utility.verify_payment_signature(
            {
                "razorpay_order_id": order_id,
                "razorpay_payment_id": payment_id,
                "razorpay_signature": signature,
            }
        )
        return True
    except razorpay.errors.SignatureVerificationError:
        return False


def apply_purchase(user, kind: str):
    """Grant quota after a verified payment. Called only after signature verification."""
    now = datetime.datetime.utcnow()
    if kind == "plan":
        # A fresh plan purchase starts a new 30-day cycle with fresh quotas.
        user.plan_active = True
        user.plan_expires_at = now + datetime.timedelta(days=PLAN_VALIDITY_DAYS)
        user.modify_quota = PLAN_MODIFY_QUOTA
        user.modify_used = 0
        user.download_quota = PLAN_DOWNLOAD_QUOTA
        user.download_used = 0
    elif kind == "topup":
        # Top-ups only add quota; they never extend an expired plan.
        user.modify_quota += TOPUP_MODIFY_QUOTA
        user.download_quota += TOPUP_DOWNLOAD_QUOTA
    else:
        raise ValueError("kind must be 'plan' or 'topup'")
