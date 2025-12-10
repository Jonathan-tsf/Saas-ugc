"""
Configuration and shared utilities for Lambda functions
"""
import json
import hashlib
import os
import boto3
from decimal import Decimal

# Configuration - Read from environment variables
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'SAASPASSWORD123')
ADMIN_PASSWORD_HASH = hashlib.sha256(ADMIN_PASSWORD.encode()).hexdigest()
TABLE_NAME = "demos"
AMBASSADORS_TABLE_NAME = "ambassadors"
OWNER_EMAIL = "support@bysepia.com"
S3_BUCKET = os.environ.get('S3_BUCKET', 'ugc-ambassadors-media')
# Using Gemini API - variable name kept for compatibility but it's a Gemini API key
NANO_BANANA_API_KEY = os.environ.get('NANO_BANANA_PRO_API_KEY', os.environ.get('NANO_BANANA_API_KEY', ''))

# AWS Clients
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(TABLE_NAME)
ambassadors_table = dynamodb.Table(AMBASSADORS_TABLE_NAME)
ses = boto3.client('ses', region_name='us-east-1')
s3 = boto3.client('s3', region_name='us-east-1')

# CORS Headers
CORS_HEADERS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type,Authorization',
    'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
}


def response(status_code, body):
    """Helper to return API Gateway response with CORS"""
    return {
        'statusCode': status_code,
        'headers': CORS_HEADERS,
        'body': json.dumps(body, default=str)
    }


def decimal_to_python(obj):
    """Convert DynamoDB Decimal to Python types"""
    if isinstance(obj, list):
        return [decimal_to_python(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: decimal_to_python(v) for k, v in obj.items()}
    elif isinstance(obj, Decimal):
        if obj % 1 == 0:
            return int(obj)
        else:
            return float(obj)
    else:
        return obj


def verify_admin(event):
    """Verify admin password from Authorization header"""
    headers = event.get('headers', {}) or {}
    auth = headers.get('Authorization') or headers.get('authorization', '')
    
    if not auth.startswith('Bearer '):
        return False
    
    token = auth[7:]
    return hashlib.sha256(token.encode()).hexdigest() == ADMIN_PASSWORD_HASH
