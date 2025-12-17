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
    generate_outfit_variations_descriptions
)
from handlers.gemini_client import generate_image

# DynamoDB tables
outfits_table = dynamodb.Table('outfits')
jobs_table = dynamodb.Table('nano_banana_jobs')

# Default and max variations
DEFAULT_VARIATIONS = 6
MAX_VARIATIONS = 30


def generate_single_variation_image(description: str, index: int, job_id: str, outfit_id: str, gender: str = 'unisex', original_image_base64: str = None) -> dict:
    """
    Generate a single outfit variation image using Nano Banana Pro (Gemini 3 Pro Image).
    Uses the original image as reference to create a variation.
    Saves the result directly to S3 and updates the job in DynamoDB.
    
    Args:
        description: The variation description
        index: The variation index (0-5)
        job_id: The job ID for tracking
        outfit_id: The outfit ID for S3 path
        gender: The gender for the clothing item (male, female, unisex)
        original_image_base64: The original outfit image in base64 format
    
    Returns:
        dict with 'index', 'description', 'image_url' or 'error'
    """
    try:
        # Map gender to clothing category description
        gender_context = {
            'male': "men's clothing / masculine style",
            'female': "women's clothing / feminine style",
            'unisex': "unisex clothing"
        }.get(gender, 'unisex clothing')
        
        prompt = f"""Based on the provided clothing item image, create a VARIATION with these changes:

{description}

CRITICAL INSTRUCTIONS:
1. Use the PROVIDED IMAGE as the base reference - keep the same garment TYPE and SILHOUETTE
2. Apply ONLY the modifications described above (color, pattern, details changes)
3. MAINTAIN the exact same proportions, scale, and positioning as the original
4. This is {gender_context} - keep the same gender-appropriate fit and style
5. Keep the same photography style: flat lay or invisible mannequin on pure white background

Requirements:
- Pure white background (#FFFFFF)
- SAME garment proportions and scale as the original image
- SAME positioning and angle as the original
- E-commerce quality product photography
- NO human model visible
- Square format (1:1), centered composition
- Apply ONLY the described variation, keep everything else identical
"""
        
        # Build reference images list
        reference_images = []
        if original_image_base64:
            reference_images.append(original_image_base64)
        
        # Call Gemini API via gemini_client (with Vertex AI fallback)
        try:
            image_base64 = generate_image(
                prompt=prompt,
                reference_images=reference_images if reference_images else None,
                aspect_ratio="1:1",
                image_size="1K"
            )
        except Exception as api_error:
            print(f"Gemini API error for variation {index}: {api_error}")
            return {
                'index': index,
                'description': description,
                'error': f"API error: {str(api_error)}"
            }
        
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
        
        # Get number of variations from request body (default 6, max 30)
        try:
            body = json.loads(event.get('body', '{}') or '{}')
        except:
            body = {}
        num_variations = body.get('num_variations', DEFAULT_VARIATIONS)
        num_variations = max(1, min(MAX_VARIATIONS, int(num_variations)))
        
        # Get the outfit from DynamoDB
        result = outfits_table.get_item(Key={'id': outfit_id})
        outfit = result.get('Item')
        
        if not outfit:
            return response(404, {'error': 'Outfit not found'})
        
        image_url = outfit.get('image_url')
        description = outfit.get('description', 'Tenue sport')
        gender = outfit.get('gender', 'unisex')
        
        if not image_url:
            return response(400, {'error': 'Outfit has no image'})
        
        print(f"Starting variations job for outfit {outfit_id}: {description}")
        
        # Download the image from S3
        s3_key = image_url.replace(f"https://{S3_BUCKET}.s3.amazonaws.com/", "")
        s3_response = s3.get_object(Bucket=S3_BUCKET, Key=s3_key)
        image_bytes = s3_response['Body'].read()
        image_base64 = base64.b64encode(image_bytes).decode('utf-8')
        
        # Generate variation descriptions using Bedrock Claude (this is fast, ~8s)
        print(f"Generating {num_variations} variation descriptions with Bedrock Claude...")
        variation_descriptions = generate_outfit_variations_descriptions(image_base64, description, num_variations)
        
        print(f"Generated {len(variation_descriptions)} variation descriptions")
        for i, desc in enumerate(variation_descriptions):
            print(f"  {i+1}. {desc}")
        
        # Create job record
        job_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        # Prepare variations list with pending status
        variations = []
        for i, desc in enumerate(variation_descriptions[:num_variations]):
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
            'gender': gender,  # Store gender for image generation
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
        
        # Get the original outfit image from S3
        outfit_result = outfits_table.get_item(Key={'id': outfit_id})
        outfit = outfit_result.get('Item')
        
        original_image_base64 = None
        if outfit and outfit.get('image_url'):
            try:
                image_url = outfit.get('image_url')
                s3_key = image_url.replace(f"https://{S3_BUCKET}.s3.amazonaws.com/", "")
                s3_response = s3.get_object(Bucket=S3_BUCKET, Key=s3_key)
                image_bytes = s3_response['Body'].read()
                original_image_base64 = base64.b64encode(image_bytes).decode('utf-8')
                print(f"Loaded original image from S3: {s3_key} ({len(image_bytes)} bytes)")
            except Exception as e:
                print(f"Warning: Could not load original image: {e}")
        
        # Generate the image with the original as reference
        gender = job.get('gender', 'unisex')
        print(f"Generating variation {variation_index} for job {job_id} (gender: {gender}, with_image: {original_image_base64 is not None})")
        result = generate_single_variation_image(
            variation['description'],
            variation_index,
            job_id,
            outfit_id,
            gender,
            original_image_base64
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
