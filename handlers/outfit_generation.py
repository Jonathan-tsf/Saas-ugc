"""
Outfit generation handlers
Generates ambassador photos wearing different outfits using Nano Banana Pro (Gemini 3 Pro Image)
"""
import json
import uuid
import base64
import requests
import boto3
from datetime import datetime

from config import (
    response, decimal_to_python, verify_admin,
    dynamodb, s3, S3_BUCKET, NANO_BANANA_API_KEY
)

# DynamoDB tables
ambassadors_table = dynamodb.Table('ambassadors')
outfits_table = dynamodb.Table('outfits')
jobs_table = dynamodb.Table('nano_banana_jobs')

# Lambda client for async invocation
lambda_client = boto3.client('lambda')
LAMBDA_FUNCTION_NAME = 'saas-ugc'

# Gemini API endpoint for image generation
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp:generateContent"


def get_image_from_s3(image_url):
    """Download image from S3 and return base64"""
    try:
        # Extract key from URL
        if 's3.amazonaws.com' in image_url:
            key = image_url.split('.com/')[1]
        elif 'amazonaws.com' in image_url:
            parts = image_url.split('amazonaws.com/')
            key = parts[1] if len(parts) > 1 else image_url.split('/')[-1]
        else:
            key = image_url.split('/')[-1]
        
        response_obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
        image_data = response_obj['Body'].read()
        return base64.b64encode(image_data).decode('utf-8')
    except Exception as e:
        print(f"Error getting image from S3: {e}")
        return None


def generate_outfit_images(profile_image_base64, outfit_image_base64, outfit_description, ambassador_name):
    """Generate 2 images of the ambassador wearing the outfit using Nano Banana Pro"""
    
    prompt = f"""Create a professional fashion photo of this person wearing this outfit.

IMPORTANT INSTRUCTIONS:
- Keep the person's face, body shape, and features EXACTLY the same as the reference photo
- Put them in the outfit shown in the second image: {outfit_description}
- The person should look natural and confident wearing this outfit
- Generate a full-body or 3/4 body shot in portrait orientation (9:16)
- Use professional studio lighting
- Clean, neutral background (solid color or simple gradient)
- The pose should be natural and flattering for fashion photography
- Make sure the outfit fits perfectly on the person's body

Generate ONE high-quality fashion photo."""

    headers = {
        "Content-Type": "application/json"
    }
    
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": profile_image_base64
                    }
                },
                {
                    "inline_data": {
                        "mime_type": "image/jpeg", 
                        "data": outfit_image_base64
                    }
                }
            ]
        }],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "responseMimeType": "text/plain"
        }
    }
    
    generated_images = []
    
    # Generate 2 images
    for i in range(2):
        try:
            api_response = requests.post(
                f"{GEMINI_API_URL}?key={NANO_BANANA_API_KEY}",
                headers=headers,
                json=payload,
                timeout=120
            )
            
            if api_response.status_code == 200:
                result = api_response.json()
                
                # Extract image from response
                if 'candidates' in result and len(result['candidates']) > 0:
                    candidate = result['candidates'][0]
                    if 'content' in candidate and 'parts' in candidate['content']:
                        for part in candidate['content']['parts']:
                            if 'inlineData' in part:
                                image_data = part['inlineData']['data']
                                generated_images.append(image_data)
                                break
            else:
                print(f"API error: {api_response.status_code} - {api_response.text}")
                
        except Exception as e:
            print(f"Error generating image {i+1}: {e}")
    
    return generated_images


def save_image_to_s3(image_base64, ambassador_id, outfit_id, index):
    """Save generated image to S3 and return URL"""
    try:
        image_data = base64.b64decode(image_base64)
        key = f"ambassador_outfits/{ambassador_id}/{outfit_id}_{index}_{uuid.uuid4().hex[:8]}.png"
        
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=image_data,
            ContentType='image/png'
        )
        
        return f"https://{S3_BUCKET}.s3.amazonaws.com/{key}"
    except Exception as e:
        print(f"Error saving image to S3: {e}")
        return None


