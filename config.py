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
# Replicate API key for fallback
REPLICATE_API_KEY = os.environ.get('REPLICATE_KEY', '')

# AWS Clients
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(TABLE_NAME)
ambassadors_table = dynamodb.Table(AMBASSADORS_TABLE_NAME)
ses = boto3.client('ses', region_name='us-east-1')
s3 = boto3.client('s3', region_name='us-east-1')
lambda_client = boto3.client('lambda', region_name='us-east-1')

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


def upload_to_s3(key: str, body: bytes, content_type: str = 'image/png', cache_days: int = 365) -> str:
    """
    Upload file to S3 with proper cache headers for fast loading.
    Returns the public URL.
    
    Args:
        key: S3 object key (path)
        body: File content as bytes
        content_type: MIME type (default: image/png)
        cache_days: Cache duration in days (default: 365)
    
    Returns:
        Public S3 URL
    """
    cache_seconds = cache_days * 24 * 60 * 60
    
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=body,
        ContentType=content_type,
        CacheControl=f'public, max-age={cache_seconds}, immutable'
    )
    
    return f"https://{S3_BUCKET}.s3.amazonaws.com/{key}"


def verify_admin(event):
    """Verify admin password from Authorization header"""
    headers = event.get('headers', {}) or {}
    auth = headers.get('Authorization') or headers.get('authorization', '')
    
    if not auth.startswith('Bearer '):
        return False
    
    token = auth[7:]
    
    # Allow internal async calls (from Lambda invoke)
    if token == 'internal-async-call':
        return True
    
    return hashlib.sha256(token.encode()).hexdigest() == ADMIN_PASSWORD_HASH

