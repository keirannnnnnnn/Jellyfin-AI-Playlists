import pytest
from app.database import hash_password, verify_password
from app.auth import create_session_token, verify_session_token


def test_password_hashing_and_verification():
    password = "MySecurePassword456"
    hashed = hash_password(password)

    assert hashed != password
    assert "$" in hashed
    assert verify_password(password, hashed) is True
    assert verify_password("WrongPassword", hashed) is False
    assert verify_password("", hashed) is False


def test_session_token_generation_and_validation():
    token = create_session_token()
    assert token is not None
    assert verify_session_token(token) is True

    # Tampered token
    tampered = token[:-4] + "AAAA"
    assert verify_session_token(tampered) is False
    assert verify_session_token(None) is False
    assert verify_session_token("invalid:token") is False