def start_outfit_generation(event):
    """Start generating outfit photos for an ambassador - POST /api/admin/ambassadors/outfits/generate"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    ambassador_id = body.get('ambassador_id')
    if not ambassador_id:
        return response(400, {'error': 'ambassador_id required'})
    
    # Get ambassador
    try:
        amb_result = ambassadors_table.get_item(Key={'id': ambassador_id})
        ambassador = amb_result.get('Item')
        if not ambassador:
            return response(404, {'error': 'Ambassador not found'})
    except Exception as e:
        return response(500, {'error': f'Failed to get ambassador: {str(e)}'})
    
    outfit_ids = ambassador.get('outfit_ids', [])
    if not outfit_ids:
        return response(400, {'error': 'Ambassador has no outfits assigned'})
    
    # Get all outfits
    outfits = []
    for outfit_id in outfit_ids:
        try:
            outfit_result = outfits_table.get_item(Key={'id': outfit_id})
            outfit = outfit_result.get('Item')
            if outfit:
                outfits.append(decimal_to_python(outfit))
        except Exception as e:
            print(f"Error getting outfit {outfit_id}: {e}")
    
    if not outfits:
        return response(400, {'error': 'No valid outfits found'})
    
    # Get profile image
    profile_url = ambassador.get('photo_profile')
    if not profile_url:
        return response(400, {'error': 'Ambassador has no profile photo'})
    
    # Create job
    job_id = str(uuid.uuid4())
    job = {
        'id': job_id,
        'type': 'outfit_generation',
        'ambassador_id': ambassador_id,
        'status': 'processing',
        'total_outfits': len(outfits),
        'completed_outfits': 0,
        'results': [],
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat()
    }
    
    jobs_table.put_item(Item=job)
    
    # Clear previous ambassador_outfits
    try:
        ambassadors_table.update_item(
            Key={'id': ambassador_id},
            UpdateExpression='SET ambassador_outfits = :empty, updated_at = :updated',
            ExpressionAttributeValues={
                ':empty': [],
                ':updated': datetime.now().isoformat()
            }
        )
    except Exception as e:
        print(f"Error clearing ambassador outfits: {e}")
    
    # Invoke Lambda async for background processing
    try:
        lambda_client.invoke(
            FunctionName=LAMBDA_FUNCTION_NAME,
            InvocationType='Event',  # Async invocation
            Payload=json.dumps({
                'action': 'generate_outfit_photos',
                'job_id': job_id,
                'ambassador_id': ambassador_id,
                'profile_url': profile_url,
                'outfits': outfits,
                'ambassador_name': ambassador.get('name', 'Ambassador')
            })
        )
    except Exception as e:
        print(f"Error invoking Lambda async: {e}")
        # Update job status
        job['status'] = 'failed'
        job['error'] = str(e)
        jobs_table.put_item(Item=job)
        return response(500, {'error': f'Failed to start generation: {str(e)}'})
    
    return response(200, {
        'success': True,
        'job_id': job_id,
        'message': f'Started generating outfit photos for {len(outfits)} outfits'
    })


def generate_outfit_photos_async(job_id, ambassador_id, profile_url, outfits, ambassador_name):
    """Background async handler to generate outfit photos"""
    print(f"Starting async generation for job {job_id}, ambassador {ambassador_id}")
    
    # Get profile image
    profile_base64 = get_image_from_s3(profile_url)
    if not profile_base64:
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET #s = :status, #e = :error, updated_at = :updated',
            ExpressionAttributeNames={'#s': 'status', '#e': 'error'},
            ExpressionAttributeValues={
                ':status': 'failed',
                ':error': 'Failed to get profile image',
                ':updated': datetime.now().isoformat()
            }
        )
        return
    
    ambassador_outfits = []
    
    # Generate images for each outfit
    for outfit in outfits:
        outfit_id = outfit['id']
        outfit_type = outfit.get('type', 'casual')
        outfit_description = outfit.get('description', '')
        outfit_image_url = outfit.get('image_url', '')
        
        # Update job status
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET current_outfit_id = :outfit_id, updated_at = :updated',
            ExpressionAttributeValues={
                ':outfit_id': outfit_id,
                ':updated': datetime.now().isoformat()
            }
        )
        
        # Get outfit image
        outfit_base64 = get_image_from_s3(outfit_image_url) if outfit_image_url else None
        
        if not outfit_base64:
            print(f"Skipping outfit {outfit_id} - no image")
            continue
        
        # Generate 2 images
        generated_images = generate_outfit_images(
            profile_base64,
            outfit_base64,
            outfit_description,
            ambassador_name
        )
        
        # Save to S3
        image_urls = []
        for idx, img_base64 in enumerate(generated_images):
            url = save_image_to_s3(img_base64, ambassador_id, outfit_id, idx)
            if url:
                image_urls.append(url)
        
        # Create outfit entry
        outfit_entry = {
            'outfit_id': outfit_id,
            'outfit_type': outfit_type,
            'generated_images': image_urls,
            'status': 'generated' if image_urls else 'failed',
            'created_at': datetime.now().isoformat()
        }
        ambassador_outfits.append(outfit_entry)
        
        # Update job progress
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET completed_outfits = completed_outfits + :one, results = list_append(results, :result), updated_at = :updated',
            ExpressionAttributeValues={
                ':one': 1,
                ':result': [outfit_entry],
                ':updated': datetime.now().isoformat()
            }
        )
    
    # Update ambassador with new outfits
    try:
        ambassadors_table.update_item(
            Key={'id': ambassador_id},
            UpdateExpression='SET ambassador_outfits = :outfits, updated_at = :updated',
            ExpressionAttributeValues={
                ':outfits': ambassador_outfits,
                ':updated': datetime.now().isoformat()
            }
        )
    except Exception as e:
        print(f"Error updating ambassador: {e}")
    
    # Mark job as completed
    jobs_table.update_item(
        Key={'id': job_id},
        UpdateExpression='SET #s = :status, updated_at = :updated',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={
            ':status': 'completed',
            ':updated': datetime.now().isoformat()
        }
    )
    
    print(f"Completed async generation for job {job_id}")


def get_outfit_generation_status(event):
    """Get outfit generation job status - GET /api/admin/ambassadors/outfits/status"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    params = event.get('queryStringParameters', {}) or {}
    job_id = params.get('job_id')
    
    if not job_id:
        return response(400, {'error': 'job_id required'})
    
    try:
        result = jobs_table.get_item(Key={'id': job_id})
        job = result.get('Item')
        
        if not job:
            return response(404, {'error': 'Job not found'})
        
        return response(200, {
            'success': True,
            'job': decimal_to_python(job)
        })
    except Exception as e:
        return response(500, {'error': f'Failed to get job: {str(e)}'})


