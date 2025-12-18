"""
Ambassadors CRUD management handlers
"""
import json
import uuid
from datetime import datetime
from boto3.dynamodb.conditions import Attr

from config import (
    response, decimal_to_python, verify_admin,
    ambassadors_table, s3, S3_BUCKET
)


def get_ambassadors(event):
    """Get all ambassadors - GET /api/admin/ambassadors"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    params = event.get('queryStringParameters', {}) or {}
    
    try:
        filter_expression = None
        
        if params.get('gender'):
            filter_expression = Attr('gender').eq(params['gender'])
        
        if params.get('style'):
            style_filter = Attr('style').eq(params['style'])
            filter_expression = filter_expression & style_filter if filter_expression else style_filter
        
        if params.get('isRecommended') == 'true':
            rec_filter = Attr('isRecommended').eq(True)
            filter_expression = filter_expression & rec_filter if filter_expression else rec_filter
        
        if filter_expression:
            scan_response = ambassadors_table.scan(FilterExpression=filter_expression)
        else:
            scan_response = ambassadors_table.scan()
        
        ambassadors = [decimal_to_python(item) for item in scan_response.get('Items', [])]
        ambassadors.sort(key=lambda x: x.get('name', ''))
        
        return response(200, {'ambassadors': ambassadors})
    except Exception as e:
        print(f"Error getting ambassadors: {e}")
        return response(500, {'error': 'Failed to get ambassadors'})


def get_ambassador(event):
    """Get a single ambassador - GET /api/admin/ambassadors/{id}"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    params = event.get('pathParameters', {}) or {}
    query_params = event.get('queryStringParameters', {}) or {}
    ambassador_id = params.get('id') or query_params.get('id')
    
    if not ambassador_id:
        return response(400, {'error': 'Ambassador ID required'})
    
    try:
        result = ambassadors_table.get_item(Key={'id': ambassador_id})
        ambassador = result.get('Item')
        
        if not ambassador:
            return response(404, {'error': 'Ambassador not found'})
        
        return response(200, decimal_to_python(ambassador))
    except Exception as e:
        print(f"Error getting ambassador: {e}")
        return response(500, {'error': 'Failed to get ambassador'})


