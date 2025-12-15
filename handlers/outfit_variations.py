"""
Outfit Variations Handler
Generate 10 variations of an outfit using Nano Banana Pro (Gemini 3 Pro Image)
"""
import json
import uuid
import base64
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (
    response, decimal_to_python, verify_admin,
    dynamodb, s3, S3_BUCKET, upload_to_s3,
    generate_outfit_variations_descriptions, NANO_BANANA_API_KEY
)

# DynamoDB table for outfits
outfits_table = dynamodb.Table('outfits')

# Gemini 3 Pro Image (Nano Banana Pro) endpoint
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-image-preview:generateContent"


def generate_single_variation(description: str, index: int) -> dict:
    """
    Generate a single outfit variation image using Nano Banana Pro (Gemini 3 Pro Image).
    
    Args:
        description: The variation description
        index: The variation index (0-9)
    
    Returns:
        dict with 'index', 'description', 'image_base64' or 'error'
    """
    try:
        prompt = f"""Generate a professional product photo of this clothing item on a pure white background:

{description}

Requirements:
- Product photography style, e-commerce quality
- Pure white background (#FFFFFF)
- The clothing item should be displayed flat lay or on invisible mannequin
- NO human model visible
- High quality, well-lit, professional
- Show the garment's details, texture, and colors clearly
- Square format, centered composition
"""
        
        headers = {
            "Content-Type": "application/json"
        }
        
        payload = {
            "contents": [{
                "parts": [{
                    "text": prompt
                }]
            }],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": {
                    "aspectRatio": "1:1",
                    "imageSize": "1K"
                }
            }
        }
        
        # Call Gemini API with API key
        api_url = f"{GEMINI_API_URL}?key={NANO_BANANA_API_KEY}"
        
        resp = requests.post(
            api_url,
            headers=headers,
            json=payload,
            timeout=120
        )
        
        if resp.status_code != 200:
            print(f"Gemini API error for variation {index}: {resp.status_code} - {resp.text}")
            return {
                'index': index,
                'description': description,
                'error': f"API error: {resp.status_code}"
            }
        
        result = resp.json()
        
        # Extract the image from the response
        candidates = result.get('candidates', [])
        if not candidates:
            return {
                'index': index,
                'description': description,
                'error': "No candidates in response"
            }
        
        content = candidates[0].get('content', {})
        parts = content.get('parts', [])
        
        image_base64 = None
        for part in parts:
            if 'inlineData' in part:
                image_base64 = part['inlineData'].get('data')
                break
        
        if not image_base64:
            return {
                'index': index,
                'description': description,
                'error': "No image in response"
            }
        
        return {
            'index': index,
            'description': description,
            'image_base64': image_base64
        }
        
    except Exception as e:
        print(f"Error generating variation {index}: {e}")
        return {
            'index': index,
            'description': description,
            'error': str(e)
        }


def generate_outfit_variations(event):
    """
    Generate 10 variations of an outfit - POST /api/admin/outfits/{id}/variations
    
    1. Fetches the outfit from DynamoDB
    2. Downloads the outfit image from S3
    3. Uses Bedrock Claude to generate 10 variation descriptions
    4. Uses Nano Banana Pro to generate 10 variation images in parallel
    5. Returns all variations with base64 images
    
    The user will then select which variation they want to apply.
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        path_params = event.get('pathParameters', {}) or {}
        outfit_id = path_params.get('id')
        
        if not outfit_id:
            return response(400, {'error': 'Outfit ID is required'})
        
        # Get the outfit from DynamoDB
        result = outfits_table.get_item(Key={'id': outfit_id})
        outfit = result.get('Item')
        
        if not outfit:
            return response(404, {'error': 'Outfit not found'})
        
        image_url = outfit.get('image_url')
        description = outfit.get('description', 'Tenue sport')
        
        if not image_url:
            return response(400, {'error': 'Outfit has no image'})
        
        print(f"Generating variations for outfit {outfit_id}: {description}")
        
        # Download the image from S3
        # Extract the S3 key from the URL
        s3_key = image_url.replace(f"https://{S3_BUCKET}.s3.amazonaws.com/", "")
        
        s3_response = s3.get_object(Bucket=S3_BUCKET, Key=s3_key)
        image_bytes = s3_response['Body'].read()
        image_base64 = base64.b64encode(image_bytes).decode('utf-8')
        
        # Generate 10 variation descriptions using Bedrock Claude
        print("Generating variation descriptions with Bedrock Claude...")
        variation_descriptions = generate_outfit_variations_descriptions(image_base64, description)
        
        print(f"Generated {len(variation_descriptions)} variation descriptions")
        for i, desc in enumerate(variation_descriptions):
            print(f"  {i+1}. {desc}")
        
        # Generate all 10 variations in parallel using Nano Banana Pro
        print("Generating variation images with Nano Banana Pro...")
        variations = []
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(generate_single_variation, desc, i): i 
                for i, desc in enumerate(variation_descriptions)
            }
            
            for future in as_completed(futures):
                result = future.result()
                variations.append(result)
        
        # Sort by index
        variations.sort(key=lambda x: x['index'])
        
        # Count successes and failures
        successes = [v for v in variations if 'image_base64' in v]
        failures = [v for v in variations if 'error' in v]
        
        print(f"Generated {len(successes)} successful variations, {len(failures)} failures")
        
        return response(200, {
            'success': True,
            'outfit_id': outfit_id,
            'original_description': description,
            'variations': variations,
            'stats': {
                'total': len(variations),
                'success': len(successes),
                'failed': len(failures)
            }
        })
        
    except Exception as e:
        print(f"Error generating outfit variations: {e}")
        import traceback
        traceback.print_exc()
        return response(500, {'error': f'Failed to generate variations: {str(e)}'})


def apply_outfit_variation(event):
    """
    Apply a selected variation to an outfit - PUT /api/admin/outfits/{id}/variations
    
    Body: {
        "description": "New description",
        "image_base64": "base64 encoded image"
    }
    
    This updates the outfit with the new description and image.
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        path_params = event.get('pathParameters', {}) or {}
        outfit_id = path_params.get('id')
        
        if not outfit_id:
            return response(400, {'error': 'Outfit ID is required'})
        
        body = json.loads(event.get('body', '{}'))
        new_description = body.get('description')
        image_base64 = body.get('image_base64')
        
        if not new_description or not image_base64:
            return response(400, {'error': 'description and image_base64 are required'})
        
        # Check if outfit exists
        result = outfits_table.get_item(Key={'id': outfit_id})
        outfit = result.get('Item')
        
        if not outfit:
            return response(404, {'error': 'Outfit not found'})
        
        # Upload new image to S3
        image_key = f"outfits/{outfit_id}.png"
        image_data = base64.b64decode(image_base64)
        image_url = upload_to_s3(image_key, image_data, 'image/png', cache_days=365)
        
        # Update the outfit in DynamoDB
        outfits_table.update_item(
            Key={'id': outfit_id},
            UpdateExpression="SET description = :desc, image_url = :url, updated_at = :updated",
            ExpressionAttributeValues={
                ':desc': new_description,
                ':url': image_url,
                ':updated': datetime.now().isoformat()
            }
        )
        
        # Get updated outfit
        result = outfits_table.get_item(Key={'id': outfit_id})
        updated_outfit = result.get('Item')
        
        return response(200, {
            'success': True,
            'outfit': decimal_to_python(updated_outfit)
        })
        
    except Exception as e:
        print(f"Error applying outfit variation: {e}")
        return response(500, {'error': f'Failed to apply variation: {str(e)}'})
