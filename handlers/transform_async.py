"""
Image transformation handlers using Gemini API - ASYNC ARCHITECTURE
"""
import json
import uuid
import base64
import requests
from datetime import datetime
from decimal import Decimal

from config import (
    response, decimal_to_python, verify_admin,
    ambassadors_table, s3, S3_BUCKET, NANO_BANANA_API_KEY, dynamodb
)

# Create jobs table reference
jobs_table = dynamodb.Table('nano_banana_jobs')

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


def call_gemini_api(image_base64, prompt):
    """Call Google Gemini API for image transformation"""
    if not NANO_BANANA_API_KEY:
        raise Exception("GEMINI_API_KEY not configured")
    
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


def start_transformation(event):
    """Start transformation - Returns session_id immediately, generates images async"""
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
        
        # Create session in DynamoDB with initial status
        session = {
            'id': session_id,
            'type': 'TRANSFORM_JOB',
            'name': name,
            'original_image_url': original_image_url,
            'current_step': 1,
            'status': 'generating',  # generating, ready, error, completed
            'progress': Decimal('0'),  # 0-100
            'step_1_variations': [],
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat()
        }
        
        jobs_table.put_item(Item=session)
        
        # Start generating variations (this will take time)
        generate_step_variations_async(session_id, 1, image_base64)
        
        # Return immediately
        return response(200, {
            'success': True,
            'session_id': session_id,
            'status': 'generating',
            'message': 'Transformation started. Poll /status to get progress.'
        })
        
    except Exception as e:
        print(f"Error starting transformation: {e}")
        return response(500, {'error': f'Failed to start transformation: {str(e)}'})


def generate_step_variations_async(session_id, step_number, image_base64):
    """Generate 4 variations ONE BY ONE, updating DynamoDB after each"""
    step_config = TRANSFORMATION_STEPS[step_number - 1]
    total_variations = len(step_config['prompts'])
    
    for i, prompt in enumerate(step_config['prompts']):
        try:
            print(f"[{session_id}] Generating step {step_number}, variation {i+1}/{total_variations}")
            
            # Generate image
            transformed_image = call_gemini_api(image_base64, prompt)
            
            # Store in S3
            var_key = f"transform_sessions/{session_id}/step{step_number}_var{i}.png"
            var_data = base64.b64decode(transformed_image)
            s3.put_object(
                Bucket=S3_BUCKET,
                Key=var_key,
                Body=var_data,
                ContentType='image/png'
            )
            image_url = f"https://{S3_BUCKET}.s3.amazonaws.com/{var_key}"
            
            variation_data = {
                'index': i,
                'prompt': prompt,
                'image_url': image_url,
                'image_base64': transformed_image  # Keep for client
            }
            
            # Update DynamoDB
            update_session_variation(session_id, step_number, i, variation_data, total_variations)
            
            print(f"[{session_id}] ✓ Variation {i+1}/{total_variations} done")
            
        except Exception as e:
            print(f"[{session_id}] ✗ Error variation {i}: {e}")
            
            error_data = {
                'index': i,
                'prompt': prompt,
                'error': str(e)
            }
            
            update_session_variation(session_id, step_number, i, error_data, total_variations)
    
    # Mark step as complete
    mark_step_ready(session_id, step_number)


def update_session_variation(session_id, step_number, variation_index, variation_data, total_variations):
    """Update a single variation in DynamoDB"""
    try:
        # Calculate progress
        progress = Decimal(str(((variation_index + 1) / total_variations) * 100))
        
        # Build the list with all previous variations + this new one
        result = jobs_table.get_item(Key={'id': session_id})
        session = result.get('Item', {})
        variations = session.get(f'step_{step_number}_variations', [])
        
        # Ensure list is big enough
        while len(variations) <= variation_index:
            variations.append({})
        
        variations[variation_index] = variation_data
        
        # Update DynamoDB
        jobs_table.update_item(
            Key={'id': session_id},
            UpdateExpression=f'SET step_{step_number}_variations = :vars, progress = :prog, updated_at = :updated',
            ExpressionAttributeValues={
                ':vars': variations,
                ':prog': progress,
                ':updated': datetime.now().isoformat()
            }
        )
        
    except Exception as e:
        print(f"Error updating variation in DynamoDB: {e}")


def mark_step_ready(session_id, step_number):
    """Mark step as ready for selection"""
    try:
        jobs_table.update_item(
            Key={'id': session_id},
            UpdateExpression='SET #status = :status, progress = :prog, updated_at = :updated',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'ready',
                ':prog': Decimal('100'),
                ':updated': datetime.now().isoformat()
            }
        )
        print(f"[{session_id}] Step {step_number} marked as READY")
    except Exception as e:
        print(f"Error marking step ready: {e}")


