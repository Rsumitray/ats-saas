"""
Self-service password reset for ShreeResumebuild.AI.

Put this file in your backend folder (next to main.py), then add two lines
to main.py (see the instructions that came with this file).

How it works
  POST /api/forgot-password  {"email": "..."}
      Emails a reset link, valid for 30 minutes:
      https://shreeresumebuild-ai.netlify.app/reset.html?token=...
      Always returns the same message, so nobody can use it to find out
      which emails are registered.
  POST /api/reset-password   {"token": "...", "new_password": "..."}
      Checks the token and saves the new password.

No new database table is needed. The token is signed with your existing
JWT_SECRET (plus a "password reset" suffix, so it can never be used as a
login token) and includes a fingerprint of the user's current password
hash. As soon as the password changes, the link stops working, so every
link can only be used once.

Railway variables needed (backend service -> Variables):
  GMAIL_USER          shreeresumebuild.ai@gmail.com
  GMAIL_APP_PASSWORD  the 16-character Google App Password
  JWT_SECRET          already set
  FRONTEND_URL        already set (https://shreeresumebuild-ai.netlify.app)
"""

import hashlib
import os
import smtplib
import ssl
import time
import datetime
from email.message import EmailMessage

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db, User
from auth import hash_password

TOKEN_TTL_MINUTES = 30
RESEND_COOLDOWN_SECONDS = 60

_frontend = os.getenv("FRONTEND_URL", "")
FRONTEND_URL = (_frontend if _frontend and _frontend != "*" else "https://shreeresumebuild-ai.netlify.app").rstrip("/")
GMAIL_USER = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
_RESET_KEY = (os.getenv("JWT_SECRET", "") + ":password-reset")

GENERIC_MESSAGE = (
    "If an account exists for that email, we've sent a password reset link. "
    "Please check your inbox and spam folder."
)

router = APIRouter()
_last_sent: dict[str, float] = {}  # email -> time of last reset email (simple rate limit)


# ------------------------------------------------------------------ helpers --
def _fingerprint(hashed_password: str) -> str:
    return hashlib.sha256(hashed_password.encode()).hexdigest()[:24]


def _make_token(user: User) -> str:
    now = datetime.datetime.utcnow()
    payload = {
        "purpose": "password_reset",
        "uid": user.id,
        "pwf": _fingerprint(user.hashed_password),
        "iat": now,
        "exp": now + datetime.timedelta(minutes=TOKEN_TTL_MINUTES),
    }
    return jwt.encode(payload, _RESET_KEY, algorithm="HS256")


def _send_reset_email(to_email: str, link: str) -> None:
    if not (GMAIL_USER and GMAIL_APP_PASSWORD):
        print("[password_reset] GMAIL_USER / GMAIL_APP_PASSWORD not set, email not sent.")
        return

    msg = EmailMessage()
    msg["Subject"] = "Reset your ShreeResumebuild.AI password"
    msg["From"] = f"ShreeResumebuild.AI <{GMAIL_USER}>"
    msg["To"] = to_email
    msg.set_content(
        "We received a request to reset your ShreeResumebuild.AI password.\n\n"
        f"Set a new password here (link works for {TOKEN_TTL_MINUTES} minutes, one time only):\n{link}\n\n"
        "If you didn't ask for this, ignore this email. Your password won't change.\n\n"
        "S2&P AI Tech"
    )
    msg.add_alternative(f"""\
<div style="font-family:Arial,sans-serif;max-width:480px;line-height:1.6;color:#222">
  <h2 style="margin:0 0 12px">Reset your password</h2>
  <p>We received a request to reset your ShreeResumebuild.AI password.</p>
  <p><a href="{link}" style="display:inline-block;background:#B8540A;color:#fff;padding:12px 22px;
     border-radius:8px;text-decoration:none;font-weight:bold">Set a new password</a></p>
  <p style="font-size:13px;color:#666">This link works for {TOKEN_TTL_MINUTES} minutes and can be used once.
     If you didn't ask for this, ignore this email. Your password won't change.</p>
  <p style="font-size:13px;color:#666">S2&amp;P AI Tech</p>
</div>""", subtype="html")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=20) as s:
            s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            s.send_message(msg)
        print(f"[password_reset] Reset email sent to {to_email}")
    except Exception as e:
        print(f"[password_reset] Failed to send email to {to_email}: {e}")


# ---------------------------------------------------------------- endpoints --
class ForgotPasswordBody(BaseModel):
    email: str


class ResetPasswordBody(BaseModel):
    token: str
    new_password: str


@router.post("/api/forgot-password")
def forgot_password(body: ForgotPasswordBody, background: BackgroundTasks, db: Session = Depends(get_db)):
    email = body.email.strip().lower()

    if "@" not in email:
        # Phone-only accounts have no email to send a link to.
        return {"message": "Reset links can only be sent to an email address. If you signed up with a phone "
                           "number, email shreeresumebuild.ai@gmail.com from any address and we'll help you."}

    now = time.time()
    if now - _last_sent.get(email, 0) < RESEND_COOLDOWN_SECONDS:
        return {"message": GENERIC_MESSAGE}

    user = db.query(User).filter(func.lower(User.email) == email).first()
    if user:
        _last_sent[email] = now
        link = f"{FRONTEND_URL}/reset.html?token={_make_token(user)}"
        background.add_task(_send_reset_email, user.email, link)

    return {"message": GENERIC_MESSAGE}


@router.post("/api/reset-password")
def reset_password(body: ResetPasswordBody, db: Session = Depends(get_db)):
    bad_link = "This reset link is invalid, expired, or already used. Please request a new one."

    if len(body.new_password) < 8:
        raise HTTPException(422, "Password must be at least 8 characters.")
    if len(body.new_password.encode()) > 72:
        raise HTTPException(422, "Password is too long. Please use 72 characters or fewer.")

    try:
        data = jwt.decode(body.token, _RESET_KEY, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(400, bad_link)

    if data.get("purpose") != "password_reset":
        raise HTTPException(400, bad_link)

    user = db.query(User).filter(User.id == data.get("uid")).first()
    if not user or data.get("pwf") != _fingerprint(user.hashed_password):
        raise HTTPException(400, bad_link)  # password already changed -> link used up

    user.hashed_password = hash_password(body.new_password)
    db.commit()
    return {"message": "Your password has been updated. You can now sign in with your new password."}
