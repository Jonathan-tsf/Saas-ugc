"""
Gender Conversion Handler
Convert outfits from one gender to another (e.g., women's legging → men's jogger)

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
    generate_gender_conversion_description, NANO_BANANA_API_KEY
)

# DynamoDB tables
outfits_table = dynamodb.Table('outfits')
jobs_table = dynamodb.Table('nano_banana_jobs')

# Gemini 3 Pro Image (Nano Banana Pro) endpoint
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-image-preview:generateContent"


def list_outfits_by_gender(event):
    """
    List all outfits filtered by gender - GET /api/admin/outfits/gender/{gender}
    
    Returns all outfits for a specific gender (male/female/unisex).
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        path_params = event.get('pathParameters', {}) or {}
        gender = path_params.get('gender', 'female')
        
        if gender not in ['male', 'female', 'unisex']:
            return response(400, {'error': 'Invalid gender. Must be male, female, or unisex'})
        
        # Scan all outfits and filter by gender
        result = outfits_table.scan()
        items = result.get('Items', [])
        
        # Filter by gender
        filtered = [
            decimal_to_python(item) 
            for item in items 
            if item.get('gender') == gender
        ]
        
        # Sort by created_at descending
        filtered.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        return response(200, {
            'success': True,
            'gender': gender,
            'count': len(filtered),
            'outfits': filtered
        })
        
    except Exception as e:
        print(f"Error listing outfits by gender: {e}")
        return response(500, {'error': str(e)})