def create_ambassador(event):
    """Create a new ambassador - POST /api/admin/ambassadors"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    name = body.get('name', '').strip()
    
    if not name:
        return response(400, {'error': 'Name is required'})
    
    ambassador_id = str(uuid.uuid4())
    created_at = datetime.now().isoformat()
    
    ambassador = {
        'id': ambassador_id,
        'name': name,
        'description': body.get('description', ''),
        'photo_profile': body.get('photo_profile', ''),
        'photo_list_base_array': body.get('photo_list_base_array', []),
        'video_list_base_array': body.get('video_list_base_array', []),
        'hasBeenChosen': body.get('hasBeenChosen', False),
        'gender': body.get('gender', ''),
        'style': body.get('style', ''),
        'isRecommended': body.get('isRecommended', False),
        'userOwnerId': body.get('userOwnerId', ''),
        'outfit_ids': body.get('outfit_ids', []),  # List of outfit IDs assigned to this ambassador
        'product_ids': body.get('product_ids', []),  # List of product IDs assigned to this ambassador
        'created_at': created_at,
        'updated_at': created_at
    }
    
    # Update outfit counts for newly assigned outfits
    outfit_ids = body.get('outfit_ids', [])
    if outfit_ids:
        from handlers.outfits import increment_outfit_count
        for outfit_id in outfit_ids:
            increment_outfit_count(outfit_id, 1)
    
    # Update product counts for newly assigned products
    product_ids = body.get('product_ids', [])
    if product_ids:
        from handlers.products import increment_product_count
        for product_id in product_ids:
            increment_product_count(product_id, 1)
    
    try:
        ambassadors_table.put_item(Item=ambassador)
        return response(201, {'success': True, 'ambassador': ambassador})
    except Exception as e:
        print(f"Error creating ambassador: {e}")
        return response(500, {'error': 'Failed to create ambassador'})


def update_ambassador(event):
    """Update an ambassador - PUT /api/admin/ambassadors"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    ambassador_id = body.get('id')
    
    if not ambassador_id:
        return response(400, {'error': 'Ambassador ID required'})
    
    # Get current ambassador to compare outfit_ids
    old_outfit_ids = []
    old_product_ids = []
    if 'outfit_ids' in body or 'product_ids' in body:
        try:
            current = ambassadors_table.get_item(Key={'id': ambassador_id})
            if current.get('Item'):
                old_outfit_ids = current['Item'].get('outfit_ids', []) or []
                old_product_ids = current['Item'].get('product_ids', []) or []
        except Exception as e:
            print(f"Warning: Could not get current ambassador: {e}")
    
    update_parts = []
    expression_values = {}
    expression_names = {}
    
    updatable_fields = [
        'name', 'description', 'photo_profile', 'photo_list_base_array',
        'video_list_base_array', 'hasBeenChosen', 'gender', 'style',
        'isRecommended', 'userOwnerId', 'outfit_ids', 'product_ids'
    ]
    
    for field in updatable_fields:
        if field in body:
            update_parts.append(f"#{field} = :{field}")
            expression_values[f":{field}"] = body[field]
            expression_names[f"#{field}"] = field
    
    if not update_parts:
        return response(400, {'error': 'No fields to update'})
    
    update_parts.append("#updated_at = :updated_at")
    expression_values[":updated_at"] = datetime.now().isoformat()
    expression_names["#updated_at"] = "updated_at"
    
    update_expression = "SET " + ", ".join(update_parts)
    
    try:
        result = ambassadors_table.update_item(
            Key={'id': ambassador_id},
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_values,
            ExpressionAttributeNames=expression_names,
            ReturnValues="ALL_NEW"
        )
        
        # Update outfit counts if outfit_ids changed
        if 'outfit_ids' in body:
            new_outfit_ids = body.get('outfit_ids', []) or []
            old_set = set(old_outfit_ids)
            new_set = set(new_outfit_ids)
            
            added = new_set - old_set
            removed = old_set - new_set
            
            if added or removed:
                from handlers.outfits import increment_outfit_count
                for outfit_id in added:
                    increment_outfit_count(outfit_id, 1)
                for outfit_id in removed:
                    increment_outfit_count(outfit_id, -1)
        
        # Update product counts if product_ids changed
        if 'product_ids' in body:
            new_product_ids = body.get('product_ids', []) or []
            old_set = set(old_product_ids)
            new_set = set(new_product_ids)
            
            added = new_set - old_set
            removed = old_set - new_set
            
            if added or removed:
                from handlers.products import increment_product_count
                for product_id in added:
                    increment_product_count(product_id, 1)
                for product_id in removed:
                    increment_product_count(product_id, -1)
        
        return response(200, {'success': True, 'ambassador': decimal_to_python(result['Attributes'])})
    except Exception as e:
        print(f"Error updating ambassador: {e}")
        return response(500, {'error': 'Failed to update ambassador'})


def delete_ambassador(event):
    """Delete an ambassador - DELETE /api/admin/ambassadors"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    params = event.get('pathParameters', {}) or {}
    query_params = event.get('queryStringParameters', {}) or {}
    ambassador_id = params.get('id') or query_params.get('id')
    
    if not ambassador_id:
        return response(400, {'error': 'Ambassador ID required'})
    
    try:
        # Get ambassador to check outfit_ids before deletion
        result = ambassadors_table.get_item(Key={'id': ambassador_id})
        ambassador = result.get('Item')
        
        if ambassador:
            # Decrement outfit counts
            outfit_ids = ambassador.get('outfit_ids', [])
            if outfit_ids:
                from handlers.outfits import increment_outfit_count
                for outfit_id in outfit_ids:
                    increment_outfit_count(outfit_id, -1)
            
            # Decrement product counts
            product_ids = ambassador.get('product_ids', [])
            if product_ids:
                from handlers.products import increment_product_count
                for product_id in product_ids:
                    increment_product_count(product_id, -1)
        
        ambassadors_table.delete_item(Key={'id': ambassador_id})
        return response(200, {'success': True})
    except Exception as e:
        print(f"Error deleting ambassador: {e}")
        return response(500, {'error': 'Failed to delete ambassador'})


def get_upload_url(event):
    """Generate presigned URL for S3 upload - POST /api/admin/ambassadors/upload-url"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    filename = body.get('filename', '').strip()
    content_type = body.get('content_type', 'application/octet-stream')
    folder = body.get('folder', 'uploads')
    
    if not filename:
        return response(400, {'error': 'Filename required'})
    
    file_extension = filename.split('.')[-1] if '.' in filename else ''
    unique_key = f"{folder}/{uuid.uuid4()}.{file_extension}"
    
    try:
        presigned_url = s3.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': S3_BUCKET,
                'Key': unique_key,
                'ContentType': content_type
            },
            ExpiresIn=3600
        )
        
        file_url = f"https://{S3_BUCKET}.s3.amazonaws.com/{unique_key}"
        
        return response(200, {
            'upload_url': presigned_url,
            'file_url': file_url,
            'key': unique_key
        })
    except Exception as e:
        print(f"Error generating presigned URL: {e}")
        return response(500, {'error': 'Failed to generate upload URL'})


