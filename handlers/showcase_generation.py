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
import urllib.request
import urllib.error
import boto3
from datetime import datetime

from config import (
    response, decimal_to_python, verify_admin,
    dynamodb, s3, S3_BUCKET, NANO_BANANA_API_KEY, REPLICATE_API_KEY
)

# DynamoDB tables
ambassadors_table = dynamodb.Table('ambassadors')
jobs_table = dynamodb.Table('nano_banana_jobs')

# AWS Bedrock client for Claude
bedrock_runtime = boto3.client('bedrock-runtime', region_name='us-east-1')

# Lambda client for async invocation
lambda_client = boto3.client('lambda')
LAMBDA_FUNCTION_NAME = 'saas-ugc'

# Claude Sonnet 4 model ID via inference profile
CLAUDE_MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"

# Gemini 3 Pro Image Preview (Nano Banana Pro)
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-image-preview:generateContent"

# Replicate API URL for fallback
REPLICATE_API_URL = "https://api.replicate.com/v1/models/google/nano-banana-pro/predictions"

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
        
        api_response = bedrock_runtime.invoke_model(
            modelId=CLAUDE_MODEL_ID,
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


def start_replicate_prediction(outfit_image_base64, scene_description):
    """
    Start a Replicate prediction and return the prediction ID immediately.
    Does NOT wait for result - caller must poll for completion.
    Returns: prediction_id or None on error
    """
    if not REPLICATE_API_KEY:
        print("REPLICATE_API_KEY not configured, cannot use Replicate")
        return None
    
    prompt = f"""Using the provided image of a person wearing an outfit, create a new photo of this EXACT same person in the following scene:

{scene_description}

CRITICAL REQUIREMENTS:
- The person's face, body, skin tone, and ALL physical features must remain COMPLETELY IDENTICAL
- The outfit they are wearing must remain EXACTLY the same as in the reference image
- DO NOT change anything about the person or their clothing
- Only change the BACKGROUND, POSE, and SETTING as described
- The person MUST be looking directly at the camera
- Use natural, professional lighting
- High quality, photo-realistic result"""

    headers = {
        "Authorization": f"Bearer {REPLICATE_API_KEY}",
        "Content-Type": "application/json"
        # NO "Prefer: wait" - we want async response
    }
    
    # Build data URI for the image
    image_data_uri = f"data:image/jpeg;base64,{outfit_image_base64}"
    
    payload = {
        "input": {
            "prompt": prompt,
            "resolution": "2K",
            "image_input": [image_data_uri],
            "aspect_ratio": "9:16",
            "output_format": "png",
            "safety_filter_level": "block_only_high"
        }
    }
    
    try:
        print(f"Starting Replicate prediction for scene: {scene_description[:50]}...")
        
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(REPLICATE_API_URL, data=data, headers=headers, method='POST')
        
        with urllib.request.urlopen(req, timeout=30) as api_response:
            result = json.loads(api_response.read().decode('utf-8'))
            
            prediction_id = result.get('id')
            status = result.get('status')
            print(f"Replicate prediction started: {prediction_id}, status: {status}")
            
            return prediction_id
            
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8') if e.fp else 'No error body'
        print(f"Replicate API HTTP error: {e.code} - {error_body[:500]}")
    except Exception as e:
        print(f"Error starting Replicate prediction: {e}")
    
    return None


def check_replicate_prediction(prediction_id):
    """
    Check the status of a Replicate prediction.
    Returns: { status: 'starting'|'processing'|'succeeded'|'failed', output: url_or_none, error: msg_or_none }
    """
    if not prediction_id or not REPLICATE_API_KEY:
        return {'status': 'failed', 'error': 'Invalid prediction_id or missing API key'}
    
    try:
        get_url = f"https://api.replicate.com/v1/predictions/{prediction_id}"
        headers = {"Authorization": f"Bearer {REPLICATE_API_KEY}"}
        
        req = urllib.request.Request(get_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode('utf-8'))
            
            status = result.get('status', 'unknown')
            output = result.get('output')
            error = result.get('error')
            
            print(f"Replicate prediction {prediction_id}: status={status}")
            
            return {
                'status': status,
                'output': output,
                'error': error
            }
            
    except Exception as e:
        print(f"Error checking Replicate prediction: {e}")
        return {'status': 'error', 'error': str(e)}


def download_image_as_base64(url):
    """Download an image from URL and return as base64"""
    try:
        print(f"Downloading image from: {url[:80]}...")
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=60) as response:
            image_data = response.read()
            return base64.b64encode(image_data).decode('utf-8')
    except Exception as e:
        print(f"Error downloading image: {e}")
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
    
    # Format correct selon la doc Gemini 3 Pro Image:
    # https://ai.google.dev/gemini-api/docs/image-generation
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {
                    "inlineData": {
                        "mimeType": "image/jpeg",
                        "data": outfit_image_base64
                    }
                }
            ]
        }],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"]
        }
    }
    
    try:
        url = f"{GEMINI_API_URL}?key={NANO_BANANA_API_KEY}"
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=headers, method='POST')
        
        print(f"Calling Gemini API for scene: {scene_description[:50]}...")
        
        with urllib.request.urlopen(req, timeout=180) as api_response:
            result = json.loads(api_response.read().decode('utf-8'))
            
            print(f"Gemini response keys: {result.keys()}")
            
            if 'candidates' in result and len(result['candidates']) > 0:
                candidate = result['candidates'][0]
                if 'content' in candidate and 'parts' in candidate['content']:
                    for part in candidate['content']['parts']:
                        if 'inlineData' in part:
                            print("Found inlineData in response - image generated successfully")
                            return part['inlineData']['data']
                        elif 'inline_data' in part:
                            print("Found inline_data in response - image generated successfully")
                            return part['inline_data']['data']
            
            print(f"No image in Gemini response: {json.dumps(result)[:500]}")
            
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8') if e.fp else 'No error body'
        print(f"Gemini API HTTP error: {e.code} - {error_body[:1000]}")
        
        # Check if quota exceeded (429) - raise specific exception
        if e.code == 429:
            raise QuotaExceededException("Gemini API quota exceeded")
        
        # For other HTTP errors, return None (Replicate needs async handling)
        return None
        
    except QuotaExceededException:
        # Re-raise quota exception to be handled by caller
        raise
    except Exception as e:
        print(f"Error generating showcase image: {e}")
        import traceback
        traceback.print_exc()
    
    return None


