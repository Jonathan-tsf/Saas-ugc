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
    ambassadors_table, s3, S3_BUCKET, dynamodb, lambda_client, upload_to_s3
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
        print(f"Calling Rekognition detect_faces... (image size: {len(image_bytes)} bytes)")
        resp = rekognition.detect_faces(
            Image={'Bytes': image_bytes},
            Attributes=['DEFAULT']
        )
        
        face_count = len(resp.get('FaceDetails', []))
        print(f"Rekognition response: {face_count} face(s) detected")
        
        if resp['FaceDetails']:
            # Get the largest/most prominent face
            face = max(resp['FaceDetails'], key=lambda f: f['BoundingBox']['Width'] * f['BoundingBox']['Height'])
            box = face['BoundingBox']
            confidence = face.get('Confidence', 0)
            print(f"Face detected at: left={box['Left']:.2f}, top={box['Top']:.2f}, "
                  f"width={box['Width']:.2f}, height={box['Height']:.2f}, confidence={confidence:.1f}%")
            return {
                'left': box['Left'],
                'top': box['Top'],
                'width': box['Width'],
                'height': box['Height']
            }
        print("No face found in image by Rekognition")
        return None
    except Exception as e:
        print(f"Rekognition error: {type(e).__name__}: {e}")
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
        
        # Different crop sizes for different styles (when no face detected)
        crop_ratios = {
            'close_up': 0.25,   # 25% of image - très serré sur le haut
            'standard': 0.45,  # 45% of image - tête et épaules
            'wide': 0.70,      # 70% of image - buste
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


def generate_single_profile_crop(image_bytes, padding_factor=0.5, crop_style='standard'):
    """
    Generate a single profile crop from an image with specified zoom level.
    
    Args:
        image_bytes: Raw image bytes
        padding_factor: Zoom level (0.3 = close, 1.0 = wide)
        crop_style: Style name for labeling
    
    Returns tuple: (cropped_bytes, face_detected boolean)
    """
    # Detect face
    face_bounds = detect_face_bounds(image_bytes)
    face_detected = face_bounds is not None
    
    if face_bounds:
        print(f"Face detected at: {face_bounds}")
    else:
        print(f"No face detected for {crop_style}, using smart center crop")
    
    try:
        cropped_bytes = smart_crop_to_square(image_bytes, face_bounds, padding_factor, crop_style=crop_style)
        return cropped_bytes, face_detected
    except Exception as e:
        print(f"Error generating crop: {e}")
        return None, face_detected


def generate_profile_crops(image_bytes, num_variations=4):
    """
    Generate multiple crop variations from the same image.
    Different padding factors to give user choice.
    
    Returns tuple: (list of cropped image data, face_detected boolean)
    """
    # Detect face
    face_bounds = detect_face_bounds(image_bytes)
    
    face_detected = face_bounds is not None
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
    
    return results, face_detected


def start_profile_generation(event):
    """
    Start profile photo cropping - Returns job_id immediately, crops images async
    POST /api/admin/ambassadors/profile-photos/generate
    Body: { ambassador_id, source_image_index (optional) }
    
    Will try photo_profile first, then showcase photos, until a face is detected.
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    ambassador_id = body.get('ambassador_id')
    
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
    # showcase_photos contains objects with 'selected_image' field
    showcase_photos_data = ambassador.get('showcase_photos', [])
    photo_list_base = ambassador.get('photo_list_base_array', [])
    current_profile = ambassador.get('photo_profile', '')
    
    # Build list of candidate images: SHOWCASE photos FIRST (they have AI-generated faces)
    # Then profile photo, then base photos
    candidate_images = []
    
    # PRIORITY 1: Showcase selected images (AI-generated vitrine photos with faces)
    if showcase_photos_data:
        for photo_obj in showcase_photos_data:
            if isinstance(photo_obj, dict):
                selected_img = photo_obj.get('selected_image')
                if selected_img and selected_img not in candidate_images:
                    candidate_images.append(selected_img)
    
    # PRIORITY 2: Current profile photo
    if current_profile and current_profile not in candidate_images:
        candidate_images.append(current_profile)
    
    # PRIORITY 3: Base photos as fallback
    if photo_list_base:
        for photo in photo_list_base:
            if photo and photo not in candidate_images:
                candidate_images.append(photo)
    
    if not candidate_images:
        return response(400, {'error': 'No source image available for this ambassador'})
    
    print(f"Found {len(candidate_images)} candidate images for face detection")
    
    # Download first image to start (will try others in async if no face found)
    source_image_url = candidate_images[0]
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
    
    # Create job in DynamoDB - include all candidate images for fallback
    job = {
        'id': job_id,
        'type': 'PROFILE_CROP_JOB',
        'ambassador_id': ambassador_id,
        'ambassador_name': name,
        'source_image_url': source_s3_url,
        'source_s3_key': source_key,
        'candidate_images': candidate_images,  # All images to try for face detection
        'status': 'generating',  # generating, completed, error
        'progress': Decimal('0'),
        'total_photos': 16,  # 4 images x 4 zoom levels
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
    Uses up to 4 showcase photos × 4 zoom levels = 16 photos total
    Each photo gets ALL zoom levels for maximum variety
    Updates DynamoDB progressively as each crop is generated
    """
    print(f"[{job_id}] Starting async profile photo cropping (16 photos mode: 4 images × 4 zooms)...")
    
    try:
        # Get job from DynamoDB
        result = jobs_table.get_item(Key={'id': job_id})
        job = result.get('Item')
        
        if not job:
            print(f"[{job_id}] Job not found")
            return
        
        ambassador_id = job.get('ambassador_id')
        candidate_images = job.get('candidate_images', [])
        
        print(f"[{job_id}] Found {len(candidate_images)} candidate images")
        
        # 4 zoom levels to apply to each image
        # Padding = multiplier for face size. Higher = more background visible
        zoom_configs = [
            {'padding': 0.5, 'style': 'close_up'},    # Zoom serré - visage presque plein cadre
            {'padding': 1.5, 'style': 'standard'},    # Zoom standard - tête et épaules
            {'padding': 3.0, 'style': 'wide'},        # Zoom large - buste
            {'padding': 6.0, 'style': 'full'}         # Vue complète - corps entier ou max
        ]
        
        generated_photos = []
        total_photos = 16  # 4 images × 4 zooms
        photo_index = 0
        
        # Use up to 4 source images
        images_to_process = candidate_images[:4]
        
        # If less than 4 images, repeat the first ones to fill
        while len(images_to_process) < 4 and len(candidate_images) > 0:
            images_to_process.append(candidate_images[len(images_to_process) % len(candidate_images)])
        
        print(f"[{job_id}] Processing {len(images_to_process)} images × 4 zoom levels = {total_photos} photos")
        
        # Download all images first
        downloaded_images = []
        for i, img_url in enumerate(images_to_process):
            try:
                print(f"[{job_id}] Downloading image {i+1}/4: {img_url[:50]}...")
                req = urllib.request.Request(img_url)
                with urllib.request.urlopen(req, timeout=30) as img_response:
                    image_data = img_response.read()
                downloaded_images.append({'url': img_url, 'data': image_data})
            except Exception as e:
                print(f"[{job_id}] Error downloading image {i+1}: {e}")
                downloaded_images.append(None)
        
        # Generate 16 photos: each image × each zoom level
        for img_idx, img_info in enumerate(downloaded_images):
            if img_info is None:
                # Skip failed downloads, but add placeholder entries
                for zoom_config in zoom_configs:
                    generated_photos.append({
                        'index': photo_index,
                        'image_index': img_idx,
                        'error': 'Image download failed',
                        'style': zoom_config['style']
                    })
                    photo_index += 1
                continue
            
            for zoom_idx, zoom_config in enumerate(zoom_configs):
                try:
                    # Generate crop with this zoom level
                    cropped_bytes, face_detected = generate_single_profile_crop(
                        img_info['data'],
                        padding_factor=zoom_config['padding'],
                        crop_style=zoom_config['style']
                    )
                    
                    if cropped_bytes:
                        # Upload to S3 with cache headers
                        photo_key = f"ambassadors/{ambassador_id}/profile_options/profile_img{img_idx+1}_zoom{zoom_idx+1}_{uuid.uuid4().hex[:8]}.png"
                        photo_url = upload_to_s3(photo_key, cropped_bytes, 'image/png', cache_days=365)
                        
                        photo_data = {
                            'index': photo_index,
                            'image_index': img_idx,  # Which source image (0-3)
                            'zoom_index': zoom_idx,  # Which zoom level (0-3)
                            'url': photo_url,
                            'style': zoom_config['style'],
                            'face_detected': face_detected,
                            'source_image': img_info['url'][:100]
                        }
                        generated_photos.append(photo_data)
                        
                        print(f"[{job_id}] ✓ Photo {photo_index+1}/16 (img{img_idx+1}/{zoom_config['style']}) - face:{face_detected}")
                    else:
                        generated_photos.append({
                            'index': photo_index,
                            'image_index': img_idx,
                            'zoom_index': zoom_idx,
                            'error': 'Failed to crop image',
                            'style': zoom_config['style']
                        })
                        print(f"[{job_id}] ✗ Failed photo {photo_index+1}/16")
                        
                except Exception as e:
                    print(f"[{job_id}] ✗ Error photo {photo_index+1}/16: {e}")
                    generated_photos.append({
                        'index': photo_index,
                        'image_index': img_idx,
                        'zoom_index': zoom_idx,
                        'error': str(e),
                        'style': zoom_config['style']
                    })
                
                photo_index += 1
                
                # Update progress in DynamoDB after each photo
                progress = Decimal(str((photo_index / total_photos) * 100))
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
        
        print(f"[{job_id}] Profile photo cropping completed: {len(successful_photos)}/16 successful")
        
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
