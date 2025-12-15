"""
Showcase video generation handlers using Kling via Replicate API
"""
import json
import uuid
import os
import urllib.request
import urllib.error
import base64
from datetime import datetime
from decimal import Decimal

from config import (
    response, decimal_to_python, verify_admin,
    ambassadors_table, s3, S3_BUCKET, dynamodb, lambda_client,
    bedrock_runtime, upload_to_s3, REPLICATE_API_KEY
)

# Jobs table for async video generation
jobs_table = dynamodb.Table('nano_banana_jobs')

# Replicate API endpoints
REPLICATE_API_URL = "https://api.replicate.com/v1/predictions"
KLING_MODEL = "kwaivgi/kling-v2.5-turbo-pro"

# Video generation templates for B-roll style content
VIDEO_PROMPT_TEMPLATES = [
    {
        "id": "gym_focus",
        "name": "Gym Focus",
        "base_prompt": "Medium shot. The person in the reference image adjusts their workout position, takes a focused breath, looks ahead with determination. Subtle breathing, natural muscle micro-movements. Camera: slow push-in, stable framing. Cool gym lighting, cinematic realism, realistic skin texture.",
    },
    {
        "id": "confident_walk",
        "name": "Confident Walk",
        "base_prompt": "Full body shot. The person takes 2-3 natural confident steps forward, slight shoulder sway, one natural blink, relaxed arms swing. Camera: slow tracking shot, stable. Golden hour light, cinematic, realistic proportions, shallow depth of field.",
    },
    {
        "id": "phone_check",
        "name": "Phone Check",
        "base_prompt": "Over-the-shoulder shot. The person holds a phone, scrolls with thumb naturally, pauses, then looks up with a subtle smile. Minimal movement, realistic finger motion. Camera locked with tiny handheld micro-shake. Neutral daylight, shallow DOF.",
    },
    {
        "id": "mirror_look",
        "name": "Mirror Look",
        "base_prompt": "Medium shot facing mirror. The person checks their reflection, adjusts clothing slightly, nods approvingly with a subtle confident smile. Natural breathing, small head movements. Camera: slow pan, stable. Soft natural lighting, cinematic realism.",
    },
    {
        "id": "ready_pose",
        "name": "Ready Pose",
        "base_prompt": "Medium shot. The person puts hands on hips confidently, takes a breath, looks at camera, then gives a subtle knowing smile. Hair and fabric move naturally with breathing. Camera: slow pull-back, stable. Soft window light, cinematic.",
    },
    {
        "id": "stretching",
        "name": "Stretching",
        "base_prompt": "Medium shot. The person does a slow arm stretch above head, exhales naturally, then brings arms down relaxed. Natural muscle movement, breathing visible. Camera: locked, slight push-in. Warm gym lighting, realistic skin texture.",
    }
]

# Default negative prompt for all videos
DEFAULT_NEGATIVE_PROMPT = "morphing, face drift, changing facial features, extra limbs, bad hands, distorted fingers, flicker, jitter, wobble, blur, low quality, text, watermark, logo, unnatural movement, robotic motion, frozen expression, teeth showing, open mouth smile, camera movement, camera shake, zooming"


