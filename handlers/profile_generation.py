"""
Profile photo generation handlers using Nano Banana Pro API - ASYNC ARCHITECTURE
Uses nano_banana_jobs table for job tracking with polling
"""
import json
import uuid
import base64
import requests
import urllib.request
import os
from datetime import datetime
from decimal import Decimal

from config import (
    response, decimal_to_python, verify_admin,
    ambassadors_table, s3, S3_BUCKET, NANO_BANANA_API_KEY, dynamodb, lambda_client
)

# Create jobs table reference
jobs_table = dynamodb.Table('nano_banana_jobs')

# Profile photo styles
PROFILE_STYLES = ['professional', 'social_media', 'business', 'lifestyle']


def get_profile_prompts(gender):
    """Get prompts for 4 different profile photo styles"""
    return [
        f"Create a professional profile photo of this {gender}. Square 1:1 ratio, face perfectly centered, clean neutral gray studio background, soft professional lighting, headshot from shoulders up, looking directly at camera with confident friendly expression. Keep the face identical to the input image.",
        f"Create a modern social media profile photo of this {gender}. Square 1:1 ratio, face centered, minimalist white background, bright even lighting, upper body visible, natural relaxed expression, professional yet approachable. Keep the face identical to the input image.",
        f"Create an elegant business profile photo of this {gender}. Square 1:1 ratio, face centered, soft gradient background from light gray to white, professional studio lighting with subtle rim light, head and shoulders framing, confident professional expression. Keep the face identical to the input image.",
        f"Create a lifestyle profile photo of this {gender}. Square 1:1 ratio, face centered, blurred modern interior background with natural light, soft shadows, chest-up framing, warm friendly smile, authentic natural look. Keep the face identical to the input image."
    ]


def call_nano_banana_pro_profile(image_base64, prompt):
    """
    Call Nano Banana Pro (Gemini 3 Pro Image Preview) for profile photo generation.
    Uses 1:1 aspect ratio for profile photos.
    """
    if not NANO_BANANA_API_KEY:
        raise Exception("NANO_BANANA_API_KEY not configured")
    
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
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {
                "aspectRatio": "1:1",
                "imageSize": "1K"
            }
        }
    }
    
    try:
        print(f"Calling Nano Banana Pro API for profile photo...")
        api_response = requests.post(api_url, headers=headers, json=payload, timeout=180)
        
        if api_response.ok:
            result = api_response.json()
            
            # Extract image from response
            for candidate in result.get('candidates', []):
                for part in candidate.get('content', {}).get('parts', []):
                    if 'inlineData' in part:
                        print("Nano Banana Pro profile generation successful")
                        return part['inlineData']['data']
            
            print("No image in Nano Banana Pro response")
            return None
        else:
            print(f"Nano Banana Pro API error: {api_response.status_code}")
            print(f"Response: {api_response.text[:500]}")
            return None
            
    except Exception as e:
        print(f"Nano Banana Pro API error: {e}")
        return None