def get_transformation_session(event):
    """Get transformation session status - GET /api/admin/ambassadors/transform/session?session_id=XXX"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    params = event.get('queryStringParameters', {}) or {}
    session_id = params.get('session_id')
    
    if not session_id:
        return response(400, {'error': 'session_id is required'})
    
    try:
        result = jobs_table.get_item(Key={'id': session_id})
        session = result.get('Item')
        
        if not session:
            return response(404, {'error': 'Session not found'})
        
        # Convert Decimal to Python types
        session = decimal_to_python(session)
        
        current_step = session.get('current_step', 1)
        step_config = TRANSFORMATION_STEPS[current_step - 1]
        
        return response(200, {
            'success': True,
            'session_id': session_id,
            'name': session.get('name'),
            'status': session.get('status'),  # generating, ready, completed
            'progress': session.get('progress', 0),
            'current_step': current_step,
            'step_name': step_config['name'],
            'total_steps': len(TRANSFORMATION_STEPS),
            'variations': session.get(f'step_{current_step}_variations', []),
            'selections': session.get('selections', {})
        })
        
    except Exception as e:
        print(f"Error getting session: {e}")
        return response(500, {'error': f'Failed to get session: {str(e)}'})


def continue_transformation(event):
    """Continue to next step with selected variation"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    session_id = body.get('session_id')
    selected_index = body.get('selected_index')
    selected_image = body.get('selected_image')  # base64
    
    if not session_id or selected_index is None or not selected_image:
        return response(400, {'error': 'session_id, selected_index and selected_image are required'})
    
    try:
        result = jobs_table.get_item(Key={'id': session_id})
        session = result.get('Item')
        
        if not session:
            return response(404, {'error': 'Session not found'})
        
        current_step = int(session.get('current_step', 1))
        next_step = current_step + 1
        
        # Save selection
        selections = session.get('selections', {})
        selections[str(current_step)] = {
            'index': selected_index,
            'step_name': TRANSFORMATION_STEPS[current_step - 1]['name']
        }
        
        # Save selected image as current
        selected_image_key = f"transform_sessions/{session_id}/step{current_step}_selected.png"
        selected_data = base64.b64decode(selected_image)
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=selected_image_key,
            Body=selected_data,
            ContentType='image/png'
        )
        current_image_url = f"https://{S3_BUCKET}.s3.amazonaws.com/{selected_image_key}"
        
        if next_step > len(TRANSFORMATION_STEPS):
            # ALL STEPS DONE
            jobs_table.update_item(
                Key={'id': session_id},
                UpdateExpression='SET #status = :status, selections = :sel, final_image_url = :final, updated_at = :updated',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={
                    ':status': 'completed',
                    ':sel': selections,
                    ':final': current_image_url,
                    ':updated': datetime.now().isoformat()
                }
            )
            
            return response(200, {
                'success': True,
                'completed': True,
                'final_image_url': current_image_url,
                'session_id': session_id
            })
        
        else:
            # CONTINUE TO NEXT STEP
            jobs_table.update_item(
                Key={'id': session_id},
                UpdateExpression='SET current_step = :step, #status = :status, selections = :sel, current_image_url = :img, progress = :prog, updated_at = :updated',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={
                    ':step': next_step,
                    ':status': 'generating',
                    ':sel': selections,
                    ':img': current_image_url,
                    ':prog': Decimal('0'),
                    ':updated': datetime.now().isoformat()
                }
            )
            
            # Start generating next step async
            generate_step_variations_async(session_id, next_step, selected_image)
            
            return response(200, {
                'success': True,
                'completed': False,
                'session_id': session_id,
                'step': next_step,
                'step_name': TRANSFORMATION_STEPS[next_step - 1]['name'],
                'status': 'generating',
                'message': 'Next step started. Poll /status for progress.'
            })
        
    except Exception as e:
        print(f"Error continuing transformation: {e}")
        return response(500, {'error': f'Failed to continue: {str(e)}'})


def finalize_ambassador(event):
    """Finalize and create ambassador from completed session"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    session_id = body.get('session_id')
    description = body.get('description', '').strip()
    gender = body.get('gender', 'female')
    style = body.get('style', '').strip()
    
    if not session_id:
        return response(400, {'error': 'session_id is required'})
    
    try:
        result = jobs_table.get_item(Key={'id': session_id})
        session = result.get('Item')
        
        if not session:
            return response(404, {'error': 'Session not found'})
        
        if session.get('status') != 'completed':
            return response(400, {'error': 'Transformation not completed yet'})
        
        # Create ambassador
        ambassador_id = str(uuid.uuid4())
        ambassador = {
            'id': ambassador_id,
            'name': session.get('name'),
            'description': description,
            'gender': gender,
            'style': style,
            'photo_profile': session.get('final_image_url'),
            'original_image_url': session.get('original_image_url'),
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat()
        }
        
        ambassadors_table.put_item(Item=ambassador)
        
        # Clean up session (optional)
        # table.delete_item(Key={'pk': 'TRANSFORM_SESSION', 'sk': session_id})
        
        return response(200, {
            'success': True,
            'ambassador': decimal_to_python(ambassador)
        })
        
    except Exception as e:
        print(f"Error finalizing ambassador: {e}")
        return response(500, {'error': f'Failed to finalize: {str(e)}'})
