"""
TikTok Short Generation Handlers
Generates scripted scenes for TikTok shorts using AWS Bedrock Claude
Shorts are linked to specific ambassadors - AI decides everything
Photos generated with Nano Banana Pro (Gemini)
"""
import json
import uuid
import base64
import urllib.request
from datetime import datetime
from decimal import Decimal

from config import (
    response, decimal_to_python, verify_admin,
    dynamodb, bedrock_runtime, ambassadors_table, upload_to_s3, lambda_client, s3, S3_BUCKET
)
from handlers.gemini_client import generate_image

# DynamoDB tables
shorts_table = dynamodb.Table('nano_banana_shorts')
products_table = dynamodb.Table('products')
jobs_table = dynamodb.Table('nano_banana_jobs')  # For async photo generation

# AWS Bedrock Claude Opus 4.5 pour le scripting (meilleure réflexion sur les durées)
# Global inference profile for cross-region routing
BEDROCK_MODEL_ID = "global.anthropic.claude-opus-4-5-20251101-v1:0"


def download_image_as_base64(image_url: str) -> str:
    """Download image from URL and return as base64 string."""
    try:
        req = urllib.request.Request(image_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as img_response:
            image_data = img_response.read()
            return base64.b64encode(image_data).decode('utf-8')
    except Exception as e:
        print(f"Error downloading image: {e}")
        raise


def get_ambassadors_for_shorts(event):
    """
    Get all ambassadors available for short creation.
    GET /api/admin/shorts/ambassadors
    
    Returns ambassadors with their outfits count, description, and product_ids.
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        result = ambassadors_table.scan()
        ambassadors = result.get('Items', [])
        
        # Format ambassadors for selection
        formatted = []
        for amb in ambassadors:
            # Count outfits from showcase_photos
            showcase_photos = amb.get('showcase_photos', [])
            outfits_count = len([p for p in showcase_photos if isinstance(p, dict) and p.get('selected_image')])
            
            formatted.append({
                'id': amb.get('id'),
                'name': amb.get('name', 'Unknown'),
                'description': amb.get('description', ''),
                'gender': amb.get('gender', 'female'),
                'profile_photo': amb.get('profile_photo', ''),
                'outfits_count': outfits_count,
                'has_showcase_videos': len(amb.get('showcase_videos', [])) > 0,
                'product_ids': amb.get('product_ids', [])  # Include product IDs
            })
        
        # Sort by name
        formatted.sort(key=lambda x: x.get('name', ''))
        
        return response(200, {
            'success': True,
            'ambassadors': decimal_to_python(formatted),
            'count': len(formatted)
        })
        
    except Exception as e:
        print(f"Error getting ambassadors: {e}")
        return response(500, {'error': f'Failed to get ambassadors: {str(e)}'})


def get_ambassador_outfits(event):
    """
    Get all outfits for a specific ambassador.
    GET /api/admin/shorts/ambassadors/{id}/outfits
    OR GET /api/admin/shorts/outfits?ambassador_id=xxx
    
    Returns the ambassador's generated outfit photos (ambassador_outfits).
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    # Support both path param and query param
    params = event.get('pathParameters', {}) or {}
    query_params = event.get('queryStringParameters', {}) or {}
    ambassador_id = params.get('id') or query_params.get('ambassador_id')
    
    if not ambassador_id:
        return response(400, {'error': 'ambassador_id is required'})
    
    try:
        result = ambassadors_table.get_item(Key={'id': ambassador_id})
        ambassador = result.get('Item')
        
        if not ambassador:
            return response(404, {'error': 'Ambassador not found'})
        
        # Get ambassador_outfits (generated outfit photos with the ambassador wearing different outfits)
        ambassador_outfits = ambassador.get('ambassador_outfits', [])
        
        outfits = []
        for idx, outfit in enumerate(ambassador_outfits):
            if isinstance(outfit, dict):
                # Use selected_image if available, otherwise use first generated image
                image_url = outfit.get('selected_image')
                if not image_url and outfit.get('generated_images'):
                    generated = outfit.get('generated_images', [])
                    if generated:
                        image_url = generated[0]
                
                if image_url:
                    outfits.append({
                        'id': outfit.get('outfit_id', f"outfit_{idx}"),
                        'index': idx,
                        'image_url': image_url,
                        'outfit_type': outfit.get('outfit_type', ''),
                        'status': outfit.get('status', 'pending'),
                        'description': outfit.get('outfit_type', f'Tenue {idx + 1}')
                    })
        
        return response(200, {
            'success': True,
            'ambassador_id': ambassador_id,
            'ambassador_name': ambassador.get('name', 'Unknown'),
            'gender': ambassador.get('gender', 'female'),
            'description': ambassador.get('description', ''),
            'outfits': outfits,
            'count': len(outfits)
        })
        
    except Exception as e:
        print(f"Error getting ambassador outfits: {e}")
        return response(500, {'error': f'Failed to get outfits: {str(e)}'})


def get_ambassador_products_for_shorts(event):
    """
    Get all products assigned to a specific ambassador.
    GET /api/admin/shorts/ambassadors/{id}/products
    
    Returns the ambassador's assigned products with full details.
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    params = event.get('pathParameters', {}) or {}
    ambassador_id = params.get('id')
    
    if not ambassador_id:
        return response(400, {'error': 'ambassador_id is required'})
    
    try:
        result = ambassadors_table.get_item(Key={'id': ambassador_id})
        ambassador = result.get('Item')
        
        if not ambassador:
            return response(404, {'error': 'Ambassador not found'})
        
        # Get product IDs assigned to this ambassador
        product_ids = ambassador.get('product_ids', [])
        
        if not product_ids:
            return response(200, {
                'success': True,
                'ambassador_id': ambassador_id,
                'products': [],
                'count': 0
            })
        
        # Fetch each product
        products = []
        for product_id in product_ids:
            try:
                product_result = products_table.get_item(Key={'id': product_id})
                product = product_result.get('Item')
                if product:
                    products.append({
                        'id': product.get('id'),
                        'name': product.get('name', ''),
                        'brand': product.get('brand', ''),
                        'category': product.get('category', ''),
                        'description': product.get('description', ''),
                        'image_url': product.get('image_url', '')
                    })
            except Exception as e:
                print(f"Error fetching product {product_id}: {e}")
        
        return response(200, {
            'success': True,
            'ambassador_id': ambassador_id,
            'products': decimal_to_python(products),
            'count': len(products)
        })
        
    except Exception as e:
        print(f"Error getting ambassador products: {e}")
        return response(500, {'error': f'Failed to get products: {str(e)}'})


def generate_short_script(event):
    """
    Generate a TikTok short script for a specific ambassador.
    AI decides everything: number of scenes, duration, hashtags, etc.
    
    POST /api/admin/shorts/generate-script
    
    Body: {
        "ambassador_id": "uuid",
        "concept": "Optional theme or concept hint",  # Optional - AI can decide
        "product_id": "uuid"  # Optional - product to promote naturally
    }
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    ambassador_id = body.get('ambassador_id')
    concept = body.get('concept', '')  # Optional hint from user
    product_id = body.get('product_id')  # Optional product to promote
    
    if not ambassador_id:
        return response(400, {'error': 'ambassador_id is required'})
    
    # Get ambassador data
    try:
        result = ambassadors_table.get_item(Key={'id': ambassador_id})
        ambassador = result.get('Item')
        
        if not ambassador:
            return response(404, {'error': 'Ambassador not found'})
    except Exception as e:
        print(f"Error fetching ambassador: {e}")
        return response(500, {'error': 'Failed to fetch ambassador'})
    
    # Get product data if product_id provided
    product = None
    if product_id:
        try:
            product_result = products_table.get_item(Key={'id': product_id})
            product = product_result.get('Item')
            if product:
                print(f"Product found: {product.get('name', 'Unknown')}")
            else:
                print(f"Product not found: {product_id}")
        except Exception as e:
            print(f"Error fetching product: {e}")
            # Continue without product - not a blocking error
    
    # Extract ambassador info
    ambassador_name = ambassador.get('name', 'Unknown')
    ambassador_description = ambassador.get('description', '')
    ambassador_gender = ambassador.get('gender', 'female')
    
    # Get ambassador_outfits (photos of ambassador wearing different outfits)
    ambassador_outfits = ambassador.get('ambassador_outfits', [])
    
    outfits = []
    for idx, outfit in enumerate(ambassador_outfits):
        if isinstance(outfit, dict):
            # Use selected_image if available, otherwise use first generated image
            image_url = outfit.get('selected_image')
            if not image_url and outfit.get('generated_images'):
                generated = outfit.get('generated_images', [])
                if generated:
                    image_url = generated[0]
            
            if image_url:
                outfits.append({
                    'id': outfit.get('outfit_id', f"outfit_{idx}"),
                    'index': idx,
                    'image_url': image_url,
                    'prompt': outfit.get('outfit_type', ''),
                    'scene_type': outfit.get('outfit_type', ''),
                })
    
    if not outfits:
        return response(400, {'error': 'Ambassador has no outfit photos. Generate outfit photos first in the Outfits tab.'})
    
    # Format outfits for AI prompt
    outfits_text = ""
    for o in outfits:
        outfits_text += f"- ID: {o['id']} | Description: {o['prompt'] or o['scene_type'] or 'Tenue sport'}\n"
    
    # Build the prompt for Claude
    system_prompt = """Tu es une VRAIE créatrice fitness sur TikTok. Tu captes des moments RÉELS de ta vie.

🚫 CE QUE TU NE FAIS JAMAIS:
- "Secret reveal" / "Tu veux savoir mon secret?"
- Clins d'œil à la caméra
- "Chut" avec le doigt
- "Maintenant tu sais"
- Regards complices forcés
- Storytelling mystérieux
- Sourires forcés à la caméra
- Call-to-action ("follow for more")
- Tout ce qui fait PUB ou MARKETING

✅ CE QUE TU FAIS:
- Tu captures des VRAIS moments
- Tu t'entraînes NORMALEMENT, pas pour la caméra
- Tu vis ta vie, la caméra est juste là
- Tu transpires, tu souffles, tu te concentres
- C'est BRUT, RÉEL, PAS SCRIPTÉ

🎬 TYPES DE CONTENUS AUTHENTIQUES:
1. "Get ready with me" (GRWM) - préparation naturelle avant la salle
2. "A day in my life" - extraits d'une journée normale
3. "Workout check" - moments random de l'entraînement
4. "POV: tu..." - point de vue immersif
5. "What I eat in a day" - repas/nutrition naturels
6. "Before/after" - transformation workout
7. "Silent vlog" - pas de parole, juste l'ambiance
8. "This or that" - choix rapides
9. "Fit check" - montrer rapidement sa tenue
10. "No talking, just vibes" - ambiance pure

💡 VRAIS MOMENTS FITNESS:
- Souffler entre les séries (pas sourire)
- Ajuster ses écouteurs
- Boire de l'eau (sans regarder la caméra)
- Se regarder dans le miroir (concentration, pas pose)
- Marcher vers une machine
- Essuyer sa sueur
- Attendre qu'une machine se libère
- Checker son téléphone pour la playlist
- Faire une grimace pendant l'effort
- Respirer fort après une série intense

📱 ESTHÉTIQUE:
- Gym lighting naturel
- Angles POV ou selfie
- Parfois légèrement flou/mouvement
- Caméra posée quelque part ou en main
- PAS de setup studio parfait

📝 RÈGLES prompt_image:
1. EN ANGLAIS
2. Commence TOUJOURS par "Put this person"
3. Max 20 mots
4. JAMAIS décrire la personne physiquement
5. JAMAIS "smiling at camera", "winking", "making gesture"
6. Toujours une action NATURELLE, pas une pose

FORMAT: JSON uniquement."""

    concept_text = f"\n\n💡 CONCEPT SUGGÉRÉ: {concept}\n(Tu peux t'en inspirer ou proposer mieux!)" if concept else ""
    
    # Build product section if product provided
    product_text = ""
    if product:
        product_name = product.get('name', '')
        product_brand = product.get('brand', '')
        product_category = product.get('category', '')
        product_description = product.get('description', '')
        product_text = f"""

🛍️ PRODUIT (ULTRA DISCRET):
- Produit: {product_name}
- Marque: {product_brand}

⚡ INTÉGRATION NATURELLE SEULEMENT:
- Le produit est juste LÀ, visible naturellement
- PAS de mise en avant, PAS de focus dessus
- Comme dans la vraie vie: le shaker est sur le banc, c'est tout
- La personne ne "présente" jamais le produit
- Elle l'utilise comme n'importe quel objet de sa routine
- Dans 1 scène MAX, en arrière-plan ou utilisation naturelle
- JAMAIS de "reveal" du produit"""

    user_prompt = f"""Crée un TikTok AUTHENTIQUE pour:

👤 {ambassador_name} ({ambassador_gender})
{ambassador_description}

👕 TENUES: {len(outfits)} disponibles
{outfits_text}
{concept_text}{product_text}

🎬 CRÉE UN CONTENU RÉEL:
- Pas de script marketing
- Des vrais moments d'entraînement
- L'ambassadrice vit sa vie, la caméra capte
- Transpiration, effort, concentration
- PAS de sourires forcés à la caméra
- PAS de "reveal" ou "secret"

Génère ce JSON:
{{
  "title": "Titre court et accrocheur (style TikTok)",
  "concept": "Le vibe du contenu",
  "total_duration": <15-30 secondes max>,
  "hashtags": ["#...", ...],
  "target_platform": "tiktok",
  "mood": "raw/intense/chill/aesthetic/focused",
  "music_suggestion": "Type de musique (trending sound, phonk, lo-fi...)",
  "scenes": [
    {{
      "order": 1,
      "scene_type": "workout/transition/lifestyle/fit-check",
      "description": "Moment capturé (NATUREL)",
      "duration": <2-5 secondes>,
      "prompt_image": "Put this person [action naturelle] in [lieu]. [ambiance]",
      "prompt_video": "La personne [action]. Caméra fixe.",
      "outfit_id": "<ID tenue>",
      "camera_angle": "pov/medium/wide",
      "transition_to_next": "cut/none"
    }}
  ]
}}

🚫 INTERDITS ABSOLUS:
- "smiling at camera" / "winking" / "making gesture"  
- "secret" / "reveal" / "mystery"
- "confident smile" / "knowing look"
- Tout regard/geste vers la caméra
- Plus de 6 scènes"""

    try:
        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4000,
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": user_prompt
                }
            ]
        }
        
        print(f"Calling Bedrock for short script generation for ambassador {ambassador_id}...")
        
        bedrock_response = bedrock_runtime.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            body=json.dumps(request_body),
            contentType="application/json",
            accept="application/json"
        )
        
        response_body = json.loads(bedrock_response['body'].read())
        content = response_body.get('content', [{}])[0].get('text', '{}')
        
        print(f"Bedrock response: {content[:500]}...")
        
        # Parse JSON from response
        json_start = content.find('{')
        json_end = content.rfind('}') + 1
        
        if json_start != -1 and json_end > json_start:
            json_str = content[json_start:json_end]
            script = json.loads(json_str)
        else:
            raise Exception("No valid JSON found in response")
        
        # Validate and enrich script
        script['id'] = str(uuid.uuid4())
        script['ambassador_id'] = ambassador_id
        script['ambassador_name'] = ambassador_name
        script['ambassador_gender'] = ambassador_gender
        script['created_at'] = datetime.now().isoformat()
        script['updated_at'] = datetime.now().isoformat()
        script['status'] = 'draft'
        
        # Add product info if provided
        if product:
            script['product_id'] = product_id
            script['product'] = {
                'id': product_id,
                'name': product.get('name', ''),
                'brand': product.get('brand', ''),
                'category': product.get('category', ''),
                'description': product.get('description', ''),
                'image_url': product.get('image_url', '')
            }
        else:
            script['product_id'] = None
            script['product'] = None
        
        # Validate scenes
        if 'scenes' not in script or not script['scenes']:
            raise Exception("No scenes generated")
        
        # Create outfit map for quick lookup
        outfit_map = {o['id']: o for o in outfits}
        
        # Enrich scenes with outfit details and product info
        for scene in script['scenes']:
            outfit_id = scene.get('outfit_id')
            if outfit_id and outfit_id in outfit_map:
                outfit = outfit_map[outfit_id]
                scene['outfit_image_url'] = outfit.get('image_url', '')
                scene['outfit_description'] = outfit.get('prompt', '')
            else:
                # If outfit not found, assign first available
                if outfits:
                    scene['outfit_id'] = outfits[0]['id']
                    scene['outfit_image_url'] = outfits[0].get('image_url', '')
                    scene['outfit_description'] = outfits[0].get('prompt', '')
            
            scene['id'] = str(uuid.uuid4())
            scene['status'] = 'pending'  # pending, generating, completed, error
            scene['generated_image_url'] = None
            scene['generated_video_url'] = None
            
            # Ensure product_placement field exists
            if 'product_placement' not in scene:
                scene['product_placement'] = False
        
        # Save script to DynamoDB immediately so generate_scene_photos can find it
        try:
            shorts_table.put_item(Item=script)
            print(f"Script saved to DynamoDB with id: {script['id']}")
        except Exception as e:
            print(f"Warning: Failed to auto-save script: {e}")
            # Continue anyway - the script will work, just won't be persisted yet
        
        return response(200, {
            'success': True,
            'script': script
        })
        
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        print(f"Raw content: {content}")
        return response(500, {'error': f'Failed to parse AI response as JSON: {str(e)}'})
    except Exception as e:
        print(f"Error generating script: {e}")
        import traceback
        traceback.print_exc()
        return response(500, {'error': f'Failed to generate script: {str(e)}'})


