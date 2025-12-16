"""
Outfit Variations Handler
Generate 6 variations of an outfit using Nano Banana Pro (Gemini 3 Pro Image)

Uses async job system with polling to avoid API Gateway 29-second timeout.
"""
import json
import uuid
import base64
import requests
from datetime import datetime
from decimal import Decimal

from config import (
    response, decimal_to_python, verify_admin,
    dynamodb, s3, S3_BUCKET, upload_to_s3,
    generate_outfit_variations_descriptions, NANO_BANANA_API_KEY
)

# DynamoDB tables
outfits_table = dynamodb.Table('outfits')
jobs_table = dynamodb.Table('nano_banana_jobs')

# Gemini 3 Pro Image (Nano Banana Pro) endpoint
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-image-preview:generateContent"

# Number of variations to generate (reduced from 10 to fit timing constraints)
NUM_VARIATIONS = 6


def generate_single_variation_image(description: str, index: int, job_id: str, outfit_id: str) -> dict:
    """
    Generate a single outfit variation image using Nano Banana Pro (Gemini 3 Pro Image).
    Saves the result directly to S3 and updates the job in DynamoDB.
    
    Args:
        description: The variation description
        index: The variation index (0-5)
        job_id: The job ID for tracking
        outfit_id: The outfit ID for S3 path
    
    Returns:
        dict with 'index', 'description', 'image_url' or 'error'
    """
    try:
        prompt = f"""Generate a professional product photo of this clothing item on a pure white background:

{description}

Requirements:
- Product photography style, e-commerce quality
- Pure white background (#FFFFFF)
- The clothing item should be displayed flat lay or on invisible mannequin
- NO human model visible
- High quality, well-lit, professional
- Show the garment's details, texture, and colors clearly
- Square format, centered composition
"""
        
        headers = {
            "Content-Type": "application/json"
        }
        
        payload = {
            "contents": [{
                "parts": [{
                    "text": prompt
                }]
            }],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": {
                    "aspectRatio": "1:1",
                    "imageSize": "1K"
                }
            }
        }
        
        # Call Gemini API with API key
        api_url = f"{GEMINI_API_URL}?key={NANO_BANANA_API_KEY}"
        
        resp = requests.post(
            api_url,
            headers=headers,
            json=payload,
            timeout=120
        )
        
        if resp.status_code != 200:
            print(f"Gemini API error for variation {index}: {resp.status_code} - {resp.text}")
            return {
                'index': index,
                'description': description,
                'error': f"API error: {resp.status_code}"
            }
        
        result = resp.json()
        
        # Extract the image from the response
        candidates = result.get('candidates', [])
        if not candidates:
            return {
                'index': index,
                'description': description,
                'error': "No candidates in response"
            }
        
        content = candidates[0].get('content', {})
        parts = content.get('parts', [])
        
        image_base64 = None
        for part in parts:
            if 'inlineData' in part:
                image_base64 = part['inlineData'].get('data')
                break
        
        if not image_base64:
            return {
                'index': index,
                'description': description,
                'error': "No image in response"
            }
        
        # Save image to S3
        s3_key = f"outfit-variations/{outfit_id}/{job_id}/variation_{index}.png"
        image_data = base64.b64decode(image_base64)
        image_url = upload_to_s3(s3_key, image_data, 'image/png', cache_days=7)
        
        return {
            'index': index,
            'description': description,
            'image_url': image_url
        }
        
    except Exception as e:
        print(f"Error generating variation {index}: {e}")
        return {
            'index': index,
            'description': description,
            'error': str(e)
        }