def get_public_ambassadors(event):
    """Get public ambassadors (for frontend) - GET /api/ambassadors
    
    Returns ambassadors that are:
    - Recommended OR hasBeenChosen
    - Optionally filtered by gender/style
    - Includes showcase_photos for vitrine display
    """
    params = event.get('queryStringParameters', {}) or {}
    
    try:
        # Get all ambassadors first, then filter
        scan_response = ambassadors_table.scan()
        all_ambassadors = [decimal_to_python(item) for item in scan_response.get('Items', [])]
        
        # Filter by isRecommended OR hasBeenChosen OR has selected showcase photos
        ambassadors = []
        for amb in all_ambassadors:
            # Check if has selected showcase photos
            showcase_photos = amb.get('showcase_photos', [])
            has_selected_showcase = any(p.get('selected_image') for p in showcase_photos)
            
            # Include if recommended, chosen, or has showcase photos
            if amb.get('isRecommended') or amb.get('hasBeenChosen') or has_selected_showcase:
                # Apply additional filters if provided
                if params.get('gender') and amb.get('gender') != params['gender']:
                    continue
                if params.get('style') and amb.get('style') != params['style']:
                    continue
                ambassadors.append(amb)
        
        public_ambassadors = []
        for amb in ambassadors:
            # Filter showcase_photos to only include selected ones for public API
            showcase_photos = amb.get('showcase_photos', [])
            public_showcase = [
                {
                    'scene_id': p.get('scene_id'),
                    'scene_number': p.get('scene_number'),
                    'scene_description': p.get('scene_description'),
                    'outfit_category': p.get('outfit_category'),
                    'selected_image': p.get('selected_image'),
                    'status': p.get('status')
                }
                for p in showcase_photos
                if p.get('selected_image')  # Only include photos with selected images
            ]
            
            public_ambassadors.append({
                'id': amb.get('id'),
                'name': amb.get('name'),
                'description': amb.get('description'),
                'photo_profile': amb.get('photo_profile'),
                'photo_list_base_array': amb.get('photo_list_base_array', []),
                'video_list_base_array': amb.get('video_list_base_array', []),
                'gender': amb.get('gender'),
                'style': amb.get('style'),
                'isRecommended': amb.get('isRecommended'),
                'showcase_photos': public_showcase  # Include showcase photos!
            })
        
        return response(200, {'ambassadors': public_ambassadors})
    except Exception as e:
        print(f"Error getting public ambassadors: {e}")
        return response(500, {'error': 'Failed to get ambassadors'})


