"""
Admin settings and authentication handlers
"""
import json
import hashlib
from datetime import datetime

from config import (
    response, decimal_to_python, verify_admin,
    table, ADMIN_PASSWORD_HASH
)


def admin_login(event):
    """Verify admin password - POST /api/admin/login"""
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON'})
    
    password = body.get('password', '')
    
    if hashlib.sha256(password.encode()).hexdigest() == ADMIN_PASSWORD_HASH:
        return response(200, {'success': True, 'token': password})
    else:
        return response(401, {'error': 'Invalid password'})


def update_availability_settings(event):
    """Update availability settings (admin only) - PUT /api/admin/settings"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON'})
    
    month = body.get('month')
    if not month:
        return response(400, {'error': 'month is required'})
    
    settings = {
        'id': f'SETTINGS#{month}',
        'type': 'settings',
        'month': month,
        'updated_at': datetime.now().isoformat()
    }
    
    if 'working_hours' in body:
        settings['working_hours'] = body['working_hours']
    if 'blocked_days' in body:
        settings['blocked_days'] = body['blocked_days']
    if 'working_days' in body:
        settings['working_days'] = body['working_days']
    if 'custom_slots' in body:
        settings['custom_slots'] = body['custom_slots']
    
    try:
        table.put_item(Item=settings)
        return response(200, {'success': True})
    except Exception as e:
        print(f"Error updating settings: {e}")
        return response(500, {'error': 'Failed to update settings'})


def get_availability_settings(event):
    """Get availability settings (admin only) - GET /api/admin/settings?month=2025-12"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    params = event.get('queryStringParameters', {}) or {}
    month = params.get('month')
    
    if not month:
        return response(400, {'error': 'month parameter required'})
    
    try:
        result = table.get_item(Key={'id': f'SETTINGS#{month}'})
        settings = result.get('Item', {
            'working_hours': {
                'start': 10,
                'end': 18,
                'break_start': 12,
                'break_end': 14,
                'slot_duration': 30
            },
            'working_days': [1, 2, 3, 4, 5],
            'blocked_days': [],
            'custom_slots': {}
        })
        return response(200, decimal_to_python(settings))
    except Exception as e:
        print(f"Error getting settings: {e}")
        return response(500, {'error': 'Failed to get settings'})
