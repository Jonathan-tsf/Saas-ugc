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
    get_hero_videos,
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

# Import product handlers
from handlers.products import (
    get_products,
    get_product,
    create_product,
    update_product,
    delete_product,
    get_product_upload_url,
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
    poll_scene_replicate,
    edit_showcase_photo,
    apply_showcase_edit,
    reject_showcase_edit,
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
    delete_showcase_videos_batch,
    generate_showcase_videos_async,
    trim_showcase_video,
    select_best_showcase_video,
)

# Import outfit variations handlers
from handlers.outfit_variations import (
    generate_outfit_variations,
    start_outfit_variations,
    generate_variation_image,
    get_variations_job_status,
    apply_outfit_variation,
)

# Import gender conversion handlers
from handlers.gender_conversion import (
    list_outfits_by_gender,
    start_gender_conversion,
    generate_conversion_image,
    get_conversion_status,
)

# Import AI outfit generator handlers
from handlers.ai_outfit_generator import (
    start_ai_outfit_generation,
    generate_ai_outfit_image,
    get_ai_generation_status,
)

# Import short generation handlers
from handlers.short_generation import (
    get_ambassadors_for_shorts,
    get_ambassador_outfits,
    generate_short_script,
    regenerate_scene,
    save_short_script,
    get_short_scripts,
    get_short_script,
    delete_short_script,
    update_scene,
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


# ============================================
# DEBUG FUNCTION - TEMPORARY - DELETE AFTER USE
# ============================================
def debug_categorize_outfit(event):
    """
    Use Claude Vision to categorize an outfit based on its IMAGE.
    TEMPORARY DEBUG FUNCTION - DELETE AFTER USE
    """
    from config import bedrock_runtime, dynamodb, s3, S3_BUCKET
    import base64
    
    try:
        body = json.loads(event.get('body', '{}') or '{}')
        outfit_id = body.get('outfit_id')
        image_url = body.get('image_url', '')
        current_type = body.get('current_type', '')
        valid_categories = body.get('valid_categories', [])
        
        if not outfit_id or not image_url:
            return response(400, {'error': 'outfit_id and image_url are required'})
        
        # Download image from S3
        try:
            s3_key = image_url.replace(f"https://{S3_BUCKET}.s3.amazonaws.com/", "")
            s3_response = s3.get_object(Bucket=S3_BUCKET, Key=s3_key)
            image_bytes = s3_response['Body'].read()
            image_base64 = base64.b64encode(image_bytes).decode('utf-8')
        except Exception as e:
            print(f"Error downloading image: {e}")
            return response(500, {'error': f'Failed to download image: {str(e)}'})
        
        # Use Claude Vision to categorize
        model_id = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
        
        categories_list = '\n'.join([f"- {cat}" for cat in valid_categories])
        
        prompt = f"""Regarde cette image de vêtement et catégorise-la dans UNE SEULE des catégories suivantes.

CATÉGORIES DISPONIBLES:
{categories_list}

DÉFINITIONS:
- Sport: Vêtements de sport, fitness, yoga, running, gym (leggings, brassières sport, t-shirts techniques, shorts sport)
- Casual: Vêtements décontractés du quotidien (jeans, t-shirts basiques, sweats, pulls)
- Formel: Vêtements habillés pour le travail ou occasions formelles (costumes, chemises, pantalons habillés, blazers)
- Soirée: Vêtements élégants pour sorties (robes de soirée, tenues chic, vêtements brillants/paillettes)
- Spécial: Tout le reste - uniformes (policier, médecin, cuisine), maillots de bain, déguisements, tenues thématiques

IMPORTANT: Réponds UNIQUEMENT avec le nom exact d'une catégorie, rien d'autre.

Catégorie:"""

        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 50,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_base64
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ]
        }
        
        response_bedrock = bedrock_runtime.invoke_model(
            modelId=model_id,
            body=json.dumps(request_body),
            contentType="application/json",
            accept="application/json"
        )
        
        response_body = json.loads(response_bedrock['body'].read())
        category = response_body.get('content', [{}])[0].get('text', '').strip()
        
        # Validate category is in the list
        if category not in valid_categories:
            # Try to find a close match
            category_lower = category.lower()
            for valid_cat in valid_categories:
                if valid_cat.lower() in category_lower or category_lower in valid_cat.lower():
                    category = valid_cat
                    break
            else:
                # Default to "Spécial" if no match
                category = "Spécial"
        
        # Update DynamoDB if category changed
        updated = False
        if category != current_type:
            outfits_table = dynamodb.Table('outfits')
            outfits_table.update_item(
                Key={'id': outfit_id},
                UpdateExpression='SET #type = :type',
                ExpressionAttributeNames={'#type': 'type'},
                ExpressionAttributeValues={':type': category}
            )
            updated = True
        
        return response(200, {
            'success': True,
            'outfit_id': outfit_id,
            'old_category': current_type,
            'new_category': category,
            'updated': updated
        })
        
    except Exception as e:
        print(f"Debug categorize error: {e}")
        import traceback
        traceback.print_exc()
        return response(500, {'error': str(e)})