def start_outfit_variations(event):
    """
    Start generating variations for an outfit - POST /api/admin/outfits/{id}/variations
    
    This is async: creates a job and returns immediately with job_id.
    The client then polls for status.
    
    Flow:
    1. Fetches the outfit from DynamoDB
    2. Downloads the outfit image from S3
    3. Uses Bedrock Claude to generate variation descriptions (fast, ~8s)
    4. Creates a job with status 'ready' and the descriptions
    5. Returns job_id for polling
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        path_params = event.get('pathParameters', {}) or {}
        outfit_id = path_params.get('id')
        
        if not outfit_id:
            return response(400, {'error': 'Outfit ID is required'})
        
        # Get the outfit from DynamoDB
        result = outfits_table.get_item(Key={'id': outfit_id})
        outfit = result.get('Item')
        
        if not outfit:
            return response(404, {'error': 'Outfit not found'})
        
        image_url = outfit.get('image_url')
        description = outfit.get('description', 'Tenue sport')
        
        if not image_url:
            return response(400, {'error': 'Outfit has no image'})
        
        print(f"Starting variations job for outfit {outfit_id}: {description}")
        
        # Download the image from S3
        s3_key = image_url.replace(f"https://{S3_BUCKET}.s3.amazonaws.com/", "")
        s3_response = s3.get_object(Bucket=S3_BUCKET, Key=s3_key)
        image_bytes = s3_response['Body'].read()
        image_base64 = base64.b64encode(image_bytes).decode('utf-8')
        
        # Generate variation descriptions using Bedrock Claude (this is fast, ~8s)
        print("Generating variation descriptions with Bedrock Claude...")
        variation_descriptions = generate_outfit_variations_descriptions(image_base64, description)
        
        print(f"Generated {len(variation_descriptions)} variation descriptions")
        for i, desc in enumerate(variation_descriptions):
            print(f"  {i+1}. {desc}")
        
        # Create job record
        job_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        # Prepare variations list with pending status
        variations = []
        for i, desc in enumerate(variation_descriptions[:NUM_VARIATIONS]):
            variations.append({
                'index': i,
                'description': desc,
                'status': 'pending',
                'image_url': None,
                'error': None
            })
        
        job_item = {
            'id': job_id,  # Primary key for nano_banana_jobs table
            'job_id': job_id,  # Also keep job_id for convenience
            'job_type': 'outfit_variations',
            'outfit_id': outfit_id,
            'original_description': description,
            'status': 'ready',  # Ready to generate images
            'variations': variations,
            'completed_count': 0,
            'total_count': len(variations),
            'created_at': now,
            'updated_at': now,
            'ttl': int(datetime.now().timestamp()) + 86400  # 24 hour TTL
        }
        
        jobs_table.put_item(Item=job_item)
        
        return response(200, {
            'success': True,
            'job_id': job_id,
            'status': 'ready',
            'outfit_id': outfit_id,
            'original_description': description,
            'total_variations': len(variations),
            'variations': variations,
            'message': 'Variation descriptions generated. Call /generate endpoint to create images.'
        })
        
    except Exception as e:
        print(f"Error starting outfit variations: {e}")
        import traceback
        traceback.print_exc()
        return response(500, {'error': f'Failed to start variations: {str(e)}'})


def generate_variation_image(event):
    """
    Generate a single variation image - POST /api/admin/outfits/{id}/variations/generate
    
    Body: {
        "job_id": "uuid",
        "variation_index": 0
    }
    
    Generates one image at a time to avoid timeout.
    Returns the image URL when done.
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        path_params = event.get('pathParameters', {}) or {}
        outfit_id = path_params.get('id')
        
        if not outfit_id:
            return response(400, {'error': 'Outfit ID is required'})
        
        body = json.loads(event.get('body', '{}'))
        job_id = body.get('job_id')
        variation_index = body.get('variation_index')
        
        if not job_id:
            return response(400, {'error': 'job_id is required'})
        
        if variation_index is None:
            return response(400, {'error': 'variation_index is required'})
        
        # Get the job
        result = jobs_table.get_item(Key={'id': job_id})
        job = result.get('Item')
        
        if not job:
            return response(404, {'error': 'Job not found'})
        
        if job.get('outfit_id') != outfit_id:
            return response(400, {'error': 'Job does not match outfit'})
        
        variations = job.get('variations', [])
        
        if variation_index < 0 or variation_index >= len(variations):
            return response(400, {'error': f'Invalid variation_index. Must be 0-{len(variations)-1}'})
        
        variation = variations[variation_index]
        
        # Check if already generated
        if variation.get('status') == 'completed' and variation.get('image_url'):
            return response(200, {
                'success': True,
                'variation': variation,
                'already_generated': True
            })
        
        # Mark as generating
        variation['status'] = 'generating'
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET variations = :vars, updated_at = :updated',
            ExpressionAttributeValues={
                ':vars': variations,
                ':updated': datetime.now().isoformat()
            }
        )
        
        # Generate the image
        print(f"Generating variation {variation_index} for job {job_id}")
        result = generate_single_variation_image(
            variation['description'],
            variation_index,
            job_id,
            outfit_id
        )
        
        # Update the variation with result
        if 'error' in result:
            variation['status'] = 'failed'
            variation['error'] = result['error']
        else:
            variation['status'] = 'completed'
            variation['image_url'] = result['image_url']
        
        # Count completed
        completed_count = sum(1 for v in variations if v.get('status') == 'completed')
        failed_count = sum(1 for v in variations if v.get('status') == 'failed')
        
        # Determine job status
        if completed_count + failed_count >= len(variations):
            job_status = 'completed'
        else:
            job_status = 'in_progress'
        
        # Update job
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET variations = :vars, #status = :status, completed_count = :completed, updated_at = :updated',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':vars': variations,
                ':status': job_status,
                ':completed': completed_count,
                ':updated': datetime.now().isoformat()
            }
        )
        
        return response(200, {
            'success': 'error' not in result,
            'variation': variation,
            'job_status': job_status,
            'completed_count': completed_count,
            'total_count': len(variations)
        })
        
    except Exception as e:
        print(f"Error generating variation image: {e}")
        import traceback
        traceback.print_exc()
        return response(500, {'error': f'Failed to generate variation: {str(e)}'})


