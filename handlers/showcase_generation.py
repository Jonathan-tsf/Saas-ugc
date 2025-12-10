"""
Showcase photos generation handlers
Generates 15 showcase photos for ambassadors using:
1. AWS Bedrock Claude Sonnet 4 for scene descriptions
2. Gemini 3 Pro Image (Nano Banana Pro) for image generation
"""
import json
import uuid
import base64
import random
import requests
import boto3
from datetime import datetime

from config import (
    response, decimal_to_python, verify_admin,
    dynamodb, s3, S3_BUCKET, NANO_BANANA_API_KEY
)

# DynamoDB tables
ambassadors_table = dynamodb.Table('ambassadors')
jobs_table = dynamodb.Table('nano_banana_jobs')

# AWS Bedrock client for Claude
bedrock_runtime = boto3.client('bedrock-runtime', region_name='us-east-1')

# Lambda client for async invocation
lambda_client = boto3.client('lambda')
LAMBDA_FUNCTION_NAME = 'saas-ugc'

# Claude Sonnet 4 model ID (using Claude 3.5 Sonnet v2 as fallback)
CLAUDE_MODEL_ID = "anthropic.claude-sonnet-4-20250514-v1:0"
CLAUDE_FALLBACK_MODEL_ID = "anthropic.claude-3-5-sonnet-20241022-v2:0"

# Gemini 3 Pro Image Preview (Nano Banana Pro)
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-image-preview:generateContent"

# Number of showcase photos to generate
NUM_SHOWCASE_PHOTOS = 15

# Few-shot learning examples for scene descriptions
FEW_SHOT_EXAMPLES = """
A. Face cam "TikTok talk", simple et exploitable:
- Assis sur une chaise, face caméra, mains posées sur les cuisses, buste légèrement penché vers l'avant, léger sourire, fond mur blanc ou chambre normale.
- Assis au bord d'un canapé, regard direct caméra, une main qui bouge légèrement comme s'il expliquait quelque chose, expression calme et sincère.
- Assis en tailleur sur le canapé, dos droit, mains jointes devant lui, regard sérieux mais détendu vers la caméra, fond salon normal.
- Debout, face caméra, pieds largeur épaules, mains liées devant le bassin, expression neutre, fond mur simple ou porte.
- Debout, face caméra, mains derrière le dos, menton légèrement relevé, petit sourire, lumière naturelle venant d'un côté.

B. Scènes avec ordinateur / bureau:
- Assis à un bureau, laptop ouvert, il regarde la caméra au-dessus de l'écran, mains posées sur le clavier comme s'il s'apprêtait à parler de ce qu'il fait.
- Assis au bureau, une main sur la souris, l'autre main légèrement levée comme s'il expliquait quelque chose, regard caméra, expression sérieuse.
- Assis au bureau, penché vers la caméra, coudes sur la table, mains jointes devant sa bouche, regard concentré vers l'objectif.

C. Scènes cuisine / manger / boire:
- Debout dans une cuisine, appuyé légèrement contre le plan de travail, regarde la caméra, un bol ou une assiette devant lui, expression calme.
- Assis à une table, fourchette dans une main, il regarde la caméra, la fourchette au-dessus de l'assiette comme s'il s'apprêtait à parler avant de manger.
- Assis, verre ou shaker à la main, posé sur la table, il tient le verre à mi-hauteur, regarde la caméra avec un air tranquille.

D. Debout / bras croisés / positions simples:
- Debout, bras croisés, face caméra, expression neutre / confiante, fond mur simple.
- Debout, une main dans la poche, l'autre bras le long du corps, il regarde la caméra calmement, décor salon / couloir.
- Debout, appuyé contre un mur, une épaule contre le mur, bras le long du corps, regard vers la caméra, expression "cool mais neutre".

E. Mode "podcast / interview" sur une chaise:
- Assis sur une chaise simple, légèrement tourné de côté, mais il tourne la tête vers la caméra, une main qui accompagne légèrement la parole.
- Assis sur une chaise type bar, pieds sur un repose-pied, dos droit, mains sur les cuisses, regarde la caméra avec un air concentré.

F. Téléphone / scroll (sans selfie, toujours regard caméra):
- Assis sur un canapé, téléphone dans une main, il tient le téléphone près de lui mais regarde la caméra, comme s'il racontait ce qu'il regarde.
- Debout, téléphone dans une main le long du corps, l'autre main esquisse un petit geste explicatif, regarde la caméra, expression neutre.

G. Quelques scènes "lifestyle + sérieux" bien TikTok-compatibles:
- Assis dans le salon, dos légèrement arrondi, coudes sur les cuisses, mains jointes, il regarde la caméra comme s'il commençait une confession.
- Debout dans la cuisine, bras croisés, appuyé sur le plan de travail, regard sérieux vers la caméra, ambiance "je parle argent / nutrition / business".
- Assis à un bureau avec un carnet ouvert, stylo dans la main, il regarde la caméra avec un air concentré.
"""


