# utils/ids.py
import secrets
import re

# 24 bokstäver, utesluter I, O, Q, U för läsbarhet
LETTERS = "ABCDEFGHJKMNPQRSTVWXYZ"
DIGITS = "0123456789"

# Regex för validering: 2 bokstäver - 3 bokstäver - 5 siffror
BOOKING_REGEX = re.compile(r"^[A-HJ-NP-TV-Z]{2}-[A-HJ-NP-TV-Z]{3}-\d{5}$")

def generate_booking_number() -> str:
    p1 = "".join(secrets.choice(LETTERS) for _ in range(2))
    p2 = "".join(secrets.choice(LETTERS) for _ in range(3))
    p3 = "".join(secrets.choice(DIGITS)  for _ in range(5))
    return f"{p1}-{p2}-{p3}"

def is_valid_booking_number(code: str) -> bool:
    return bool(BOOKING_REGEX.fullmatch(code))
