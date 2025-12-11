"""
Image transformation handlers using Nano Banana Pro API with Replicate fallback
"""
import json
import uuid
import base64
import requests
import urllib.request
import urllib.error
from datetime import datetime

from config import (
    response, decimal_to_python, verify_admin,
    table, ambassadors_table, s3, S3_BUCKET, NANO_BANANA_API_KEY, REPLICATE_API_KEY
)

# Replicate API URL for fallback (same model as showcase)
REPLICATE_API_URL = "https://api.replicate.com/v1/models/google/imagen-3/predictions"

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
            "Transform this man's hair to short clean fade haircut, professional masculine look, keep face identical",
            "Change hair to textured modern quiff style, sleek dark color, masculine look, keep face identical",
            "Style hair as classic short business cut, neat and professional man style, keep face identical",
            "Update hair to trendy undercut with styled top, modern masculine look, keep face identical"
        ]
    },
    {
        'step': 2,
        'name': 'clothing',
        'prompts': [
            "Dress this man in professional athletic wear, sporty masculine modern style, keep face and hair identical",
            "Put him in fitted casual business attire, sophisticated man look, keep face and hair identical",
            "Dress in trendy men's streetwear fitness outfit, urban masculine style, keep face and hair identical",
            "Wear high-end luxury men's sportswear, premium masculine aesthetic, keep face and hair identical"
        ]
    },
    {
        'step': 3,
        'name': 'background',
        'prompts': [
            "Place in modern luxury gym setting with dramatic lighting, keep person identical",
            "Set background to outdoor urban sports setting with morning light, keep person identical",
            "Put in professional photo studio with neutral backdrop, keep person identical",
            "Place in urban rooftop setting with city skyline, keep person identical"
        ]
    },
    {
        'step': 4,
        'name': 'facial_features',
        'prompts': [
            "Enhance with natural masculine grooming, clean shaven fresh look, keep face structure identical",
            "Add well-groomed short beard, defined masculine jawline, keep face structure identical",
            "Natural masculine look with light stubble, athletic healthy appearance, keep face structure identical",
            "Clean professional appearance, confident masculine expression, keep face structure identical"
        ]
    },
    {
        'step': 5,
        'name': 'skin_tone',
        'prompts': [
            "Adjust skin to slightly sun-kissed warm bronze glow, healthy masculine skin, keep everything else identical",
            "Refine skin to natural fair tone with healthy undertones, clean look, keep everything else identical",
            "Enhance skin to natural olive Mediterranean tone, healthy athletic glow, keep everything else identical",
            "Adjust skin to light golden tan, healthy masculine complexion, keep everything else identical"
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
    This is SYNCHRONOUS - waits for result (up to 120s).
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
            "aspect_ratio": "1:1",
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
    """Call Google Gemini API for image transformation, with Replicate fallback"""
    
    # Try Gemini first if API key is configured
    if NANO_BANANA_API_KEY:
        try:
            # Use Gemini Pro model for image generation
            api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp-image-generation:generateContent?key={NANO_BANANA_API_KEY}"
            
            headers = {
                "Content-Type": "application/json"
            }
            
            payload = {
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": "image/jpeg", "data": image_base64}}
                    ]
                }],
                "generationConfig": {
                    "responseModalities": ["TEXT", "IMAGE"]
                }
            }
            
            api_response = requests.post(api_url, headers=headers, json=payload, timeout=120)
            
            if api_response.ok:
                result = api_response.json()
                
                # Extract image from Gemini response format
                for candidate in result.get('candidates', []):
                    for part in candidate.get('content', {}).get('parts', []):
                        if 'inlineData' in part:
                            print("Gemini transformation successful")
                            return part['inlineData']['data']
                
                print("No image in Gemini response, trying fallback...")
            else:
                print(f"Gemini API error status: {api_response.status_code}")
                print(f"Gemini API error body: {api_response.text[:500]}")
                
                # Check for quota exceeded (429) or other server errors
                if api_response.status_code in [429, 500, 503]:
                    print("Gemini quota/server error, trying Replicate fallback...")
                else:
                    # For other errors, still try fallback
                    print(f"Gemini error {api_response.status_code}, trying fallback...")
                    
        except requests.exceptions.RequestException as e:
            print(f"Gemini API request error: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"Response content: {e.response.text[:500]}")
    else:
        print("NANO_BANANA_API_KEY not configured, using Replicate directly")
    
    # Fallback to Replicate
    if use_fallback and REPLICATE_API_KEY:
        print("Attempting Replicate fallback for transformation...")
        return call_replicate_api_sync(image_base64, prompt)
    
    raise Exception("Both Gemini and Replicate APIs failed or not configured")


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
            'created_at': datetime.now().isoformat(),
            'status': 'in_progress'
        }
        
        table.put_item(Item=session)
        
        return response(200, {
            'success': True,
            'session_id': session_id,
            'step': 1,
            'step_name': step_config['name'],
            'total_steps': len(TRANSFORMATION_STEPS),
            'variations': variations
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
    selected_index = body.get('selected_index')
    selected_image = body.get('selected_image')  # base64
    
    if not session_id:
        return response(400, {'error': 'session_id is required'})
    
    if selected_index is None or selected_image is None:
        return response(400, {'error': 'selected_index and selected_image are required'})
    
    try:
        result = table.get_item(Key={'pk': 'TRANSFORM_SESSION', 'sk': session_id})
        session = result.get('Item')
        
        if not session:
            return response(404, {'error': 'Session not found'})
        
        current_step = int(session.get('current_step', 1))
        gender = session.get('gender', 'female')  # Get gender from session
        transformation_steps = get_transformation_steps(gender)
        
        selections = session.get('selections', {})
        selections[str(current_step)] = {
            'index': selected_index,
            'step_name': transformation_steps[current_step - 1]['name']
        }
        
        next_step = current_step + 1
        
        # Save selected image to S3
        selected_image_key = f"transform_sessions/{session_id}/step{current_step}_selected.png"
        image_data = base64.b64decode(selected_image)
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=selected_image_key,
            Body=image_data,
            ContentType='image/png'
        )
        selected_image_url = f"https://{S3_BUCKET}.s3.amazonaws.com/{selected_image_key}"
        
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
                    ':curr_url': selected_image_url
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
        variations = generate_transformation_variations(selected_image, step_config)
        
        # Store variation images in S3
        for i, var in enumerate(variations):
            if 'image_base64' in var and not var.get('error'):
                var_key = f"transform_sessions/{session_id}/step{next_step}_var{i}.png"
                var_data = base64.b64decode(var['image_base64'])
                s3.put_object(
                    Bucket=S3_BUCKET,
                    Key=var_key,
                    Body=var_data,
                    ContentType='image/png'
                )
                var['image_url'] = f"https://{S3_BUCKET}.s3.amazonaws.com/{var_key}"
        
        table.update_item(
            Key={'pk': 'TRANSFORM_SESSION', 'sk': session_id},
            UpdateExpression="SET current_step = :step, current_image_url = :img, selections = :sel",
            ExpressionAttributeValues={
                ':step': next_step,
                ':img': selected_image_url,
                ':sel': selections
            }
        )
        
        return response(200, {
            'success': True,
            'session_id': session_id,
            'step': next_step,
            'step_name': step_config['name'],
            'total_steps': len(transformation_steps),
            'variations': variations,
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
        
        return response(200, {
            'session_id': session_id,
            'name': session.get('name'),
            'gender': gender,
            'current_step': int(session.get('current_step', 1)),
            'total_steps': len(transformation_steps),
            'status': session.get('status'),
            'selections': session.get('selections', {}),
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
