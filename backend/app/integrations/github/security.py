"""GitHub webhook signature verification — HMAC-SHA256 over the raw
request body, per GitHub's own scheme
(https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries).
Deliberately takes raw bytes, not a parsed dict — re-serializing JSON
before hashing would produce a different signature than the one GitHub
computed over the exact bytes it sent."""
import hashlib
import hmac


def verify_signature(secret: str, raw_body: bytes, signature_header: str | None) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    provided = signature_header[len("sha256="):]
    return hmac.compare_digest(expected, provided)