def regenerate_scene(event):
    """
    Regenerate a single scene in a script.
    POST /api/admin/shorts/regenerate-scene
    
    Body: {
        "script_id": "uuid",
        "scene_index": 2,
        "feedback": "Make it more energetic"  # Optional
    }
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    script_id = body.get('script_id')
    scene_index = body.get('scene_index')
    feedback = body.get('feedback', '')
    full_script = body.get('script')  # Can pass full script instead of ID
    
    if scene_index is None:
        return response(400, {'error': 'scene_index is required'})
    
    # Get script from DB or use provided one
    if full_script:
        script = full_script
    elif script_id:
        try:
            result = shorts_table.get_item(Key={'id': script_id})
            script = result.get('Item')
            if not script:
                return response(404, {'error': 'Script not found'})
            script = decimal_to_python(script)
        except Exception as e:
            return response(500, {'error': f'Failed to fetch script: {str(e)}'})
    else:
        return response(400, {'error': 'script_id or script is required'})
    
    scenes = script.get('scenes', [])
    if scene_index < 0 or scene_index >= len(scenes):
        return response(400, {'error': 'Invalid scene_index'})
    
    current_scene = scenes[scene_index]
    ambassador_gender = script.get('ambassador_gender', 'female')
    
    # Get ambassador outfits
    ambassador_id = script.get('ambassador_id')
    outfits_text = ""
    
    if ambassador_id:
        try:
            result = ambassadors_table.get_item(Key={'id': ambassador_id})
            ambassador = result.get('Item')
            if ambassador:
                showcase_photos = ambassador.get('showcase_photos', [])
                for idx, photo in enumerate(showcase_photos):
                    if isinstance(photo, dict) and photo.get('selected_image'):
                        outfits_text += f"- ID: outfit_{idx} | Description: {photo.get('prompt', 'Tenue sport')}\n"
        except:
            pass
    
    # Build prompt
    system_prompt = """Tu es un expert TikTok. Tu dois régénérer UNE SEULE scène d'un script existant.
