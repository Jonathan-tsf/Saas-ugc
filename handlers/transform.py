"""
Image transformation handlers using Nano Banana Pro API with Vertex AI + Replicate fallback
"""
import json
import uuid
import base64
import requests
import urllib.request
import urllib.error
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO

from config import (
    response, decimal_to_python, verify_admin,
    table, ambassadors_table, s3, S3_BUCKET, REPLICATE_API_KEY
)
from handlers.gemini_client import generate_image as gemini_generate_image

# Replicate API URL for fallback (same model as showcase)
REPLICATE_API_URL = "https://api.replicate.com/v1/models/google/imagen-3/predictions"

# Valid aspect ratios for Gemini (map to closest supported ratio)
SUPPORTED_ASPECT_RATIOS = ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"]


def detect_image_aspect_ratio(image_base64: str) -> str:
    """
    Detect the aspect ratio of a base64-encoded image and return the closest supported ratio.
    Returns aspect ratio string like "9:16", "16:9", "1:1", etc.
    Works with or without PIL by parsing image headers.
    """
    # Map ratios to their numeric values
    ratio_values = {
        "1:1": 1.0,
        "2:3": 2/3,      # ~0.67 (portrait)
        "3:2": 3/2,      # ~1.5 (landscape)
        "3:4": 3/4,      # 0.75 (portrait)
        "4:3": 4/3,      # ~1.33 (landscape)
        "4:5": 4/5,      # 0.8 (portrait)
        "5:4": 5/4,      # 1.25 (landscape)
        "9:16": 9/16,    # ~0.56 (portrait - phone)
        "16:9": 16/9,    # ~1.78 (landscape - video)
        "21:9": 21/9,    # ~2.33 (ultrawide)
    }
    
    def find_closest_ratio(width, height):
        actual_ratio = width / height
        closest_ratio = "1:1"
        min_diff = float('inf')
        for ratio_str, ratio_val in ratio_values.items():
            diff = abs(actual_ratio - ratio_val)
            if diff < min_diff:
                min_diff = diff
                closest_ratio = ratio_str
        print(f"[AspectRatio] Image size: {width}x{height}, ratio: {actual_ratio:.2f}, closest: {closest_ratio}")
        return closest_ratio
    
    try:
        # Decode base64 to bytes
        image_data = base64.b64decode(image_base64)
        
        # Try PIL first
        try:
            from PIL import Image
            img = Image.open(BytesIO(image_data))
            width, height = img.size
            return find_closest_ratio(width, height)
        except ImportError:
            print("[AspectRatio] PIL not available, trying manual parsing...")
        
        # Fallback: Parse image headers manually (works for PNG and JPEG)
        # PNG: width at bytes 16-20, height at bytes 20-24 (big-endian)
        if image_data[:8] == b'\x89PNG\r\n\x1a\n':
            width = int.from_bytes(image_data[16:20], 'big')
            height = int.from_bytes(image_data[20:24], 'big')
            return find_closest_ratio(width, height)
        
        # JPEG: Need to find SOF0 marker (0xFF 0xC0) and read dimensions
        if image_data[:2] == b'\xff\xd8':  # JPEG magic bytes
            i = 2
            while i < len(image_data) - 10:
                if image_data[i] == 0xFF:
                    marker = image_data[i+1]
                    # SOF0, SOF1, SOF2 markers contain dimensions
                    if marker in (0xC0, 0xC1, 0xC2):
                        height = int.from_bytes(image_data[i+5:i+7], 'big')
                        width = int.from_bytes(image_data[i+7:i+9], 'big')
                        return find_closest_ratio(width, height)
                    elif marker == 0xD9:  # EOI
                        break
                    elif marker not in (0x00, 0x01, 0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8):
                        # Skip to next marker
                        length = int.from_bytes(image_data[i+2:i+4], 'big')
                        i += length + 2
                        continue
                i += 1
        
        print("[AspectRatio] Could not parse image dimensions, defaulting to 9:16")
        return "9:16"  # Default to portrait for ambassador photos
        
    except Exception as e:
        print(f"[AspectRatio] Error detecting aspect ratio: {e}, defaulting to 9:16")
        return "9:16"  # Default to portrait for ambassador photos