def download_image_as_base64(image_url: str) -> str:
    """Download image from URL and return as base64 string."""
    try:
        req = urllib.request.Request(image_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as img_response:
            image_data = img_response.read()
            return base64.b64encode(image_data).decode('utf-8')
    except Exception as e:
        print(f"Error downloading image: {e}")
        raise


def generate_video_prompt_with_bedrock(image_url: str, scene_context: str = "") -> dict:
    """
    Use AWS Bedrock Claude Vision to analyze the image and generate an optimized video prompt.
    
    Args:
        image_url: URL of the showcase photo to analyze
        scene_context: Additional context about the scene/outfit
    
    Returns:
        dict with 'prompt', 'negative_prompt' keys
    """
    model_id = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
    
    system_prompt = """You are an expert at creating prompts for AI video generation from images.
Your goal is to create prompts that produce ultra-realistic human videos with minimal artifacts.

CRITICAL RULES - MUST FOLLOW:
1. CAMERA MUST BE COMPLETELY STATIC - NO camera movement AT ALL (no pan, no zoom, no push-in, no tracking)
2. NO SMILING WITH TEETH - only subtle closed-mouth expressions allowed
3. Movement speed must be REALISTIC - not slow motion, natural human speed
4. Focus 70% of the prompt on MOTION DIRECTIVES (what moves, how, speed)
5. Describe ONE simple action only - complex actions cause glitches
6. Keep the subject's identity, clothing, and proportions IDENTICAL to source
7. Emphasize micro-movements: breathing, blinking, subtle head tilts
8. Describe EXACTLY what is in the image - clothes, pose, objects, setting

PROMPT STRUCTURE:
Static camera shot. [Describe exactly what you see: person, clothing, pose, objects held, setting]
Action (ONE): [single simple realistic-speed action appropriate to what they're holding/doing]
Micro-motion: subtle breathing, natural blink, tiny head movement, hair/clothes react naturally
Expression: neutral or subtle closed-mouth confidence, NO teeth showing
Look: cinematic, realistic skin texture, natural motion blur, shallow depth of field

Generate for TikTok/Instagram style B-roll content - professional but authentic."""

    try:
        # Download and encode image
        print(f"Downloading image for analysis: {image_url[:80]}...")
        image_base64 = download_image_as_base64(image_url)
        
        # Determine media type from URL
        media_type = "image/jpeg"
        if ".png" in image_url.lower():
            media_type = "image/png"
        elif ".webp" in image_url.lower():
            media_type = "image/webp"
        
        user_prompt = f"""Look at this image carefully and create a video prompt.

{f"Additional context: {scene_context}" if scene_context else ""}

IMPORTANT:
1. Describe EXACTLY what you see in the image (person, clothing, objects, pose, setting)
2. If they are holding something, the action should relate to that object
3. Camera MUST stay completely still - NO movement
4. NO smiling with teeth - only closed-mouth expressions
5. Movement should be realistic speed, not slow motion

Create a 10-second video prompt based on what you actually see.

Respond ONLY with valid JSON in this exact format:
{{
    "prompt": "Static camera shot. [your detailed video prompt describing what you see and one simple action]",
    "negative_prompt": "camera movement, zooming, panning, teeth showing, open mouth smile, morphing, face drift, changing facial features, extra limbs, bad hands, slow motion"
}}"""

        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 600,
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_base64
                            }
                        },
                        {
                            "type": "text",
                            "text": user_prompt
                        }
                    ]
                }
            ]
        }
        
        response = bedrock_runtime.invoke_model(
            modelId=model_id,
            body=json.dumps(request_body),
            contentType="application/json",
            accept="application/json"
        )
        
        response_body = json.loads(response['body'].read())
        content = response_body.get('content', [{}])[0].get('text', '{}')
        
        # Parse the JSON response
        result = json.loads(content)
        
        # Ensure we have required fields
        if 'prompt' not in result:
            raise ValueError("No prompt in response")
        
        if 'negative_prompt' not in result:
            result['negative_prompt'] = DEFAULT_NEGATIVE_PROMPT
        
        print(f"Bedrock video prompt generated: {result['prompt'][:100]}...")
        return result
        
    except Exception as e:
        print(f"Error generating video prompt with Bedrock: {e}")
        # Return a default prompt on error
        return {
            'prompt': f"Static camera shot. The person in the reference image stands confidently, takes a breath, subtle closed-mouth expression. Natural breathing, small head movement. Camera completely still. Soft natural lighting, cinematic realism.",
            'negative_prompt': DEFAULT_NEGATIVE_PROMPT
        }