def get_available_outfit_categories(ambassador):
    """Get outfit categories where ambassador has validated photos"""
    ambassador_outfits = ambassador.get('ambassador_outfits', [])
    
    available_categories = set()
    for outfit in ambassador_outfits:
        if outfit.get('status') == 'selected' and outfit.get('selected_image'):
            outfit_type = outfit.get('outfit_type', 'casual')
            available_categories.add(outfit_type)
    
    return list(available_categories)


def get_outfit_image_for_category(ambassador, category):
    """Get a random validated outfit image for a specific category"""
    ambassador_outfits = ambassador.get('ambassador_outfits', [])
    
    matching_outfits = [
        outfit for outfit in ambassador_outfits
        if outfit.get('outfit_type') == category 
        and outfit.get('status') == 'selected' 
        and outfit.get('selected_image')
    ]
    
    if matching_outfits:
        return random.choice(matching_outfits)['selected_image']
    return None


def generate_scene_descriptions_with_claude(available_categories, ambassador_gender):
    """Use AWS Bedrock Claude to generate scene descriptions"""
    
    categories_str = ", ".join(available_categories)
    gender_pronoun = "il" if ambassador_gender == "male" else "elle"
    gender_article = "un homme" if ambassador_gender == "male" else "une femme"
    
    system_prompt = f"""Tu es un expert en création de contenu pour TikTok et réseaux sociaux. 
Tu dois générer exactement 15 descriptions de scènes pour des photos d'ambassadeurs UGC.

RÈGLES CRITIQUES:
1. Chaque scène doit TOUJOURS avoir un regard caméra
2. PAS de selfie (la caméra filme, pas de téléphone tenu pour se prendre en photo)
3. PAS d'expressions exagérées (pas de surprise, colère, etc.)
4. Expressions autorisées: neutre, léger sourire, concentré, calme, sérieux mais détendu
5. La tenue doit être cohérente avec le décor (pas de sport dans une bibliothèque)
6. Tu ne peux utiliser QUE ces catégories de tenues: {categories_str}
7. Répartis équitablement les catégories sur les 15 photos

La personne est {gender_article}.

IMPORTANT: Tu dois UNIQUEMENT répondre avec un JSON valide, sans aucun texte avant ou après."""

    user_prompt = f"""Génère 15 descriptions de scènes pour un ambassadeur UGC.

Catégories de tenues disponibles: {categories_str}

Exemples de scènes inspirantes (few-shot learning):
{FEW_SHOT_EXAMPLES}

Réponds UNIQUEMENT avec un JSON valide au format suivant (sans markdown, sans ```json, juste le JSON pur):
{{
    "picture_1": {{
        "position": "Description détaillée de la scène, pose, décor, expression. {gender_pronoun.capitalize()} regarde la caméra...",
        "outfit_category": "casual"
    }},
    "picture_2": {{
        "position": "...",
        "outfit_category": "fitness"
    }},
    ...jusqu'à picture_15
}}

Assure-toi que:
- Chaque description mentionne explicitement "regard caméra" ou "{gender_pronoun} regarde la caméra"
- La catégorie de tenue est cohérente avec le décor de la scène
- Les scènes sont variées (bureau, cuisine, salon, debout, assis, etc.)
- Ne rien changer à la tenue de la personne, juste la positionner dans le décor"""

    try:
        # Try Claude Sonnet 4 first, fallback to 3.5 Sonnet v2
        model_id = CLAUDE_MODEL_ID
        
        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": user_prompt
                }
            ]
        }
        
        try:
            api_response = bedrock_runtime.invoke_model(
                modelId=model_id,
                body=json.dumps(request_body)
            )
        except Exception as e:
            print(f"Claude Sonnet 4 failed, trying fallback: {e}")
            model_id = CLAUDE_FALLBACK_MODEL_ID
            api_response = bedrock_runtime.invoke_model(
                modelId=model_id,
                body=json.dumps(request_body)
            )
        
        response_body = json.loads(api_response['body'].read())
        
        # Extract text content from Claude response
        content = response_body.get('content', [])
        text_content = ""
        for block in content:
            if block.get('type') == 'text':
                text_content = block.get('text', '')
                break
        
        # Parse JSON from response
        # Clean up any potential markdown formatting
        text_content = text_content.strip()
        if text_content.startswith('```json'):
            text_content = text_content[7:]
        if text_content.startswith('```'):
            text_content = text_content[3:]
        if text_content.endswith('```'):
            text_content = text_content[:-3]
        text_content = text_content.strip()
        
        scenes = json.loads(text_content)
        return scenes
        
    except Exception as e:
        print(f"Error calling Claude: {e}")
        # Fallback to default scenes if Claude fails
        return generate_fallback_scenes(available_categories, ambassador_gender)


