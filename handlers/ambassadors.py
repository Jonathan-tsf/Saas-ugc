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
        'created_at': created_at,
        'updated_at': created_at
    }
    
    # Update outfit counts for newly assigned outfits
    outfit_ids = body.get('outfit_ids', [])
    if outfit_ids:
        from handlers.outfits import increment_outfit_count
        for outfit_id in outfit_ids:
            increment_outfit_count(outfit_id, 1)
    
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
    if 'outfit_ids' in body:
        try:
            current = ambassadors_table.get_item(Key={'id': ambassador_id})
            if current.get('Item'):
                old_outfit_ids = current['Item'].get('outfit_ids', []) or []
        except Exception as e:
            print(f"Warning: Could not get current ambassador: {e}")
    
    update_parts = []
    expression_values = {}
    expression_names = {}
    
    updatable_fields = [
        'name', 'description', 'photo_profile', 'photo_list_base_array',
        'video_list_base_array', 'hasBeenChosen', 'gender', 'style',
        'isRecommended', 'userOwnerId', 'outfit_ids'
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