# Transformation steps configuration - FEMALE
TRANSFORMATION_STEPS_FEMALE = [
    {
        'step': 1,
        'name': 'hair',
        'prompts': [
            "Transform this woman's hair to elegant wavy blonde highlights, professional feminine look, keep face identical",
            "Change hair to sleek dark brunette with subtle layers, sophisticated woman style, keep face identical",
            "Style hair as modern shoulder-length cut with copper tones, feminine look, keep face identical",
            "Update hair to flowing auburn waves with natural shine, elegant woman style, keep face identical"
        ]
    },
    {
        'step': 2,
        'name': 'clothing',
        'prompts': [
            "Dress this woman in professional athletic wear, sporty modern feminine style, keep face and hair identical",
            "Put her in elegant casual business attire, sophisticated woman look, keep face and hair identical",
            "Dress in trendy women's streetwear fitness outfit, urban feminine style, keep face and hair identical",
            "Wear high-end luxury women's sportswear, premium aesthetic, keep face and hair identical"
        ]
    },
    {
        'step': 3,
        'name': 'background',
        'prompts': [
            "Place in modern luxury gym setting with soft lighting, keep person identical",
            "Set background to outdoor natural park with morning light, keep person identical",
            "Put in professional photo studio with neutral backdrop, keep person identical",
            "Place in urban rooftop setting with city skyline, keep person identical"
        ]
    },
    {
        'step': 4,
        'name': 'facial_features',
        'prompts': [
            "Enhance with natural feminine makeup, subtle contouring, fresh dewy look, keep face structure identical",
            "Apply glamorous makeup with defined eyes, elegant feminine style, keep face structure identical",
            "Add minimal natural makeup, dewy skin, athletic feminine glow, keep face structure identical",
            "Style with soft expressive makeup, confident feminine appearance, keep face structure identical"
        ]
    },
    {
        'step': 5,
        'name': 'skin_tone',
        'prompts': [
            "Adjust skin to slightly sun-kissed warm bronze glow, smooth feminine skin, keep everything else identical",
            "Refine skin to fair porcelain with healthy undertones, flawless look, keep everything else identical",
            "Enhance skin to natural olive Mediterranean tone, healthy glow, keep everything else identical",
            "Adjust skin to light golden summer tan, radiant feminine complexion, keep everything else identical"
        ]
    }
]

# Transformation steps configuration - MALE
TRANSFORMATION_STEPS_MALE = [
    {
        'step': 1,
        'name': 'hair',
        'prompts': [
            "IMPORTANT: This is a MAN. Give him a SHORT MASCULINE HAIRCUT. Transform his hair to a clean high skin fade with very short hair on top (maximum 2 inches), dark brown color. NO long hair. NO feminine styles. Keep his face 100% identical. Professional male athlete look.",
            "IMPORTANT: This is a MAN. Give him a BUZZ CUT / CREW CUT - extremely short hair all over (less than 1 inch), military style, clean and masculine. NO long hair at all. Keep his face 100% identical. Athletic masculine look.",
            "IMPORTANT: This is a MAN. Give him a classic SHORT SIDE PART haircut, neat and professional, hair length maximum 3 inches on top, faded sides. Dark hair color. NO long hair. Keep his face 100% identical. Business professional male look.",
            "IMPORTANT: This is a MAN. Give him a modern TEXTURED CROP haircut, short messy top (2-3 inches max), skin fade on sides. NO long hair. NO curls. Keep his face 100% identical. Trendy young professional male look."
        ]
    },
    {
        'step': 2,
        'name': 'clothing',
        'prompts': [
            "Dress this man in fitted black athletic compression shirt and joggers, sporty masculine modern style, keep face and hair EXACTLY identical",
            "Put him in smart casual polo shirt and chinos, sophisticated professional man look, keep face and hair EXACTLY identical",
            "Dress in premium men's gym tank top and shorts, athletic masculine fitness style, keep face and hair EXACTLY identical",
            "Wear fitted henley shirt and dark jeans, casual masculine streetwear look, keep face and hair EXACTLY identical"
        ]
    },
    {
        'step': 3,
        'name': 'background',
        'prompts': [
            "Place in modern luxury gym setting with dramatic lighting and weight equipment visible, keep person EXACTLY identical",
            "Set background to outdoor urban setting with morning golden hour light, keep person EXACTLY identical",
            "Put in professional photo studio with clean neutral gray backdrop, keep person EXACTLY identical",
            "Place in modern minimalist indoor space with natural window light, keep person EXACTLY identical"
        ]
    },
    {
        'step': 4,
        'name': 'facial_features',
        'prompts': [
            "Keep this man clean shaven with clear healthy skin, fresh masculine appearance. Do NOT add beard. Keep face structure 100% identical",
            "Add short well-groomed stubble beard (3-5 day growth), masculine rugged look. Keep face structure 100% identical",
            "Add neat trimmed short beard, professional masculine appearance. Keep face structure 100% identical",
            "Keep natural clean look, healthy clear skin, confident masculine expression. Keep face structure 100% identical"
        ]
    },
    {
        'step': 5,
        'name': 'skin_tone',
        'prompts': [
            "Adjust skin to light sun-kissed bronze tan, healthy athletic masculine glow, keep everything else EXACTLY identical",
            "Keep natural skin tone but enhance with healthy glow, clear complexion, keep everything else EXACTLY identical",
            "Adjust skin to natural olive Mediterranean tone, healthy warm undertones, keep everything else EXACTLY identical",
            "Enhance skin to light golden summer tan, healthy athletic look, keep everything else EXACTLY identical"
        ]
    }
]

