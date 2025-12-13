"""
Profile photo generation handlers - SMART CROP (no AI generation)
Simply crops existing photos to center on the face for profile use.
Uses AWS Rekognition for face detection or falls back to center crop.
"""
import json
import uuid
import boto3
import urllib.request
import os
from datetime import datetime
from decimal import Decimal
from io import BytesIO

from config import (
    response, decimal_to_python, verify_admin,
    ambassadors_table, s3, S3_BUCKET, dynamodb, lambda_client
)

# PIL is imported lazily to avoid Lambda crash if Pillow binary is incompatible
# This allows other handlers to work even if profile_generation isn't used
Image = None

def _ensure_pil():
    """Lazy import PIL only when needed"""
    global Image
    if Image is None:
        try:
            from PIL import Image as PILImage
            Image = PILImage
        except ImportError as e:
            print(f"PIL import error: {e}")
            raise RuntimeError("Pillow is not available. Profile cropping requires a Lambda Layer with Pillow.")

# AWS Rekognition client for face detection
rekognition = boto3.client('rekognition', region_name='us-east-1')

# Create jobs table reference
jobs_table = dynamodb.Table('nano_banana_jobs')

# Profile crop styles
CROP_STYLES = ['close_up', 'standard', 'wide', 'full']


def detect_face_bounds(image_bytes):
    """
    Use AWS Rekognition to detect face bounding box.
    Returns dict with left, top, width, height as fractions, or None if no face.
    """
    try:
        resp = rekognition.detect_faces(
            Image={'Bytes': image_bytes},
            Attributes=['DEFAULT']
        )
        
        if resp['FaceDetails']:
            # Get the largest/most prominent face
            face = max(resp['FaceDetails'], key=lambda f: f['BoundingBox']['Width'] * f['BoundingBox']['Height'])
            box = face['BoundingBox']
            return {
                'left': box['Left'],
                'top': box['Top'],
                'width': box['Width'],
                'height': box['Height']
            }
        return None
    except Exception as e:
        print(f"Rekognition error: {e}")
        return None