Garde le même style et contexte, mais améliore la scène selon le feedback.
FORMAT: JSON uniquement."""

    feedback_text = f"\n\nFEEDBACK UTILISATEUR: {feedback}" if feedback else ""
    
    other_scenes = [f"Scene {i+1}: {s.get('description', '')}" for i, s in enumerate(scenes) if i != scene_index]

    user_prompt = f"""Régénère cette scène:

SCÈNE ACTUELLE (index {scene_index}):
{json.dumps(current_scene, indent=2, ensure_ascii=False)}

CONTEXTE DU SCRIPT:
- Titre: {script.get('title', '')}
- Concept: {script.get('concept', '')}
- Genre: {ambassador_gender}
- Durée totale: {script.get('total_duration', 30)}s

AUTRES SCÈNES DU SCRIPT:
{chr(10).join(other_scenes)}

TENUES DISPONIBLES:
{outfits_text}
{feedback_text}

Génère une NOUVELLE version de cette scène au format:
{{
  "order": {current_scene.get('order', scene_index + 1)},
  "scene_type": "...",
  "description": "...",
  "duration": ...,
  "prompt_image": "... EN ANGLAIS ...",
  "prompt_video": "... EN FRANÇAIS ...",
  "outfit_id": "...",
  "camera_angle": "...",
  "transition_to_next": "..."
}}"""

    try:
        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1000,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}]
        }
        
        bedrock_response = bedrock_runtime.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            body=json.dumps(request_body),
            contentType="application/json",
            accept="application/json"
        )
        
        response_body = json.loads(bedrock_response['body'].read())
        content = response_body.get('content', [{}])[0].get('text', '{}')
        
        # Parse JSON
        json_start = content.find('{')
        json_end = content.rfind('}') + 1
        
        if json_start != -1 and json_end > json_start:
            new_scene = json.loads(content[json_start:json_end])
        else:
            raise Exception("No valid JSON found")
        
        # Keep original ID and add metadata
        new_scene['id'] = current_scene.get('id', str(uuid.uuid4()))
        new_scene['status'] = 'pending'
        new_scene['regenerated_at'] = datetime.now().isoformat()
        new_scene['generated_image_url'] = None
        new_scene['generated_video_url'] = None
        
        # Get outfit image URL
        if ambassador_id and new_scene.get('outfit_id'):
            try:
                result = ambassadors_table.get_item(Key={'id': ambassador_id})
                ambassador = result.get('Item')
                if ambassador:
                    showcase_photos = ambassador.get('showcase_photos', [])
                    outfit_idx = int(new_scene['outfit_id'].replace('outfit_', ''))
                    if 0 <= outfit_idx < len(showcase_photos):
                        photo = showcase_photos[outfit_idx]
                        new_scene['outfit_image_url'] = photo.get('selected_image', '')
                        new_scene['outfit_description'] = photo.get('prompt', '')
            except:
                pass
        
        return response(200, {
            'success': True,
            'scene': new_scene,
            'scene_index': scene_index
        })
        
    except Exception as e:
        print(f"Error regenerating scene: {e}")
        return response(500, {'error': f'Failed to regenerate scene: {str(e)}'})


def save_short_script(event):
    """
    Save a short script to DynamoDB.
    POST /api/admin/shorts/save
    
    Body: { script: { ... } }
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    script = body.get('script')
    if not script:
        return response(400, {'error': 'script is required'})
    
    # Ensure required fields
    if not script.get('id'):
        script['id'] = str(uuid.uuid4())
    
    script['updated_at'] = datetime.now().isoformat()
    if not script.get('created_at'):
        script['created_at'] = script['updated_at']
    
    # Convert floats to Decimal for DynamoDB
    def convert_to_decimal(obj):
        if isinstance(obj, float):
            return Decimal(str(obj))
        elif isinstance(obj, dict):
            return {k: convert_to_decimal(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_to_decimal(i) for i in obj]
        return obj
    
    script = convert_to_decimal(script)
    
    try:
        shorts_table.put_item(Item=script)
        
        return response(200, {
            'success': True,
            'script_id': script['id'],
            'message': 'Script saved successfully'
        })
        
    except Exception as e:
        print(f"Error saving script: {e}")
        return response(500, {'error': f'Failed to save script: {str(e)}'})


def get_short_scripts(event):
    """
    Get all saved short scripts.
    GET /api/admin/shorts
    Optional: ?ambassador_id=xxx to filter by ambassador
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    params = event.get('queryStringParameters', {}) or {}
    ambassador_id = params.get('ambassador_id')
    
    try:
        result = shorts_table.scan()
        scripts = result.get('Items', [])
        
        # Filter by ambassador if provided
        if ambassador_id:
            scripts = [s for s in scripts if s.get('ambassador_id') == ambassador_id]
        
        # Sort by created_at descending
        scripts.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        return response(200, {
            'success': True,
            'scripts': decimal_to_python(scripts),
            'count': len(scripts)
        })
        
    except Exception as e:
        print(f"Error getting scripts: {e}")
        return response(500, {'error': f'Failed to get scripts: {str(e)}'})


def get_short_script(event):
    """
    Get a specific short script by ID.
    GET /api/admin/shorts/{id}
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    params = event.get('pathParameters', {}) or {}
    script_id = params.get('id')
    
    if not script_id:
        return response(400, {'error': 'script_id is required'})
    
    try:
        result = shorts_table.get_item(Key={'id': script_id})
        script = result.get('Item')
        
        if not script:
            return response(404, {'error': 'Script not found'})
        
        return response(200, {
            'success': True,
            'script': decimal_to_python(script)
        })
        
    except Exception as e:
        print(f"Error getting script: {e}")
        return response(500, {'error': f'Failed to get script: {str(e)}'})


def delete_short_script(event):
    """
    Delete a short script.
    DELETE /api/admin/shorts/{id}
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    params = event.get('pathParameters', {}) or {}
    script_id = params.get('id')
    
    if not script_id:
        return response(400, {'error': 'script_id is required'})
    
    try:
        shorts_table.delete_item(Key={'id': script_id})
        
        return response(200, {
            'success': True,
            'message': 'Script deleted successfully'
        })
        
    except Exception as e:
        print(f"Error deleting script: {e}")
        return response(500, {'error': f'Failed to delete script: {str(e)}'})


def update_scene(event):
    """
    Manually update a scene in a saved script.
    PUT /api/admin/shorts/scene
    
    Body: {
        "script_id": "uuid",
        "scene_index": 2,
        "scene": { ... updated scene data ... }
    }
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    script_id = body.get('script_id')
    scene_index = body.get('scene_index')
    scene_data = body.get('scene')
    
    if not script_id or scene_index is None or not scene_data:
        return response(400, {'error': 'script_id, scene_index, and scene are required'})
    
    try:
        # Get existing script
        result = shorts_table.get_item(Key={'id': script_id})
        script = result.get('Item')
        
        if not script:
            return response(404, {'error': 'Script not found'})
        
        scenes = script.get('scenes', [])
        if scene_index < 0 or scene_index >= len(scenes):
            return response(400, {'error': 'Invalid scene_index'})
        
        # Update scene
        scene_data['updated_at'] = datetime.now().isoformat()
        scenes[scene_index] = scene_data
        
        # Update script
        script['scenes'] = scenes
        script['updated_at'] = datetime.now().isoformat()
        
        # Convert floats to Decimal
        def convert_to_decimal(obj):
            if isinstance(obj, float):
                return Decimal(str(obj))
            elif isinstance(obj, dict):
                return {k: convert_to_decimal(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_to_decimal(i) for i in obj]
            return obj
        
        script = convert_to_decimal(script)
        shorts_table.put_item(Item=script)
        
        return response(200, {
            'success': True,
            'message': 'Scene updated successfully'
        })
        
    except Exception as e:
        print(f"Error updating scene: {e}")
        return response(500, {'error': f'Failed to update scene: {str(e)}'})


def start_scene_photos_generation(event):
    """
    Start async photo generation for a scene - Returns job_id immediately.
    Photos are generated in background using Lambda async invocation.
    
    POST /api/admin/shorts/generate-scene-photos
    Body: {
        "script_id": "uuid",
        "scene_index": 0,
        "outfit_image_url": "https://s3...jpg"
    }
    
    Returns:
    {
        "success": True,
        "job_id": "uuid",
        "status": "pending"
    }
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    script_id = body.get('script_id')
    scene_index = body.get('scene_index')
    outfit_image_url = body.get('outfit_image_url')
    
    if not script_id or scene_index is None or not outfit_image_url:
        return response(400, {'error': 'script_id, scene_index, and outfit_image_url are required'})
    
    try:
        # Get the script to validate it exists
        result = shorts_table.get_item(Key={'id': script_id})
        script = result.get('Item')
        
        if not script:
            return response(404, {'error': 'Script not found'})
        
        scenes = script.get('scenes', [])
        if scene_index < 0 or scene_index >= len(scenes):
            return response(400, {'error': 'Invalid scene_index'})
        
        scene = scenes[scene_index]
        scene_prompt = scene.get('prompt_image', 'Put this person in an aesthetic room, casual pose, relaxed vibe.')
        ambassador_id = script.get('ambassador_id', 'unknown')
        
        # Create job in DynamoDB
        job_id = str(uuid.uuid4())
        job = {
            'id': job_id,
            'type': 'scene_photos',
            'status': 'pending',
            'script_id': script_id,
            'scene_index': scene_index,
            'ambassador_id': ambassador_id,
            'outfit_image_url': outfit_image_url,
            'scene_prompt': scene_prompt,
            'photos': [],
            'progress': 0,
            'total': 2,  # Always generate 2 photos
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat()
        }
        
        jobs_table.put_item(Item=job)
        print(f"Created scene photos job: {job_id}")
        
        # Invoke Lambda async for photo generation immediately
        # Image will be downloaded in async handler (faster response)
        payload = {
            'action': 'generate_scene_photos_async',
            'job_id': job_id,
            'outfit_image_url': outfit_image_url  # Pass URL, download in async
        }
        
        lambda_client.invoke(
            FunctionName='nano-banana-api',
            InvocationType='Event',  # Async
            Payload=json.dumps(payload)
        )
        print(f"Launched async scene photo generation for job {job_id}")
        
        return response(200, {
            'success': True,
            'job_id': job_id,
            'status': 'pending'
        })
        
    except Exception as e:
        print(f"Error starting scene photos generation: {e}")
        import traceback
        traceback.print_exc()
        return response(500, {'error': f'Failed to start generation: {str(e)}'})


def generate_scene_photos_async(job_id: str, outfit_image_url: str):
    """
    Async handler - Generate 2 photos for a scene using Nano Banana Pro.
    Called by Lambda async invocation.
    
    Args:
        job_id: The job ID to update
        outfit_image_url: URL of the outfit image to use as reference
    """
    print(f"Starting async scene photo generation for job {job_id}")
    
    try:
        # Get job data
        result = jobs_table.get_item(Key={'id': job_id})
        job = result.get('Item')
        
        if not job:
            print(f"Job {job_id} not found")
            return
        
        # Update status to processing
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET #status = :status, updated_at = :updated',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'processing',
                ':updated': datetime.now().isoformat()
            }
        )
        
        # Download outfit image directly (moved from sync handler)
        print(f"Downloading outfit reference from URL: {outfit_image_url}")
        outfit_base64 = download_image_as_base64(outfit_image_url)
        
        script_id = job.get('script_id')
        scene_index = int(job.get('scene_index', 0))
        ambassador_id = job.get('ambassador_id', 'unknown')
        scene_prompt = job.get('scene_prompt', 'Put this person in an aesthetic room, casual pose, relaxed vibe.')
        
        # Build the full prompt
        if scene_prompt.lower().startswith('put this person'):
            full_prompt = f"{scene_prompt} Keep exact same face, body and clothes. TikTok aesthetic, natural lighting, 9:16 vertical."
        else:
            full_prompt = f"Put this person {scene_prompt}. Keep exact same face, body and clothes. TikTok aesthetic, natural lighting, 9:16 vertical."
        
        print(f"Generating 2 photos with prompt: {full_prompt[:100]}...")
        
        # Generate 2 photos
        scene_photos = []
        
        for photo_index in range(2):
            try:
                print(f"Generating photo {photo_index + 1}/2...")
                
                # Call Gemini to generate image with reference
                image_base64 = generate_image(
                    prompt=full_prompt,
                    reference_images=[outfit_base64],
                    image_size="2K"
                )
                
                if image_base64:
                    # Upload to S3
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    s3_key = f"shorts/{ambassador_id}/{script_id}/scene_{scene_index}_photo_{photo_index}_{timestamp}.png"
                    
                    photo_url = upload_to_s3(
                        image_base64,
                        s3_key,
                        content_type='image/png'
                    )
                    
                    if photo_url:
                        scene_photos.append({
                            'url': photo_url,
                            'index': photo_index
                        })
                        print(f"Photo {photo_index + 1} uploaded: {photo_url}")
                        
                        # Update job progress
                        jobs_table.update_item(
                            Key={'id': job_id},
                            UpdateExpression='SET photos = :photos, progress = :progress, updated_at = :updated',
                            ExpressionAttributeValues={
                                ':photos': scene_photos,
                                ':progress': photo_index + 1,
                                ':updated': datetime.now().isoformat()
                            }
                        )
                    else:
                        print(f"Failed to upload photo {photo_index + 1} to S3")
                else:
                    print(f"Failed to generate photo {photo_index + 1}")
                    
            except Exception as e:
                print(f"Error generating photo {photo_index + 1}: {e}")
                import traceback
                traceback.print_exc()
                continue
        
        # Update script with generated photos
        if scene_photos:
            try:
                script_result = shorts_table.get_item(Key={'id': script_id})
                script = script_result.get('Item')
                
                if script:
                    scenes = script.get('scenes', [])
                    if 0 <= scene_index < len(scenes):
                        scenes[scene_index]['generated_photos'] = scene_photos
                        scenes[scene_index]['photos_generated_at'] = datetime.now().isoformat()
                        script['scenes'] = scenes
                        script['updated_at'] = datetime.now().isoformat()
                        
                        # Convert floats to Decimal for DynamoDB
                        def convert_to_decimal(obj):
                            if isinstance(obj, float):
                                return Decimal(str(obj))
                            elif isinstance(obj, dict):
                                return {k: convert_to_decimal(v) for k, v in obj.items()}
                            elif isinstance(obj, list):
                                return [convert_to_decimal(i) for i in obj]
                            return obj
                        
                        script = convert_to_decimal(script)
                        shorts_table.put_item(Item=script)
                        print(f"Updated script {script_id} with {len(scene_photos)} photos")
            except Exception as e:
                print(f"Error updating script: {e}")
        
        # Mark job as completed
        final_status = 'completed' if scene_photos else 'failed'
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET #status = :status, photos = :photos, updated_at = :updated',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': final_status,
                ':photos': scene_photos,
                ':updated': datetime.now().isoformat()
            }
        )
        
        # Cleanup temp S3 file
        try:
            s3.delete_object(Bucket=S3_BUCKET, Key=outfit_s3_key)
            print(f"Cleaned up temp file: {outfit_s3_key}")
        except:
            pass
        
        print(f"Job {job_id} {final_status} with {len(scene_photos)} photos")
        
    except Exception as e:
        print(f"Error in async scene photo generation: {e}")
        import traceback
        traceback.print_exc()
        
        # Mark job as failed
        try:
            jobs_table.update_item(
                Key={'id': job_id},
                UpdateExpression='SET #status = :status, #error = :error, updated_at = :updated',
                ExpressionAttributeNames={'#status': 'status', '#error': 'error'},
                ExpressionAttributeValues={
                    ':status': 'failed',
                    ':error': str(e),
                    ':updated': datetime.now().isoformat()
                }
            )
        except:
            pass


def get_scene_photos_status(event):
    """
    Get status of scene photos generation job.
    GET /api/admin/shorts/scene-photos/status?job_id=xxx
    
    Returns:
    {
        "success": True,
        "status": "pending|processing|completed|failed",
        "progress": 1,
        "total": 2,
        "photos": [...] // when completed
    }
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    # Get job_id from query params
    query_params = event.get('queryStringParameters', {}) or {}
    job_id = query_params.get('job_id')
    
    if not job_id:
        return response(400, {'error': 'job_id query parameter is required'})
    
    try:
        result = jobs_table.get_item(Key={'id': job_id})
        job = result.get('Item')
        
        if not job:
            return response(404, {'error': 'Job not found'})
        
        return response(200, {
            'success': True,
            'job_id': job_id,
            'status': job.get('status', 'unknown'),
            'progress': int(job.get('progress', 0)),
            'total': int(job.get('total', 2)),
            'photos': decimal_to_python(job.get('photos', [])),
            'error': job.get('error'),
            'script_id': job.get('script_id'),
            'scene_index': int(job.get('scene_index', 0))
        })
        
    except Exception as e:
        print(f"Error getting scene photos status: {e}")
        return response(500, {'error': f'Failed to get status: {str(e)}'})


# Keep old function name as alias for backward compatibility
def generate_scene_photos(event):
    """Backward compatible alias - now starts async generation"""
    return start_scene_photos_generation(event)
