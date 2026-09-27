import os
import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, Text, Float, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ats.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    # A user signs up with EITHER an email OR a phone number (or both later).
    # Both are nullable + unique so either can be used as the login identifier.
    email = Column(String, unique=True, index=True, nullable=True)
    phone = Column(String, unique=True, index=True, nullable=True)
    hashed_password = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Plan / quota state. Analysis (ATS score report) is always free.
    # The rewrite feature (generate = "modify", and download) is gated by this quota block,
    # bought as a 30-day plan and optionally extended with top-ups.
    plan_active = Column(Boolean, default=False)
    plan_expires_at = Column(DateTime, nullable=True)
    modify_quota = Column(Integer, default=0)
    modify_used = Column(Integer, default=0)
    download_quota = Column(Integer, default=0)
    download_used = Column(Integer, default=0)

    usage_logs = relationship("UsageLog", back_populates="user")
    orders = relationship("Order", back_populates="user")


class UsageLog(Base):
    __tablename__ = "usage_logs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String)  # "analyze" | "modify" | "download"
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="usage_logs")


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    kind = Column(String)  # "plan" | "topup"
    currency = Column(String, default="INR")  # "INR" | "USD" — which price list this order used
    amount_inr = Column(Float)  # kept for backward compat / reporting; the amount actually charged
    razorpay_order_id = Column(String, unique=True)
    razorpay_payment_id = Column(String, nullable=True)
    status = Column(String, default="created")  # created | paid | failed
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="orders")


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