class QuotaExceededException(Exception):
    """Raised when API quota is exceeded - triggers Replicate async fallback"""
    pass


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
    """
    Start showcase generation - generates 15 scene descriptions with Claude
    POST /api/admin/ambassadors/showcase/generate
    
    Returns immediately with scenes - frontend then calls generate_scene for each scene
    """
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
    
    ambassador_gender = ambassador.get('gender', 'male')
    
    # Step 1: Generate scene descriptions with Claude (synchronous - takes ~10-15s)
    print(f"Generating scenes for ambassador {ambassador_id}...")
    try:
        scenes = generate_scene_descriptions_with_claude(available_categories, ambassador_gender)
        print(f"Claude generated {len(scenes)} scenes")
    except Exception as e:
        print(f"ERROR calling Claude: {e}")
        import traceback
        traceback.print_exc()
        scenes = generate_fallback_scenes(available_categories, ambassador_gender)
        print(f"Using fallback scenes: {len(scenes)} scenes")
    
    # Convert scenes to list format
    scenes_list = []
    for i, (key, scene) in enumerate(scenes.items(), 1):
        scene_id = str(uuid.uuid4())
        scenes_list.append({
            'scene_id': scene_id,
            'scene_number': i,
            'scene_description': scene['position'],
            'outfit_category': scene['outfit_category'],
            'generated_images': [],
            'selected_image': None,
            'status': 'pending'
        })
    
    # Create job with scenes
    job_id = str(uuid.uuid4())
    job = {
        'id': job_id,
        'job_id': job_id,
        'type': 'showcase_generation',
        'ambassador_id': ambassador_id,
        'status': 'scenes_ready',  # Scenes are ready, images not yet generated
        'total_scenes': NUM_SHOWCASE_PHOTOS,
        'completed_scenes': 0,
        'current_scene_number': 0,
        'scenes': scenes_list,
        'results': scenes_list,  # Frontend uses results
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat()
    }
    
    jobs_table.put_item(Item=job)
    
    # Clear previous showcase photos and save new scenes
    try:
        ambassadors_table.update_item(
            Key={'id': ambassador_id},
            UpdateExpression='SET showcase_photos = :photos, updated_at = :updated',
            ExpressionAttributeValues={
                ':photos': scenes_list,
                ':updated': datetime.now().isoformat()
            }
        )
    except Exception as e:
        print(f"Error saving showcase photos: {e}")
    
    # Return job with scenes - frontend will call generate_scene for each
    return response(200, {
        'success': True,
        'job_id': job_id,
        'status': 'scenes_ready',
        'total_scenes': NUM_SHOWCASE_PHOTOS,
        'scenes': scenes_list,
        'message': f'Generated {len(scenes_list)} scene descriptions. Call /showcase/scene to generate images for each scene.'
    })