# ============================================
# END DEBUG FUNCTION
# ============================================


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
        ('GET', '/api/hero-videos'): get_hero_videos,
        
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
        
        # Admin products CRUD
        ('GET', '/api/admin/products'): get_products,
        ('POST', '/api/admin/products'): create_product,
        ('GET', '/api/admin/products/upload-url'): get_product_upload_url,
        
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
        ('POST', '/api/admin/ambassadors/showcase/edit'): edit_showcase_photo,
        ('POST', '/api/admin/ambassadors/showcase/edit/apply'): apply_showcase_edit,
        ('POST', '/api/admin/ambassadors/showcase/edit/reject'): reject_showcase_edit,
        
        # Admin profile photo generation (async with polling)
        ('POST', '/api/admin/ambassadors/profile-photos/generate'): start_profile_generation,
        ('GET', '/api/admin/ambassadors/profile-photos/status'): get_profile_generation_status,
        ('POST', '/api/admin/ambassadors/profile-photos/select'): select_profile_photo,
        
        # Admin showcase video generation
        ('POST', '/api/admin/ambassadors/showcase-videos/generate'): start_showcase_video_generation,
        ('GET', '/api/admin/ambassadors/showcase-videos/status'): get_showcase_video_status,
        ('POST', '/api/admin/ambassadors/showcase-videos/trim'): trim_showcase_video,
        ('POST', '/api/admin/ambassadors/showcase-videos/select'): select_best_showcase_video,
        
        # Admin short/TikTok generation
        ('GET', '/api/admin/shorts/ambassadors'): get_ambassadors_for_shorts,
        ('POST', '/api/admin/shorts/generate-script'): generate_short_script,
        ('POST', '/api/admin/shorts/regenerate-scene'): regenerate_scene,
        ('POST', '/api/admin/shorts/save'): save_short_script,
        ('GET', '/api/admin/shorts'): get_short_scripts,
        ('PUT', '/api/admin/shorts/scene'): update_scene,
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
    
    # Batch delete showcase videos
    if path == '/api/admin/ambassadors/showcase-videos/delete-batch' and http_method == 'POST':
        return delete_showcase_videos_batch(event)
    
    if http_method == 'GET' and path.startswith('/api/admin/ambassadors/') and path != '/api/admin/ambassadors/upload-url' and '/showcase-videos' not in path:
        return get_ambassador(event)
    
    if http_method == 'DELETE' and path.startswith('/api/admin/ambassadors/'):
        return delete_ambassador(event)
    
    # Gender conversion routes
    if path == '/api/admin/outfits/convert-gender':
        if http_method == 'POST':
            return start_gender_conversion(event)
    
    if path == '/api/admin/outfits/convert-gender/generate':
        if http_method == 'POST':
            return generate_conversion_image(event)
    
    if path.startswith('/api/admin/outfits/convert-gender/status/'):
        if http_method == 'GET':
            # Extract job_id from path
            parts = path.split('/')
            if len(parts) >= 7:
                job_id = parts[6]
                event['pathParameters'] = event.get('pathParameters', {}) or {}
                event['pathParameters']['job_id'] = job_id
                return get_conversion_status(event)
    
    if path.startswith('/api/admin/outfits/gender/'):
        if http_method == 'GET':
            # Extract gender from path
            parts = path.split('/')
            if len(parts) >= 6:
                gender = parts[5]
                event['pathParameters'] = event.get('pathParameters', {}) or {}
                event['pathParameters']['gender'] = gender
                return list_outfits_by_gender(event)
    
    # AI outfit generation routes
    if path == '/api/admin/outfits/ai-generate':
        if http_method == 'POST':
            return start_ai_outfit_generation(event)
    
    if path == '/api/admin/outfits/ai-generate/generate':
        if http_method == 'POST':
            return generate_ai_outfit_image(event)
    
    if path.startswith('/api/admin/outfits/ai-generate/status/'):
        if http_method == 'GET':
            # Extract job_id from path
            parts = path.split('/')
            if len(parts) >= 7:
                job_id = parts[6]
                event['pathParameters'] = event.get('pathParameters', {}) or {}
                event['pathParameters']['job_id'] = job_id
                return get_ai_generation_status(event)
    
    # Short/TikTok script parameterized routes
    if path.startswith('/api/admin/shorts/') and path not in [
        '/api/admin/shorts/ambassadors',
        '/api/admin/shorts/generate-script',
        '/api/admin/shorts/regenerate-scene',
        '/api/admin/shorts/save',
        '/api/admin/shorts/scene'
    ] and path != '/api/admin/shorts':
        parts = path.split('/')
        
        # Handle /api/admin/shorts/ambassadors/{id}/outfits
        if len(parts) >= 7 and parts[4] == 'ambassadors' and parts[6] == 'outfits':
            ambassador_id = parts[5]
            event['pathParameters'] = event.get('pathParameters', {}) or {}
            event['pathParameters']['id'] = ambassador_id
            if http_method == 'GET':
                return get_ambassador_outfits(event)
        
        # Handle /api/admin/shorts/{id} (GET/DELETE script by ID)
        elif len(parts) == 5:
            script_id = parts[4]
            event['pathParameters'] = event.get('pathParameters', {}) or {}
            event['pathParameters']['id'] = script_id
            
            if http_method == 'GET':
                return get_short_script(event)
            elif http_method == 'DELETE':
                return delete_short_script(event)
    
    # DEBUG: Categorize outfit route (TEMPORARY - DELETE AFTER USE)
    if path == '/api/admin/outfits/debug-categorize':
        if http_method == 'POST':
            return debug_categorize_outfit(event)
    
    # Outfit parameterized routes
    # Handle outfit variations routes first (more specific path)
    if '/variations' in path and path.startswith('/api/admin/outfits/'):
        # Extract outfit_id from path: /api/admin/outfits/{id}/variations[/generate|/status]
        parts = path.split('/')
        if len(parts) >= 6 and parts[5] == 'variations':
            outfit_id = parts[4]
            event['pathParameters'] = event.get('pathParameters', {}) or {}
            event['pathParameters']['id'] = outfit_id
            
            # Check for sub-routes: /variations/generate or /variations/status
            if len(parts) >= 7:
                sub_route = parts[6]
                if sub_route == 'generate' and http_method == 'POST':
                    return generate_variation_image(event)
                elif sub_route == 'status' and http_method == 'GET':
                    return get_variations_job_status(event)
            
            # Base variations route
            if http_method == 'POST':
                return start_outfit_variations(event)
            elif http_method == 'PUT':
                return apply_outfit_variation(event)
    
    if path.startswith('/api/admin/outfits/') and path != '/api/admin/outfits/upload-url':
        if http_method == 'GET':
            return get_outfit(event)
        elif http_method == 'PUT':
            return update_outfit(event)
        elif http_method == 'DELETE':
            return delete_outfit(event)
    
    # Product parameterized routes
    if path.startswith('/api/admin/products/') and path != '/api/admin/products/upload-url':
        if http_method == 'GET':
            return get_product(event)
        elif http_method == 'PUT':
            return update_product(event)
        elif http_method == 'DELETE':
            return delete_product(event)
    
    return response(404, {'error': f'Not found: {http_method} {path}'})