def get_transformation_steps(gender='female'):
    """Get the appropriate transformation steps based on gender"""
    if gender == 'male':
        return TRANSFORMATION_STEPS_MALE
    return TRANSFORMATION_STEPS_FEMALE


def call_replicate_api_sync(image_base64, prompt):
    """
    Call Replicate API as fallback for image transformation.
    This is SYNCHRONOUS - waits for result (up to 120 seconds).
    Returns base64 image data or raises Exception.
    """
    if not REPLICATE_API_KEY:
        raise Exception("REPLICATE_API_KEY not configured for fallback")
    
    print(f"Using Replicate fallback for transformation...")
    
    headers = {
        "Authorization": f"Bearer {REPLICATE_API_KEY}",
        "Content-Type": "application/json",
        "Prefer": "wait"  # Wait for result synchronously
    }
    
    # Build data URI for the image
    image_data_uri = f"data:image/jpeg;base64,{image_base64}"
    
    payload = {
        "input": {
            "prompt": prompt,
            "image": image_data_uri,
            "output_format": "png",
            "safety_tolerance": 2
        }
    }
    
    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(REPLICATE_API_URL, data=data, headers=headers, method='POST')
        
        with urllib.request.urlopen(req, timeout=120) as api_response:
            result = json.loads(api_response.read().decode('utf-8'))
            
            status = result.get('status')
            output = result.get('output')
            
            if status == 'succeeded' and output:
                # Output is a URL, we need to download and convert to base64
                image_url = output if isinstance(output, str) else output[0] if output else None
                
                if image_url:
                    print(f"Replicate succeeded, downloading from: {image_url[:50]}...")
                    img_req = urllib.request.Request(image_url)
                    with urllib.request.urlopen(img_req, timeout=30) as img_response:
                        image_data = img_response.read()
                        return base64.b64encode(image_data).decode('utf-8')
            
            error = result.get('error', 'Unknown error')
            raise Exception(f"Replicate failed: {status} - {error}")
            
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8') if e.fp else 'No error body'
        print(f"Replicate API HTTP error: {e.code} - {error_body[:500]}")
        raise Exception(f"Replicate HTTP error: {e.code}")
    except Exception as e:
        print(f"Replicate error: {e}")
        raise


def call_nano_banana_api(image_base64, prompt, use_fallback=True):
    """Call Gemini API for image transformation with Replicate fallback
    
    Uses gemini_client which handles model fallback automatically.
    If Gemini fails, falls back to Replicate.
    
    Args:
        image_base64: Base64-encoded source image
        prompt: Transformation prompt
        use_fallback: Whether to use Replicate as final fallback
    """
    
    # Try Gemini (with automatic model fallback via gemini_client)
    try:
        print(f"Calling Gemini for transformation...")
        result = gemini_generate_image(
            prompt=prompt,
            reference_images=[image_base64],
            image_size="1K"
        )
        
        if result:
            print("Gemini transformation successful")
            return result
        else:
            print("No image returned from Gemini, trying Replicate fallback...")
            
    except Exception as e:
        error_msg = str(e)
        print(f"Gemini API error: {error_msg}")
        
        # If it's a quota error and we have Replicate, try that
        if use_fallback and REPLICATE_API_KEY:
            print("Gemini failed, attempting Replicate fallback for transformation...")
        else:
            raise
    
    # Fallback to Replicate
    if use_fallback and REPLICATE_API_KEY:
        print(f"Attempting Replicate fallback for transformation...")
        return call_replicate_api_sync(image_base64, prompt)
    
    raise Exception("All image generation APIs failed or not configured")


