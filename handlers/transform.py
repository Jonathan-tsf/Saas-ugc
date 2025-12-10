"""
Image transformation handlers using Nano Banana Pro API
"""
import json
import uuid
import base64
import requests
from datetime import datetime

from config import (
    response, decimal_to_python, verify_admin,
    table, ambassadors_table, s3, S3_BUCKET, NANO_BANANA_API_KEY
)

# Transformation steps configuration
TRANSFORMATION_STEPS = [
    {
        'step': 1,
        'name': 'hair',
        'prompts': [
            "Change hair to elegant wavy blonde highlights, professional look",
            "Transform hair to sleek dark brunette with subtle layers",
            "Style hair as modern short pixie cut with copper tones",
            "Update hair to flowing auburn waves with natural shine"
        ]
    },
    {
        'step': 2,
        'name': 'clothing',
        'prompts': [
            "Dress in professional athletic wear, sporty modern style",
            "Wear elegant casual business attire, sophisticated look",
            "Put on trendy streetwear fitness outfit, urban style",
            "Dress in high-end luxury sportswear, premium aesthetic"
        ]
    },
    {
        'step': 3,
        'name': 'background',
        'prompts': [
            "Place in modern luxury gym setting with soft lighting",
            "Set background to outdoor natural park with morning light",
            "Put in professional photo studio with neutral backdrop",
            "Place in urban rooftop setting with city skyline"
        ]
    },
    {
        'step': 4,
        'name': 'facial_features',
        'prompts': [
            "Enhance with natural makeup, subtle contouring, fresh look",
            "Apply glamorous makeup with defined eyes, elegant style",
            "Add minimal natural makeup, dewy skin, athletic glow",
            "Style with bold expressive makeup, confident appearance"
        ]
    },
    {
        'step': 5,
        'name': 'skin_tone',
        'prompts': [
            "Adjust skin to slightly sun-kissed warm bronze glow",
            "Refine skin to fair porcelain with healthy undertones",
            "Enhance skin to natural olive Mediterranean tone",
            "Adjust skin to light golden summer tan"
        ]
    }
]


def call_nano_banana_api(image_base64, prompt):
    """Call Google Gemini API for image transformation"""
    if not NANO_BANANA_API_KEY:
        raise Exception("NANO_BANANA_API_KEY not configured")
    
    # Use Gemini Pro model for image generation
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-image-preview:generateContent?key={NANO_BANANA_API_KEY}"
    
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
            "responseModalities": ["IMAGE"]
        }
    }
    
    try:
        api_response = requests.post(api_url, headers=headers, json=payload, timeout=120)
        
        if not api_response.ok:
            print(f"Gemini API error status: {api_response.status_code}")
            print(f"Gemini API error body: {api_response.text}")
            
        api_response.raise_for_status()
        result = api_response.json()
        
        # Extract image from Gemini response format
        for candidate in result.get('candidates', []):
            for part in candidate.get('content', {}).get('parts', []):
                if 'inlineData' in part:
                    return part['inlineData']['data']
        
        raise Exception("No image in API response")
            
    except requests.exceptions.RequestException as e:
        print(f"Gemini API error: {e}")
        if hasattr(e, 'response') and e.response is not None:
             print(f"Response content: {e.response.text}")
        raise Exception(f"Image transformation failed: {str(e)}")


def generate_transformation_variations(session_id, step_number, image_base64, step_config):
    """Generate 4 variations for a transformation step - ONE BY ONE with DynamoDB updates"""
    variations = []
    
    for i, prompt in enumerate(step_config['prompts']):
        try:
            print(f"Generating variation {i+1}/4 for step {step_number}")
            transformed_image = call_nano_banana_api(image_base64, prompt)
            
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
    
    if not image_base64:
        return response(400, {'error': 'image_base64 is required'})
    
    if not name:
        return response(400, {'error': 'name is required'})
    
    session_id = str(uuid.uuid4())
    
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
        
        # Create session in DynamoDB with status "generating"
        session = {
            'id': session_id,
            'pk': 'TRANSFORM_SESSION',
            'sk': session_id,
            'name': name,
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
        
        selections = session.get('selections', {})
        selections[str(current_step)] = {
            'index': selected_index,
            'step_name': TRANSFORMATION_STEPS[current_step - 1]['name']
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
        
        if next_step > len(TRANSFORMATION_STEPS):
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
                'name': session.get('name')
            })
        
        step_config = TRANSFORMATION_STEPS[next_step - 1]
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
            'total_steps': len(TRANSFORMATION_STEPS),
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
        
        return response(200, {
            'session_id': session_id,
            'name': session.get('name'),
            'current_step': int(session.get('current_step', 1)),
            'total_steps': len(TRANSFORMATION_STEPS),
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
    gender = body.get('gender', 'female')
    style = body.get('style', '')
    
    if not session_id:
        return response(400, {'error': 'session_id is required'})
    
    try:
        result = table.get_item(Key={'pk': 'TRANSFORM_SESSION', 'sk': session_id})
        session = result.get('Item')
        
        if not session:
            return response(404, {'error': 'Session not found'})
        
        if session.get('status') != 'completed':
            return response(400, {'error': 'Transformation not completed'})
        
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