def start_gender_conversion(event):
    """
    Start gender conversion for multiple outfits - POST /api/admin/outfits/convert-gender
    
    Body: {
        "outfit_ids": ["id1", "id2", ...],
        "target_gender": "male" or "female"
    }
    
    Creates a job that will convert all specified outfits to the target gender.
    Returns job_id for polling.
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}') or '{}')
        outfit_ids = body.get('outfit_ids', [])
        target_gender = body.get('target_gender', 'male')
        
        if not outfit_ids:
            return response(400, {'error': 'outfit_ids is required'})
        
        if target_gender not in ['male', 'female']:
            return response(400, {'error': 'target_gender must be male or female'})
        
        # Fetch all outfits
        outfits_to_convert = []
        for outfit_id in outfit_ids:
            result = outfits_table.get_item(Key={'id': outfit_id})
            outfit = result.get('Item')
            if outfit:
                outfits_to_convert.append(decimal_to_python(outfit))
        
        if not outfits_to_convert:
            return response(400, {'error': 'No valid outfits found'})
        
        # Create job record
        job_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        # Prepare conversions list
        conversions = []
        for outfit in outfits_to_convert:
            conversions.append({
                'outfit_id': outfit['id'],
                'original_description': outfit.get('description', ''),
                'original_gender': outfit.get('gender', 'female'),
                'original_image_url': outfit.get('image_url', ''),
                'original_type': outfit.get('type', ''),
                'status': 'pending',
                'new_description': None,
                'new_type': None,
                'new_image_url': None,
                'new_outfit_id': None,
                'error': None
            })
        
        job_item = {
            'id': job_id,
            'job_id': job_id,
            'job_type': 'gender_conversion',
            'target_gender': target_gender,
            'status': 'ready',
            'conversions': conversions,
            'completed_count': 0,
            'total_count': len(conversions),
            'created_at': now,
            'updated_at': now,
            'ttl': int(datetime.now().timestamp()) + 86400  # 24 hour TTL
        }
        
        jobs_table.put_item(Item=job_item)
        
        return response(200, {
            'success': True,
            'job_id': job_id,
            'status': 'ready',
            'target_gender': target_gender,
            'total_conversions': len(conversions),
            'conversions': conversions,
            'message': 'Conversion job created. Call /generate endpoint to process conversions.'
        })
        
    except Exception as e:
        print(f"Error starting gender conversion: {e}")
        import traceback
        traceback.print_exc()
        return response(500, {'error': str(e)})


def generate_conversion_image(event):
    """
    Generate a single gender conversion - POST /api/admin/outfits/convert-gender/generate
    
    Body: {
        "job_id": "uuid",
        "conversion_index": 0
    }
    
    Generates one conversion at a time to avoid timeout.
    Returns the new outfit info when done.
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}') or '{}')
        job_id = body.get('job_id')
        conversion_index = body.get('conversion_index', 0)
        
        if not job_id:
            return response(400, {'error': 'job_id is required'})
        
        # Get job from DynamoDB
        result = jobs_table.get_item(Key={'id': job_id})
        job = result.get('Item')
        
        if not job:
            return response(404, {'error': 'Job not found'})
        
        if job.get('job_type') != 'gender_conversion':
            return response(400, {'error': 'Invalid job type'})
        
        conversions = job.get('conversions', [])
        target_gender = job.get('target_gender', 'male')
        
        if conversion_index >= len(conversions):
            return response(400, {'error': 'Invalid conversion index'})
        
        conversion = conversions[conversion_index]
        
        # Skip if already processed
        if conversion.get('status') == 'completed':
            return response(200, {
                'success': True,
                'status': 'already_completed',
                'conversion': conversion
            })
        
        # Download original image from S3
        original_image_url = conversion.get('original_image_url', '')
        s3_key = original_image_url.replace(f"https://{S3_BUCKET}.s3.amazonaws.com/", "")
        
        try:
            s3_response = s3.get_object(Bucket=S3_BUCKET, Key=s3_key)
            image_bytes = s3_response['Body'].read()
            image_base64 = base64.b64encode(image_bytes).decode('utf-8')
        except Exception as e:
            print(f"Error downloading image: {e}")
            conversion['status'] = 'error'
            conversion['error'] = f"Failed to download original image: {str(e)}"
            jobs_table.update_item(
                Key={'id': job_id},
                UpdateExpression='SET conversions = :c, updated_at = :u',
                ExpressionAttributeValues={
                    ':c': conversions,
                    ':u': datetime.now().isoformat()
                }
            )
            return response(200, {
                'success': False,
                'status': 'error',
                'error': conversion['error'],
                'conversion': conversion
            })
        
        # Generate conversion description using Claude
        print(f"Generating gender conversion description for outfit {conversion['outfit_id']}...")
        conversion_result = generate_gender_conversion_description(
            image_base64,
            conversion['original_description'],
            conversion['original_gender'],
            target_gender
        )
        
        # Check if conversion is possible
        if not conversion_result.get('convertible', True):
            reason = conversion_result.get('reason', 'Non convertible')
            print(f"Skipping conversion - not convertible: {reason}")
            conversion['status'] = 'skipped'
            conversion['error'] = f"Non convertible: {reason}"
            jobs_table.update_item(
                Key={'id': job_id},
                UpdateExpression='SET conversions = :c, updated_at = :u',
                ExpressionAttributeValues={
                    ':c': conversions,
                    ':u': datetime.now().isoformat()
                }
            )
            return response(200, {
                'success': True,
                'status': 'skipped',
                'reason': reason,
                'conversion': conversion
            })
        
        new_description = conversion_result.get('description', '')
        new_type = conversion_result.get('type', conversion['original_type'])
        
        print(f"Conversion: {conversion['original_description']} → {new_description}")
        
        # Generate the new image with Nano Banana Pro
        prompt = f"""Transform this clothing item into its {target_gender.upper()} equivalent:

CRITICAL: Keep EVERYTHING IDENTICAL except the garment type:
- SAME exact presentation style 
- SAME exact lighting and shadows
- SAME exact colors and color tones
- SAME exact image quality and resolution

ONLY CHANGE: The garment itself to be the {target_gender} version."""
        
        headers = {"Content-Type": "application/json"}
        
        parts = [
            {
                "inlineData": {
                    "mimeType": "image/jpeg",
                    "data": image_base64
                }
            },
            {"text": prompt}
        ]
        
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": {
                    "aspectRatio": "1:1",
                    "imageSize": "1K"
                }
            }
        }
        
        api_url = f"{GEMINI_API_URL}?key={NANO_BANANA_API_KEY}"
        
        resp = requests.post(
            api_url,
            headers=headers,
            json=payload,
            timeout=120
        )
        
        if resp.status_code != 200:
            print(f"Gemini API error: {resp.status_code} - {resp.text}")
            conversion['status'] = 'error'
            conversion['error'] = f"Image generation failed: {resp.status_code}"
            jobs_table.update_item(
                Key={'id': job_id},
                UpdateExpression='SET conversions = :c, updated_at = :u',
                ExpressionAttributeValues={
                    ':c': conversions,
                    ':u': datetime.now().isoformat()
                }
            )
            return response(200, {
                'success': False,
                'status': 'error',
                'error': conversion['error'],
                'conversion': conversion
            })
        
        result = resp.json()
        
        # Extract image from response
        candidates = result.get('candidates', [])
        image_base64_result = None
        
        if candidates:
            content = candidates[0].get('content', {})
            parts = content.get('parts', [])
            for part in parts:
                if 'inlineData' in part:
                    image_base64_result = part['inlineData'].get('data')
                    break
        
        if not image_base64_result:
            conversion['status'] = 'error'
            conversion['error'] = 'No image in API response'
            jobs_table.update_item(
                Key={'id': job_id},
                UpdateExpression='SET conversions = :c, updated_at = :u',
                ExpressionAttributeValues={
                    ':c': conversions,
                    ':u': datetime.now().isoformat()
                }
            )
            return response(200, {
                'success': False,
                'status': 'error',
                'error': conversion['error'],
                'conversion': conversion
            })
        
        # Create new outfit in DynamoDB
        new_outfit_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        # Save image to S3
        s3_key = f"outfits/{new_outfit_id}/main.png"
        image_data = base64.b64decode(image_base64_result)
        new_image_url = upload_to_s3(s3_key, image_data, 'image/png', cache_days=365)
        
        # Create new outfit record
        new_outfit = {
            'id': new_outfit_id,
            'description': new_description,
            'type': new_type,
            'gender': target_gender,
            'image_url': new_image_url,
            'source': 'gender_conversion',
            'source_outfit_id': conversion['outfit_id'],
            'created_at': now,
            'updated_at': now
        }
        
        outfits_table.put_item(Item=new_outfit)
        
        # Update conversion status
        conversion['status'] = 'completed'
        conversion['new_description'] = new_description
        conversion['new_type'] = new_type
        conversion['new_image_url'] = new_image_url
        conversion['new_outfit_id'] = new_outfit_id
        
        # Update job
        completed_count = sum(1 for c in conversions if c.get('status') == 'completed')
        job_status = 'completed' if completed_count >= len(conversions) else 'in_progress'
        
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET conversions = :c, completed_count = :cc, #s = :st, updated_at = :u',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={
                ':c': conversions,
                ':cc': completed_count,
                ':st': job_status,
                ':u': datetime.now().isoformat()
            }
        )
        
        return response(200, {
            'success': True,
            'status': 'completed',
            'conversion': conversion,
            'new_outfit': new_outfit,
            'completed_count': completed_count,
            'total_count': len(conversions)
        })
        
    except Exception as e:
        print(f"Error generating conversion: {e}")
        import traceback
        traceback.print_exc()
        return response(500, {'error': str(e)})


def get_conversion_status(event):
    """
    Get status of a gender conversion job - GET /api/admin/outfits/convert-gender/status/{job_id}
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        path_params = event.get('pathParameters', {}) or {}
        job_id = path_params.get('job_id')
        
        if not job_id:
            return response(400, {'error': 'job_id is required'})
        
        result = jobs_table.get_item(Key={'id': job_id})
        job = result.get('Item')
        
        if not job:
            return response(404, {'error': 'Job not found'})
        
        return response(200, {
            'success': True,
            'job': decimal_to_python(job)
        })
        
    except Exception as e:
        print(f"Error getting conversion status: {e}")
        return response(500, {'error': str(e)})
