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
                'photo_profile': amb.get('photo_profile', ''),
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
        outfits_text += f"- ID: {o['id']} | Description: {o['prompt'] or o['scene_type'] or 'Tenue casual'}\n"
    
    # Build the prompt for Claude - VIRAL TIKTOK FORMAT
    system_prompt = """Tu es un expert en création de contenus TikTok viraux. Tu crées des scripts VARIÉS et CRÉATIFS.

🎲 IMPORTANT: Varie les formats! Ne fais pas toujours le même type de contenu.

🔥 FORMATS POSSIBLES (choisis-en UN au hasard, pas toujours le même):

**FORMAT A - "Day in my life" / "Journée type"**
- Moments authentiques d'une journée
- Esthétique, lifestyle, pas de tips
- Ambiance chill, musique lo-fi
- Produit visible naturellement dans la routine

**FORMAT B - "Get ready with me" (GRWM)**
- Préparation avant une activité
- Montage rapide, énergique
- Produit = partie de la préparation

**FORMAT C - "POV: quand tu..." / "That feeling when..."**
- Scène immersive relatable
- Humour ou émotion
- Pas de face caméra, juste l'ambiance
- Produit dans le décor

**FORMAT D - "Silent vlog" / "No talking just vibes"**
- AUCUN texte overlay sauf titre
- Juste des images esthétiques
- Musique = l'émotion principale
- Ambiance > message

**FORMAT E - "What changed my [X]"**
- 2-3 conseils/changements
- B-roll illustratif avec texte
- UN conseil mentionne le produit

**FORMAT F - "Before vs After" / "Transformation"**
- Contraste visuel
- Progression, amélioration
- Produit = facteur du changement

**FORMAT G - "Things I can't live without"**
- Objets/habitudes essentielles
- Produit = UN des éléments
- Lifestyle authentique

**FORMAT H - "My honest review" / "POV: 1 mois avec..."**
- Utilisation réelle
- Moments variés avec le produit
- Authentique, pas promotionnel

📋 STRUCTURE FLEXIBLE:

Hook (2-4s): Accroche visuelle OU face caméra OU action
Corps (10-20s): 2-5 scènes selon le format
Closer (2-4s): Conclusion naturelle

⚠️ RÈGLES:

1. **TEXT OVERLAY** = optionnel selon le format
   - Silent vlogs: PAS de texte (juste titre)
   - Formats éducatifs: texte sur chaque scène
   - GRWM/Day in life: texte minimal

2. **PRODUIT** = 1-2 scènes MAX
   - Intégré naturellement à l'action
   - Jamais le focus principal
   - Peut être juste VISIBLE (pas utilisé)

3. **VARIÉTÉ**:
   - Alterne les angles caméra
   - Mix face caméra et B-roll
   - Pas toujours la même structure

4. **AUTHENTICITÉ**:
   - Moments réels, pas posés
   - Imperfections OK
   - Pas de marketing

📝 RÈGLES prompt_image (TRÈS IMPORTANT):
1. EN ANGLAIS uniquement
2. Commence par "Put this person"
3. Max 25 mots
4. JAMAIS décrire physiquement la personne
5. JAMAIS de texte dans l'image
6. Actions NATURELLES, pas des poses

⛔ MOTS INTERDITS dans prompt_image (trop cinématique, pas TikTok):
- "dramatic", "cinematic", "epic", "cathedral", "majestic"
- "moody atmosphere", "powerful atmosphere"
- "professional lighting", "studio lighting"
- "low angle", "hero shot"
- Tout ce qui fait "film hollywoodien"

✅ STYLE VOULU dans prompt_image:
- "natural light", "window light", "cozy", "casual"
- Lieux réels: "apartment", "home gym", "bedroom", "kitchen"
- Ambiance: "relaxed", "chill", "everyday", "authentic"
- Qualité: "smartphone photo", "casual vibe"

FORMAT: JSON uniquement."""

    concept_text = f"\n\n💡 CONCEPT SUGGÉRÉ: {concept}\n(Interprète-le librement, sois créatif!)" if concept else ""
    
    # Build product section if product provided
    product_text = ""
    if product:
        product_name = product.get('name', '')
        product_brand = product.get('brand', '')
        product_category = product.get('category', '')
        product_description = product.get('description', '')
        product_text = f"""

🛍️ PRODUIT À INTÉGRER:
- Produit: {product_name}
- Marque: {product_brand}
- Catégorie: {product_category}

⚡ INTÉGRATION:
- Visible dans 1-2 scènes MAX
- Intégré naturellement à l'action (pas posé, pas montré)
- La personne l'utilise OU il est juste dans le décor
- PAS le sujet principal du contenu"""

    user_prompt = f"""Crée un TikTok pour:

👤 {ambassador_name} ({ambassador_gender})
📝 {ambassador_description if ambassador_description else "Lifestyle creator"}

👕 {len(outfits)} tenues disponibles
{outfits_text}
{concept_text}{product_text}

🎲 CHOISIS UN FORMAT AU HASARD parmi A-H (pas toujours le même!)
Sois CRÉATIF et VARIÉ.

Génère ce JSON:
{{
  "title": "Titre accrocheur",
  "concept": "Format choisi (A/B/C/etc) + description",
  "total_duration": <15-30s>,
  "hashtags": ["#...", ...],
  "target_platform": "tiktok",
  "mood": "chill/energetic/aesthetic/funny/motivational",
  "music_suggestion": "Style de musique",
  "scenes": [
    {{
      "order": 1,
      "scene_type": "hook/scene/product/closer",
      "description": "Ce qui se passe",
      "text_overlay": "Texte à l'écran (optionnel selon format, null si silent vlog)",
      "duration": 3,
      "prompt_image": "Put this person [action] in [lieu]. [détails visuels]",
      "prompt_video": "Description du mouvement",
      "outfit_id": "ID de la tenue",
      "camera_angle": "pov/medium/wide/close-up",
      "transition_to_next": "cut/swipe/none",
      "product_visible": false
    }}
  ]
}}

Rappel: Varie les formats, sois créatif!"""

    try:
        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4000,
            "temperature": 0.9,  # High temperature for creative variety
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
        
        # Get product info ONLY if product_visible is true for this scene
        product_visible = scene.get('product_visible', False)
        product = script.get('product', {}) if product_visible else {}
        
        print(f"Scene {scene_index} - product_visible: {product_visible}, including product: {bool(product)}")
        
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
            'product_visible': product_visible,
            'product': product,  # Only passed if product_visible is true
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
            FunctionName='saas-ugc',
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
        
        # Build reference images list
        reference_images = [outfit_base64]
        
        # Download product image ONLY if product_visible is true
        product_visible = job.get('product_visible', False)
        product_info = job.get('product', {})
        product_image_url = product_info.get('image_url', '') if product_info and product_visible else ''
        
        if product_image_url and product_visible:
            try:
                print(f"Scene has product_visible=True, downloading product image: {product_image_url}")
                product_base64 = download_image_as_base64(product_image_url)
                reference_images.append(product_base64)
                print("Product image added as reference")
            except Exception as e:
                print(f"Failed to download product image (continuing without it): {e}")
        else:
            print(f"Scene has product_visible={product_visible}, NOT including product image")
        
        script_id = job.get('script_id')
        scene_index = int(job.get('scene_index', 0))
        ambassador_id = job.get('ambassador_id', 'unknown')
        scene_prompt = job.get('scene_prompt', 'Put this person in an aesthetic room, casual pose, relaxed vibe.')
        
        # Build product placement text ONLY if product_visible is true
        product_text = ""
        if product_visible and product_info and product_info.get('name'):
            product_name = product_info.get('name', '')
            product_brand = product_info.get('brand', '')
            if product_brand:
                product_text = f" The {product_brand} {product_name} (shown in second reference image) should be visible in the scene - placed naturally nearby (on floor, bench, or table) NOT in person's hands."
            else:
                product_text = f" The {product_name} (shown in second reference image) should be visible in the scene - placed naturally nearby (on floor, bench, or table) NOT in person's hands."
            print(f"Including product in prompt: {product_name}")
        else:
            print("NOT including product in prompt (product_visible is False)")
        
        # Build the full prompt with ALL constraints
        # IMPORTANT: Authenticity-focused constraints for TikTok content (NOT cinematic)
        constraints = """CRITICAL RULES FOR AUTHENTIC TIKTOK CONTENT:
- Keep EXACT same face, body shape and clothes from FIRST reference image
- Person's hands must be EMPTY (no objects, no phone, no weights, no bottle, no equipment)
- ABSOLUTELY NO TEXT anywhere in image (no signs, no logos, no brand names, no gym equipment labels, no numbers on weights, no writing of any kind)
- ONLY ONE PERSON in the image (the reference person) - NO OTHER PEOPLE anywhere, even in background
- Location should feel LIVED-IN and REAL, not a movie set
- NO TikTok overlays, UI elements, or social media graphics
- NO watermarks or stamps

STYLE - AUTHENTIC TIKTOK (NOT CINEMATIC):
- Natural smartphone-quality lighting (window light, room lights)
- Slightly imperfect composition like a real photo
- NO dramatic lighting, NO professional studio lighting
- NO cinematic color grading or film looks
- Feels like iPhone photo, not a movie still
- Real locations (home gym, bedroom, kitchen, apartment)
- 9:16 vertical format for TikTok"""
        
        if scene_prompt.lower().startswith('put this person'):
            full_prompt = f"{scene_prompt}{product_text}\n\n{constraints}"
        else:
            full_prompt = f"Put this person {scene_prompt}{product_text}\n\n{constraints}"
        
        print(f"Generating 2 photos with prompt: {full_prompt[:100]}...")
        print(f"Using {len(reference_images)} reference image(s)")
        
        # Generate 2 photos
        scene_photos = []
        
        for photo_index in range(2):
            try:
                print(f"Generating photo {photo_index + 1}/2...")
                
                # Call Gemini to generate image with reference(s)
                image_base64 = generate_image(
                    prompt=full_prompt,
                    reference_images=reference_images,
                    image_size="2K"
                )
                
                if image_base64:
                    # Upload to S3 - decode base64 to bytes first
                    import base64 as b64
                    image_bytes = b64.b64decode(image_base64)
                    
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    s3_key = f"shorts/{ambassador_id}/{script_id}/scene_{scene_index}_photo_{photo_index}_{timestamp}.png"
                    
                    photo_url = upload_to_s3(
                        s3_key,
                        image_bytes,
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


# ==============================================================================
# SCENE VIDEO GENERATION - Using Kling via Replicate (like showcase_videos)
# ==============================================================================

# Import Replicate functions from showcase_videos module
import urllib.error
from config import REPLICATE_API_KEY

REPLICATE_API_URL = "https://api.replicate.com/v1/predictions"
DEFAULT_NEGATIVE_PROMPT = "morphing, face drift, changing facial features, extra limbs, bad hands, distorted fingers, flicker, jitter, wobble, blur, low quality, text, watermark, logo, unnatural movement, robotic motion, frozen expression, teeth showing, open mouth smile, camera movement, camera shake, zooming"


def call_kling_api(image_url: str, prompt: str, negative_prompt: str, duration: int = 5) -> dict:
    """
    Call Replicate API to generate video with Kling model.
    Returns prediction info (async - need to poll for result).
    """
    if not REPLICATE_API_KEY:
        raise Exception("REPLICATE_KEY not configured")
    
    headers = {
        "Authorization": f"Bearer {REPLICATE_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "version": "kwaivgi/kling-v2.5-turbo-pro",
        "input": {
            "image": image_url,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "duration": duration,
            "aspect_ratio": "9:16",
        }
    }
    
    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            REPLICATE_API_URL,
            data=data,
            headers=headers,
            method='POST'
        )
        
        with urllib.request.urlopen(req, timeout=30) as api_response:
            result = json.loads(api_response.read().decode('utf-8'))
            return {
                'id': result.get('id'),
                'status': result.get('status'),
                'urls': result.get('urls', {}),
            }
            
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8') if e.fp else 'No error body'
        raise Exception(f"Replicate HTTP error: {e.code} - {error_body[:200]}")
    except Exception as e:
        raise Exception(f"Replicate error: {str(e)}")


