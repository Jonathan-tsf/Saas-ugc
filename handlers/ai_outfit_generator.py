"""
AI Outfit Generator Handler
Generate new outfit ideas using Claude 4.5 based on existing outfits,
then create images with Nano Banana Pro using existing photos as style reference.

Flow:
1. Fetch all existing outfit descriptions for a gender
2. Send to Claude 4.5 to generate N new unique outfit descriptions
3. Pick 2-3 random existing outfit images as style reference
4. Generate each new outfit image with Nano Banana Pro
"""
import json
import uuid
import base64
import random
import requests
from datetime import datetime
from decimal import Decimal

from config import (
    response, decimal_to_python, verify_admin,
    dynamodb, s3, S3_BUCKET, upload_to_s3,
    bedrock_runtime, NANO_BANANA_API_KEY
)

# DynamoDB tables
outfits_table = dynamodb.Table('outfits')
jobs_table = dynamodb.Table('nano_banana_jobs')

# Gemini 3 Pro Image (Nano Banana Pro) endpoint
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-image-preview:generateContent"


def generate_new_outfit_descriptions(existing_descriptions: list, gender: str, num_to_generate: int) -> list:
    """
    Use Claude 4.5 to generate new unique outfit descriptions based on existing ones.
    
    Args:
        existing_descriptions: List of existing outfit descriptions
        gender: 'male' or 'female'
        num_to_generate: Number of new outfits to generate
    
    Returns:
        List of new outfit description dicts with 'description' and 'type'
    """
    model_id = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    
    gender_context = "femme (vêtements féminins: brassières, leggings, crop tops, etc.)" if gender == 'female' else "homme (vêtements masculins: t-shirts, joggings, shorts, etc.)"
    
    existing_list = "\n".join([f"- {desc}" for desc in existing_descriptions[:50]])  # Limit to 50 for context
    
    prompt = f"""Tu es un expert en mode sportswear/fitness. Voici les tenues {gender_context} qui existent déjà dans notre catalogue:

{existing_list}

Génère exactement {num_to_generate} NOUVELLES descriptions de tenues de sport/fitness pour {gender_context} qui:
1. N'existent PAS déjà dans la liste ci-dessus (évite les doublons)
2. Sont variées en termes de couleurs, motifs, styles et coupes
3. Sont réalistes et vendables
4. Correspondent au style sportswear/fitness moderne

Pour chaque tenue, fournis:
- Une description détaillée (80-120 caractères) incluant: type de vêtement, couleur(s), marque fictive ou style, détails distinctifs
- Le type de vêtement (t-shirt, brassiere, legging, jogging, short, debardeur, crop-top, sweat, veste)

Réponds UNIQUEMENT avec du JSON valide:
{{
    "outfits": [
        {{"description": "Description de la tenue 1...", "type": "legging"}},
        {{"description": "Description de la tenue 2...", "type": "t-shirt"}},
        ...
    ]
}}

Exemples de bonnes descriptions:
- "Legging noir haute taille avec bandes latérales roses fluo, tissu compression, logo discret"
- "T-shirt oversize gris chiné avec imprimé graphique géométrique, col large, coupe décontractée"
- "Brassière sport turquoise tie-dye, bretelles croisées, maintien moyen, bande élastique large"
- "Jogging slim bleu marine avec poches zippées, chevilles resserrées, logo brodé"
"""

    try:
        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4000,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        }
        
        print(f"Calling Claude to generate {num_to_generate} new outfit descriptions...")
        
        response_bedrock = bedrock_runtime.invoke_model(
            modelId=model_id,
            body=json.dumps(request_body),
            contentType="application/json",
            accept="application/json"
        )
        
        response_body = json.loads(response_bedrock['body'].read())
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
        outfits = result.get('outfits', [])
        
        print(f"Claude generated {len(outfits)} new outfit descriptions")
        return outfits[:num_to_generate]
        
    except Exception as e:
        print(f"Error generating outfit descriptions with Claude: {e}")
        import traceback
        traceback.print_exc()
        return []