def call_replicate_kling_api(image_url: str, prompt: str, negative_prompt: str, duration: int = 10) -> dict:
    """
    Call Replicate API to generate video with Kling model.
    Returns prediction info (async - need to poll for result).
    
    Args:
        image_url: URL of the source image
        prompt: Video generation prompt
        negative_prompt: What to avoid
        duration: Video duration in seconds (5 or 10)
    
    Returns:
        dict with prediction id and status URL
    """
    if not REPLICATE_API_KEY:
        raise Exception("REPLICATE_KEY not configured")
    
    headers = {
        "Authorization": f"Bearer {REPLICATE_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "version": "kwaivgi/kling-v2.5-turbo-pro",
        "input": {
            "image": image_url,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "duration": duration,
            "aspect_ratio": "9:16",  # Vertical for TikTok/Instagram
        }
    }
    
    try:
        # Create prediction
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            f"{REPLICATE_API_URL}",
            data=data,
            headers=headers,
            method='POST'
        )
        
        with urllib.request.urlopen(req, timeout=30) as api_response:
            result = json.loads(api_response.read().decode('utf-8'))
            
            return {
                'id': result.get('id'),
                'status': result.get('status'),
                'urls': result.get('urls', {}),
                'created_at': result.get('created_at')
            }
            
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8') if e.fp else 'No error body'
        print(f"Replicate API HTTP error: {e.code} - {error_body[:500]}")
        raise Exception(f"Replicate HTTP error: {e.code} - {error_body[:200]}")
    except Exception as e:
        print(f"Replicate error: {e}")
        raise


def check_replicate_prediction(prediction_id: str) -> dict:
    """
    Check status of a Replicate prediction.
    
    Returns:
        dict with status, output (if completed), error (if failed)
    """
    if not REPLICATE_API_KEY:
        raise Exception("REPLICATE_KEY not configured")
    
    headers = {
        "Authorization": f"Bearer {REPLICATE_API_KEY}",
    }
    
    try:
        req = urllib.request.Request(
            f"{REPLICATE_API_URL}/{prediction_id}",
            headers=headers,
            method='GET'
        )
        
        with urllib.request.urlopen(req, timeout=30) as api_response:
            result = json.loads(api_response.read().decode('utf-8'))
            
            return {
                'id': result.get('id'),
                'status': result.get('status'),  # starting, processing, succeeded, failed, canceled
                'output': result.get('output'),
                'error': result.get('error'),
                'metrics': result.get('metrics', {})
            }
            
    except Exception as e:
        print(f"Error checking prediction: {e}")
        raise