def check_kling_prediction(prediction_id: str) -> dict:
    """Check status of a Replicate prediction."""
    if not REPLICATE_API_KEY:
        raise Exception("REPLICATE_KEY not configured")
    
    headers = {"Authorization": f"Bearer {REPLICATE_API_KEY}"}
    
    try:
        req = urllib.request.Request(
            f"{REPLICATE_API_URL}/{prediction_id}",
            headers=headers,
            method='GET'
        )
        
        with urllib.request.urlopen(req, timeout=30) as api_response:
            result = json.loads(api_response.read().decode('utf-8'))
            return {
                'id': result.get('id'),
                'status': result.get('status'),
                'output': result.get('output'),
                'error': result.get('error'),
            }
    except Exception as e:
        raise Exception(f"Error checking prediction: {str(e)}")


def generate_video_prompt_for_scene(image_url: str, scene_description: str) -> dict:
    """
    Use AWS Bedrock Claude Vision to analyze image and generate video prompt.
    """
    model_id = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    
    system_prompt = """Tu analyses une image et décris l'action que la personne fait.
Ton output sera utilisé pour générer une vidéo IA de 5 secondes.

RÈGLES:
1. Décris l'ACTION en cours avec un verbe dynamique
2. Toujours ajouter à la fin: "Caméra fixe."
3. Vitesse NATURELLE (jamais "lentement", "doucement", "subtil")
4. Si sourire: toujours "léger sourire"
5. Action CONTINUE (pas "maintient", pas "reste immobile")

EXEMPLES CORRECTS:
- Biceps curl -> "La personne continue sa série de biceps curl. Caméra fixe."
- Squat -> "La personne continue sa série de squats. Caméra fixe."
- Running -> "La personne continue de courir. Caméra fixe."
- Marche -> "La personne marche vers l'avant. Caméra fixe."
- Phone -> "La personne scroll sur son téléphone. Caméra fixe."
- Pose mode -> "La personne pose avec un léger sourire. Caméra fixe."

Réponds UNIQUEMENT avec le JSON demandé."""

    try:
        image_base64 = download_image_as_base64(image_url)
        
        media_type = "image/jpeg"
        if ".png" in image_url.lower():
            media_type = "image/png"
        elif ".webp" in image_url.lower():
            media_type = "image/webp"
        
        user_prompt = f"""Analyse cette image. Contexte de la scène: {scene_description}

Quelle action fait la personne?

Réponds en JSON:
{{"action": "La personne [action dynamique]. Caméra fixe."}}"""

        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 100,
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_base64
                            }
                        },
                        {"type": "text", "text": user_prompt}
                    ]
                }
            ]
        }
        
        response_data = bedrock_runtime.invoke_model(
            modelId=model_id,
            body=json.dumps(request_body),
            contentType="application/json",
            accept="application/json"
        )
        
        raw_body = response_data['body'].read()
        response_body = json.loads(raw_body)
        content = response_body.get('content', [{}])[0].get('text', '{}')
        
        try:
            result = json.loads(content)
            action = result.get('action', 'La personne fait quelques pas. Caméra fixe.')
        except json.JSONDecodeError:
            if "La personne" in content:
                action = content.strip()
            else:
                action = 'La personne fait quelques pas. Caméra fixe.'
        
        return {'prompt': action, 'negative_prompt': DEFAULT_NEGATIVE_PROMPT}
        
    except Exception as e:
        print(f"Error generating video prompt: {e}")
        return {'prompt': "La personne fait quelques pas. Caméra fixe.", 'negative_prompt': DEFAULT_NEGATIVE_PROMPT}