def smart_crop_to_square(image_bytes, face_bounds=None, padding_factor=0.5, crop_style='standard'):
    """
    Crop image to square, centering on face if detected.
    
    Args:
        image_bytes: Raw image bytes
        face_bounds: Dict with left, top, width, height (fractions)
        padding_factor: Extra padding around face (0.5 = 50% extra on each side)
        crop_style: One of 'close_up', 'standard', 'wide', 'full'
    
    Returns:
        Cropped image as bytes (PNG)
    """
    _ensure_pil()  # Lazy import PIL
    img = Image.open(BytesIO(image_bytes))
    img_width, img_height = img.size
    
    if face_bounds:
        # Calculate face center in pixels
        face_center_x = (face_bounds['left'] + face_bounds['width'] / 2) * img_width
        face_center_y = (face_bounds['top'] + face_bounds['height'] / 2) * img_height
        
        # Calculate crop size based on face size with padding
        face_size = max(face_bounds['width'] * img_width, face_bounds['height'] * img_height)
        crop_size = int(face_size * (1 + padding_factor * 2))
        
        # Ensure minimum crop size
        crop_size = max(crop_size, min(img_width, img_height) // 2)
        
        # Ensure crop doesn't exceed image bounds
        crop_size = min(crop_size, img_width, img_height)
        
    else:
        # No face detected - use different crop sizes based on style
        # This creates variety even without face detection
        min_dim = min(img_width, img_height)
        
        # Different crop sizes for different styles
        crop_ratios = {
            'close_up': 0.4,   # 40% of image - tight crop on upper portion
            'standard': 0.55,  # 55% of image - medium crop
            'wide': 0.75,      # 75% of image - wider view
            'full': 1.0        # 100% - full square crop
        }
        crop_ratio = crop_ratios.get(crop_style, 0.55)
        crop_size = int(min_dim * crop_ratio)
        
        # For close_up and standard, center on upper third (typical face position)
        # For wide and full, center on middle
        if crop_style in ['close_up', 'standard']:
            face_center_x = img_width / 2
            face_center_y = img_height * 0.35  # Upper portion where face usually is
        else:
            face_center_x = img_width / 2
            face_center_y = img_height / 2
    
    # Calculate crop bounds
    half_crop = crop_size / 2
    left = int(max(0, face_center_x - half_crop))
    top = int(max(0, face_center_y - half_crop))
    
    # Adjust if crop goes beyond image bounds
    if left + crop_size > img_width:
        left = img_width - crop_size
    if top + crop_size > img_height:
        top = img_height - crop_size
    
    # Ensure non-negative
    left = max(0, int(left))
    top = max(0, int(top))
    crop_size = int(crop_size)
    
    right = left + crop_size
    bottom = top + crop_size
    
    # Crop
    cropped = img.crop((left, top, right, bottom))
    
    # Resize to standard profile size (512x512)
    cropped = cropped.resize((512, 512), Image.Resampling.LANCZOS)
    
    # Convert to bytes
    output = BytesIO()
    cropped.save(output, format='PNG', quality=95)
    output.seek(0)
    
    return output.read()


def generate_profile_crops(image_bytes, num_variations=4):
    """
    Generate multiple crop variations from the same image.
    Different padding factors to give user choice.
    
    Returns list of cropped image data.
    """
    # Detect face
    face_bounds = detect_face_bounds(image_bytes)
    
    if face_bounds:
        print(f"Face detected at: {face_bounds}")
    else:
        print("No face detected, using smart center crop with different sizes")
    
    # Different padding factors for variety (used when face is detected)
    padding_factors = [0.3, 0.5, 0.7, 1.0]  # Tighter to wider crops
    
    results = []
    for i, padding in enumerate(padding_factors):
        try:
            style = CROP_STYLES[i]
            cropped_bytes = smart_crop_to_square(image_bytes, face_bounds, padding, crop_style=style)
            results.append({
                'index': i,
                'bytes': cropped_bytes,
                'style': style
            })
            print(f"Generated crop {i+1}/{num_variations}: {style}")
        except Exception as e:
            print(f"Error generating crop {i}: {e}")
    
    return results


def start_profile_generation(event):
    """
    Start profile photo cropping - Returns job_id immediately, crops images async
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
        'type': 'PROFILE_CROP_JOB',
        'ambassador_id': ambassador_id,
        'ambassador_name': name,
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
    
    # Invoke Lambda asynchronously to crop photos in background
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
        'message': 'Profile photo cropping started. Poll /status endpoint to get progress.'
    })


def generate_profile_photos_async(job_id):
    """
    Generate profile photo crops asynchronously - called by Lambda invoke
    Uses smart crop with face detection (no AI generation)
    Updates DynamoDB progressively as each crop is generated
    """
    print(f"[{job_id}] Starting async profile photo cropping...")
    
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
        
        ambassador_id = job.get('ambassador_id')
        
        # Generate crop variations (smart crop with face detection)
        crops = generate_profile_crops(source_data, num_variations=4)
        
        generated_photos = []
        
        for i, crop in enumerate(crops):
            try:
                # Upload to S3
                photo_key = f"ambassadors/{ambassador_id}/profile_options/profile_{i+1}_{uuid.uuid4().hex[:8]}.png"
                
                s3.put_object(
                    Bucket=S3_BUCKET,
                    Key=photo_key,
                    Body=crop['bytes'],
                    ContentType='image/png'
                )
                
                photo_url = f"https://{S3_BUCKET}.s3.amazonaws.com/{photo_key}"
                
                photo_data = {
                    'index': crop['index'],
                    'url': photo_url,
                    'style': crop['style']
                }
                generated_photos.append(photo_data)
                
                print(f"[{job_id}] ✓ Profile crop {i+1}/4 uploaded: {photo_url}")
                
            except Exception as e:
                print(f"[{job_id}] ✗ Error uploading crop {i+1}: {e}")
                generated_photos.append({
                    'index': i,
                    'error': str(e),
                    'style': crop.get('style', 'unknown')
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
        
        print(f"[{job_id}] Profile photo cropping completed: {len(successful_photos)}/4 successful")
        
    except Exception as e:
        print(f"[{job_id}] Fatal error in async cropping: {e}")
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