def generate_transformation_variations(session_id, step_number, image_base64, step_config):
    """Generate 4 variations for a transformation step - ONE BY ONE with DynamoDB updates"""
    variations = []
    
    for i, prompt in enumerate(step_config['prompts']):
        try:
            print(f"Generating variation {i+1}/4 for step {step_number}")
            transformed_image = call_nano_banana_api(image_base64, prompt, use_fallback=True)
            
            variation_data = {
                'index': i,
                'prompt': prompt,
                'image_base64': transformed_image
            }
            variations.append(variation_data)
            
            # Update DynamoDB immediately after each generation
            update_session_variation(session_id, step_number, i, variation_data)
            
        except Exception as e:
            print(f"Error generating variation {i}: {e}")
            error_data = {
                'index': i,
                'prompt': prompt,
                'error': str(e)
            }
            variations.append(error_data)
            update_session_variation(session_id, step_number, i, error_data)
    
    return variations


def update_session_variation(session_id, step_number, variation_index, variation_data):
    """Update a single variation in DynamoDB session"""
    try:
        # Store variation image in S3 if present
        if 'image_base64' in variation_data and not variation_data.get('error'):
            var_key = f"transform_sessions/{session_id}/step{step_number}_var{variation_index}.png"
            var_data = base64.b64decode(variation_data['image_base64'])
            s3.put_object(
                Bucket=S3_BUCKET,
                Key=var_key,
                Body=var_data,
                ContentType='image/png'
            )
            variation_data['image_url'] = f"https://{S3_BUCKET}.s3.amazonaws.com/{var_key}"
        
        # Update DynamoDB with the new variation
        table.update_item(
            Key={'pk': 'TRANSFORM_SESSION', 'sk': session_id},
            UpdateExpression=f'SET step_{step_number}_variations[{variation_index}] = :var, updated_at = :updated',
            ExpressionAttributeValues={
                ':var': variation_data,
                ':updated': datetime.now().isoformat()
            }
        )
        print(f"✓ Updated variation {variation_index} for step {step_number}")
    except Exception as e:
        print(f"Error updating variation in DynamoDB: {e}")