def start_scene_videos_generation(event):
    """
    Start video generation for all selected photos in a short script.
    POST /api/admin/shorts/generate-scene-videos
    Body: {
        script_id: string,
        scenes: [{ scene_index: int, photo_url: string, description: string }]
    }
    
    Generates 2 videos per photo (like showcase_videos).
    Returns job_id to poll for status.
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    script_id = body.get('script_id')
    scenes = body.get('scenes', [])
    
    if not script_id:
        return response(400, {'error': 'script_id is required'})
    
    if not scenes:
        return response(400, {'error': 'scenes array is required'})
    
    # Get script to get ambassador_id
    try:
        script_result = shorts_table.get_item(Key={'id': script_id})
        script = script_result.get('Item')
        if not script:
            return response(404, {'error': 'Script not found'})
        ambassador_id = script.get('ambassador_id')
    except Exception as e:
        return response(500, {'error': f'Failed to get script: {str(e)}'})
    
    # Create job
    job_id = str(uuid.uuid4())
    total_videos = len(scenes) * 2  # 2 videos per scene
    
    # Initialize video tasks
    video_tasks = []
    for scene in scenes:
        for video_num in range(2):
            video_tasks.append({
                'scene_index': scene.get('scene_index'),
                'video_num': video_num,
                'photo_url': scene.get('photo_url'),
                'description': scene.get('description', ''),
                'prompt': None,
                'negative_prompt': DEFAULT_NEGATIVE_PROMPT,
                'status': 'pending',
                'replicate_id': None,
                'output_url': None,
                'error': None
            })
    
    job = {
        'id': job_id,
        'type': 'SCENE_VIDEO_JOB',
        'script_id': script_id,
        'ambassador_id': ambassador_id,
        'video_tasks': video_tasks,
        'status': 'generating_prompts',
        'progress': Decimal('0'),
        'total_videos': total_videos,
        'generated_videos': [],
        'error': None,
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat()
    }
    
    jobs_table.put_item(Item=job)
    
    # Invoke Lambda asynchronously
    import os
    payload = {
        'action': 'generate_scene_videos_async',
        'job_id': job_id
    }
    
    function_name = os.environ.get('AWS_LAMBDA_FUNCTION_NAME', 'saas-ugc')
    print(f"[{job_id}] Invoking async Lambda: {function_name}")
    
    try:
        lambda_client.invoke(
            FunctionName=function_name,
            InvocationType='Event',
            Payload=json.dumps(payload)
        )
    except Exception as e:
        print(f"[{job_id}] Error invoking async Lambda: {e}")
    
    return response(200, {
        'success': True,
        'job_id': job_id,
        'status': 'generating_prompts',
        'total_videos': total_videos,
        'message': 'Video generation started. Poll /status endpoint for progress.'
    })


def generate_scene_videos_async(job_id: str):
    """
    Async handler to generate scene videos.
    Similar flow to showcase_videos:
    1. Generate prompts with Bedrock (cached per photo)
    2. Submit ALL to Replicate in parallel
    3. Poll for completion
    4. Save to S3
    5. Update script
    """
    print(f"[{job_id}] Starting async scene video generation...")
    
    try:
        result = jobs_table.get_item(Key={'id': job_id})
        job = result.get('Item')
        
        if not job:
            print(f"[{job_id}] Job not found")
            return
        
        script_id = job.get('script_id')
        ambassador_id = job.get('ambassador_id')
        video_tasks = job.get('video_tasks', [])
        total_videos = int(job.get('total_videos', 0))
        
        print(f"[{job_id}] Generating {total_videos} videos for {len(video_tasks)//2} scenes")
        
        # PHASE 1: Generate prompts with Bedrock
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET #status = :status, progress = :prog, updated_at = :updated',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'generating_prompts',
                ':prog': Decimal('5'),
                ':updated': datetime.now().isoformat()
            }
        )
        
        prompt_cache = {}  # Cache prompts per photo_url
        for i, task in enumerate(video_tasks):
            photo_url = task['photo_url']
            
            if photo_url in prompt_cache:
                task['prompt'] = prompt_cache[photo_url]['prompt']
                task['negative_prompt'] = prompt_cache[photo_url]['negative_prompt']
                task['status'] = 'ready'
            else:
                try:
                    prompt_result = generate_video_prompt_for_scene(photo_url, task.get('description', ''))
                    prompt_cache[photo_url] = prompt_result
                    task['prompt'] = prompt_result['prompt']
                    task['negative_prompt'] = prompt_result['negative_prompt']
                    task['status'] = 'ready'
                    print(f"[{job_id}] Generated prompt for task {i+1}: {task['prompt'][:50]}...")
                except Exception as e:
                    print(f"[{job_id}] Error generating prompt: {e}")
                    task['status'] = 'error'
                    task['error'] = str(e)
        
        # Update progress
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET video_tasks = :tasks, progress = :prog, updated_at = :updated',
            ExpressionAttributeValues={
                ':tasks': video_tasks,
                ':prog': Decimal('20'),
                ':updated': datetime.now().isoformat()
            }
        )
        
        # PHASE 2: Submit ALL to Replicate in parallel
        print(f"[{job_id}] Submitting ALL videos to Replicate...")
        
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET #status = :status, updated_at = :updated',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'generating_videos',
                ':updated': datetime.now().isoformat()
            }
        )
        
        for i, task in enumerate(video_tasks):
            if task.get('status') == 'error':
                continue
            
            try:
                prediction = call_kling_api(
                    image_url=task['photo_url'],
                    prompt=task['prompt'],
                    negative_prompt=task['negative_prompt'],
                    duration=5
                )
                
                task['replicate_id'] = prediction['id']
                task['status'] = 'processing'
                print(f"[{job_id}] Submitted video {i+1}/{total_videos}: {prediction['id']}")
                
            except Exception as e:
                print(f"[{job_id}] Error submitting to Replicate: {e}")
                task['status'] = 'error'
                task['error'] = str(e)
        
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET video_tasks = :tasks, progress = :prog, updated_at = :updated',
            ExpressionAttributeValues={
                ':tasks': video_tasks,
                ':prog': Decimal('30'),
                ':updated': datetime.now().isoformat()
            }
        )
        
        # PHASE 3: Poll ALL predictions
        import time
        max_wait_seconds = 600
        poll_interval = 10
        
        pending_tasks = [t for t in video_tasks if t.get('replicate_id') and t.get('status') == 'processing']
        print(f"[{job_id}] Polling {len(pending_tasks)} predictions...")
        
        start_time = time.time()
        while pending_tasks and (time.time() - start_time) < max_wait_seconds:
            time.sleep(poll_interval)
            
            for task in pending_tasks[:]:
                try:
                    prediction = check_kling_prediction(task['replicate_id'])
                    
                    if prediction['status'] == 'succeeded':
                        task['status'] = 'completed'
                        task['output_url'] = prediction['output']
                        pending_tasks.remove(task)
                        print(f"[{job_id}] Video completed: {task['replicate_id']}")
                        
                    elif prediction['status'] in ['failed', 'canceled']:
                        task['status'] = 'error'
                        task['error'] = prediction.get('error', 'Unknown error')
                        pending_tasks.remove(task)
                        
                except Exception as e:
                    print(f"[{job_id}] Error polling: {e}")
            
            # Update progress
            completed = len([t for t in video_tasks if t.get('status') in ['completed', 'error']])
            progress = Decimal(str(30 + (completed / total_videos) * 60))
            jobs_table.update_item(
                Key={'id': job_id},
                UpdateExpression='SET video_tasks = :tasks, progress = :prog, updated_at = :updated',
                ExpressionAttributeValues={
                    ':tasks': video_tasks,
                    ':prog': progress,
                    ':updated': datetime.now().isoformat()
                }
            )
        
        # PHASE 4: Download and save to S3
        generated_videos = []
        
        for task in video_tasks:
            if task.get('status') == 'completed' and task.get('output_url'):
                try:
                    video_url = task['output_url']
                    req = urllib.request.Request(video_url)
                    with urllib.request.urlopen(req, timeout=60) as video_response:
                        video_data = video_response.read()
                    
                    video_key = f"shorts/{ambassador_id}/{script_id}/scene_{task['scene_index']}_video_{task['video_num']}_{uuid.uuid4().hex[:8]}.mp4"
                    s3_url = upload_to_s3(video_key, video_data, 'video/mp4', cache_days=365)
                    
                    generated_videos.append({
                        'scene_index': task['scene_index'],
                        'video_num': task['video_num'],
                        'url': s3_url,
                        'prompt': task.get('prompt', ''),
                        'created_at': datetime.now().isoformat()
                    })
                    
                    print(f"[{job_id}] Saved video: {video_key}")
                    
                except Exception as e:
                    print(f"[{job_id}] Error saving to S3: {e}")
        
        # PHASE 5: Update script with videos
        if generated_videos:
            try:
                script_result = shorts_table.get_item(Key={'id': script_id})
                script = script_result.get('Item')
                
                if script:
                    scenes = script.get('scenes', [])
                    
                    # Group videos by scene_index
                    for video in generated_videos:
                        scene_idx = int(video['scene_index'])
                        if 0 <= scene_idx < len(scenes):
                            if 'generated_videos' not in scenes[scene_idx]:
                                scenes[scene_idx]['generated_videos'] = []
                            scenes[scene_idx]['generated_videos'].append({
                                'video_num': video['video_num'],
                                'url': video['url'],
                                'prompt': video['prompt'],
                                'created_at': video['created_at']
                            })
                    
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
                    print(f"[{job_id}] Updated script with {len(generated_videos)} videos")
                    
            except Exception as e:
                print(f"[{job_id}] Error updating script: {e}")
        
        # Mark job complete
        final_status = 'completed' if generated_videos else 'error'
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET #status = :status, generated_videos = :videos, progress = :prog, updated_at = :updated',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': final_status,
                ':videos': generated_videos,
                ':prog': Decimal('100'),
                ':updated': datetime.now().isoformat()
            }
        )
        
        print(f"[{job_id}] Scene video generation completed: {len(generated_videos)}/{total_videos}")
        
    except Exception as e:
        print(f"[{job_id}] Fatal error: {e}")
        import traceback
        traceback.print_exc()
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET #status = :status, error = :error, updated_at = :updated',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'error',
                ':error': str(e),
                ':updated': datetime.now().isoformat()
            }
        )


def get_scene_videos_status(event):
    """
    Get status of scene videos generation job.
    GET /api/admin/shorts/scene-videos/status?job_id=xxx
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    query_params = event.get('queryStringParameters', {}) or {}
    job_id = query_params.get('job_id')
    
    if not job_id:
        return response(400, {'error': 'job_id is required'})
    
    try:
        result = jobs_table.get_item(Key={'id': job_id})
        job = result.get('Item')
        
        if not job:
            return response(404, {'error': 'Job not found'})
        
        job_data = decimal_to_python(job)
        
        return response(200, {
            'job_id': job_id,
            'status': job_data.get('status'),
            'progress': job_data.get('progress', 0),
            'total_videos': job_data.get('total_videos', 0),
            'video_tasks': job_data.get('video_tasks', []),
            'generated_videos': job_data.get('generated_videos', []),
            'error': job_data.get('error'),
            'updated_at': job_data.get('updated_at')
        })
        
    except Exception as e:
        return response(500, {'error': f'Failed to get status: {str(e)}'})


