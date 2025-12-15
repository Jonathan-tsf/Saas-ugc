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
bedrock_runtime = boto3.client('bedrock-runtime', region_name='us-east-1')


def get_bedrock_client():
    """Get Bedrock runtime client for AI analysis"""
    return bedrock_runtime

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


def analyze_outfit_image(image_base64: str, valid_types: list) -> dict:
    """
    Use AWS Bedrock Claude Sonnet to analyze an outfit image.
    Returns detailed description and type.
    
    Args:
        image_base64: Base64 encoded image
        valid_types: List of valid outfit types to choose from
    
    Returns:
        dict with 'description' and 'type' keys
    """
    import base64
    
    # Claude Sonnet 4.5 model ID - use 'us.' prefix (same as showcase_generation.py)
    model_id = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    
    types_list = ", ".join(valid_types)
    
    prompt = f"""Analyse cette image de vêtement/tenue et fournis:

1. Une description TRÈS DÉTAILLÉE en français (100-150 caractères) qui décrit précisément:
   - Le type de vêtement exact (t-shirt, débardeur, sweat, legging, short, etc.)
   - La couleur principale et les couleurs secondaires
   - Les motifs ou imprimés s'il y en a (logo, rayures, graphiques, etc.)
   - Le style/coupe (ajusté, ample, crop, oversize, etc.)
   - La marque si visible
   - Les détails distinctifs (col, manches, fermetures, etc.)

2. Choisis le type le plus approprié parmi: {types_list}

Réponds UNIQUEMENT avec du JSON valide dans ce format exact:
{{"description": "Ta description détaillée ici", "type": "type_choisi"}}

Exemples de bonnes descriptions:
- "T-shirt noir Nike Dri-FIT ajusté avec logo swoosh blanc sur la poitrine, col rond, manches courtes"
- "Legging sport rose pastel taille haute, tissu compression, coutures apparentes sur les côtés"
- "Sweat à capuche gris chiné oversize avec poche kangourou, cordon de serrage blanc, logo brodé"
- "Brassière sport bleue marine avec bretelles croisées dans le dos, maintien fort, bande élastique large"
"""

    try:
        # Prepare the request body for Claude
        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 200,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",  # Frontend compresses to JPEG
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
        
        # Call Bedrock
        print(f"Calling Bedrock with model: {model_id}")
        print(f"Image base64 length: {len(image_base64)}")
        
        response = bedrock_runtime.invoke_model(
            modelId=model_id,
            body=json.dumps(request_body)
        )
        
        # Parse response
        response_body = json.loads(response['body'].read())
        print(f"Bedrock raw response: {response_body}")
        
        # Extract text content from Claude response (same pattern as showcase_generation.py)
        content_blocks = response_body.get('content', [])
        text_content = ""
        for block in content_blocks:
            if block.get('type') == 'text':
                text_content = block.get('text', '')
                break
        
        if not text_content:
            raise Exception(f"Empty response from Claude. Full response: {response_body}")
        
        print(f"Claude text response: {text_content}")
        
        # Strip markdown code blocks if present (Claude often wraps JSON in ```json ... ```)
        json_text = text_content.strip()
        if json_text.startswith('```'):
            # Remove opening ```json or ``` line
            lines = json_text.split('\n')
            # Find start of actual JSON (skip ```json line)
            start_idx = 1 if lines[0].startswith('```') else 0
            # Find end (remove closing ```)
            end_idx = len(lines)
            for i in range(len(lines) - 1, -1, -1):
                if lines[i].strip() == '```':
                    end_idx = i
                    break
            json_text = '\n'.join(lines[start_idx:end_idx])
        
        print(f"Extracted JSON text: {json_text}")
        
        # Parse the JSON response from Claude
        result = json.loads(json_text)
        
        # Validate the type is in the valid list
        if result.get('type') not in valid_types:
            result['type'] = valid_types[0]  # Default to first type
        
        # Ensure description is not too long (now allows up to 200 chars for detailed descriptions)
        if len(result.get('description', '')) > 200:
            result['description'] = result['description'][:197] + '...'
        
        print(f"Bedrock analysis result: {result}")
        return result
        
    except Exception as e:
        import traceback
        error_msg = f"Error analyzing outfit with Bedrock: {e}"
        print(error_msg)
        print(f"Full traceback: {traceback.format_exc()}")
        # Raise the error instead of using fallback - we want to know when AI fails
        raise Exception(f"AI analysis failed: {str(e)}. Please check Bedrock configuration and permissions.")


