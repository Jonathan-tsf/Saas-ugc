"""
Outfits management handlers
Handles CRUD operations for ambassador outfits/tenues
"""
import json
import uuid
import base64
from datetime import datetime

from config import (
    response, decimal_to_python, verify_admin,
    dynamodb, s3, S3_BUCKET, upload_to_s3
)

# DynamoDB table for outfits
outfits_table = dynamodb.Table('outfits')


def get_outfits(event):
    """Get all outfits with optional filters - GET /api/admin/outfits"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        params = event.get('queryStringParameters', {}) or {}
        outfit_type = params.get('type')
        gender = params.get('gender')
        
        # Scan all outfits (for admin panel)
        result = outfits_table.scan()
        outfits = result.get('Items', [])
        
        # Apply filters if provided
        if outfit_type:
            outfits = [o for o in outfits if o.get('type') == outfit_type]
        if gender:
            outfits = [o for o in outfits if o.get('gender') == gender]
        
        # Sort by created_at descending
        outfits.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        return response(200, {
            'success': True,
            'outfits': decimal_to_python(outfits)
        })
        
    except Exception as e:
        print(f"Error getting outfits: {e}")
        return response(500, {'error': f'Failed to get outfits: {str(e)}'})


def get_outfit(event):
    """Get single outfit by ID - GET /api/admin/outfits/{id}"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        path_params = event.get('pathParameters', {}) or {}
        outfit_id = path_params.get('id')
        
        if not outfit_id:
            return response(400, {'error': 'Outfit ID is required'})
        
        result = outfits_table.get_item(Key={'id': outfit_id})
        outfit = result.get('Item')
        
        if not outfit:
            return response(404, {'error': 'Outfit not found'})
        
        return response(200, {
            'success': True,
            'outfit': decimal_to_python(outfit)
        })
        
    except Exception as e:
        print(f"Error getting outfit: {e}")
        return response(500, {'error': f'Failed to get outfit: {str(e)}'})


def create_outfit(event):
    """Create a new outfit - POST /api/admin/outfits"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    description = body.get('description')
    outfit_type = body.get('type')
    gender = body.get('gender')
    image_base64 = body.get('image_base64')
    
    if not all([description, outfit_type, gender, image_base64]):
        return response(400, {'error': 'description, type, gender, and image_base64 are required'})
    
    # Validate type
    valid_types = ['sport', 'casual', 'elegant', 'streetwear', 'fitness', 'outdoor']
    if outfit_type not in valid_types:
        return response(400, {'error': f'Invalid type. Must be one of: {", ".join(valid_types)}'})
    
    # Validate gender
    valid_genders = ['male', 'female', 'unisex']
    if gender not in valid_genders:
        return response(400, {'error': f'Invalid gender. Must be one of: {", ".join(valid_genders)}'})
    
    try:
        outfit_id = str(uuid.uuid4())
        
        # Upload image to S3 with cache headers
        image_key = f"outfits/{outfit_id}.png"
        image_data = base64.b64decode(image_base64)
        image_url = upload_to_s3(image_key, image_data, 'image/png', cache_days=365)
        
        # Create outfit record
        outfit = {
            'id': outfit_id,
            'description': description,
            'type': outfit_type,
            'gender': gender,
            'image_url': image_url,
            'ambassador_count': 0,  # Counter for ambassadors using this outfit
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat()
        }
        
        outfits_table.put_item(Item=outfit)
        
        return response(201, {
            'success': True,
            'outfit': outfit
        })
        
    except Exception as e:
        print(f"Error creating outfit: {e}")
        return response(500, {'error': f'Failed to create outfit: {str(e)}'})


def update_outfit(event):
    """Update an outfit - PUT /api/admin/outfits/{id}"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        path_params = event.get('pathParameters', {}) or {}
        outfit_id = path_params.get('id')
        
        if not outfit_id:
            return response(400, {'error': 'Outfit ID is required'})
        
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    try:
        # Check if outfit exists
        result = outfits_table.get_item(Key={'id': outfit_id})
        existing = result.get('Item')
        
        if not existing:
            return response(404, {'error': 'Outfit not found'})
        
        # Build update expression
        update_expr = "SET updated_at = :updated_at"
        expr_values = {':updated_at': datetime.now().isoformat()}
        
        if 'description' in body:
            update_expr += ", description = :description"
            expr_values[':description'] = body['description']
        
        if 'type' in body:
            valid_types = ['sport', 'casual', 'elegant', 'streetwear', 'fitness', 'outdoor']
            if body['type'] not in valid_types:
                return response(400, {'error': f'Invalid type. Must be one of: {", ".join(valid_types)}'})
            update_expr += ", #type = :type"
            expr_values[':type'] = body['type']
        
        if 'gender' in body:
            valid_genders = ['male', 'female', 'unisex']
            if body['gender'] not in valid_genders:
                return response(400, {'error': f'Invalid gender. Must be one of: {", ".join(valid_genders)}'})
            update_expr += ", gender = :gender"
            expr_values[':gender'] = body['gender']
        
        # Handle image update
        if 'image_base64' in body:
            image_key = f"outfits/{outfit_id}.png"
            image_data = base64.b64decode(body['image_base64'])
            image_url = upload_to_s3(image_key, image_data, 'image/png', cache_days=365)
            update_expr += ", image_url = :image_url"
            expr_values[':image_url'] = image_url
        
        # Update with expression attribute names for reserved word 'type'
        expr_names = {}
        if 'type' in body:
            expr_names['#type'] = 'type'
        
        update_params = {
            'Key': {'id': outfit_id},
            'UpdateExpression': update_expr,
            'ExpressionAttributeValues': expr_values,
            'ReturnValues': 'ALL_NEW'
        }
        
        if expr_names:
            update_params['ExpressionAttributeNames'] = expr_names
        
        result = outfits_table.update_item(**update_params)
        
        return response(200, {
            'success': True,
            'outfit': decimal_to_python(result['Attributes'])
        })
        
    except Exception as e:
        print(f"Error updating outfit: {e}")
        return response(500, {'error': f'Failed to update outfit: {str(e)}'})