def select_outfit_image(event):
    """Select the best image for an outfit - POST /api/admin/ambassadors/outfits/select"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    ambassador_id = body.get('ambassador_id')
    outfit_id = body.get('outfit_id')
    selected_image = body.get('selected_image')
    
    if not all([ambassador_id, outfit_id, selected_image]):
        return response(400, {'error': 'ambassador_id, outfit_id, and selected_image required'})
    
    try:
        # Get current ambassador
        result = ambassadors_table.get_item(Key={'id': ambassador_id})
        ambassador = result.get('Item')
        
        if not ambassador:
            return response(404, {'error': 'Ambassador not found'})
        
        # Update the specific outfit
        ambassador_outfits = ambassador.get('ambassador_outfits', [])
        updated = False
        
        for outfit in ambassador_outfits:
            if outfit.get('outfit_id') == outfit_id:
                outfit['selected_image'] = selected_image
                outfit['status'] = 'selected'
                updated = True
                break
        
        if not updated:
            return response(404, {'error': 'Outfit not found in ambassador'})
        
        # Save back to DynamoDB
        ambassadors_table.update_item(
            Key={'id': ambassador_id},
            UpdateExpression='SET ambassador_outfits = :outfits, updated_at = :updated',
            ExpressionAttributeValues={
                ':outfits': ambassador_outfits,
                ':updated': datetime.now().isoformat()
            }
        )
        
        # Get updated ambassador
        result = ambassadors_table.get_item(Key={'id': ambassador_id})
        
        return response(200, {
            'success': True,
            'ambassador': decimal_to_python(result.get('Item'))
        })
        
    except Exception as e:
        print(f"Error selecting outfit image: {e}")
        return response(500, {'error': f'Failed to select image: {str(e)}'})
