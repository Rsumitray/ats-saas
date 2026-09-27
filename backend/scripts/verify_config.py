"""
Run this after filling in backend/.env to confirm every external dependency
is actually reachable and correctly configured, BEFORE you open the app to
real users. Run it from the backend/ directory:

    python3 scripts/verify_config.py

It makes real network calls (to Anthropic and Razorpay), so run it somewhere
with normal internet access — your laptop or your deployed server. It never
prints your secret values back to you.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
WARN = "\033[93mWARN\033[0m"

results = []


def check(name, fn):
    try:
        detail = fn()
        results.append((PASS, name, detail or ""))
    except SkipCheck as e:
        results.append((WARN, name, str(e)))
    except Exception as e:
        results.append((FAIL, name, str(e)))


class SkipCheck(Exception):
    pass


def check_jwt_secret():
    secret = os.getenv("JWT_SECRET", "")
    if not secret or secret == "change-this-secret-in-production":
        raise Exception("JWT_SECRET is missing or still the insecure default.")
    if len(secret) < 20:
        raise Exception("JWT_SECRET is too short (< 20 chars) — generate a longer one.")
    return f"{len(secret)} characters, looks fine"


def check_database():
    from database import engine
    with engine.connect() as conn:
        pass
    url = os.getenv("DATABASE_URL", "sqlite:///./ats.db")
    if url.startswith("sqlite") :
        return "SQLite connection OK (fine for testing — switch to Postgres before real users)"
    return "Database connection OK"


def check_anthropic():
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key or key.startswith("REPLACE_ME"):
        raise SkipCheck("ANTHROPIC_API_KEY not set yet — analysis/rewrite will fail until it is.")
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    # Smallest possible real call — just proves the key authenticates.
    resp = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        max_tokens=10,
        messages=[{"role": "user", "content": "Reply with the single word: OK"}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    return f"Key authenticated, model replied: {text.strip()!r}"


def check_razorpay():
    key_id = os.getenv("RAZORPAY_KEY_ID", "")
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "")
    if not key_id or not key_secret:
        raise SkipCheck("RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not set yet — payments will fail until they are.")
    import razorpay
    client = razorpay.Client(auth=(key_id, key_secret))
    # A ₹1 test order that's never charged (no payment is captured without checkout).
    order = client.order.create({"amount": 100, "currency": "INR", "receipt": "config-check"})
    return f"Test order created OK: {order['id']}"


def check_pricing_env():
    from billing import PLAN_PRICE_INR, PLAN_VALIDITY_DAYS, PLAN_MODIFY_QUOTA, PLAN_DOWNLOAD_QUOTA, TOPUP_PRICE_INR
    return (
        f"Plan ₹{PLAN_PRICE_INR}/{PLAN_VALIDITY_DAYS}d "
        f"({PLAN_MODIFY_QUOTA} rewrites / {PLAN_DOWNLOAD_QUOTA} downloads), "
        f"top-up ₹{TOPUP_PRICE_INR}"
    )


def main():
    check("JWT_SECRET is set and strong", check_jwt_secret)
    check("Database is reachable", check_database)
    check("Pricing/quota env vars load correctly", check_pricing_env)
    check("Anthropic API key works (real API call)", check_anthropic)
    check("Razorpay keys work (real ₹1 test order)", check_razorpay)

    print("\n--- Backend configuration check ---\n")
    for status, name, detail in results:
        print(f"[{status}] {name}")
        if detail:
            print(f"       {detail}")
    print()

    if any(s == FAIL for s, _, _ in results):
        print("Some checks FAILED — fix those before deploying.")
        sys.exit(1)
    if any(s == WARN for s, _, _ in results):
        print("Some checks were SKIPPED (missing keys) — fine for now, required before going live.")
    else:
        print("All checks passed — backend is fully configured.")


if __name__ == "__main__":
    main()