def delete_outfit(event):
    """Delete an outfit - DELETE /api/admin/outfits/{id}"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        path_params = event.get('pathParameters', {}) or {}
        outfit_id = path_params.get('id')
        
        if not outfit_id:
            return response(400, {'error': 'Outfit ID is required'})
        
        # Check if outfit exists and get ambassador_count
        result = outfits_table.get_item(Key={'id': outfit_id})
        existing = result.get('Item')
        
        if not existing:
            return response(404, {'error': 'Outfit not found'})
        
        # Prevent deletion if ambassadors are using this outfit
        if existing.get('ambassador_count', 0) > 0:
            return response(400, {
                'error': f"Cannot delete outfit. {existing['ambassador_count']} ambassador(s) are using it."
            })
        
        # Delete from S3
        try:
            s3.delete_object(Bucket=S3_BUCKET, Key=f"outfits/{outfit_id}.png")
        except Exception as e:
            print(f"Warning: Could not delete S3 object: {e}")
        
        # Delete from DynamoDB
        outfits_table.delete_item(Key={'id': outfit_id})
        
        return response(200, {
            'success': True,
            'message': 'Outfit deleted successfully'
        })
        
    except Exception as e:
        print(f"Error deleting outfit: {e}")
        return response(500, {'error': f'Failed to delete outfit: {str(e)}'})


def get_upload_url(event):
    """Get presigned URL for outfit image upload - GET /api/admin/outfits/upload-url"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        outfit_id = str(uuid.uuid4())
        key = f"outfits/{outfit_id}.png"
        
        presigned_url = s3.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': S3_BUCKET,
                'Key': key,
                'ContentType': 'image/png'
            },
            ExpiresIn=300  # 5 minutes
        )
        
        image_url = f"https://{S3_BUCKET}.s3.amazonaws.com/{key}"
        
        return response(200, {
            'success': True,
            'upload_url': presigned_url,
            'image_url': image_url,
            'outfit_id': outfit_id
        })
        
    except Exception as e:
        print(f"Error generating upload URL: {e}")
        return response(500, {'error': f'Failed to generate upload URL: {str(e)}'})


def increment_outfit_count(outfit_id, increment=1):
    """Helper function to increment/decrement ambassador count for an outfit"""
    try:
        outfits_table.update_item(
            Key={'id': outfit_id},
            UpdateExpression='SET ambassador_count = if_not_exists(ambassador_count, :zero) + :inc, updated_at = :updated',
            ExpressionAttributeValues={
                ':inc': increment,
                ':zero': 0,
                ':updated': datetime.now().isoformat()
            }
        )
        return True
    except Exception as e:
        print(f"Error updating outfit count: {e}")
        return False
