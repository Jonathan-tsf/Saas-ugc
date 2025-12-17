"""
Gemini Image Generation Client with Vertex AI Fallback

Uses Google AI Studio (Nano Banana Pro) as primary, falls back to Vertex AI when quota exceeded.
Both use the same billing account but have SEPARATE quotas.

Primary: Google AI Studio API (generativelanguage.googleapis.com)
Fallback: Vertex AI API (aiplatform.googleapis.com)
"""
import json
import base64
import urllib.request
import urllib.error
import urllib.parse
import os
from datetime import datetime, timedelta

from config import (
    NANO_BANANA_API_KEY, 
    VERTEX_AI_PROJECT_ID, 
    VERTEX_AI_LOCATION,
    VERTEX_AI_CREDENTIALS_B64
)

# API endpoints
GOOGLE_AI_STUDIO_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-image-preview:generateContent"
VERTEX_AI_URL_TEMPLATE = "https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/publishers/google/models/gemini-2.0-flash-exp:generateContent"

# Track quota status to avoid repeated failed calls
_quota_status = {
    'google_ai_studio': {'exhausted': False, 'reset_time': None},
    'vertex_ai': {'exhausted': False, 'reset_time': None}
}


def _reset_quota_if_needed():
    """Reset quota status if reset time has passed"""
    now = datetime.now()
    for provider in _quota_status:
        if _quota_status[provider]['reset_time'] and now > _quota_status[provider]['reset_time']:
            _quota_status[provider]['exhausted'] = False
            _quota_status[provider]['reset_time'] = None
            print(f"[GeminiClient] {provider} quota reset")


def _mark_quota_exhausted(provider: str):
    """Mark a provider's quota as exhausted"""
    _quota_status[provider]['exhausted'] = True
    # Google quotas typically reset at midnight Pacific time
    # Set reset time to ~9 hours from now as a safe estimate
    _quota_status[provider]['reset_time'] = datetime.now() + timedelta(hours=9)
    print(f"[GeminiClient] {provider} quota marked as exhausted, will retry after {_quota_status[provider]['reset_time']}")


def _get_vertex_ai_access_token():
    """
    Get access token for Vertex AI using service account credentials.
    The credentials JSON should be base64-encoded in VERTEX_AI_CREDENTIALS_B64.
    """
    if not VERTEX_AI_CREDENTIALS_B64:
        print("[GeminiClient] No Vertex AI credentials configured")
        return None
    
    try:
        import time
        import json
        
        # Decode service account JSON from base64
        credentials_json = base64.b64decode(VERTEX_AI_CREDENTIALS_B64).decode('utf-8')
        creds = json.loads(credentials_json)
        
        # Create a JWT assertion
        now = int(time.time())
        
        header = {
            "alg": "RS256",
            "typ": "JWT",
            "kid": creds.get('private_key_id', '')
        }
        
        payload = {
            "iss": creds['client_email'],
            "sub": creds['client_email'],
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
            "scope": "https://www.googleapis.com/auth/cloud-platform"
        }
        
        # Sign the JWT with RS256
        # We need to use cryptography or similar library
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding
            from cryptography.hazmat.backends import default_backend
            
            # Encode header and payload
            def b64url_encode(data):
                if isinstance(data, str):
                    data = data.encode('utf-8')
                elif isinstance(data, dict):
                    data = json.dumps(data, separators=(',', ':')).encode('utf-8')
                return base64.urlsafe_b64encode(data).rstrip(b'=').decode('utf-8')
            
            header_b64 = b64url_encode(header)
            payload_b64 = b64url_encode(payload)
            
            signing_input = f"{header_b64}.{payload_b64}"
            
            # Load private key
            private_key = serialization.load_pem_private_key(
                creds['private_key'].encode('utf-8'),
                password=None,
                backend=default_backend()
            )
            
            # Sign
            signature = private_key.sign(
                signing_input.encode('utf-8'),
                padding.PKCS1v15(),
                hashes.SHA256()
            )
            
            signature_b64 = base64.urlsafe_b64encode(signature).rstrip(b'=').decode('utf-8')
            
            jwt_assertion = f"{signing_input}.{signature_b64}"
            
            # Exchange JWT for access token
            token_url = "https://oauth2.googleapis.com/token"
            token_data = urllib.parse.urlencode({
                'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
                'assertion': jwt_assertion
            }).encode('utf-8')
            
            token_req = urllib.request.Request(token_url, data=token_data, method='POST')
            token_req.add_header('Content-Type', 'application/x-www-form-urlencoded')
            
            with urllib.request.urlopen(token_req, timeout=30) as resp:
                token_response = json.loads(resp.read().decode('utf-8'))
                return token_response.get('access_token')
                
        except ImportError as e:
            print(f"[GeminiClient] cryptography library not available: {e}")
            return None
            
    except Exception as e:
        print(f"[GeminiClient] Error getting Vertex AI token: {e}")
        import traceback
        traceback.print_exc()
        return None


def _get_vertex_token_via_metadata():
    """
    Alternative: Get token from GCP metadata server (only works on GCP)
    For Lambda, we need to use the service account key approach
    """
    # This won't work in Lambda, return None
    return None


def _call_google_ai_studio(payload: dict) -> dict:
    """Call Google AI Studio API (primary)"""
    if _quota_status['google_ai_studio']['exhausted']:
        _reset_quota_if_needed()
        if _quota_status['google_ai_studio']['exhausted']:
            raise QuotaExhaustedException("Google AI Studio quota exhausted")
    
    url = f"{GOOGLE_AI_STUDIO_URL}?key={NANO_BANANA_API_KEY}"
    headers = {"Content-Type": "application/json"}
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8') if e.fp else ''
        
        if e.code == 429:
            _mark_quota_exhausted('google_ai_studio')
            raise QuotaExhaustedException(f"Google AI Studio quota exceeded: {error_body[:200]}")
        
        raise Exception(f"Google AI Studio error {e.code}: {error_body[:500]}")


