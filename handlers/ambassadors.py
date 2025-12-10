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
        'created_at': created_at,
        'updated_at': created_at
    }
    
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
    
    update_parts = []
    expression_values = {}
    expression_names = {}
    
    updatable_fields = [
        'name', 'description', 'photo_profile', 'photo_list_base_array',
        'video_list_base_array', 'hasBeenChosen', 'gender', 'style',
        'isRecommended', 'userOwnerId'
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
    """Get public ambassadors (for frontend) - GET /api/ambassadors"""
    params = event.get('queryStringParameters', {}) or {}
    
    try:
        filter_expression = Attr('isRecommended').eq(True) | Attr('hasBeenChosen').eq(True)
        
        if params.get('gender'):
            filter_expression = filter_expression & Attr('gender').eq(params['gender'])
        
        if params.get('style'):
            filter_expression = filter_expression & Attr('style').eq(params['style'])
        
        scan_response = ambassadors_table.scan(FilterExpression=filter_expression)
        ambassadors = [decimal_to_python(item) for item in scan_response.get('Items', [])]
        
        public_ambassadors = []
        for amb in ambassadors:
            public_ambassadors.append({
                'id': amb.get('id'),
                'name': amb.get('name'),
                'description': amb.get('description'),
                'photo_profile': amb.get('photo_profile'),
                'photo_list_base_array': amb.get('photo_list_base_array', []),
                'video_list_base_array': amb.get('video_list_base_array', []),
                'gender': amb.get('gender'),
                'style': amb.get('style'),
                'isRecommended': amb.get('isRecommended')
            })
        
        return response(200, {'ambassadors': public_ambassadors})
    except Exception as e:
        print(f"Error getting public ambassadors: {e}")
        return response(500, {'error': 'Failed to get ambassadors'})