def get_hero_videos(event):
    """Get random showcase videos for hero section - GET /api/hero-videos
    
    Returns a diversified selection of showcase videos:
    - From ALL ambassadors (including attributed ones) for maximum content
    - Diversified by gender (alternate male/female)
    - Diversified semantically by scene type (uses prompt analysis)
    - Returns random selection each time
    
    Query params:
    - count: number of videos to return (default: 6, max: 24)
    """
    import random
    import re
    
    params = event.get('queryStringParameters', {}) or {}
    requested_count = min(int(params.get('count', 6)), 24)  # Increased max to 24
    
    print(f"[HERO_VIDEOS] Requested count: {requested_count}")
    
    try:
        # Get all ambassadors
        scan_response = ambassadors_table.scan()
        all_ambassadors = [decimal_to_python(item) for item in scan_response.get('Items', [])]
        
        print(f"[HERO_VIDEOS] Total ambassadors found: {len(all_ambassadors)}")
        
        # Collect all videos from ALL ambassadors (not just non-attributed)
        male_videos = []
        female_videos = []
        
        for amb in all_ambassadors:
            # Include ALL ambassadors for hero videos
            # (removed hasBeenChosen filter to show more content)
            
            # Get showcase videos
            showcase_videos = amb.get('showcase_videos', [])
            if showcase_videos:
                print(f"[HERO_VIDEOS] Ambassador {amb.get('name', 'Unknown')} has {len(showcase_videos)} videos")
            gender = amb.get('gender', 'other')
            
            for video in showcase_videos:
                video_data = {
                    'url': video.get('url'),
                    'prompt': video.get('prompt', ''),
                    'ambassador_id': amb.get('id'),
                    'ambassador_name': amb.get('name'),
                    'gender': gender,
                    'scene_category': categorize_scene(video.get('prompt', ''))
                }
                
                if gender == 'male':
                    male_videos.append(video_data)
                else:
                    female_videos.append(video_data)
        
        # Shuffle both lists
        random.shuffle(male_videos)
        random.shuffle(female_videos)
        
        # Diversified selection algorithm
        selected_videos = []
        used_categories = set()
        male_idx = 0
        female_idx = 0
        use_male = random.choice([True, False])  # Random starting gender
        
        while len(selected_videos) < requested_count:
            # Alternate between genders
            if use_male and male_idx < len(male_videos):
                video = male_videos[male_idx]
                male_idx += 1
            elif not use_male and female_idx < len(female_videos):
                video = female_videos[female_idx]
                female_idx += 1
            elif male_idx < len(male_videos):
                video = male_videos[male_idx]
                male_idx += 1
            elif female_idx < len(female_videos):
                video = female_videos[female_idx]
                female_idx += 1
            else:
                break  # No more videos available
            
            # Check semantic diversity - prefer different scene categories
            scene_cat = video.get('scene_category', 'other')
            
            # Accept if category not used recently (last 3) or if we need videos
            recent_categories = [v['scene_category'] for v in selected_videos[-3:]]
            if scene_cat not in recent_categories or len(selected_videos) >= requested_count - 2:
                selected_videos.append(video)
                used_categories.add(scene_cat)
            
            use_male = not use_male  # Alternate gender
        
        # Final shuffle to randomize the order
        random.shuffle(selected_videos)
        
        print(f"[HERO_VIDEOS] Returning {len(selected_videos)} videos (male: {len(male_videos)}, female: {len(female_videos)})")
        
        return response(200, {
            'videos': selected_videos,
            'count': len(selected_videos),
            'total_male': len(male_videos),
            'total_female': len(female_videos)
        })
        
    except Exception as e:
        print(f"Error getting hero videos: {e}")
        return response(500, {'error': 'Failed to get hero videos'})


def categorize_scene(prompt: str) -> str:
    """Categorize a video scene based on its prompt using semantic analysis."""
    if not prompt:
        return 'other'
    
    prompt_lower = prompt.lower()
    
    # Scene categories based on keywords
    categories = {
        'gym': ['gym', 'workout', 'exercise', 'fitness', 'weight', 'dumbbell', 'treadmill', 'plank', 'push-up', 'stretching', 'musculation'],
        'kitchen': ['kitchen', 'cooking', 'smoothie', 'food', 'meal', 'chopping', 'preparing', 'nutrition', 'healthy eating'],
        'office': ['laptop', 'computer', 'typing', 'desk', 'office', 'keyboard', 'work', 'bureau'],
        'phone': ['phone', 'scrolling', 'mobile', 'texting', 'smartphone'],
        'relaxation': ['couch', 'sofa', 'relaxing', 'reading', 'book', 'resting', 'sitting'],
        'walking': ['walking', 'steps', 'standing', 'posing'],
        'mirror': ['mirror', 'reflection', 'checking']
    }
    
    for category, keywords in categories.items():
        for keyword in keywords:
            if keyword in prompt_lower:
                return category
    
    return 'other'