def start_transformation(event):
    """Start transformation process - Returns immediately with session_id, generates async"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    image_base64 = body.get('image_base64')
    name = body.get('name', '').strip()
    gender = body.get('gender', 'female')  # Get gender for prompt selection
    
    if not image_base64:
        return response(400, {'error': 'image_base64 is required'})
    
    if not name:
        return response(400, {'error': 'name is required'})
    
    # Validate gender
    if gender not in ['male', 'female']:
        gender = 'female'
    
    session_id = str(uuid.uuid4())
    
    # Get gender-specific transformation steps
    transformation_steps = get_transformation_steps(gender)
    
    try:
        # Store original image in S3
        original_image_key = f"transform_sessions/{session_id}/original.png"
        image_data = base64.b64decode(image_base64)
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=original_image_key,
            Body=image_data,
            ContentType='image/png'
        )
        original_image_url = f"https://{S3_BUCKET}.s3.amazonaws.com/{original_image_key}"
        
        # Create session in DynamoDB with status "generating" and gender
        # Initialize step_1_variations as empty list (will be filled by generate function)
        session = {
            'id': session_id,
            'pk': 'TRANSFORM_SESSION',
            'sk': session_id,
            'name': name,
            'gender': gender,  # Store gender in session
            'original_image_url': original_image_url,
            'current_step': 1,
            'current_image_url': original_image_url,
            'selections': {},
            'step_1_variations': [None, None, None, None],  # Pre-allocate for updates
            'created_at': datetime.now().isoformat(),
            'status': 'generating'  # Start with 'generating' status
        }
        
        table.put_item(Item=session)
        
        # Generate variations (this updates DynamoDB as each one completes)
        step_config = transformation_steps[0]
        variations = generate_transformation_variations(session_id, 1, image_base64, step_config)
        
        # Prepare response with S3 URLs (no base64)
        response_variations = []
        for v in variations:
            response_variations.append({
                'image_url': v.get('image_url'),
                'prompt': v.get('prompt'),
                'error': v.get('error')
            })
        
        # Update status to ready after all variations generated
        table.update_item(
            Key={'pk': 'TRANSFORM_SESSION', 'sk': session_id},
            UpdateExpression="SET #status = :status",
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':status': 'ready'}
        )
        
        return response(200, {
            'success': True,
            'session_id': session_id,
            'step': 1,
            'step_name': step_config['name'],
            'total_steps': len(transformation_steps),
            'variations': response_variations
        })
        
    except Exception as e:
        print(f"Error starting transformation: {e}")
        return response(500, {'error': f'Failed to start transformation: {str(e)}'})


def continue_transformation(event):
    """Continue transformation with selected variation - POST /api/admin/ambassadors/transform/continue"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    session_id = body.get('session_id')
    selected_index = body.get('selected_index')  # -1 = keep current, 0+ = variation index
    
    if not session_id:
        return response(400, {'error': 'session_id is required'})
    
    if selected_index is None:
        return response(400, {'error': 'selected_index is required'})
    
    try:
        result = table.get_item(Key={'pk': 'TRANSFORM_SESSION', 'sk': session_id})
        session = result.get('Item')
        
        if not session:
            return response(404, {'error': 'Session not found'})
        
        current_step = int(session.get('current_step', 1))
        # Read variations from step-specific key
        variations = session.get(f'step_{current_step}_variations', [])
        current_image_url = session.get('current_image_url')
        
        # Determine which image to use based on selected_index
        if selected_index == -1:
            # Keep current image (skip this transformation)
            selected_image_url = current_image_url
            print(f"User chose to keep current image for step {current_step}")
        else:
            # Use selected variation
            if selected_index < 0 or selected_index >= len(variations):
                return response(400, {'error': f'Invalid selected_index: {selected_index}, variations count: {len(variations)}'})
            
            variation = variations[selected_index]
            selected_image_url = variation.get('image_url')
            
            if not selected_image_url:
                return response(400, {'error': 'Selected variation has no image_url'})
        
        # Download selected image from S3
        print(f"Fetching selected image from: {selected_image_url}")
        try:
            # Extract S3 key from URL
            if S3_BUCKET in selected_image_url:
                s3_key = selected_image_url.split(f"{S3_BUCKET}.s3.amazonaws.com/")[1]
                s3_response = s3.get_object(Bucket=S3_BUCKET, Key=s3_key)
                image_data = s3_response['Body'].read()
            else:
                # Fallback: fetch from URL
                req = urllib.request.Request(selected_image_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    image_data = resp.read()
        except Exception as e:
            print(f"Error fetching selected image: {e}")
            return response(500, {'error': f'Failed to fetch selected image: {str(e)}'})
        
        gender = session.get('gender', 'female')  # Get gender from session
        transformation_steps = get_transformation_steps(gender)
        
        selections = session.get('selections', {})
        selections[str(current_step)] = {
            'index': selected_index,
            'step_name': transformation_steps[current_step - 1]['name']
        }
        
        next_step = current_step + 1
        
        # Save selected image to S3 for this step
        selected_image_key = f"transform_sessions/{session_id}/step{current_step}_selected.png"
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=selected_image_key,
            Body=image_data,
            ContentType='image/png'
        )
        new_current_image_url = f"https://{S3_BUCKET}.s3.amazonaws.com/{selected_image_key}"
        
        # Convert image_data to base64 for next transformation
        selected_image_base64 = base64.b64encode(image_data).decode('utf-8')
        
        if next_step > len(transformation_steps):
            # All transformations complete
            file_name = f"profile_{session_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            s3_key = f"profiles/{file_name}"
            
            s3.put_object(
                Bucket=S3_BUCKET,
                Key=s3_key,
                Body=image_data,
                ContentType='image/png'
            )
            
            file_url = f"https://{S3_BUCKET}.s3.amazonaws.com/{s3_key}"
            
            table.update_item(
                Key={'pk': 'TRANSFORM_SESSION', 'sk': session_id},
                UpdateExpression="SET #status = :status, selections = :selections, current_step = :step, final_image_url = :url, current_image_url = :curr_url",
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={
                    ':status': 'completed',
                    ':selections': selections,
                    ':step': next_step,
                    ':url': file_url,
                    ':curr_url': new_current_image_url
                }
            )
            
            return response(200, {
                'success': True,
                'completed': True,
                'session_id': session_id,
                'final_image_url': file_url,
                'name': session.get('name'),
                'gender': gender  # Return gender for finalization
            })
        
        step_config = transformation_steps[next_step - 1]
        
        # Pre-allocate variations list for this step
        table.update_item(
            Key={'pk': 'TRANSFORM_SESSION', 'sk': session_id},
            UpdateExpression=f"SET step_{next_step}_variations = :vars, current_step = :step, current_image_url = :img, selections = :sel, #status = :status",
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':vars': [None, None, None, None],
                ':step': next_step,
                ':img': new_current_image_url,
                ':sel': selections,
                ':status': 'generating'
            }
        )
        
        # Generate variations (this updates DynamoDB as each one completes)
        variations = generate_transformation_variations(session_id, next_step, selected_image_base64, step_config)
        
        # Prepare clean response (no base64)
        response_variations = []
        for v in variations:
            response_variations.append({
                'image_url': v.get('image_url'),
                'prompt': v.get('prompt'),
                'error': v.get('error')
            })
        
        # Update status to ready
        table.update_item(
            Key={'pk': 'TRANSFORM_SESSION', 'sk': session_id},
            UpdateExpression="SET #status = :status",
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':status': 'ready'}
        )
        
        return response(200, {
            'success': True,
            'session_id': session_id,
            'step': next_step,
            'step_name': step_config['name'],
            'total_steps': len(transformation_steps),
            'variations': response_variations,
            'completed': False
        })
        
    except Exception as e:
        print(f"Error continuing transformation: {e}")
        return response(500, {'error': f'Failed to continue transformation: {str(e)}'})


