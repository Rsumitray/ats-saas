import os
import uuid
import datetime

# Load backend/.env into the process environment BEFORE any other local module
# is imported — several modules (billing.py, llm.py, database.py) read
# os.getenv(...) at import time to set module-level constants, so the .env
# file has to be loaded first or those reads would just see nothing.
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import init_db, get_db, User, UsageLog, Order
from auth import hash_password, verify_password, create_access_token, get_current_user
import extract
import llm
import billing

FRONTEND_URL = os.getenv("FRONTEND_URL", "*")

app = FastAPI(title="Resume ATS & OCR Analyzer API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL] if FRONTEND_URL != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()


# ============================================================================
# AUTH
# An "identifier" is whatever the user types to sign up/in with: either an
# email address or a phone number. We tell them apart with a simple rule
# (contains "@" -> email, otherwise -> phone) so the frontend doesn't need to
# send a separate "channel" flag — one less thing to get out of sync.
# ============================================================================

import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Accepts things like "+919876543210" or "9876543210" (8-15 digits, optional leading +).
PHONE_RE = re.compile(r"^\+?[0-9]{8,15}$")


def _classify_identifier(identifier: str) -> str:
    """Return 'email' or 'phone' for a raw identifier string, or raise a clean 422."""
    identifier = identifier.strip()
    if "@" in identifier:
        if not EMAIL_RE.match(identifier):
            raise HTTPException(422, f"'{identifier}' doesn't look like a valid email address.")
        return "email"
    if not PHONE_RE.match(identifier.replace(" ", "")):
        raise HTTPException(422, f"'{identifier}' doesn't look like a valid phone number (digits only, optional +country code).")
    return "phone"


class RegisterBody(BaseModel):
    identifier: str  # email OR phone, e.g. "jane@x.com" or "+919876543210"
    password: str


class LoginBody(BaseModel):
    identifier: str
    password: str


@app.post("/api/register")
def register(body: RegisterBody, db: Session = Depends(get_db)):
    kind = _classify_identifier(body.identifier)
    identifier = body.identifier.strip()

    if len(body.password) < 8:
        raise HTTPException(422, "Password must be at least 8 characters.")

    # Look up by whichever column matches this identifier's kind.
    lookup_col = User.email if kind == "email" else User.phone
    if db.query(User).filter(lookup_col == identifier).first():
        raise HTTPException(400, f"An account with this {kind} already exists. Try signing in instead.")

    user = User(
        email=identifier if kind == "email" else None,
        phone=identifier if kind == "phone" else None,
        hashed_password=hash_password(body.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # NOTE: we deliberately do NOT log the user in here. Per the intended flow,
    # after account creation the UI sends them to the Sign In form so the
    # "create account -> then log in with that email/phone + password" path
    # is explicit and unambiguous (see frontend AUTH section for the same note).
    return {"created": True, "identifier": identifier, "kind": kind}


@app.post("/api/login")
def login(body: LoginBody, db: Session = Depends(get_db)):
    kind = _classify_identifier(body.identifier)
    identifier = body.identifier.strip()
    lookup_col = User.email if kind == "email" else User.phone

    user = db.query(User).filter(lookup_col == identifier).first()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(401, "Incorrect email/phone or password.")
    token = create_access_token(user.id)
    return {"access_token": token, "token_type": "bearer"}


class AnalyzeBody(BaseModel):
    resume_text: str
    jd_text: str = ""  # optional now — a target_role alone is also accepted
    target_role: str | None = None


class RewriteBody(BaseModel):
    resume_text: str
    jd_text: str = ""  # optional now — a target_role alone is also accepted
    target_role: str | None = None
    findings: dict | None = None


class CreateOrderBody(BaseModel):
    kind: str  # "plan" | "topup"
    currency: str = "INR"  # "INR" | "USD"


class VerifyPaymentBody(BaseModel):
    kind: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


# ---------- Session helpers ----------

def _plan_is_live(user: User) -> bool:
    return bool(user.plan_active and user.plan_expires_at and user.plan_expires_at > datetime.datetime.utcnow())


@app.get("/api/me")
def me(user: User = Depends(get_current_user)):
    live = _plan_is_live(user)
    return {
        "email": user.email,
        "phone": user.phone,
        "plan_active": live,
        "plan_expires_at": user.plan_expires_at.isoformat() if user.plan_expires_at else None,
        "modify_quota": user.modify_quota,
        "modify_used": user.modify_used,
        "modify_remaining": max(user.modify_quota - user.modify_used, 0) if live else 0,
        "download_quota": user.download_quota,
        "download_used": user.download_used,
        "download_remaining": max(user.download_quota - user.download_used, 0) if live else 0,
        "prices": {
            "plan_inr": billing.PLAN_PRICE_INR,
            "plan_usd": billing.PLAN_PRICE_USD,
            "plan_validity_days": billing.PLAN_VALIDITY_DAYS,
            "plan_modify_quota": billing.PLAN_MODIFY_QUOTA,
            "plan_download_quota": billing.PLAN_DOWNLOAD_QUOTA,
            "topup_inr": billing.TOPUP_PRICE_INR,
            "topup_usd": billing.TOPUP_PRICE_USD,
            "topup_modify_quota": billing.TOPUP_MODIFY_QUOTA,
            "topup_download_quota": billing.TOPUP_DOWNLOAD_QUOTA,
            "usd_enabled": billing.ENABLE_USD,
        },
    }


def _log_usage(db: Session, user_id: int, action: str):
    db.add(UsageLog(user_id=user_id, action=action))
    db.commit()


# ---------- File extraction ----------

@app.post("/api/extract")
async def extract_file(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    content = await file.read()
    try:
        text = extract.extract_text(file.filename, content)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"text": text}


# ---------- Analysis (always free) ----------

@app.post("/api/analyze")
def analyze(body: AnalyzeBody, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not body.resume_text.strip():
        raise HTTPException(422, "resume_text is required.")
    if not body.jd_text.strip() and not (body.target_role or "").strip():
        raise HTTPException(422, "Provide either a job description (jd_text) or a target_role.")
    try:
        result = llm.analyze_resume(body.resume_text, body.jd_text, body.target_role or "")
    except Exception as e:
        raise HTTPException(502, f"Analysis failed: {e}")
    _log_usage(db, user.id, "analyze")
    return result


# ---------- Rewrite ("modify") — paid, quota-gated ----------

@app.post("/api/rewrite")
def rewrite(body: RewriteBody, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not _plan_is_live(user):
        raise HTTPException(402, "Your plan isn't active. Buy a plan to unlock resume rewriting.")
    if user.modify_used >= user.modify_quota:
        raise HTTPException(
            402,
            f"You've used all {user.modify_quota} rewrites on your current plan. "
            f"Top up to get {billing.TOPUP_MODIFY_QUOTA} more (your plan is still valid until "
            f"{user.plan_expires_at.date()}).",
        )
    if not body.resume_text.strip():
        raise HTTPException(422, "resume_text is required.")
    if not body.jd_text.strip() and not (body.target_role or "").strip():
        raise HTTPException(422, "Provide either a job description (jd_text) or a target_role.")
    try:
        result = llm.rewrite_resume(body.resume_text, body.jd_text, body.target_role or "", body.findings or {})
    except Exception as e:
        raise HTTPException(502, f"Rewrite failed: {e}")
    user.modify_used += 1
    db.commit()
    _log_usage(db, user.id, "modify")
    result["modify_remaining"] = user.modify_quota - user.modify_used
    return result


# ---------- Job recommendations & interview prep — free, like analysis ----------
# Deliberately separate endpoints from /api/analyze (see the comment in llm.py above
# job_recommendations()/interview_prep()) rather than extra fields on the analysis
# response. Free for now, same as analysis — if free-tier AI cost ever needs
# tightening, these two are the natural first candidates to gate behind the paid
# plan, since they're an enhancement on top of the core ATS score, not the core
# product itself. That's a pricing decision to make deliberately later, not now.

@app.post("/api/job-recommendations")
def job_recommendations(body: AnalyzeBody, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not body.resume_text.strip():
        raise HTTPException(422, "resume_text is required.")
    try:
        result = llm.job_recommendations(body.resume_text, body.target_role or "")
    except Exception as e:
        raise HTTPException(502, f"Job recommendations failed: {e}")
    _log_usage(db, user.id, "job_recommendations")
    return result


@app.post("/api/interview-prep")
def interview_prep(body: AnalyzeBody, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not body.resume_text.strip():
        raise HTTPException(422, "resume_text is required.")
    try:
        result = llm.interview_prep(body.resume_text, body.jd_text, body.target_role or "")
    except Exception as e:
        raise HTTPException(502, f"Interview prep failed: {e}")
    _log_usage(db, user.id, "interview_prep")
    return result


class DownloadBody(BaseModel):
    resume_text: str  # the already-generated rewritten text, to record + hand back


@app.post("/api/rewrite/download")
def download_rewrite(body: DownloadBody, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not _plan_is_live(user):
        raise HTTPException(402, "Your plan isn't active. Buy a plan to download your rewritten resume.")
    if user.download_used >= user.download_quota:
        raise HTTPException(
            402,
            f"You've used all {user.download_quota} downloads on your current plan. "
            f"Top up to get {billing.TOPUP_DOWNLOAD_QUOTA} more (your plan is still valid until "
            f"{user.plan_expires_at.date()}).",
        )
    user.download_used += 1
    db.commit()
    _log_usage(db, user.id, "download")
    return {"resume_text": body.resume_text, "download_remaining": user.download_quota - user.download_used}


# ---------- Billing (Razorpay one-time orders: plan + top-up) ----------

@app.post("/api/billing/create-order")
def create_order(body: CreateOrderBody, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.kind not in ("plan", "topup"):
        raise HTTPException(422, "kind must be 'plan' or 'topup'.")
    currency = (body.currency or "INR").upper()
    if currency not in billing.SUPPORTED_CURRENCIES:
        raise HTTPException(422, f"currency must be one of {billing.SUPPORTED_CURRENCIES}.")
    if body.kind == "topup" and not _plan_is_live(user):
        raise HTTPException(400, "Top-ups are only available while your plan is still valid. Buy a plan first.")
    try:
        receipt = f"{body.kind}-{user.id}-{uuid.uuid4().hex[:8]}"
        rp_order = billing.create_order(body.kind, receipt, currency)
    except Exception as e:
        raise HTTPException(400, str(e))

    order = Order(
        user_id=user.id,
        kind=body.kind,
        currency=currency,
        amount_inr=billing.price_for(body.kind, currency),
        razorpay_order_id=rp_order["id"],
        status="created",
    )
    db.add(order)
    db.commit()
    return {
        "order_id": rp_order["id"],
        "amount": rp_order["amount"],
        "currency": rp_order["currency"],
        "key_id": billing.RAZORPAY_KEY_ID,
    }


@app.post("/api/billing/verify")
def verify_payment(body: VerifyPaymentBody, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = (
        db.query(Order)
        .filter(Order.razorpay_order_id == body.razorpay_order_id, Order.user_id == user.id)
        .first()
    )
    if not order:
        raise HTTPException(404, "Order not found.")
    if order.status == "paid":
        return {"status": "already_verified"}

    ok = billing.verify_signature(body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature)
    if not ok:
        order.status = "failed"
        db.commit()
        raise HTTPException(400, "Payment signature verification failed.")

    order.status = "paid"
    order.razorpay_payment_id = body.razorpay_payment_id
    billing.apply_purchase(user, order.kind)
    db.commit()
    return {"status": "paid"}


@app.get("/api/health")
def health():
    return {"status": "ok"}
