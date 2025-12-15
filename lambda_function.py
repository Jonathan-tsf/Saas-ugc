"""
AWS Lambda Entry Point - Routes requests to appropriate handlers
"""
import json
from config import response

# Import all handlers
from handlers import (
    # Bookings
    get_availability,
    create_booking,
    get_bookings,
    delete_booking,
    # Admin
    admin_login,
    update_availability_settings,
    get_availability_settings,
    # Contact
    send_contact_email,
    # Ambassadors
    get_ambassadors,
    get_ambassador,
    create_ambassador,
    update_ambassador,
    delete_ambassador,
    get_upload_url,
    get_public_ambassadors,
    # Transform
    start_transformation,
    continue_transformation,
    get_transformation_session,
    finalize_ambassador,
)

# Import outfit handlers
from handlers.outfits import (
    get_outfits,
    get_outfit,
    create_outfit,
    update_outfit,
    delete_outfit,
    get_upload_url as get_outfit_upload_url,
)

# Import outfit generation handlers
from handlers.outfit_generation import (
    start_outfit_generation,
    get_outfit_generation_status,
    select_outfit_image,
    generate_outfit_photos_async,
)

# Import showcase generation handlers
from handlers.showcase_generation import (
    start_showcase_generation,
    get_showcase_generation_status,
    generate_showcase_photos_async,
    generate_showcase_scenes_async,
    select_showcase_photo,
    generate_scene,
    poll_scene_replicate
)

# Import profile photo generation handlers
from handlers.profile_generation import (
    start_profile_generation,
    get_profile_generation_status,
    select_profile_photo,
    generate_profile_photos_async,
)

# Import showcase video generation handlers
from handlers.showcase_videos import (
    start_showcase_video_generation,
    get_showcase_video_status,
    get_ambassador_showcase_videos,
    delete_showcase_video,
    generate_showcase_videos_async,
)

# Import authentication handlers
from handlers.auth import (
    sign_up,
    confirm_sign_up,
    sign_in,
    resend_confirmation_code,
    forgot_password,
    confirm_forgot_password,
    refresh_token,
    get_user_profile,
    update_user_profile,
    create_user_from_oauth,
)