def generate_scene(event):
    """
    Generate 2 images for a single scene
    POST /api/admin/ambassadors/showcase/scene
    
    Body: { ambassador_id, scene_id, job_id }
    Returns: { scene with generated_images }
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    ambassador_id = body.get('ambassador_id')
    scene_id = body.get('scene_id')
    job_id = body.get('job_id')
    
    if not all([ambassador_id, scene_id]):
        return response(400, {'error': 'ambassador_id and scene_id required'})
    
    # Get ambassador
    try:
        result = ambassadors_table.get_item(Key={'id': ambassador_id})
        ambassador = result.get('Item')
        if not ambassador:
            return response(404, {'error': 'Ambassador not found'})
    except Exception as e:
        return response(500, {'error': f'Failed to get ambassador: {str(e)}'})
    
    # Find the scene in showcase_photos
    showcase_photos = ambassador.get('showcase_photos', [])
    scene = None
    scene_index = -1
    
    for i, photo in enumerate(showcase_photos):
        if photo.get('scene_id') == scene_id:
            scene = photo
            scene_index = i
            break
    
    if not scene:
        return response(404, {'error': 'Scene not found'})
    
    # Skip if already generated
    if scene.get('generated_images') and len(scene.get('generated_images', [])) > 0:
        return response(200, {
            'success': True,
            'scene': decimal_to_python(scene),
            'message': 'Scene already has generated images'
        })
    
    scene_number = scene.get('scene_number', scene_index + 1)
    scene_description = scene.get('scene_description', '')
    outfit_category = scene.get('outfit_category', 'casual')
    
    print(f"Generating images for scene {scene_number}: {scene_description[:50]}...")
    
    # Get outfit image for this category
    outfit_image_url = get_outfit_image_for_category(ambassador, outfit_category)
    if not outfit_image_url:
        # Try any available category
        available_categories = get_available_outfit_categories(ambassador)
        if available_categories:
            outfit_image_url = get_outfit_image_for_category(ambassador, available_categories[0])
    
    if not outfit_image_url:
        return response(400, {'error': f'No validated outfit image available for category {outfit_category}'})
    
    # Get base64 of outfit image
    outfit_image_base64 = get_image_from_s3(outfit_image_url)
    if not outfit_image_base64:
        return response(500, {'error': 'Failed to get outfit image from S3'})
    
    print(f"Using outfit image: {outfit_image_url[:80]}...")
    
    # Generate 2 variations
    generated_urls = []
    replicate_predictions = []  # Store prediction IDs for async processing
    quota_exceeded = False
    
    for variation in range(2):
        print(f"Generating variation {variation + 1}/2...")
        try:
            image_base64 = generate_showcase_image(outfit_image_base64, scene_description)
            if image_base64:
                url = save_showcase_image_to_s3(image_base64, ambassador_id, f"{scene_number}_{variation}")
                if url:
                    generated_urls.append(url)
                    print(f"Variation {variation + 1} saved: {url}")
            else:
                print(f"WARNING: Variation {variation + 1} generation failed")
        except QuotaExceededException:
            print("QUOTA EXCEEDED - falling back to Replicate...")
            quota_exceeded = True
            
            # Start Replicate prediction for this variation (async)
            prediction_id = start_replicate_prediction(outfit_image_base64, scene_description)
            if prediction_id:
                replicate_predictions.append({
                    'prediction_id': prediction_id,
                    'variation': variation,
                    'status': 'starting'
                })
                print(f"Started Replicate prediction: {prediction_id}")
    
    # If we have Replicate predictions pending, return them for polling
    if replicate_predictions and not generated_urls:
        # Save prediction info to scene for polling
        scene['replicate_predictions'] = replicate_predictions
        scene['outfit_image_used'] = outfit_image_url
        scene['status'] = 'processing_replicate'
        scene['generated_at'] = datetime.now().isoformat()
        
        # Update ambassador's showcase_photos
        showcase_photos[scene_index] = scene
        
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
        
        return response(202, {
            'success': True,
            'status': 'processing_replicate',
            'scene_id': scene_id,
            'replicate_predictions': replicate_predictions,
            'message': 'Gemini quota exceeded. Images being generated via Replicate. Poll /showcase/scene/poll for results.'
        })
    
    # If quota exceeded and no images generated and no Replicate predictions, return error
    if quota_exceeded and not generated_urls and not replicate_predictions:
        return response(429, {
            'error': 'quota_exceeded',
            'message': 'Gemini API quota exceeded and Replicate fallback unavailable.',
            'scene_id': scene_id
        })
    
    # Update scene
    scene['generated_images'] = generated_urls
    scene['outfit_image_used'] = outfit_image_url
    scene['status'] = 'generated' if generated_urls else 'failed'
    scene['generated_at'] = datetime.now().isoformat()
    
    # Update ambassador's showcase_photos
    showcase_photos[scene_index] = scene
    
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
        return response(500, {'error': f'Failed to save generated images: {str(e)}'})
    
    # Update job if job_id provided
    if job_id:
        try:
            # Get current job
            job_result = jobs_table.get_item(Key={'id': job_id})
            job = job_result.get('Item')
            
            if job:
                job_results = job.get('results', [])
                # Update the scene in results
                for i, result in enumerate(job_results):
                    if result.get('scene_id') == scene_id:
                        job_results[i] = scene
                        break
                
                # Count completed scenes
                completed = sum(1 for r in job_results if r.get('generated_images') and len(r.get('generated_images', [])) > 0)
                
                jobs_table.update_item(
                    Key={'id': job_id},
                    UpdateExpression='SET results = :results, completed_scenes = :completed, updated_at = :updated, #s = :status',
                    ExpressionAttributeNames={'#s': 'status'},
                    ExpressionAttributeValues={
                        ':results': job_results,
                        ':completed': completed,
                        ':status': 'completed' if completed >= NUM_SHOWCASE_PHOTOS else 'processing',
                        ':updated': datetime.now().isoformat()
                    }
                )
        except Exception as e:
            print(f"Error updating job: {e}")
    
    print(f"Scene {scene_number} generation complete: {len(generated_urls)} images")
    
    return response(200, {
        'success': True,
        'scene': decimal_to_python(scene),
        'generated_count': len(generated_urls)
    })


def generate_showcase_photos_async(job_id, ambassador_id, available_categories, ambassador_gender):
    """
    DEPRECATED: This function is kept for backward compatibility.
    The new architecture uses generate_scene() called per-scene from the frontend.
    """
    print(f"WARNING: generate_showcase_photos_async called but is DEPRECATED")
    print(f"Job ID: {job_id}, Ambassador ID: {ambassador_id}")
    print("Please use the new scene-by-scene architecture with /showcase/scene endpoint")
    return


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
    scene_id = body.get('scene_id')  # Frontend sends scene_id
    selected_image = body.get('selected_image')
    
    if not all([ambassador_id, scene_id, selected_image]):
        return response(400, {'error': 'ambassador_id, scene_id, and selected_image required'})
    
    try:
        result = ambassadors_table.get_item(Key={'id': ambassador_id})
        ambassador = result.get('Item')
        
        if not ambassador:
            return response(404, {'error': 'Ambassador not found'})
        
        # Update the specific showcase photo
        showcase_photos = ambassador.get('showcase_photos', [])
        updated = False
        
        for photo in showcase_photos:
            if photo.get('scene_id') == scene_id:  # Match on scene_id
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


def poll_scene_replicate(event):
    """
    Poll Replicate predictions for a scene and download completed images
    POST /api/admin/ambassadors/showcase/scene/poll
    
    Body: { ambassador_id, scene_id }
    Returns: { status, generated_images (if complete) }
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    ambassador_id = body.get('ambassador_id')
    scene_id = body.get('scene_id')
    
    if not all([ambassador_id, scene_id]):
        return response(400, {'error': 'ambassador_id and scene_id required'})
    
    # Get ambassador
    try:
        result = ambassadors_table.get_item(Key={'id': ambassador_id})
        ambassador = result.get('Item')
        if not ambassador:
            return response(404, {'error': 'Ambassador not found'})
    except Exception as e:
        return response(500, {'error': f'Failed to get ambassador: {str(e)}'})
    
    # Find the scene
    showcase_photos = ambassador.get('showcase_photos', [])
    scene = None
    scene_index = -1
    
    for i, photo in enumerate(showcase_photos):
        if photo.get('scene_id') == scene_id:
            scene = photo
            scene_index = i
            break
    
    if not scene:
        return response(404, {'error': 'Scene not found'})
    
    # Check if already completed
    if scene.get('status') == 'generated' and scene.get('generated_images'):
        return response(200, {
            'success': True,
            'status': 'completed',
            'scene': decimal_to_python(scene)
        })
    
    # Get Replicate predictions
    replicate_predictions = scene.get('replicate_predictions', [])
    if not replicate_predictions:
        return response(200, {
            'success': True,
            'status': scene.get('status', 'unknown'),
            'scene': decimal_to_python(scene),
            'message': 'No Replicate predictions to poll'
        })
    
    # Check each prediction
    generated_urls = scene.get('generated_images', [])
    all_completed = True
    any_succeeded = False
    
    for pred in replicate_predictions:
        prediction_id = pred.get('prediction_id')
        if pred.get('status') in ['succeeded', 'failed', 'canceled']:
            # Already processed
            if pred.get('status') == 'succeeded':
                any_succeeded = True
            continue
        
        # Check prediction status
        check_result = check_replicate_prediction(prediction_id)
        pred['status'] = check_result.get('status')
        
        if check_result.get('status') == 'succeeded':
            any_succeeded = True
            output_url = check_result.get('output')
            if output_url:
                # Download and save to S3
                print(f"Downloading completed image from Replicate: {prediction_id}")
                image_base64 = download_image_as_base64(output_url)
                if image_base64:
                    scene_number = scene.get('scene_number', scene_index + 1)
                    variation = pred.get('variation', 0)
                    s3_url = save_showcase_image_to_s3(image_base64, ambassador_id, f"{scene_number}_{variation}")
                    if s3_url:
                        generated_urls.append(s3_url)
                        pred['s3_url'] = s3_url
                        print(f"Saved Replicate image to S3: {s3_url}")
        elif check_result.get('status') in ['starting', 'processing']:
            all_completed = False
        elif check_result.get('status') in ['failed', 'canceled']:
            pred['error'] = check_result.get('error')
            print(f"Replicate prediction {prediction_id} failed: {check_result.get('error')}")
    
    # Update scene
    scene['replicate_predictions'] = replicate_predictions
    scene['generated_images'] = generated_urls
    
    if all_completed:
        scene['status'] = 'generated' if generated_urls else 'failed'
    else:
        scene['status'] = 'processing_replicate'
    
    # Save to DynamoDB
    showcase_photos[scene_index] = scene
    
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
        print(f"Error updating showcase photos: {e}")
    
    return response(200, {
        'success': True,
        'status': 'completed' if all_completed else 'processing',
        'all_completed': all_completed,
        'generated_images': generated_urls,
        'scene': decimal_to_python(scene)
    })