def generate_fallback_scenes(available_categories, ambassador_gender):
    """Generate fallback scenes if Claude fails"""
    pronoun = "il" if ambassador_gender == "male" else "elle"
    
    fallback_scenes = [
        ("Assis sur une chaise face caméra, mains posées sur les cuisses, buste légèrement penché vers l'avant, léger sourire, fond mur blanc.", "casual"),
        ("Debout face caméra, bras croisés, expression neutre confiante, fond mur simple.", "elegant"),
        ("Assis à un bureau, laptop ouvert, {} regarde la caméra au-dessus de l'écran, expression concentrée.".format(pronoun), "casual"),
        ("Debout dans une cuisine, appuyé contre le plan de travail, {} regarde la caméra, expression calme.".format(pronoun), "casual"),
        ("Assis au bord d'un canapé, regard direct caméra, expression calme et sincère.", "casual"),
        ("Debout, une main dans la poche, l'autre bras le long du corps, {} regarde la caméra calmement.".format(pronoun), "streetwear"),
        ("Assis sur une chaise type bar, dos droit, mains sur les cuisses, {} regarde la caméra avec un air concentré.".format(pronoun), "elegant"),
        ("Debout face caméra, mains derrière le dos, menton légèrement relevé, petit sourire.", "elegant"),
        ("Assis en tailleur sur le canapé, dos droit, mains jointes, regard sérieux mais détendu vers la caméra.", "casual"),
        ("Assis au bureau, coudes sur la table, mains jointes devant la bouche, regard concentré vers la caméra.", "casual"),
        ("Debout appuyé contre un mur, une épaule contre le mur, regard vers la caméra, expression cool mais neutre.", "streetwear"),
        ("Assis dans le salon, coudes sur les cuisses, mains jointes, {} regarde la caméra.".format(pronoun), "casual"),
        ("Debout dans la cuisine, bras croisés, appuyé sur le plan de travail, regard sérieux vers la caméra.", "casual"),
        ("Assis à un bureau avec un carnet ouvert, stylo dans la main, {} regarde la caméra avec un air concentré.".format(pronoun), "casual"),
        ("Debout près d'une fenêtre, lumière sur le visage, corps légèrement de côté, regard dans la caméra, expression sérieuse mais calme.", "elegant"),
    ]
    
    scenes = {}
    for i, (position, default_category) in enumerate(fallback_scenes, 1):
        # Use default category if available, otherwise pick random from available
        category = default_category if default_category in available_categories else random.choice(available_categories)
        scenes[f"picture_{i}"] = {
            "position": position,
            "outfit_category": category
        }
    
    return scenes


