"""
Gemini Image Generation Client

Uses Google AI Studio with automatic model fallback:
- Primary: gemini-3-pro-image-preview (Nano Banana Pro) - Best quality
- Fallback: gemini-2.0-flash-exp - When Pro quota is exhausted
"""
import json
import base64
import urllib.request
import urllib.error
from datetime import datetime, timedelta

from config import NANO_BANANA_API_KEY

# API base URL
GOOGLE_AI_STUDIO_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# Models in order of preference
MODELS = [
    {
        'name': 'gemini-3-pro-image-preview',
        'display': 'Nano Banana Pro',
        'supports_image_size': True  # Supports 1K, 2K, 4K
    },
    {
        'name': 'gemini-2.0-flash-exp',
        'display': 'Gemini 2.0 Flash',
        'supports_image_size': False  # Only supports default resolution
    }
]

# Track quota status per model
_quota_status = {
    'gemini-3-pro-image-preview': {'exhausted': False, 'reset_time': None},
    'gemini-2.0-flash-exp': {'exhausted': False, 'reset_time': None}
}


def _reset_quota_if_needed():
    """Reset quota status if reset time has passed"""
    now = datetime.now()
    for model in _quota_status:
        if _quota_status[model]['reset_time'] and now > _quota_status[model]['reset_time']:
            _quota_status[model]['exhausted'] = False
            _quota_status[model]['reset_time'] = None
            print(f"[GeminiClient] {model} quota reset")


def _mark_quota_exhausted(model: str):
    """Mark a model's quota as exhausted"""
    if model not in _quota_status:
        _quota_status[model] = {'exhausted': False, 'reset_time': None}
    
    _quota_status[model]['exhausted'] = True
    # Google quotas typically reset at midnight Pacific time
    # Set reset time to ~9 hours from now as a safe estimate
    _quota_status[model]['reset_time'] = datetime.now() + timedelta(hours=9)
    print(f"[GeminiClient] {model} quota marked as exhausted, will retry after {_quota_status[model]['reset_time']}")


class QuotaExhaustedException(Exception):
    """Raised when API quota is exhausted"""
    pass


def _call_model(model_name: str, payload: dict) -> dict:
    """Call Google AI Studio API for a specific model"""
    _reset_quota_if_needed()
    
    if _quota_status.get(model_name, {}).get('exhausted'):
        raise QuotaExhaustedException(f"{model_name} quota exhausted")
    
    url = f"{GOOGLE_AI_STUDIO_BASE}/{model_name}:generateContent?key={NANO_BANANA_API_KEY}"
    headers = {"Content-Type": "application/json"}
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8') if e.fp else ''
        
        if e.code == 429:
            _mark_quota_exhausted(model_name)
            raise QuotaExhaustedException(f"{model_name} quota exceeded: {error_body[:200]}")
        
        raise Exception(f"{model_name} error {e.code}: {error_body[:500]}")


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


def generate_image(
    prompt: str,
    reference_images: list = None,
    aspect_ratio: str = "1:1",
    image_size: str = "1K"
) -> str:
    """
    Generate an image using Gemini with automatic model fallback.
    
    Args:
        prompt: Text description for the image
        reference_images: List of base64-encoded images for reference
        aspect_ratio: Output aspect ratio (1:1, 9:16, 16:9, etc.)
        image_size: Output size (1K, 2K, 4K) - only for Pro model
    
    Returns:
        Base64-encoded generated image
    
    Raises:
        Exception if all models fail
    """
    if not NANO_BANANA_API_KEY:
        raise Exception("NANO_BANANA_API_KEY not configured")
    
    # Build parts
    parts = [{"text": prompt}]
    
    if reference_images:
        for img_b64 in reference_images:
            parts.append({
                "inlineData": {
                    "mimeType": "image/jpeg",
                    "data": img_b64
                }
            })
    
    print(f"[GeminiClient] Generating image with aspect_ratio={aspect_ratio}, image_size={image_size}")
    
    errors = []
    
    # Try each model in order
    for model_config in MODELS:
        model_name = model_config['name']
        display_name = model_config['display']
        supports_image_size = model_config['supports_image_size']
        
        # Skip if quota exhausted
        if _quota_status.get(model_name, {}).get('exhausted'):
            _reset_quota_if_needed()
            if _quota_status.get(model_name, {}).get('exhausted'):
                print(f"[GeminiClient] Skipping {display_name} - quota exhausted")
                continue
        
        try:
            print(f"[GeminiClient] Trying {display_name} ({model_name})...")
            
            # Build payload for this model
            image_config = {"aspectRatio": aspect_ratio}
            if supports_image_size:
                image_config["imageSize"] = image_size
            
            payload = {
                "contents": [{"parts": parts}],
                "generationConfig": {
                    "responseModalities": ["TEXT", "IMAGE"],
                    "imageConfig": image_config
                }
            }
            
            result = _call_model(model_name, payload)
            image_b64 = _extract_image_from_response(result)
            
            if image_b64:
                print(f"[GeminiClient] ✓ Success with {display_name}")
                return image_b64
            else:
                # No image in response
                candidate = result.get('candidates', [{}])[0]
                finish_reason = candidate.get('finishReason', 'unknown')
                error_msg = f"{display_name}: No image returned (finishReason={finish_reason})"
                errors.append(error_msg)
                print(f"[GeminiClient] {error_msg}")
                
        except QuotaExhaustedException as e:
            errors.append(f"{display_name}: {e}")
            print(f"[GeminiClient] {display_name} quota exhausted, trying next model...")
            
        except Exception as e:
            errors.append(f"{display_name}: {e}")
            print(f"[GeminiClient] {display_name} error: {e}")
    
    # All models failed
    raise Exception(f"All Gemini models failed: {'; '.join(errors)}")


def is_quota_available() -> bool:
    """Check if any model quota is available"""
    _reset_quota_if_needed()
    for model_config in MODELS:
        model_name = model_config['name']
        if not _quota_status.get(model_name, {}).get('exhausted'):
            return True
    return False


def get_quota_status() -> dict:
    """Get current quota status for debugging"""
    _reset_quota_if_needed()
    status = {}
    for model_config in MODELS:
        model_name = model_config['name']
        model_status = _quota_status.get(model_name, {'exhausted': False, 'reset_time': None})
        status[model_name] = {
            'display': model_config['display'],
            'exhausted': model_status.get('exhausted', False),
            'reset_time': str(model_status['reset_time']) if model_status.get('reset_time') else None
        }
    return status
