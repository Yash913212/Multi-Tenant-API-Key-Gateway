import base64
import hashlib
import secrets
from typing import Tuple

def generate_api_key(prefix: str = "sk_live_") -> Tuple[str, str, str, str]:
    """
    Generates a cryptographically secure random API key.
    
    1. Generates 24 cryptographically secure random bytes.
    2. Encodes using Base64 URL-safe encoding (RFC 4648, Section 5).
    3. Prepends prefix (e.g., 'sk_live_').
    4. Computes SHA-256 hash.
    5. Extracts last four characters for masking/display.
    
    Returns:
        (plaintext_key, key_hash, prefix, last_four)
    """
    raw_bytes = secrets.token_bytes(24)
    # URL-safe Base64 without trailing '=' padding
    token = base64.urlsafe_b64encode(raw_bytes).decode('ascii').rstrip('=')
    full_key = f"{prefix}{token}"
    key_hash = hash_api_key(full_key)
    last_four = full_key[-4:]
    return full_key, key_hash, prefix, last_four

def hash_api_key(key: str) -> str:
    """
    Computes standard SHA-256 hexadecimal digest for an API key.
    Raw keys are never stored in the database.
    """
    return hashlib.sha256(key.strip().encode('utf-8')).hexdigest()

def mask_api_key(prefix: str, last_four: str) -> str:
    """
    Formats an API key for safe display, e.g. 'sk_live_...3f9a'.
    """
    return f"{prefix}...{last_four}"