def get_image_from_s3(image_url):
    """Download image from S3 and return base64"""
    try:
        if 's3.amazonaws.com' in image_url:
            key = image_url.split('.com/')[1]
        elif 'amazonaws.com' in image_url:
            parts = image_url.split('amazonaws.com/')
            key = parts[1] if len(parts) > 1 else image_url.split('/')[-1]
        else:
            key = image_url.split('/')[-1]
        
        response_obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
        image_data = response_obj['Body'].read()
        return base64.b64encode(image_data).decode('utf-8')
    except Exception as e:
        print(f"Error getting image from S3: {e}")
        return None


def generate_showcase_image(outfit_image_base64, scene_description):
    """Generate a showcase image using Gemini 3 Pro Image (Nano Banana Pro)"""
    
    prompt = f"""Using the provided image of a person wearing an outfit, create a new photo of this EXACT same person in the following scene:

{scene_description}

CRITICAL REQUIREMENTS:
- The person's face, body, skin tone, and ALL physical features must remain COMPLETELY IDENTICAL
- The outfit they are wearing must remain EXACTLY the same as in the reference image
- DO NOT change anything about the person or their clothing
- Only change the BACKGROUND, POSE, and SETTING as described
- The person MUST be looking directly at the camera
- Use natural, professional lighting
- High quality, photo-realistic result

Generate a professional photo in portrait orientation (9:16 aspect ratio)."""

    headers = {"Content-Type": "application/json"}
    
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": outfit_image_base64
                    }
                }
            ]
        }],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {
                "aspectRatio": "9:16",
                "imageSize": "2K"
            }
        }
    }
    
    try:
        api_response = requests.post(
            f"{GEMINI_API_URL}?key={NANO_BANANA_API_KEY}",
            headers=headers,
            json=payload,
            timeout=180
        )
        
        if api_response.status_code == 200:
            result = api_response.json()
            
            if 'candidates' in result and len(result['candidates']) > 0:
                candidate = result['candidates'][0]
                if 'content' in candidate and 'parts' in candidate['content']:
                    for part in candidate['content']['parts']:
                        if 'inlineData' in part:
                            return part['inlineData']['data']
        else:
            print(f"Gemini API error: {api_response.status_code} - {api_response.text}")
            
    except Exception as e:
        print(f"Error generating showcase image: {e}")
    
    return None


def save_showcase_image_to_s3(image_base64, ambassador_id, index):
    """Save generated showcase image to S3 and return URL"""
    try:
        image_data = base64.b64decode(image_base64)
        key = f"showcase_photos/{ambassador_id}/showcase_{index}_{uuid.uuid4().hex[:8]}.png"
        
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=image_data,
            ContentType='image/png'
        )
        
        return f"https://{S3_BUCKET}.s3.amazonaws.com/{key}"
    except Exception as e:
        print(f"Error saving showcase image to S3: {e}")
        return None