def _call_vertex_ai(payload: dict) -> dict:
    """Call Vertex AI API (fallback)"""
    if not VERTEX_AI_PROJECT_ID:
        raise Exception("Vertex AI not configured (missing VERTEX_AI_PROJECT_ID)")
    
    if _quota_status['vertex_ai']['exhausted']:
        _reset_quota_if_needed()
        if _quota_status['vertex_ai']['exhausted']:
            raise QuotaExhaustedException("Vertex AI quota exhausted")
    
    # For Vertex AI, we need to adapt the payload format slightly
    # Vertex AI uses the same format but different auth
    
    url = VERTEX_AI_URL_TEMPLATE.format(
        location=VERTEX_AI_LOCATION,
        project=VERTEX_AI_PROJECT_ID
    )
    
    # Get access token
    access_token = _get_vertex_ai_access_token()
    if not access_token:
        raise Exception("Failed to get Vertex AI access token")
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8') if e.fp else ''
        
        if e.code == 429:
            _mark_quota_exhausted('vertex_ai')
            raise QuotaExhaustedException(f"Vertex AI quota exceeded: {error_body[:200]}")
        
        raise Exception(f"Vertex AI error {e.code}: {error_body[:500]}")


class QuotaExhaustedException(Exception):
    """Raised when API quota is exhausted"""
    pass


def generate_image(
    prompt: str,
    reference_images: list = None,
    aspect_ratio: str = "1:1",
    image_size: str = "1K"
) -> str:
    """
    Generate an image using Gemini with automatic fallback.
    
    Args:
        prompt: Text description for the image
        reference_images: List of base64-encoded images for reference
        aspect_ratio: Output aspect ratio (1:1, 9:16, 16:9, etc.)
        image_size: Output size (1K, 2K, 4K)
    
    Returns:
        Base64-encoded generated image
    
    Raises:
        Exception if both providers fail
    """
    # Build payload
    parts = [{"text": prompt}]
    
    if reference_images:
        for img_b64 in reference_images:
            parts.append({
                "inlineData": {
                    "mimeType": "image/jpeg",
                    "data": img_b64
                }
            })
    
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {
                "aspectRatio": aspect_ratio,
                "imageSize": image_size
            }
        }
    }
    
    errors = []
    
    # Try Google AI Studio first (primary)
    if NANO_BANANA_API_KEY and not _quota_status['google_ai_studio']['exhausted']:
        try:
            print("[GeminiClient] Trying Google AI Studio...")
            result = _call_google_ai_studio(payload)
            image_b64 = _extract_image_from_response(result)
            if image_b64:
                print("[GeminiClient] Success with Google AI Studio")
                return image_b64
        except QuotaExhaustedException as e:
            errors.append(f"Google AI Studio: {e}")
            print(f"[GeminiClient] {e}")
        except Exception as e:
            errors.append(f"Google AI Studio: {e}")
            print(f"[GeminiClient] Google AI Studio error: {e}")
    
    # Try Vertex AI as fallback
    if VERTEX_AI_PROJECT_ID and not _quota_status['vertex_ai']['exhausted']:
        try:
            print("[GeminiClient] Trying Vertex AI fallback...")
            result = _call_vertex_ai(payload)
            image_b64 = _extract_image_from_response(result)
            if image_b64:
                print("[GeminiClient] Success with Vertex AI")
                return image_b64
        except QuotaExhaustedException as e:
            errors.append(f"Vertex AI: {e}")
            print(f"[GeminiClient] {e}")
        except Exception as e:
            errors.append(f"Vertex AI: {e}")
            print(f"[GeminiClient] Vertex AI error: {e}")
    
    # Both failed
    raise Exception(f"All Gemini providers failed: {'; '.join(errors)}")


def _extract_image_from_response(result: dict) -> str:
    """Extract base64 image from Gemini API response"""
    if 'candidates' in result and len(result['candidates']) > 0:
        candidate = result['candidates'][0]
        if 'content' in candidate and 'parts' in candidate['content']:
            for part in candidate['content']['parts']:
                # Skip thought images
                if part.get('thought'):
                    continue
                if 'inlineData' in part:
                    return part['inlineData']['data']
                elif 'inline_data' in part:
                    return part['inline_data']['data']
    return None


def is_quota_available() -> bool:
    """Check if any quota is available"""
    _reset_quota_if_needed()
    return (
        (NANO_BANANA_API_KEY and not _quota_status['google_ai_studio']['exhausted']) or
        (VERTEX_AI_PROJECT_ID and not _quota_status['vertex_ai']['exhausted'])
    )


def get_quota_status() -> dict:
    """Get current quota status for debugging"""
    _reset_quota_if_needed()
    return {
        'google_ai_studio': {
            'configured': bool(NANO_BANANA_API_KEY),
            'exhausted': _quota_status['google_ai_studio']['exhausted'],
            'reset_time': str(_quota_status['google_ai_studio']['reset_time']) if _quota_status['google_ai_studio']['reset_time'] else None
        },
        'vertex_ai': {
            'configured': bool(VERTEX_AI_PROJECT_ID),
            'exhausted': _quota_status['vertex_ai']['exhausted'],
            'reset_time': str(_quota_status['vertex_ai']['reset_time']) if _quota_status['vertex_ai']['reset_time'] else None
        }
    }
