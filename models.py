# models.py
from sqlalchemy import Column, String, Float, DateTime, ForeignKey, Text, JSON, Boolean, Date, Integer, CheckConstraint
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func
from datetime import datetime
import uuid

Base = declarative_base()

def generate_uuid():
    return str(uuid.uuid4())

class Organization(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    vat_number = Column(String, unique=True, index=True, nullable=False)
    company_name = Column(String, nullable=False)
    address = Column(String, nullable=False)
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
    role = Column(String, CheckConstraint("role IN ('admin','user')"), nullable=False, default="user")
    password_hash = Column(String, nullable=False)   # <-- MATCHAR DB & app.py
    created_at = Column(DateTime, default=datetime.utcnow)

    organization = relationship("Organization", back_populates="users")
    bookings = relationship("Booking", back_populates="user")

class Address(Base):
    __tablename__ = "addresses"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
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

    selected_mode = Column(String, nullable=False)
    price_eur = Column(Float)
    pickup_date = Column(DateTime)
    transit_time_days = Column(String)
    co2_emissions = Column(Float)

    asap_pickup = Column(Boolean, nullable=True)
    requested_pickup_date = Column(Date, nullable=True)
    asap_delivery = Column(Boolean, nullable=True)
    requested_delivery_date = Column(Date, nullable=True)

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
