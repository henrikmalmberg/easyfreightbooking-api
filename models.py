# models.py

from sqlalchemy import Column, String, Float, DateTime, ForeignKey, Text, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
import uuid

Base = declarative_base()

def generate_uuid():
    return str(uuid.uuid4())

from models import Address
from sqlalchemy.exc import SQLAlchemyError

@app.route("/test-db", methods=["GET"])
def test_db():
    try:
        session = SessionLocal()
        count = session.query(Address).count()
        session.close()
        return jsonify({"status": "success", "address_count": count})
    except SQLAlchemyError as e:
        return jsonify({"status": "error", "message": str(e)}), 500

class Address(Base):
    __tablename__ = "addresses"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, nullable=False)
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


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, nullable=False)

    selected_mode = Column(String, nullable=False)
    price_eur = Column(Float)
    pickup_date = Column(DateTime)
    transit_time_days = Column(String)
    co2_emissions = Column(Float)

    sender_address_id = Column(String, ForeignKey("addresses.id"))
    receiver_address_id = Column(String, ForeignKey("addresses.id"))

    goods = Column(JSON)
    references = Column(JSON)
    addons = Column(JSON)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SearchLog(Base):
    __tablename__ = "search_logs"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, nullable=False)

    from_country = Column(String(2))
    from_postal = Column(String(20))
    to_country = Column(String(2))
    to_postal = Column(String(20))

    goods = Column(JSON)
    available_options = Column(JSON)
    selected_option = Column(String)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