def start_showcase_video_generation(event):
    """
    Start showcase video generation for an ambassador.
    POST /api/admin/ambassadors/showcase-videos/generate
    Body: { ambassador_id, selected_photo_indices: [0, 1, 2...] }
    
    Returns job_id to poll for status.
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    ambassador_id = body.get('ambassador_id')
    selected_indices = body.get('selected_photo_indices', [])
    
    if not ambassador_id:
        return response(400, {'error': 'ambassador_id is required'})
    
    if not selected_indices:
        return response(400, {'error': 'selected_photo_indices is required (select at least 1 photo)'})
    
    # Get ambassador data
    try:
        result = ambassadors_table.get_item(Key={'id': ambassador_id})
        ambassador = result.get('Item')
        if not ambassador:
            return response(404, {'error': 'Ambassador not found'})
    except Exception as e:
        print(f"Error fetching ambassador: {e}")
        return response(500, {'error': 'Failed to fetch ambassador'})
    
    # Get showcase photos
    showcase_photos = ambassador.get('showcase_photos', [])
    if not showcase_photos:
        return response(400, {'error': 'No showcase photos available'})
    
    # Validate selected indices
    valid_photos = []
    for idx in selected_indices:
        if 0 <= idx < len(showcase_photos):
            photo = showcase_photos[idx]
            if isinstance(photo, dict) and photo.get('selected_image'):
                valid_photos.append({
                    'index': idx,
                    'image_url': photo.get('selected_image'),
                    'description': photo.get('prompt', ''),
                    'scene_type': photo.get('scene_type', '')
                })
    
    if not valid_photos:
        return response(400, {'error': 'No valid photos selected'})
    
    # Create job
    job_id = str(uuid.uuid4())
    
    job = {
        'id': job_id,
        'type': 'SHOWCASE_VIDEO_JOB',
        'ambassador_id': ambassador_id,
        'ambassador_name': ambassador.get('name', 'Unknown'),
        'selected_photos': valid_photos,
        'status': 'generating_prompts',  # generating_prompts, generating_videos, completed, error
        'progress': Decimal('0'),
        'total_videos': len(valid_photos) * 2,  # 2 videos per photo
        'video_tasks': [],  # Will hold individual video generation tasks
        'generated_videos': [],
        'error': None,
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat()
    }
    
    jobs_table.put_item(Item=job)
    
    # Invoke Lambda asynchronously
    payload = {
        'action': 'generate_showcase_videos_async',
        'job_id': job_id
    }
    
    lambda_client.invoke(
        FunctionName=os.environ.get('AWS_LAMBDA_FUNCTION_NAME', 'ugc-booking'),
        InvocationType='Event',
        Payload=json.dumps(payload)
    )
    
    return response(200, {
        'success': True,
        'job_id': job_id,
        'status': 'generating_prompts',
        'total_videos': len(valid_photos) * 2,
        'message': 'Video generation started. Poll /status endpoint for progress.'
    })


def generate_showcase_videos_async(job_id: str):
    """
    Async handler to generate showcase videos.
    Called by Lambda invoke.
    
    Flow:
    1. For each selected photo, generate 2 video prompts with Bedrock
    2. Submit each video to Replicate Kling API
    3. Poll for completion
    4. Download and save to S3
    5. Update ambassador record
    """
    print(f"[{job_id}] Starting async showcase video generation...")
    
    try:
        # Get job
        result = jobs_table.get_item(Key={'id': job_id})
        job = result.get('Item')
        
        if not job:
            print(f"[{job_id}] Job not found")
            return
        
        ambassador_id = job.get('ambassador_id')
        selected_photos = job.get('selected_photos', [])
        total_videos = int(job.get('total_videos', 0))
        
        print(f"[{job_id}] Generating {total_videos} videos for {len(selected_photos)} photos")
        
        # PHASE 1: Generate prompts with Bedrock (analyzing the actual image)
        video_tasks = []
        
        for photo in selected_photos:
            image_url = photo.get('image_url')
            scene_type = photo.get('scene_type', '')
            
            # Generate 2 different video prompts for each photo
            for video_num in range(2):
                try:
                    # Use image URL directly - Bedrock will analyze the image
                    prompt_result = generate_video_prompt_with_bedrock(image_url, scene_type)
                    
                    video_tasks.append({
                        'photo_index': photo.get('index'),
                        'video_num': video_num,
                        'image_url': image_url,
                        'prompt': prompt_result['prompt'],
                        'negative_prompt': prompt_result['negative_prompt'],
                        'status': 'pending',
                        'replicate_id': None,
                        'output_url': None,
                        'error': None
                    })
                    
                    print(f"[{job_id}] Generated prompt for photo {photo.get('index')} video {video_num+1}")
                    
                except Exception as e:
                    print(f"[{job_id}] Error generating prompt: {e}")
                    video_tasks.append({
                        'photo_index': photo.get('index'),
                        'video_num': video_num,
                        'image_url': image_url,
                        'status': 'error',
                        'error': str(e)
                    })
        
        # Update job with video tasks
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET video_tasks = :tasks, #status = :status, updated_at = :updated',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':tasks': video_tasks,
                ':status': 'generating_videos',
                ':updated': datetime.now().isoformat()
            }
        )
        
        # PHASE 2: Submit videos to Replicate
        for i, task in enumerate(video_tasks):
            if task.get('status') == 'error':
                continue
            
            try:
                prediction = call_replicate_kling_api(
                    image_url=task['image_url'],
                    prompt=task['prompt'],
                    negative_prompt=task['negative_prompt'],
                    duration=10
                )
                
                task['replicate_id'] = prediction['id']
                task['status'] = 'processing'
                
                print(f"[{job_id}] Submitted video {i+1}/{total_videos} to Replicate: {prediction['id']}")
                
            except Exception as e:
                print(f"[{job_id}] Error submitting to Replicate: {e}")
                task['status'] = 'error'
                task['error'] = str(e)
            
            # Update progress
            progress = Decimal(str(((i + 1) / total_videos) * 30))  # 0-30% for submissions
            jobs_table.update_item(
                Key={'id': job_id},
                UpdateExpression='SET video_tasks = :tasks, progress = :prog, updated_at = :updated',
                ExpressionAttributeValues={
                    ':tasks': video_tasks,
                    ':prog': progress,
                    ':updated': datetime.now().isoformat()
                }
            )
        
        # PHASE 3: Poll for completion (this can take several minutes per video)
        import time
        max_wait_seconds = 600  # 10 minutes max per video
        poll_interval = 15  # Check every 15 seconds
        
        pending_tasks = [t for t in video_tasks if t.get('replicate_id') and t.get('status') == 'processing']
        
        start_time = time.time()
        while pending_tasks and (time.time() - start_time) < max_wait_seconds * len(pending_tasks):
            time.sleep(poll_interval)
            
            for task in pending_tasks[:]:  # Copy list to allow removal during iteration
                try:
                    prediction = check_replicate_prediction(task['replicate_id'])
                    
                    if prediction['status'] == 'succeeded':
                        task['status'] = 'completed'
                        task['output_url'] = prediction['output']
                        pending_tasks.remove(task)
                        print(f"[{job_id}] Video completed: {task['replicate_id']}")
                        
                    elif prediction['status'] in ['failed', 'canceled']:
                        task['status'] = 'error'
                        task['error'] = prediction.get('error', 'Unknown error')
                        pending_tasks.remove(task)
                        print(f"[{job_id}] Video failed: {task['replicate_id']} - {task['error']}")
                        
                except Exception as e:
                    print(f"[{job_id}] Error polling: {e}")
            
            # Update progress (30-90%)
            completed = len([t for t in video_tasks if t.get('status') in ['completed', 'error']])
            progress = Decimal(str(30 + (completed / total_videos) * 60))
            jobs_table.update_item(
                Key={'id': job_id},
                UpdateExpression='SET video_tasks = :tasks, progress = :prog, updated_at = :updated',
                ExpressionAttributeValues={
                    ':tasks': video_tasks,
                    ':prog': progress,
                    ':updated': datetime.now().isoformat()
                }
            )
        
        # PHASE 4: Download completed videos and save to S3
        generated_videos = []
        
        for task in video_tasks:
            if task.get('status') == 'completed' and task.get('output_url'):
                try:
                    # Download video from Replicate
                    video_url = task['output_url']
                    req = urllib.request.Request(video_url)
                    with urllib.request.urlopen(req, timeout=60) as video_response:
                        video_data = video_response.read()
                    
                    # Upload to S3
                    video_key = f"ambassadors/{ambassador_id}/showcase_videos/video_{task['photo_index']}_{task['video_num']}_{uuid.uuid4().hex[:8]}.mp4"
                    s3_url = upload_to_s3(video_key, video_data, 'video/mp4', cache_days=365)
                    
                    generated_videos.append({
                        'photo_index': task['photo_index'],
                        'video_num': task['video_num'],
                        'url': s3_url,
                        'prompt': task.get('prompt', ''),
                        'created_at': datetime.now().isoformat()
                    })
                    
                    print(f"[{job_id}] Saved video to S3: {video_key}")
                    
                except Exception as e:
                    print(f"[{job_id}] Error saving video to S3: {e}")
        
        # PHASE 5: Update ambassador record
        if generated_videos:
            try:
                # Get existing videos
                result = ambassadors_table.get_item(Key={'id': ambassador_id})
                ambassador = result.get('Item', {})
                existing_videos = ambassador.get('showcase_videos', [])
                
                # Add new videos
                all_videos = existing_videos + generated_videos
                
                ambassadors_table.update_item(
                    Key={'id': ambassador_id},
                    UpdateExpression='SET showcase_videos = :videos, updated_at = :updated',
                    ExpressionAttributeValues={
                        ':videos': all_videos,
                        ':updated': datetime.now().isoformat()
                    }
                )
                
                print(f"[{job_id}] Updated ambassador with {len(generated_videos)} new videos")
                
            except Exception as e:
                print(f"[{job_id}] Error updating ambassador: {e}")
        
        # Mark job complete
        final_status = 'completed' if generated_videos else 'error'
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET #status = :status, generated_videos = :videos, progress = :prog, updated_at = :updated',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': final_status,
                ':videos': generated_videos,
                ':prog': Decimal('100'),
                ':updated': datetime.now().isoformat()
            }
        )
        
        print(f"[{job_id}] Showcase video generation completed: {len(generated_videos)}/{total_videos} videos")
        
    except Exception as e:
        print(f"[{job_id}] Fatal error: {e}")
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


def get_showcase_video_status(event):
    """
    Get showcase video generation status.
    GET /api/admin/ambassadors/showcase-videos/status?job_id=XXX
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
        
        # Clean up response
        job_data = decimal_to_python(job)
        
        return response(200, {
            'job_id': job_id,
            'status': job_data.get('status'),
            'progress': job_data.get('progress', 0),
            'total_videos': job_data.get('total_videos', 0),
            'generated_videos': job_data.get('generated_videos', []),
            'error': job_data.get('error'),
            'updated_at': job_data.get('updated_at')
        })
        
    except Exception as e:
        print(f"Error getting job status: {e}")
        return response(500, {'error': 'Failed to get job status'})