def generate_outfit_variations_descriptions(image_base64: str, original_description: str) -> list:
    """
    Use AWS Bedrock Claude Sonnet to generate 6 variation descriptions for an outfit.
    These descriptions will be used to generate new outfit images with Nano Banana Pro.
    
    Note: Reduced from 10 to 6 to stay under API Gateway 29-second timeout.
    
    Args:
        image_base64: Base64 encoded image of the original outfit
        original_description: The original outfit description
    
    Returns:
        list of 6 variation description strings
    """
    # Claude Sonnet 4.5 model ID - use 'us.' prefix (same as showcase_generation.py)
    model_id = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    
    prompt = f"""Regarde cette image de vêtement. La description originale est: "{original_description}"

Génère exactement 6 variations créatives de ce vêtement. Chaque variation doit:
- Garder le même TYPE de vêtement (si c'est un t-shirt, reste un t-shirt)
- Changer les couleurs, motifs, ou style de manière créative
- Être réaliste et vendable comme vêtement de sport/fitness
- Être décrite en français avec 80-120 caractères

Pour chaque variation, fournis une description COMPLÈTE qui pourrait être utilisée pour générer l'image du vêtement seul (sans mannequin, fond blanc, photo produit).

Réponds UNIQUEMENT avec du JSON valide:
{{"variations": [
    "Description variation 1...",
    "Description variation 2...",
    "Description variation 3...",
    "Description variation 4...",
    "Description variation 5...",
    "Description variation 6..."
]}}

Exemples de bonnes variations pour un "T-shirt noir Nike":
- "T-shirt blanc Nike avec logo swoosh rouge, coupe ajustée, col en V, tissu respirant"
- "T-shirt bleu marine Nike avec bandes blanches sur les manches, col rond, coupe regular"
- "T-shirt gris chiné Nike avec grand logo noir sur la poitrine, oversize, manches raglan"
"""

    try:
        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 2000,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",  # Frontend compresses to JPEG
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
        
        response = bedrock_runtime.invoke_model(
            modelId=model_id,
            body=json.dumps(request_body),
            contentType="application/json",
            accept="application/json"
        )
        
        response_body = json.loads(response['body'].read())
        content = response_body.get('content', [{}])[0].get('text', '{}')
        
        # Strip markdown code blocks if present
        json_text = content.strip()
        if json_text.startswith('```'):
            lines = json_text.split('\n')
            start_idx = 1 if lines[0].startswith('```') else 0
            end_idx = len(lines)
            for i in range(len(lines) - 1, -1, -1):
                if lines[i].strip() == '```':
                    end_idx = i
                    break
            json_text = '\n'.join(lines[start_idx:end_idx])
        
        result = json.loads(json_text)
        variations = result.get('variations', [])
        
        # Ensure we have exactly 6 variations (reduced from 10 to fit under API Gateway timeout)
        if len(variations) < 6:
            # Pad with generic variations if needed
            base_colors = ['rouge', 'bleu', 'vert', 'jaune', 'orange', 'violet']
            while len(variations) < 6:
                color = base_colors[len(variations) % len(base_colors)]
                variations.append(f"{original_description} en {color}")
        
        print(f"Generated {len(variations)} outfit variations")
        return variations[:6]
        
    except Exception as e:
        print(f"Error generating outfit variations: {e}")
        # Return basic color variations on error
        colors = ['rouge', 'bleu royal', 'vert émeraude', 'jaune soleil', 'orange vif', 'violet']
        return [f"{original_description} en {color}" for color in colors]