def get_variations_job_status(event):
    """
    Get status of a variations job - GET /api/admin/outfits/{id}/variations/status?job_id=xxx
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        path_params = event.get('pathParameters', {}) or {}
        outfit_id = path_params.get('id')
        
        query_params = event.get('queryStringParameters', {}) or {}
        job_id = query_params.get('job_id')
        
        if not outfit_id:
            return response(400, {'error': 'Outfit ID is required'})
        
        if not job_id:
            return response(400, {'error': 'job_id query parameter is required'})
        
        # Get the job
        result = jobs_table.get_item(Key={'id': job_id})
        job = result.get('Item')
        
        if not job:
            return response(404, {'error': 'Job not found'})
        
        if job.get('outfit_id') != outfit_id:
            return response(400, {'error': 'Job does not match outfit'})
        
        return response(200, {
            'success': True,
            'job': decimal_to_python(job)
        })
        
    except Exception as e:
        print(f"Error getting variations job status: {e}")
        return response(500, {'error': f'Failed to get job status: {str(e)}'})


def apply_outfit_variation(event):
    """
    Apply selected variations as NEW outfits - PUT /api/admin/outfits/{id}/variations
    
    Each selected variation creates a NEW outfit with the same gender and a generated type.
    The original outfit is NOT modified.
    
    Body: {
        "variations": [
            {"description": "...", "image_url": "..."},
            {"description": "...", "image_url": "..."}
        ]
    }
    
    OR single variation (legacy support):
    {
        "description": "New description",
        "image_url": "S3 URL of the variation image"
    }
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        path_params = event.get('pathParameters', {}) or {}
        outfit_id = path_params.get('id')
        
        if not outfit_id:
            return response(400, {'error': 'Outfit ID is required'})
        
        body = json.loads(event.get('body', '{}'))
        
        # Check if outfit exists to get gender
        result = outfits_table.get_item(Key={'id': outfit_id})
        original_outfit = result.get('Item')
        
        if not original_outfit:
            return response(404, {'error': 'Outfit not found'})
        
        gender = original_outfit.get('gender', 'unisex')
        
        # Support both array format and legacy single variation format
        variations = body.get('variations', [])
        
        # Legacy support: single variation
        if not variations and body.get('description'):
            variations = [{
                'description': body.get('description'),
                'image_url': body.get('image_url'),
                'image_base64': body.get('image_base64')
            }]
        
        if not variations:
            return response(400, {'error': 'variations array is required'})
        
        created_outfits = []
        errors = []
        
        for i, variation in enumerate(variations):
            try:
                new_description = variation.get('description')
                image_url = variation.get('image_url')
                image_base64 = variation.get('image_base64')
                
                if not new_description:
                    errors.append(f"Variation {i}: description is required")
                    continue
                
                if not image_url and not image_base64:
                    errors.append(f"Variation {i}: image_url or image_base64 is required")
                    continue
                
                # Generate new outfit ID
                new_outfit_id = str(uuid.uuid4())
                
                # Get image data
                if image_url:
                    s3_key = image_url.replace(f"https://{S3_BUCKET}.s3.amazonaws.com/", "")
                    s3_response = s3.get_object(Bucket=S3_BUCKET, Key=s3_key)
                    image_data = s3_response['Body'].read()
                else:
                    image_data = base64.b64decode(image_base64)
                
                # Upload to permanent outfit location
                permanent_key = f"outfits/{new_outfit_id}.png"
                final_image_url = upload_to_s3(permanent_key, image_data, 'image/png', cache_days=365)
                
                # Determine type from description using simple heuristics
                # Default to the original outfit type
                outfit_type = original_outfit.get('type', 'casual')
                description_lower = new_description.lower()
                if any(word in description_lower for word in ['sport', 'athletic', 'running', 'workout', 'training']):
                    outfit_type = 'sport'
                elif any(word in description_lower for word in ['elegant', 'formal', 'dress', 'suit', 'chic']):
                    outfit_type = 'elegant'
                elif any(word in description_lower for word in ['street', 'urban', 'hip', 'baggy']):
                    outfit_type = 'streetwear'
                elif any(word in description_lower for word in ['fitness', 'gym', 'legging', 'yoga']):
                    outfit_type = 'fitness'
                elif any(word in description_lower for word in ['outdoor', 'hiking', 'camping', 'nature']):
                    outfit_type = 'outdoor'
                elif any(word in description_lower for word in ['casual', 'everyday', 'comfortable', 'relaxed']):
                    outfit_type = 'casual'
                
                # Create NEW outfit record
                new_outfit = {
                    'id': new_outfit_id,
                    'description': new_description,
                    'type': outfit_type,
                    'gender': gender,
                    'image_url': final_image_url,
                    'ambassador_count': 0,
                    'created_at': datetime.now().isoformat(),
                    'updated_at': datetime.now().isoformat(),
                    'generated_from': outfit_id  # Track source outfit
                }
                
                outfits_table.put_item(Item=new_outfit)
                created_outfits.append(decimal_to_python(new_outfit))
                
            except Exception as e:
                print(f"Error creating outfit from variation {i}: {e}")
                errors.append(f"Variation {i}: {str(e)}")
        
        return response(200, {
            'success': True,
            'created_count': len(created_outfits),
            'outfits': created_outfits,
            'errors': errors if errors else None
        })
        
    except Exception as e:
        print(f"Error applying outfit variations: {e}")
        return response(500, {'error': f'Failed to apply variations: {str(e)}'})


# Legacy function name for backwards compatibility
def generate_outfit_variations(event):
    """
    Legacy endpoint - redirects to start_outfit_variations
    POST /api/admin/outfits/{id}/variations
    """
    return start_outfit_variations(event)