def start_ai_outfit_generation(event):
    """
    Start AI outfit generation - POST /api/admin/outfits/ai-generate
    
    Body: {
        "gender": "female" or "male",
        "num_outfits": 10 (number of new outfits to generate, 1-30)
    }
    
    Flow:
    1. Fetch all existing outfits for the gender
    2. Use Claude to generate new unique descriptions
    3. Pick random existing images as style reference
    4. Create job for image generation
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}') or '{}')
        gender = body.get('gender', 'female')
        num_outfits = body.get('num_outfits', 10)
        
        if gender not in ['male', 'female']:
            return response(400, {'error': 'gender must be male or female'})
        
        num_outfits = max(1, min(30, int(num_outfits)))
        
        # Fetch all existing outfits for this gender
        result = outfits_table.scan()
        all_outfits = result.get('Items', [])
        
        gender_outfits = [
            decimal_to_python(o) for o in all_outfits 
            if o.get('gender') == gender
        ]
        
        if len(gender_outfits) < 2:
            return response(400, {'error': f'Need at least 2 existing {gender} outfits as style reference'})
        
        # Get existing descriptions
        existing_descriptions = [o.get('description', '') for o in gender_outfits if o.get('description')]
        
        print(f"Found {len(gender_outfits)} existing {gender} outfits")
        
        # Generate new outfit descriptions with Claude
        new_outfits = generate_new_outfit_descriptions(existing_descriptions, gender, num_outfits)
        
        if not new_outfits:
            return response(500, {'error': 'Failed to generate new outfit descriptions'})
        
        # Pick 2-3 random existing outfits as style reference (just store URLs, download later)
        num_references = min(3, len(gender_outfits))
        reference_outfits = random.sample(gender_outfits, num_references)
        
        # Just store URLs - we'll download images when generating each outfit
        reference_images = []
        for ref_outfit in reference_outfits:
            image_url = ref_outfit.get('image_url', '')
            if image_url:
                reference_images.append({
                    'url': image_url,
                    'description': ref_outfit.get('description', '')
                })
        
        if not reference_images:
            return response(500, {'error': 'No reference images found'})
        
        print(f"Using {len(reference_images)} reference images for style")
        
        # Create job record
        job_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        # Prepare generations list
        generations = []
        for i, outfit in enumerate(new_outfits):
            generations.append({
                'index': i,
                'description': outfit.get('description', ''),
                'type': outfit.get('type', 'sport'),
                'status': 'pending',
                'image_url': None,
                'outfit_id': None,
                'error': None
            })
        
        job_item = {
            'id': job_id,
            'job_id': job_id,
            'job_type': 'ai_outfit_generation',
            'gender': gender,
            'status': 'ready',
            'generations': generations,
            # Only store URLs, not base64 images (DynamoDB 400KB limit)
            'reference_images': reference_images,
            'completed_count': 0,
            'total_count': len(generations),
            'created_at': now,
            'updated_at': now,
            'ttl': int(datetime.now().timestamp()) + 86400  # 24 hour TTL
        }
        
        jobs_table.put_item(Item=job_item)
        
        return response(200, {
            'success': True,
            'job_id': job_id,
            'status': 'ready',
            'gender': gender,
            'total_generations': len(generations),
            'generations': generations,
            'reference_images': [
                {'url': img['url'], 'description': img['description']} 
                for img in reference_images
            ],
            'message': f'Generated {len(generations)} new outfit descriptions. Call /generate endpoint to create images.'
        })
        
    except Exception as e:
        print(f"Error starting AI outfit generation: {e}")
        import traceback
        traceback.print_exc()
        return response(500, {'error': str(e)})


def generate_ai_outfit_image(event):
    """
    Generate a single AI outfit image - POST /api/admin/outfits/ai-generate/generate
    
    Body: {
        "job_id": "uuid",
        "generation_index": 0
    }
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}') or '{}')
        job_id = body.get('job_id')
        generation_index = body.get('generation_index', 0)
        
        if not job_id:
            return response(400, {'error': 'job_id is required'})
        
        # Get job from DynamoDB
        result = jobs_table.get_item(Key={'id': job_id})
        job = result.get('Item')
        
        if not job:
            return response(404, {'error': 'Job not found'})
        
        if job.get('job_type') != 'ai_outfit_generation':
            return response(400, {'error': 'Invalid job type'})
        
        generations = job.get('generations', [])
        gender = job.get('gender', 'female')
        reference_images = job.get('reference_images', [])
        
        if generation_index >= len(generations):
            return response(400, {'error': 'Invalid generation index'})
        
        generation = generations[generation_index]
        
        # Skip if already processed
        if generation.get('status') == 'completed':
            return response(200, {
                'success': True,
                'status': 'already_completed',
                'generation': generation
            })
        
        # Download reference images from S3 (not stored in job to avoid DynamoDB size limit)
        reference_images_base64 = []
        for ref_img in reference_images[:3]:
            image_url = ref_img.get('url', '')
            if image_url:
                try:
                    s3_key = image_url.replace(f"https://{S3_BUCKET}.s3.amazonaws.com/", "")
                    s3_response = s3.get_object(Bucket=S3_BUCKET, Key=s3_key)
                    image_bytes = s3_response['Body'].read()
                    image_base64 = base64.b64encode(image_bytes).decode('utf-8')
                    reference_images_base64.append(image_base64)
                except Exception as e:
                    print(f"Error downloading reference image {image_url}: {e}")
        
        if not reference_images_base64:
            generation['status'] = 'error'
            generation['error'] = 'Failed to download reference images'
            jobs_table.update_item(
                Key={'id': job_id},
                UpdateExpression='SET generations = :g, updated_at = :u',
                ExpressionAttributeValues={
                    ':g': generations,
                    ':u': datetime.now().isoformat()
                }
            )
            return response(200, {
                'success': False,
                'status': 'error',
                'error': generation['error'],
                'generation': generation
            })
        
        description = generation.get('description', '')
        outfit_type = generation.get('type', 'sport')
        
        # Build prompt with reference images
        gender_context = "women's fitness/sport clothing" if gender == 'female' else "men's fitness/sport clothing"
        
        prompt = f"""Based on the style of the reference images provided, create a NEW {gender_context} item:

{description}

CRITICAL INSTRUCTIONS:
1. Match the EXACT SAME photography style as the reference images (flat lay or invisible mannequin, pure white background)
2. Create a COMPLETELY NEW garment design based on the description
3. Keep the same professional e-commerce quality
4. This is {gender_context} - use appropriate fit and style

Requirements:
- Pure white background (#FFFFFF)
- E-commerce quality product photography  
- NO human model visible
- Square format (1:1), centered composition
- Match the lighting and presentation style of the reference images
"""
        
        headers = {"Content-Type": "application/json"}
        
        # Build parts with reference images first
        parts = []
        for ref_base64 in reference_images_base64[:3]:  # Max 3 references
            parts.append({
                "inlineData": {
                    "mimeType": "image/jpeg",
                    "data": ref_base64
                }
            })
        
        # Add the text prompt
        parts.append({"text": prompt})
        
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": {
                    "aspectRatio": "1:1",
                    "imageSize": "1K"
                }
            }
        }
        
        api_url = f"{GEMINI_API_URL}?key={NANO_BANANA_API_KEY}"
        
        print(f"Generating AI outfit {generation_index}: {description}")
        
        resp = requests.post(
            api_url,
            headers=headers,
            json=payload,
            timeout=120
        )
        
        if resp.status_code != 200:
            print(f"Gemini API error: {resp.status_code} - {resp.text}")
            generation['status'] = 'error'
            generation['error'] = f"Image generation failed: {resp.status_code}"
            jobs_table.update_item(
                Key={'id': job_id},
                UpdateExpression='SET generations = :g, updated_at = :u',
                ExpressionAttributeValues={
                    ':g': generations,
                    ':u': datetime.now().isoformat()
                }
            )
            return response(200, {
                'success': False,
                'status': 'error',
                'error': generation['error'],
                'generation': generation
            })
        
        result = resp.json()
        
        # Extract image from response
        candidates = result.get('candidates', [])
        image_base64_result = None
        
        if candidates:
            content = candidates[0].get('content', {})
            parts = content.get('parts', [])
            for part in parts:
                if 'inlineData' in part:
                    image_base64_result = part['inlineData'].get('data')
                    break
        
        if not image_base64_result:
            generation['status'] = 'error'
            generation['error'] = 'No image in API response'
            jobs_table.update_item(
                Key={'id': job_id},
                UpdateExpression='SET generations = :g, updated_at = :u',
                ExpressionAttributeValues={
                    ':g': generations,
                    ':u': datetime.now().isoformat()
                }
            )
            return response(200, {
                'success': False,
                'status': 'error',
                'error': generation['error'],
                'generation': generation
            })
        
        # Create new outfit in DynamoDB
        new_outfit_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        # Save image to S3
        s3_key = f"outfits/{new_outfit_id}/main.png"
        image_data = base64.b64decode(image_base64_result)
        new_image_url = upload_to_s3(s3_key, image_data, 'image/png', cache_days=365)
        
        # Create new outfit record
        new_outfit = {
            'id': new_outfit_id,
            'description': description,
            'type': outfit_type,
            'gender': gender,
            'image_url': new_image_url,
            'source': 'ai_generated',
            'created_at': now,
            'updated_at': now
        }
        
        outfits_table.put_item(Item=new_outfit)
        
        # Update generation status
        generation['status'] = 'completed'
        generation['image_url'] = new_image_url
        generation['outfit_id'] = new_outfit_id
        
        # Update job
        completed_count = sum(1 for g in generations if g.get('status') == 'completed')
        job_status = 'completed' if completed_count >= len(generations) else 'in_progress'
        
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET generations = :g, completed_count = :cc, #s = :st, updated_at = :u',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={
                ':g': generations,
                ':cc': completed_count,
                ':st': job_status,
                ':u': datetime.now().isoformat()
            }
        )
        
        return response(200, {
            'success': True,
            'status': 'completed',
            'generation': generation,
            'new_outfit': decimal_to_python(new_outfit),
            'completed_count': completed_count,
            'total_count': len(generations)
        })
        
    except Exception as e:
        print(f"Error generating AI outfit: {e}")
        import traceback
        traceback.print_exc()
        return response(500, {'error': str(e)})


def get_ai_generation_status(event):
    """
    Get status of an AI generation job - GET /api/admin/outfits/ai-generate/status/{job_id}
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        path_params = event.get('pathParameters', {}) or {}
        job_id = path_params.get('job_id')
        
        if not job_id:
            return response(400, {'error': 'job_id is required'})
        
        result = jobs_table.get_item(Key={'id': job_id})
        job = result.get('Item')
        
        if not job:
            return response(404, {'error': 'Job not found'})
        
        # Don't return the large base64 images in status response
        job_response = decimal_to_python(job)
        if 'reference_images_base64' in job_response:
            del job_response['reference_images_base64']
        
        return response(200, {
            'success': True,
            'job': job_response
        })
        
    except Exception as e:
        print(f"Error getting AI generation status: {e}")
        return response(500, {'error': str(e)})