def start_showcase_generation(event):
    """Start generating showcase photos for an ambassador - POST /api/admin/ambassadors/showcase/generate"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    ambassador_id = body.get('ambassador_id')
    if not ambassador_id:
        return response(400, {'error': 'ambassador_id required'})
    
    # Get ambassador
    try:
        result = ambassadors_table.get_item(Key={'id': ambassador_id})
        ambassador = result.get('Item')
        if not ambassador:
            return response(404, {'error': 'Ambassador not found'})
    except Exception as e:
        return response(500, {'error': f'Failed to get ambassador: {str(e)}'})
    
    # Check if ambassador has validated outfit photos
    available_categories = get_available_outfit_categories(ambassador)
    if not available_categories:
        return response(400, {'error': 'Ambassador has no validated outfit photos. Please generate and validate outfit photos first.'})
    
    # Create job
    job_id = str(uuid.uuid4())
    job = {
        'id': job_id,
        'type': 'showcase_generation',
        'ambassador_id': ambassador_id,
        'status': 'processing',
        'total_photos': NUM_SHOWCASE_PHOTOS,
        'completed_photos': 0,
        'current_step': 'generating_scenes',
        'scenes': [],
        'results': [],
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat()
    }
    
    jobs_table.put_item(Item=job)
    
    # Clear previous showcase photos
    try:
        ambassadors_table.update_item(
            Key={'id': ambassador_id},
            UpdateExpression='SET showcase_photos = :empty, updated_at = :updated',
            ExpressionAttributeValues={
                ':empty': [],
                ':updated': datetime.now().isoformat()
            }
        )
    except Exception as e:
        print(f"Error clearing showcase photos: {e}")
    
    # Invoke Lambda async for background processing
    try:
        lambda_client.invoke(
            FunctionName=LAMBDA_FUNCTION_NAME,
            InvocationType='Event',
            Payload=json.dumps({
                'action': 'generate_showcase_photos',
                'job_id': job_id,
                'ambassador_id': ambassador_id,
                'available_categories': available_categories,
                'ambassador_gender': ambassador.get('gender', 'male')
            })
        )
    except Exception as e:
        print(f"Error invoking Lambda async: {e}")
        job['status'] = 'failed'
        job['error'] = str(e)
        jobs_table.put_item(Item=job)
        return response(500, {'error': f'Failed to start generation: {str(e)}'})
    
    return response(200, {
        'success': True,
        'job_id': job_id,
        'message': f'Started generating {NUM_SHOWCASE_PHOTOS} showcase photos'
    })


def generate_showcase_photos_async(job_id, ambassador_id, available_categories, ambassador_gender):
    """Background async handler to generate showcase photos"""
    print(f"Starting async showcase generation for job {job_id}, ambassador {ambassador_id}")
    
    # Get ambassador for outfit images
    try:
        result = ambassadors_table.get_item(Key={'id': ambassador_id})
        ambassador = result.get('Item')
        if not ambassador:
            raise Exception("Ambassador not found")
    except Exception as e:
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET #s = :status, #e = :error, updated_at = :updated',
            ExpressionAttributeNames={'#s': 'status', '#e': 'error'},
            ExpressionAttributeValues={
                ':status': 'failed',
                ':error': f'Failed to get ambassador: {str(e)}',
                ':updated': datetime.now().isoformat()
            }
        )
        return
    
    # Step 1: Generate scene descriptions with Claude
    jobs_table.update_item(
        Key={'id': job_id},
        UpdateExpression='SET current_step = :step, updated_at = :updated',
        ExpressionAttributeValues={
            ':step': 'generating_scenes_with_claude',
            ':updated': datetime.now().isoformat()
        }
    )
    
    scenes = generate_scene_descriptions_with_claude(available_categories, ambassador_gender)
    
    # Save scenes to job
    scenes_list = [
        {
            'key': key,
            'position': scene['position'],
            'outfit_category': scene['outfit_category']
        }
        for key, scene in scenes.items()
    ]
    
    jobs_table.update_item(
        Key={'id': job_id},
        UpdateExpression='SET scenes = :scenes, current_step = :step, updated_at = :updated',
        ExpressionAttributeValues={
            ':scenes': scenes_list,
            ':step': 'generating_images',
            ':updated': datetime.now().isoformat()
        }
    )
    
    # Step 2: Generate images for each scene
    showcase_photos = []
    
    for i, (scene_key, scene_data) in enumerate(scenes.items(), 1):
        position = scene_data['position']
        outfit_category = scene_data['outfit_category']
        
        # Get outfit image for this category
        outfit_image_url = get_outfit_image_for_category(ambassador, outfit_category)
        if not outfit_image_url:
            print(f"No outfit image for category {outfit_category}, skipping scene {i}")
            continue
        
        # Get base64 of outfit image
        outfit_image_base64 = get_image_from_s3(outfit_image_url)
        if not outfit_image_base64:
            print(f"Failed to get outfit image, skipping scene {i}")
            continue
        
        # Update job progress
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET current_photo = :photo, updated_at = :updated',
            ExpressionAttributeValues={
                ':photo': i,
                ':updated': datetime.now().isoformat()
            }
        )
        
        # Generate 2 variations
        generated_urls = []
        for variation in range(2):
            image_base64 = generate_showcase_image(outfit_image_base64, position)
            if image_base64:
                url = save_showcase_image_to_s3(image_base64, ambassador_id, f"{i}_{variation}")
                if url:
                    generated_urls.append(url)
        
        # Create showcase photo entry
        photo_entry = {
            'id': str(uuid.uuid4()),
            'scene_index': i,
            'scene_description': position,
            'outfit_category': outfit_category,
            'generated_images': generated_urls,
            'selected_image': None,
            'status': 'generated' if generated_urls else 'failed',
            'created_at': datetime.now().isoformat()
        }
        showcase_photos.append(photo_entry)
        
        # Update job results
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET completed_photos = :completed, results = :results, updated_at = :updated',
            ExpressionAttributeValues={
                ':completed': i,
                ':results': showcase_photos,
                ':updated': datetime.now().isoformat()
            }
        )
    
    # Save to ambassador
    try:
        ambassadors_table.update_item(
            Key={'id': ambassador_id},
            UpdateExpression='SET showcase_photos = :photos, updated_at = :updated',
            ExpressionAttributeValues={
                ':photos': showcase_photos,
                ':updated': datetime.now().isoformat()
            }
        )
    except Exception as e:
        print(f"Error updating ambassador showcase photos: {e}")
    
    # Mark job as completed
    jobs_table.update_item(
        Key={'id': job_id},
        UpdateExpression='SET #s = :status, current_step = :step, updated_at = :updated',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={
            ':status': 'completed',
            ':step': 'done',
            ':updated': datetime.now().isoformat()
        }
    )
    
    print(f"Completed async showcase generation for job {job_id}")


def get_showcase_generation_status(event):
    """Get showcase generation job status - GET /api/admin/ambassadors/showcase/status"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    params = event.get('queryStringParameters', {}) or {}
    job_id = params.get('job_id')
    
    if not job_id:
        return response(400, {'error': 'job_id required'})
    
    try:
        result = jobs_table.get_item(Key={'id': job_id})
        job = result.get('Item')
        
        if not job:
            return response(404, {'error': 'Job not found'})
        
        return response(200, {
            'success': True,
            'job': decimal_to_python(job)
        })
    except Exception as e:
        return response(500, {'error': f'Failed to get job: {str(e)}'})