def get_ambassador_showcase_videos(event):
    """
    Get all showcase videos for an ambassador.
    GET /api/admin/ambassadors/{id}/showcase-videos
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    params = event.get('pathParameters', {}) or {}
    ambassador_id = params.get('id')
    
    if not ambassador_id:
        return response(400, {'error': 'ambassador_id is required'})
    
    try:
        result = ambassadors_table.get_item(Key={'id': ambassador_id})
        ambassador = result.get('Item')
        
        if not ambassador:
            return response(404, {'error': 'Ambassador not found'})
        
        videos = ambassador.get('showcase_videos', [])
        
        return response(200, {
            'success': True,
            'ambassador_id': ambassador_id,
            'videos': decimal_to_python(videos),
            'count': len(videos)
        })
        
    except Exception as e:
        print(f"Error getting showcase videos: {e}")
        return response(500, {'error': 'Failed to get showcase videos'})


def delete_showcase_video(event):
    """
    Delete a showcase video.
    DELETE /api/admin/ambassadors/{id}/showcase-videos?video_index=X
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    params = event.get('pathParameters', {}) or {}
    query_params = event.get('queryStringParameters', {}) or {}
    
    ambassador_id = params.get('id')
    video_index = query_params.get('video_index')
    
    if not ambassador_id or video_index is None:
        return response(400, {'error': 'ambassador_id and video_index are required'})
    
    try:
        video_index = int(video_index)
        
        result = ambassadors_table.get_item(Key={'id': ambassador_id})
        ambassador = result.get('Item')
        
        if not ambassador:
            return response(404, {'error': 'Ambassador not found'})
        
        videos = ambassador.get('showcase_videos', [])
        
        if video_index < 0 or video_index >= len(videos):
            return response(400, {'error': 'Invalid video_index'})
        
        # Remove video
        deleted_video = videos.pop(video_index)
        
        # Update ambassador
        ambassadors_table.update_item(
            Key={'id': ambassador_id},
            UpdateExpression='SET showcase_videos = :videos, updated_at = :updated',
            ExpressionAttributeValues={
                ':videos': videos,
                ':updated': datetime.now().isoformat()
            }
        )
        
        # Optionally delete from S3
        if deleted_video.get('url') and S3_BUCKET in deleted_video['url']:
            try:
                s3_key = deleted_video['url'].split(f"{S3_BUCKET}.s3.amazonaws.com/")[1]
                s3.delete_object(Bucket=S3_BUCKET, Key=s3_key)
            except Exception as e:
                print(f"Error deleting from S3: {e}")
        
        return response(200, {
            'success': True,
            'message': 'Video deleted',
            'remaining_count': len(videos)
        })
        
    except Exception as e:
        print(f"Error deleting video: {e}")
        return response(500, {'error': 'Failed to delete video'})