def get_transformation_session(event):
    """Get transformation session status - GET /api/admin/ambassadors/transform/session"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    params = event.get('queryStringParameters', {}) or {}
    session_id = params.get('session_id')
    
    if not session_id:
        return response(400, {'error': 'session_id is required'})
    
    try:
        result = table.get_item(Key={'pk': 'TRANSFORM_SESSION', 'sk': session_id})
        session = result.get('Item')
        
        if not session:
            return response(404, {'error': 'Session not found'})
        
        gender = session.get('gender', 'female')
        transformation_steps = get_transformation_steps(gender)
        current_step = int(session.get('current_step', 1))
        
        # Get variations from step-specific key
        variations = session.get(f'step_{current_step}_variations', [])
        clean_variations = []
        for v in variations:
            if v:  # Skip None entries
                clean_variations.append({
                    'image_url': v.get('image_url'),
                    'prompt': v.get('prompt'),
                    'error': v.get('error')
                })
        
        # Determine status based on variations
        status = session.get('status', 'generating')
        if len(clean_variations) >= 4 or all(v.get('image_url') or v.get('error') for v in clean_variations if v):
            # All variations generated (or errored), ready for selection
            if status != 'completed':
                status = 'ready'
        
        return response(200, {
            'session_id': session_id,
            'name': session.get('name'),
            'gender': gender,
            'current_step': current_step,
            'total_steps': len(transformation_steps),
            'status': status,
            'selections': session.get('selections', {}),
            'variations': clean_variations,
            'current_image_url': session.get('current_image_url'),
            'original_image_url': session.get('original_image_url'),
            'final_image_url': session.get('final_image_url')
        })
        
    except Exception as e:
        print(f"Error getting session: {e}")
        return response(500, {'error': 'Failed to get session'})


def finalize_ambassador(event):
    """Create ambassador from completed transformation - POST /api/admin/ambassadors/transform/finalize"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    session_id = body.get('session_id')
    description = body.get('description', '')
    style = body.get('style', '')
    outfit_ids = body.get('outfit_ids', [])  # Optional outfit IDs
    
    if not session_id:
        return response(400, {'error': 'session_id is required'})
    
    try:
        result = table.get_item(Key={'pk': 'TRANSFORM_SESSION', 'sk': session_id})
        session = result.get('Item')
        
        if not session:
            return response(404, {'error': 'Session not found'})
        
        if session.get('status') != 'completed':
            return response(400, {'error': 'Transformation not completed'})
        
        # Use gender from session (already set during start_transformation)
        gender = session.get('gender', 'female')
        
        ambassador_id = str(uuid.uuid4())
        created_at = datetime.now().isoformat()
        
        ambassador = {
            'id': ambassador_id,
            'name': session.get('name'),
            'description': description,
            'photo_profile': session.get('final_image_url', ''),
            'photo_list_base_array': [],
            'video_list_base_array': [],
            'hasBeenChosen': False,
            'gender': gender,
            'style': style,
            'isRecommended': False,
            'userOwnerId': '',
            'transformation_session_id': session_id,
            'created_at': created_at,
            'updated_at': created_at
        }
        
        ambassadors_table.put_item(Item=ambassador)
        
        table.update_item(
            Key={'pk': 'TRANSFORM_SESSION', 'sk': session_id},
            UpdateExpression="SET #status = :status, ambassador_id = :amb_id",
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'finalized',
                ':amb_id': ambassador_id
            }
        )
        
        return response(201, {'success': True, 'ambassador': ambassador})
        
    except Exception as e:
        print(f"Error finalizing ambassador: {e}")
        return response(500, {'error': f'Failed to create ambassador: {str(e)}'})