def select_showcase_photo(event):
    """Select the best image for a showcase photo - POST /api/admin/ambassadors/showcase/select"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    ambassador_id = body.get('ambassador_id')
    photo_id = body.get('photo_id')
    selected_image = body.get('selected_image')
    
    if not all([ambassador_id, photo_id, selected_image]):
        return response(400, {'error': 'ambassador_id, photo_id, and selected_image required'})
    
    try:
        result = ambassadors_table.get_item(Key={'id': ambassador_id})
        ambassador = result.get('Item')
        
        if not ambassador:
            return response(404, {'error': 'Ambassador not found'})
        
        # Update the specific showcase photo
        showcase_photos = ambassador.get('showcase_photos', [])
        updated = False
        
        for photo in showcase_photos:
            if photo.get('id') == photo_id:
                photo['selected_image'] = selected_image
                photo['status'] = 'selected'
                updated = True
                break
        
        if not updated:
            return response(404, {'error': 'Showcase photo not found'})
        
        # Save back to DynamoDB
        ambassadors_table.update_item(
            Key={'id': ambassador_id},
            UpdateExpression='SET showcase_photos = :photos, updated_at = :updated',
            ExpressionAttributeValues={
                ':photos': showcase_photos,
                ':updated': datetime.now().isoformat()
            }
        )
        
        # Get updated ambassador
        result = ambassadors_table.get_item(Key={'id': ambassador_id})
        
        return response(200, {
            'success': True,
            'ambassador': decimal_to_python(result.get('Item'))
        })
        
    except Exception as e:
        print(f"Error selecting showcase photo: {e}")
        return response(500, {'error': f'Failed to select photo: {str(e)}'})
