# models.py
from sqlalchemy import (
    Column, String, Float, DateTime, ForeignKey, Text, JSON, Boolean,
    Date, Integer, CheckConstraint, Time
)
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func
from datetime import datetime
import uuid

Base = declarative_base()

def generate_uuid():
    return str(uuid.uuid4())

# ---------------------------------------------------------
# Booking status – tillåten uppsättning (lagom granularitet)
# ---------------------------------------------------------
STATUS_VALUES = (
    "NEW",               # skapad
    "CONFIRMED",         # bekräftad internt
    "PICKUP_PLANNED",    # planerat lastningsdatum finns
    "PICKED_UP",         # verklig lastning satt
    "IN_TRANSIT",        # på väg
    "DELIVERY_PLANNED",  # planerat lossningsdatum finns
    "DELIVERED",         # verklig lossning satt
    "COMPLETED",         # administrativt avslutad
    "ON_HOLD",           # pausad / väntar info
    "CANCELLED",         # avbruten
    "EXCEPTION",         # avvikelse
)

class Organization(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    vat_number = Column(String, unique=True, index=True, nullable=False)
    company_name = Column(String, nullable=False)
    address = Column(String, nullable=False)
    # 👇 NYA FÄLT (måste finnas för /admin/organizations)
    postal_code  = Column(String, nullable=True)
    country_code = Column(String(2), nullable=True)

    invoice_email = Column(String, nullable=False)
    payment_terms_days = Column(Integer, default=10)
    currency = Column(String, default="EUR")
    created_at = Column(DateTime, default=datetime.utcnow)

    users = relationship("User", back_populates="organization", cascade="all, delete")
    bookings = relationship("Booking", back_populates="organization")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)

    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    # 👇 tillåt superadmin också
    role = Column(
        String,
        CheckConstraint("role IN ('admin','user','superadmin')"),
        nullable=False,
        default="user"
    )
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    organization = relationship("Organization", back_populates="users")
    bookings = relationship("Booking", back_populates="user")


class Address(Base):
    __tablename__ = "addresses"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    type = Column(String, nullable=False)  # "sender" or "receiver"

    business_name = Column(String(100))
    address = Column(String(200))
    postal_code = Column(String(20))
    city = Column(String(100))
    country_code = Column(String(2))

    contact_name = Column(String(100))
    phone = Column(String(50))
    email = Column(String(100))

    opening_hours = Column(String(200))
    instructions = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    bookings_as_sender = relationship("Booking", foreign_keys="Booking.sender_address_id", back_populates="sender_address")
    bookings_as_receiver = relationship("Booking", foreign_keys="Booking.receiver_address_id", back_populates="receiver_address")

class Booking(Base):
    __tablename__ = "bookings"

    id = Column(String, primary_key=True, default=generate_uuid)

    # --------- NYTT: identitet & meta ----------
    booking_number = Column(String(16), unique=True, index=True, nullable=False)  # ex. "FT-KMV-49285"
    booking_date   = Column(Date, nullable=False, server_default=func.current_date())  # sätts av DB vid insert
    status = Column(
        String(32),
        CheckConstraint(
            "status IN ("
            "'NEW','CONFIRMED','PICKUP_PLANNED','PICKED_UP','IN_TRANSIT',"
            "'DELIVERY_PLANNED','DELIVERED','COMPLETED','ON_HOLD','CANCELLED','EXCEPTION'"
            ")",
            name="ck_booking_status",
        ),
        nullable=False,
        default="NEW",
        server_default="NEW",
    )

    # --------- Befintliga pris/offer-fält ----------
    selected_mode = Column(String, nullable=False)
    price_eur = Column(Float)
    pickup_date = Column(DateTime)
    transit_time_days = Column(String)
    co2_emissions = Column(Float)

    # --------- Legacy "request" från tidigare UI (behålls för bakåtkomp.) ----------
    asap_pickup = Column(Boolean, nullable=True)
    requested_pickup_date = Column(Date, nullable=True)
    asap_delivery = Column(Boolean, nullable=True)
    requested_delivery_date = Column(Date, nullable=True)

    # --------- NYTT: Requested / Planned / Actual (lastning) ----------
    loading_requested_date = Column(Date, nullable=True)
    loading_requested_time = Column(Time, nullable=True)
    loading_planned_date   = Column(Date, nullable=True)
    loading_planned_time   = Column(Time, nullable=True)
    loading_actual_date    = Column(Date, nullable=True)
    loading_actual_time    = Column(Time, nullable=True)

    # --------- NYTT: Requested / Planned / Actual (lossning) ----------
    unloading_requested_date = Column(Date, nullable=True)
    unloading_requested_time = Column(Time, nullable=True)
    unloading_planned_date   = Column(Date, nullable=True)
    unloading_planned_time   = Column(Time, nullable=True)
    unloading_actual_date    = Column(Date, nullable=True)
    unloading_actual_time    = Column(Time, nullable=True)

    # --------- Adresser & övrigt ----------
    sender_address_id = Column(String, ForeignKey("addresses.id"))
    receiver_address_id = Column(String, ForeignKey("addresses.id"))

    goods = Column(JSON)
    references = Column(JSON)
    addons = Column(JSON)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    org_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    organization = relationship("Organization", back_populates="bookings")

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user = relationship("User", back_populates="bookings")

    sender_address = relationship("Address", foreign_keys=[sender_address_id], back_populates="bookings_as_sender")
    receiver_address = relationship("Address", foreign_keys=[receiver_address_id], back_populates="bookings_as_receiver")

# models.py (tillägg längst ner)

from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Text, JSON
from sqlalchemy.sql import func

# ...

class PricingConfig(Base):
    __tablename__ = "pricing_configs"

    id = Column(String, primary_key=True, default=generate_uuid)
    # status: 'published' eller 'draft'
    status = Column(String, nullable=False)
    # version: sätts på publicerade rader (draft kan vara NULL)
    version = Column(Integer, nullable=True)

    data = Column(JSON, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    comment = Column(Text, nullable=True)
    effective_at = Column(DateTime(timezone=True), nullable=True)