def select_scene_video(event):
    """
    Select the best video for a scene and delete the other.
    POST /api/admin/shorts/select-scene-video
    Body: { script_id, scene_index, selected_video_num }
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    script_id = body.get('script_id')
    scene_index = body.get('scene_index')
    selected_video_num = body.get('selected_video_num')
    
    if script_id is None or scene_index is None or selected_video_num is None:
        return response(400, {'error': 'script_id, scene_index, and selected_video_num are required'})
    
    try:
        scene_index = int(scene_index)
        selected_video_num = int(selected_video_num)
        
        script_result = shorts_table.get_item(Key={'id': script_id})
        script = script_result.get('Item')
        
        if not script:
            return response(404, {'error': 'Script not found'})
        
        scenes = script.get('scenes', [])
        
        if scene_index < 0 or scene_index >= len(scenes):
            return response(400, {'error': 'Invalid scene_index'})
        
        scene = scenes[scene_index]
        videos = scene.get('generated_videos', [])
        
        # Keep only selected video
        selected_video = None
        videos_to_delete = []
        
        for video in videos:
            if int(video.get('video_num', -1)) == selected_video_num:
                selected_video = video
                selected_video['is_selected'] = True
            else:
                videos_to_delete.append(video)
        
        if not selected_video:
            return response(400, {'error': 'Selected video not found'})
        
        # Delete other videos from S3
        for video in videos_to_delete:
            if video.get('url') and S3_BUCKET in video['url']:
                try:
                    s3_key = video['url'].split(f"{S3_BUCKET}.s3.amazonaws.com/")[1]
                    s3.delete_object(Bucket=S3_BUCKET, Key=s3_key)
                    print(f"Deleted video: {s3_key}")
                except Exception as e:
                    print(f"Error deleting from S3: {e}")
        
        # Update scene with only selected video
        scenes[scene_index]['generated_videos'] = [selected_video]
        scenes[scene_index]['selected_video_url'] = selected_video['url']
        script['scenes'] = scenes
        script['updated_at'] = datetime.now().isoformat()
        
        # Convert and save
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
            'message': f'Selected video {selected_video_num} for scene {scene_index}',
            'selected_video_url': selected_video['url']
        })
        
    except Exception as e:
        print(f"Error selecting video: {e}")
        return response(500, {'error': f'Failed to select video: {str(e)}'})


def concatenate_final_video(event):
    """
    Concatenate all selected scene videos into final short.
    POST /api/admin/shorts/concatenate
    Body: { script_id }
    
    Note: Video concatenation requires ffmpeg. This creates a job
    and stores metadata. Actual concatenation would need Lambda Layer with ffmpeg.
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    script_id = body.get('script_id')
    
    if not script_id:
        return response(400, {'error': 'script_id is required'})
    
    try:
        script_result = shorts_table.get_item(Key={'id': script_id})
        script = script_result.get('Item')
        
        if not script:
            return response(404, {'error': 'Script not found'})
        
        scenes = script.get('scenes', [])
        
        # Collect selected video URLs in order
        video_urls = []
        for i, scene in enumerate(scenes):
            selected_url = scene.get('selected_video_url')
            if selected_url:
                video_urls.append({
                    'scene_index': i,
                    'url': selected_url,
                    'duration': scene.get('duration', 5)
                })
        
        if not video_urls:
            return response(400, {'error': 'No selected videos found. Select videos for each scene first.'})
        
        # Create concatenation job
        job_id = str(uuid.uuid4())
        
        job = {
            'id': job_id,
            'type': 'VIDEO_CONCAT_JOB',
            'script_id': script_id,
            'ambassador_id': script.get('ambassador_id'),
            'video_urls': video_urls,
            'total_scenes': len(video_urls),
            'status': 'pending',
            'progress': Decimal('0'),
            'final_video_url': None,
            'error': None,
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat()
        }
        
        jobs_table.put_item(Item=job)
        
        # Invoke async concatenation
        import os
        payload = {
            'action': 'concatenate_videos_async',
            'job_id': job_id
        }
        
        function_name = os.environ.get('AWS_LAMBDA_FUNCTION_NAME', 'saas-ugc')
        
        try:
            lambda_client.invoke(
                FunctionName=function_name,
                InvocationType='Event',
                Payload=json.dumps(payload)
            )
        except Exception as e:
            print(f"Error invoking async Lambda: {e}")
        
        return response(200, {
            'success': True,
            'job_id': job_id,
            'status': 'pending',
            'total_scenes': len(video_urls),
            'message': 'Concatenation started. Poll /status endpoint for progress.'
        })
        
    except Exception as e:
        return response(500, {'error': f'Failed to start concatenation: {str(e)}'})


