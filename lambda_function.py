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


def lambda_handler(event, context):
    """Main Lambda handler - routes requests to appropriate functions"""
    print(f"Event: {json.dumps(event)}")
    
    # Handle async background task invocations
    if 'action' in event and event['action'] == 'generate_variations':
        from handlers.transform_async import generate_step_variations_async
        session_id = event['session_id']
        step = event['step']
        image_base64 = event['image_base64']
        generate_step_variations_async(session_id, step, image_base64)
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
    }
    
    # Find matching route
    handler = routes.get((http_method, path))
    
    if handler:
        return handler(event)
    
    # Handle parameterized routes
    if http_method == 'DELETE' and path.startswith('/api/admin/bookings/'):
        return delete_booking(event)
    
    if http_method == 'GET' and path.startswith('/api/admin/ambassadors/') and path != '/api/admin/ambassadors/upload-url':
        return get_ambassador(event)
    
    if http_method == 'DELETE' and path.startswith('/api/admin/ambassadors/'):
        return delete_ambassador(event)
    
    return response(404, {'error': f'Not found: {http_method} {path}'})