def generate_profile_photos(event):
    """
    Generate 4 profile photo options using Nano Banana Pro (Gemini 3 Pro Image)
    POST /api/admin/ambassadors/profile-photos
    Body: { ambassador_id, source_image_index (optional, defaults to first showcase image) }
    
    Profile photos are:
    - 1:1 square ratio
    - Face centered
    - Clean/neutral background
    - Professional headshot style
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON'})
    
    ambassador_id = body.get('ambassador_id')
    source_image_index = body.get('source_image_index', 0)
    
    if not ambassador_id:
        return response(400, {'error': 'ambassador_id is required'})
    
    # Get ambassador data
    try:
        result = ambassadors_table.get_item(Key={'id': ambassador_id})
        ambassador = result.get('Item')
        if not ambassador:
            return response(404, {'error': 'Ambassador not found'})
    except Exception as e:
        print(f"Error fetching ambassador: {e}")
        return response(500, {'error': 'Failed to fetch ambassador'})
    
    # Get source image (from showcase photos or profile photo)
    showcase_photos = ambassador.get('photo_list_base_array', [])
    current_profile = ambassador.get('photo_profile', '')
    
    source_image_url = None
    if showcase_photos and source_image_index < len(showcase_photos):
        source_image_url = showcase_photos[source_image_index]
    elif current_profile:
        source_image_url = current_profile
    
    if not source_image_url:
        return response(400, {'error': 'No source image available for this ambassador'})
    
    # Download source image and convert to base64
    try:
        print(f"Downloading source image: {source_image_url[:50]}...")
        req = urllib.request.Request(source_image_url)
        with urllib.request.urlopen(req, timeout=30) as img_response:
            image_data = img_response.read()
            image_base64 = base64.b64encode(image_data).decode('utf-8')
    except Exception as e:
        print(f"Error downloading source image: {e}")
        return response(500, {'error': 'Failed to download source image'})
    
    # Generate 4 profile photo options with Nano Banana Pro
    gender = ambassador.get('gender', 'female')
    name = ambassador.get('name', 'the person')
    
    profile_prompts = [
        f"Create a professional profile photo of this {gender}. Square 1:1 ratio, face perfectly centered, clean neutral gray studio background, soft professional lighting, headshot from shoulders up, looking directly at camera with confident friendly expression. Keep the face identical to the input image.",
        f"Create a modern social media profile photo of this {gender}. Square 1:1 ratio, face centered, minimalist white background, bright even lighting, upper body visible, natural relaxed expression, professional yet approachable. Keep the face identical to the input image.",
        f"Create an elegant business profile photo of this {gender}. Square 1:1 ratio, face centered, soft gradient background from light gray to white, professional studio lighting with subtle rim light, head and shoulders framing, confident professional expression. Keep the face identical to the input image.",
        f"Create a lifestyle profile photo of this {gender}. Square 1:1 ratio, face centered, blurred modern interior background with natural light, soft shadows, chest-up framing, warm friendly smile, authentic natural look. Keep the face identical to the input image."
    ]
    
    generated_photos = []
    styles = ['professional', 'social_media', 'business', 'lifestyle']
    
    def generate_single_photo(args):
        """Generate a single profile photo - for parallel execution"""
        i, prompt = args
        try:
            print(f"Generating profile photo {i+1}/4...")
            result_base64 = call_nano_banana_pro_profile(image_base64, prompt)
            
            if result_base64:
                # Upload to S3
                photo_key = f"ambassadors/{ambassador_id}/profile_options/profile_{i+1}_{uuid.uuid4().hex[:8]}.png"
                
                s3.put_object(
                    Bucket=S3_BUCKET,
                    Key=photo_key,
                    Body=base64.b64decode(result_base64),
                    ContentType='image/png'
                )
                
                photo_url = f"https://{S3_BUCKET}.s3.amazonaws.com/{photo_key}"
                print(f"Profile photo {i+1} uploaded: {photo_url}")
                return {
                    'index': i,
                    'url': photo_url,
                    'prompt_style': styles[i]
                }
            else:
                print(f"Failed to generate profile photo {i+1}")
                return None
        except Exception as e:
            print(f"Error generating profile photo {i+1}: {e}")
            return None
    
    # Execute all 4 generations in parallel
    print("Starting parallel profile photo generation...")
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(generate_single_photo, (i, prompt)): i for i, prompt in enumerate(profile_prompts)}
        
        for future in as_completed(futures):
            result = future.result()
            if result:
                generated_photos.append(result)
    
    # Sort by index
    generated_photos.sort(key=lambda x: x['index'])
    print(f"Parallel generation complete: {len(generated_photos)}/4 photos generated")
    
    if not generated_photos:
        return response(500, {'error': 'Failed to generate any profile photos'})
    
    # Store the options in DynamoDB for later selection
    try:
        ambassadors_table.update_item(
            Key={'id': ambassador_id},
            UpdateExpression="SET profile_photo_options = :options, updated_at = :updated",
            ExpressionAttributeValues={
                ':options': generated_photos,
                ':updated': datetime.now().isoformat()
            }
        )
    except Exception as e:
        print(f"Error storing profile options: {e}")
    
    return response(200, {
        'success': True,
        'profile_photos': generated_photos,
        'ambassador_id': ambassador_id
    })


def call_nano_banana_pro_profile(image_base64, prompt):
    """
    Call Nano Banana Pro (Gemini 3 Pro Image Preview) for profile photo generation.
    Uses 1:1 aspect ratio for profile photos.
    Uses gemini_client which handles Google AI Studio -> Vertex AI fallback.
    """
    try:
        print(f"Calling Gemini for profile photo (with Vertex AI fallback)...")
        result = gemini_generate_image(
            prompt=prompt,
            reference_images=[image_base64],
            image_size="1K"
        )
        
        if result:
            print("Profile photo generation successful")
            return result
        else:
            print("No image returned from Gemini for profile photo")
            return None
            
    except Exception as e:
        print(f"Profile photo generation error: {e}")
        return None


def select_profile_photo(event):
    """
    Select one of the generated profile photos as the ambassador profile photo.
    POST /api/admin/ambassadors/profile-photos/select
    Body: { ambassador_id, selected_index }
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON'})
    
    ambassador_id = body.get('ambassador_id')
    selected_index = body.get('selected_index')
    
    if not ambassador_id:
        return response(400, {'error': 'ambassador_id is required'})
    if selected_index is None:
        return response(400, {'error': 'selected_index is required'})
    
    # Get ambassador data
    try:
        result = ambassadors_table.get_item(Key={'id': ambassador_id})
        ambassador = result.get('Item')
        if not ambassador:
            return response(404, {'error': 'Ambassador not found'})
    except Exception as e:
        print(f"Error fetching ambassador: {e}")
        return response(500, {'error': 'Failed to fetch ambassador'})
    
    # Get the profile options
    profile_options = ambassador.get('profile_photo_options', [])
    
    selected_photo = None
    for option in profile_options:
        if option.get('index') == selected_index:
            selected_photo = option
            break
    
    if not selected_photo:
        return response(400, {'error': f'Invalid selected_index: {selected_index}'})
    
    # Update ambassador's profile photo
    try:
        ambassadors_table.update_item(
            Key={'id': ambassador_id},
            UpdateExpression="SET photo_profile = :photo, updated_at = :updated",
            ExpressionAttributeValues={
                ':photo': selected_photo['url'],
                ':updated': datetime.now().isoformat()
            }
        )
        
        return response(200, {
            'success': True,
            'photo_profile': selected_photo['url'],
            'ambassador_id': ambassador_id
        })
        
    except Exception as e:
        print(f"Error updating profile photo: {e}")
        return response(500, {'error': 'Failed to update profile photo'})