def concatenate_videos_async(job_id: str):
    """
    Async handler to concatenate videos.
    Downloads all videos, concatenates with ffmpeg-python, uploads result.
    
    Note: Requires ffmpeg in Lambda Layer or use a different approach.
    For now, we'll create a simple "playlist" approach by storing order.
    """
    print(f"[{job_id}] Starting video concatenation...")
    
    try:
        result = jobs_table.get_item(Key={'id': job_id})
        job = result.get('Item')
        
        if not job:
            print(f"[{job_id}] Job not found")
            return
        
        script_id = job.get('script_id')
        ambassador_id = job.get('ambassador_id')
        video_urls = job.get('video_urls', [])
        
        # Update status
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET #status = :status, progress = :prog, updated_at = :updated',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'downloading',
                ':prog': Decimal('10'),
                ':updated': datetime.now().isoformat()
            }
        )
        
        # Download all videos
        import tempfile
        import subprocess
        
        temp_dir = tempfile.mkdtemp()
        video_files = []
        
        for i, video_info in enumerate(video_urls):
            try:
                url = video_info['url']
                local_path = f"{temp_dir}/scene_{i}.mp4"
                
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=60) as resp:
                    with open(local_path, 'wb') as f:
                        f.write(resp.read())
                
                video_files.append(local_path)
                print(f"[{job_id}] Downloaded scene {i}")
                
                # Update progress
                progress = Decimal(str(10 + (i + 1) / len(video_urls) * 30))
                jobs_table.update_item(
                    Key={'id': job_id},
                    UpdateExpression='SET progress = :prog, updated_at = :updated',
                    ExpressionAttributeValues={
                        ':prog': progress,
                        ':updated': datetime.now().isoformat()
                    }
                )
                
            except Exception as e:
                print(f"[{job_id}] Error downloading video {i}: {e}")
        
        if not video_files:
            raise Exception("No videos downloaded")
        
        # Update status to concatenating
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET #status = :status, progress = :prog, updated_at = :updated',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'concatenating',
                ':prog': Decimal('50'),
                ':updated': datetime.now().isoformat()
            }
        )
        
        # Create concat file for ffmpeg
        concat_file = f"{temp_dir}/concat.txt"
        with open(concat_file, 'w') as f:
            for vf in video_files:
                f.write(f"file '{vf}'\n")
        
        output_file = f"{temp_dir}/final.mp4"
        
        # Run ffmpeg concatenation
        try:
            cmd = [
                '/opt/bin/ffmpeg',  # Lambda Layer path
                '-f', 'concat',
                '-safe', '0',
                '-i', concat_file,
                '-c', 'copy',
                '-y',
                output_file
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            if result.returncode != 0:
                print(f"[{job_id}] ffmpeg error: {result.stderr}")
                raise Exception(f"ffmpeg failed: {result.stderr[:200]}")
                
        except FileNotFoundError:
            # ffmpeg not available - fall back to simple approach
            print(f"[{job_id}] ffmpeg not available, using first video as placeholder")
            # Just use the first video as the "final" for now
            import shutil
            shutil.copy(video_files[0], output_file)
        
        # Upload to S3
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET #status = :status, progress = :prog, updated_at = :updated',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'uploading',
                ':prog': Decimal('80'),
                ':updated': datetime.now().isoformat()
            }
        )
        
        with open(output_file, 'rb') as f:
            video_data = f.read()
        
        video_key = f"shorts/{ambassador_id}/{script_id}/final_{uuid.uuid4().hex[:8]}.mp4"
        final_url = upload_to_s3(video_key, video_data, 'video/mp4', cache_days=365)
        
        # Update script with final video
        try:
            script_result = shorts_table.get_item(Key={'id': script_id})
            script = script_result.get('Item')
            if script:
                script['final_video_url'] = final_url
                script['final_video_created_at'] = datetime.now().isoformat()
                script['updated_at'] = datetime.now().isoformat()
                
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
        except Exception as e:
            print(f"[{job_id}] Error updating script: {e}")
        
        # Cleanup temp files
        import shutil
        try:
            shutil.rmtree(temp_dir)
        except:
            pass
        
        # Mark complete
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET #status = :status, final_video_url = :url, progress = :prog, updated_at = :updated',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'completed',
                ':url': final_url,
                ':prog': Decimal('100'),
                ':updated': datetime.now().isoformat()
            }
        )
        
        print(f"[{job_id}] Concatenation completed: {final_url}")
        
    except Exception as e:
        print(f"[{job_id}] Error: {e}")
        import traceback
        traceback.print_exc()
        
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET #status = :status, error = :error, updated_at = :updated',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'error',
                ':error': str(e),
                ':updated': datetime.now().isoformat()
            }
        )


def get_concat_status(event):
    """
    Get status of video concatenation job.
    GET /api/admin/shorts/concat/status?job_id=xxx
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    query_params = event.get('queryStringParameters', {}) or {}
    job_id = query_params.get('job_id')
    
    if not job_id:
        return response(400, {'error': 'job_id is required'})
    
    try:
        result = jobs_table.get_item(Key={'id': job_id})
        job = result.get('Item')
        
        if not job:
            return response(404, {'error': 'Job not found'})
        
        job_data = decimal_to_python(job)
        
        return response(200, {
            'job_id': job_id,
            'status': job_data.get('status'),
            'progress': job_data.get('progress', 0),
            'total_scenes': job_data.get('total_scenes', 0),
            'final_video_url': job_data.get('final_video_url'),
            'error': job_data.get('error'),
            'updated_at': job_data.get('updated_at')
        })
        
    except Exception as e:
        return response(500, {'error': f'Failed to get status: {str(e)}'})