def lambda_handler(event, context):
    """Main Lambda handler - routes requests to appropriate functions"""
    print(f"Event: {json.dumps(event)}")
    
    # Handle async background task invocations
    if 'action' in event and event['action'] == 'generate_variations':
        from handlers.transform_async import generate_step_variations_async
        from config import s3, S3_BUCKET
        import base64
        
        session_id = event['session_id']
        step = event['step']
        
        # Get image from S3 (to avoid 1MB Lambda payload limit)
        if 'image_s3_key' in event:
            image_s3_key = event['image_s3_key']
            image_obj = s3.get_object(Bucket=S3_BUCKET, Key=image_s3_key)
            image_data = image_obj['Body'].read()
            image_base64 = base64.b64encode(image_data).decode('utf-8')
        else:
            # Fallback for old format
            image_base64 = event['image_base64']
        
        generate_step_variations_async(session_id, step, image_base64)
        return {'statusCode': 200, 'body': json.dumps({'success': True})}
    
    # Handle async outfit generation
    if 'action' in event and event['action'] == 'generate_outfit_photos':
        generate_outfit_photos_async(
            job_id=event['job_id'],
            ambassador_id=event['ambassador_id'],
            profile_url=event['profile_url'],
            outfits=event['outfits'],
            ambassador_name=event['ambassador_name']
        )
        return {'statusCode': 200, 'body': json.dumps({'success': True})}
    
    # Handle async showcase generation
    if 'action' in event and event['action'] == 'generate_showcase_photos':
        generate_showcase_photos_async(
            job_id=event['job_id'],
            ambassador_id=event['ambassador_id'],
            available_categories=event['available_categories'],
            ambassador_gender=event['ambassador_gender']
        )
        return {'statusCode': 200, 'body': json.dumps({'success': True})}
    
    # Handle async scene generation (new pattern)
    if 'action' in event and event['action'] == 'generate_scene_async':
        # Build a fake event for generate_scene with is_async=True
        fake_event = {
            'body': json.dumps({
                'ambassador_id': event['ambassador_id'],
                'scene_id': event['scene_id'],
                'job_id': event.get('job_id'),
                'is_async': True
            }),
            'headers': {'Authorization': 'Bearer internal-async-call'}  # Skip auth for internal calls
        }
        result = generate_scene(fake_event)
        print(f"Async scene generation result: {result}")
        return result
    
    # Handle async profile photo generation
    if 'action' in event and event['action'] == 'generate_profile_photos_async':
        job_id = event['job_id']
        generate_profile_photos_async(job_id)
        return {'statusCode': 200, 'body': json.dumps({'success': True})}
    
    # Handle async showcase scene generation (Claude generates scene descriptions)
    if 'action' in event and event['action'] == 'generate_showcase_scenes_async':
        job_id = event['job_id']
        generate_showcase_scenes_async(job_id)
        return {'statusCode': 200, 'body': json.dumps({'success': True})}
    
    # Handle async showcase video generation
    if 'action' in event and event['action'] == 'generate_showcase_videos_async':
        job_id = event['job_id']
        generate_showcase_videos_async(job_id)
        return {'statusCode': 200, 'body': json.dumps({'success': True})}
    
    http_method = event.get('httpMethod', '')
    path = event.get('path', '')
    
    # Handle CORS preflight
    if http_method == 'OPTIONS':
        return response(200, {})
    
    # Route mapping
    routes = {
        # Public endpoints
        ('GET', '/api/availability'): get_availability,
        ('POST', '/api/book-demo'): create_booking,
        ('POST', '/api/contact'): send_contact_email,
        ('GET', '/api/ambassadors'): get_public_ambassadors,
        
        # User Authentication (public)
        ('POST', '/api/auth/signup'): sign_up,
        ('POST', '/api/auth/confirm'): confirm_sign_up,
        ('POST', '/api/auth/signin'): sign_in,
        ('POST', '/api/auth/resend-code'): resend_confirmation_code,
        ('POST', '/api/auth/forgot-password'): forgot_password,
        ('POST', '/api/auth/reset-password'): confirm_forgot_password,
        ('POST', '/api/auth/refresh'): refresh_token,
        
        # User Profile (authenticated)
        ('GET', '/api/user/profile'): get_user_profile,
        ('PUT', '/api/user/profile'): update_user_profile,
        ('POST', '/api/user/profile'): create_user_from_oauth,
        
        # Admin auth
        ('POST', '/api/admin/login'): admin_login,
        ('POST', '/api/admin/auth'): admin_login,
        
        # Admin bookings
        ('GET', '/api/admin/bookings'): get_bookings,
        ('DELETE', '/api/admin/bookings'): delete_booking,
        
        # Admin settings
        ('GET', '/api/admin/settings'): get_availability_settings,
        ('PUT', '/api/admin/settings'): update_availability_settings,
        
        # Admin ambassadors CRUD
        ('GET', '/api/admin/ambassadors'): get_ambassadors,
        ('POST', '/api/admin/ambassadors'): create_ambassador,
        ('PUT', '/api/admin/ambassadors'): update_ambassador,
        ('DELETE', '/api/admin/ambassadors'): delete_ambassador,
        ('POST', '/api/admin/ambassadors/upload-url'): get_upload_url,
        
        # Admin transformation
        ('POST', '/api/admin/ambassadors/transform/start'): start_transformation,
        ('POST', '/api/admin/ambassadors/transform/continue'): continue_transformation,
        ('GET', '/api/admin/ambassadors/transform/session'): get_transformation_session,
        ('POST', '/api/admin/ambassadors/transform/finalize'): finalize_ambassador,
        
        # Admin outfits CRUD
        ('GET', '/api/admin/outfits'): get_outfits,
        ('POST', '/api/admin/outfits'): create_outfit,
        ('GET', '/api/admin/outfits/upload-url'): get_outfit_upload_url,
        
        # Admin outfit generation
        ('POST', '/api/admin/ambassadors/outfits/generate'): start_outfit_generation,
        ('GET', '/api/admin/ambassadors/outfits/status'): get_outfit_generation_status,
        ('POST', '/api/admin/ambassadors/outfits/select'): select_outfit_image,
        
        # Admin showcase generation
        ('POST', '/api/admin/ambassadors/showcase/generate'): start_showcase_generation,
        ('GET', '/api/admin/ambassadors/showcase/status'): get_showcase_generation_status,
        ('POST', '/api/admin/ambassadors/showcase/select'): select_showcase_photo,
        ('POST', '/api/admin/ambassadors/showcase/scene'): generate_scene,
        ('POST', '/api/admin/ambassadors/showcase/scene/poll'): poll_scene_replicate,
        
        # Admin profile photo generation (async with polling)
        ('POST', '/api/admin/ambassadors/profile-photos/generate'): start_profile_generation,
        ('GET', '/api/admin/ambassadors/profile-photos/status'): get_profile_generation_status,
        ('POST', '/api/admin/ambassadors/profile-photos/select'): select_profile_photo,
        
        # Admin showcase video generation
        ('POST', '/api/admin/ambassadors/showcase-videos/generate'): start_showcase_video_generation,
        ('GET', '/api/admin/ambassadors/showcase-videos/status'): get_showcase_video_status,
    }
    
    # Find matching route
    handler = routes.get((http_method, path))
    
    if handler:
        return handler(event)
    
    # Handle parameterized routes
    if http_method == 'DELETE' and path.startswith('/api/admin/bookings/'):
        return delete_booking(event)
    
    # Ambassador showcase videos parameterized routes - MUST come before get_ambassador
    # /api/admin/ambassadors/{id}/showcase-videos
    if '/showcase-videos' in path and path.startswith('/api/admin/ambassadors/'):
        # Extract ambassador_id from path
        parts = path.split('/')
        if len(parts) >= 6 and parts[5] == 'showcase-videos':
            # Path is /api/admin/ambassadors/{id}/showcase-videos
            ambassador_id = parts[4]
            event['pathParameters'] = event.get('pathParameters', {}) or {}
            event['pathParameters']['id'] = ambassador_id
            
            if http_method == 'GET':
                return get_ambassador_showcase_videos(event)
            elif http_method == 'DELETE':
                return delete_showcase_video(event)
    
    if http_method == 'GET' and path.startswith('/api/admin/ambassadors/') and path != '/api/admin/ambassadors/upload-url' and '/showcase-videos' not in path:
        return get_ambassador(event)
    
    if http_method == 'DELETE' and path.startswith('/api/admin/ambassadors/'):
        return delete_ambassador(event)
    
    # Outfit parameterized routes
    if path.startswith('/api/admin/outfits/') and path != '/api/admin/outfits/upload-url':
        if http_method == 'GET':
            return get_outfit(event)
        elif http_method == 'PUT':
            return update_outfit(event)
        elif http_method == 'DELETE':
            return delete_outfit(event)
    
    return response(404, {'error': f'Not found: {http_method} {path}'})