def start_profile_generation(event):
    """
    Start profile photo generation - Returns job_id immediately, generates images async
    POST /api/admin/ambassadors/profile-photos/generate
    Body: { ambassador_id, source_image_index (optional) }
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
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
    
    # Download source image
    try:
        print(f"Downloading source image: {source_image_url[:50]}...")
        req = urllib.request.Request(source_image_url)
        with urllib.request.urlopen(req, timeout=30) as img_response:
            image_data = img_response.read()
    except Exception as e:
        print(f"Error downloading source image: {e}")
        return response(500, {'error': 'Failed to download source image'})
    
    # Create job ID
    job_id = str(uuid.uuid4())
    gender = ambassador.get('gender', 'female')
    name = ambassador.get('name', 'Unknown')
    
    # Store source image in S3
    source_key = f"profile_jobs/{job_id}/source.png"
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=source_key,
        Body=image_data,
        ContentType='image/png'
    )
    source_s3_url = f"https://{S3_BUCKET}.s3.amazonaws.com/{source_key}"
    
    # Create job in DynamoDB
    job = {
        'id': job_id,
        'type': 'PROFILE_PHOTO_JOB',
        'ambassador_id': ambassador_id,
        'ambassador_name': name,
        'gender': gender,
        'source_image_url': source_s3_url,
        'source_s3_key': source_key,
        'status': 'generating',  # generating, completed, error
        'progress': Decimal('0'),
        'total_photos': 4,
        'generated_photos': [],
        'error': None,
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat()
    }
    
    jobs_table.put_item(Item=job)
    
    # Invoke Lambda asynchronously to generate photos in background
    payload = {
        'action': 'generate_profile_photos_async',
        'job_id': job_id
    }
    
    lambda_client.invoke(
        FunctionName=os.environ.get('AWS_LAMBDA_FUNCTION_NAME', 'ugc-booking'),
        InvocationType='Event',  # Asynchronous invocation
        Payload=json.dumps(payload)
    )
    
    # Return immediately
    return response(200, {
        'success': True,
        'job_id': job_id,
        'status': 'generating',
        'message': 'Profile photo generation started. Poll /status endpoint to get progress.'
    })


def generate_profile_photos_async(job_id):
    """
    Generate profile photos asynchronously - called by Lambda invoke
    Updates DynamoDB progressively as each photo is generated
    """
    print(f"[{job_id}] Starting async profile photo generation...")
    
    try:
        # Get job from DynamoDB
        result = jobs_table.get_item(Key={'id': job_id})
        job = result.get('Item')
        
        if not job:
            print(f"[{job_id}] Job not found")
            return
        
        # Get source image from S3
        source_key = job.get('source_s3_key')
        source_obj = s3.get_object(Bucket=S3_BUCKET, Key=source_key)
        source_data = source_obj['Body'].read()
        image_base64 = base64.b64encode(source_data).decode('utf-8')
        
        gender = job.get('gender', 'female')
        ambassador_id = job.get('ambassador_id')
        prompts = get_profile_prompts(gender)
        
        generated_photos = []
        
        # Generate each photo one by one, updating progress
        for i, prompt in enumerate(prompts):
            try:
                print(f"[{job_id}] Generating profile photo {i+1}/4...")
                
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
                    
                    photo_data = {
                        'index': i,
                        'url': photo_url,
                        'style': PROFILE_STYLES[i]
                    }
                    generated_photos.append(photo_data)
                    
                    print(f"[{job_id}] ✓ Profile photo {i+1}/4 uploaded: {photo_url}")
                else:
                    print(f"[{job_id}] ✗ Failed to generate profile photo {i+1}")
                    generated_photos.append({
                        'index': i,
                        'error': 'Generation failed',
                        'style': PROFILE_STYLES[i]
                    })
                
            except Exception as e:
                print(f"[{job_id}] ✗ Error generating profile photo {i+1}: {e}")
                generated_photos.append({
                    'index': i,
                    'error': str(e),
                    'style': PROFILE_STYLES[i]
                })
            
            # Update progress in DynamoDB after each photo
            progress = Decimal(str(((i + 1) / 4) * 100))
            jobs_table.update_item(
                Key={'id': job_id},
                UpdateExpression='SET generated_photos = :photos, progress = :prog, updated_at = :updated',
                ExpressionAttributeValues={
                    ':photos': generated_photos,
                    ':prog': progress,
                    ':updated': datetime.now().isoformat()
                }
            )
        
        # Mark job as completed
        successful_photos = [p for p in generated_photos if 'url' in p]
        final_status = 'completed' if successful_photos else 'error'
        
        # Also save to ambassador profile_photo_options
        if successful_photos:
            try:
                ambassadors_table.update_item(
                    Key={'id': ambassador_id},
                    UpdateExpression="SET profile_photo_options = :options, updated_at = :updated",
                    ExpressionAttributeValues={
                        ':options': successful_photos,
                        ':updated': datetime.now().isoformat()
                    }
                )
            except Exception as e:
                print(f"[{job_id}] Error saving to ambassador: {e}")
        
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET #status = :status, updated_at = :updated',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': final_status,
                ':updated': datetime.now().isoformat()
            }
        )
        
        print(f"[{job_id}] Profile photo generation completed: {len(successful_photos)}/4 successful")
        
    except Exception as e:
        print(f"[{job_id}] Fatal error in async generation: {e}")
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET #status = :status, error = :error, updated_at = :updated',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'error',
                ':error': str(e),
                ':updated': datetime.now().isoformat()
            }
        )


def get_profile_generation_status(event):
    """
    Get profile photo generation status - for polling
    GET /api/admin/ambassadors/profile-photos/status?job_id=XXX
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    params = event.get('queryStringParameters', {}) or {}
    job_id = params.get('job_id')
    
    if not job_id:
        return response(400, {'error': 'job_id is required'})
    
    try:
        result = jobs_table.get_item(Key={'id': job_id})
        job = result.get('Item')
        
        if not job:
            return response(404, {'error': 'Job not found'})
        
        # Convert Decimal to Python types
        job = decimal_to_python(job)
        
        return response(200, {
            'success': True,
            'job_id': job_id,
            'status': job.get('status'),  # generating, completed, error
            'progress': job.get('progress', 0),
            'generated_photos': job.get('generated_photos', []),
            'ambassador_id': job.get('ambassador_id'),
            'ambassador_name': job.get('ambassador_name'),
            'error': job.get('error'),
            'created_at': job.get('created_at'),
            'updated_at': job.get('updated_at')
        })
        
    except Exception as e:
        print(f"Error getting job status: {e}")
        return response(500, {'error': f'Failed to get job status: {str(e)}'})


def select_profile_photo(event):
    """
    Select one of the generated profile photos as the ambassador's profile photo.
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
