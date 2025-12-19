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
    dynamodb, bedrock_runtime, ambassadors_table, upload_to_s3
)
from handlers.gemini_client import generate_image

# DynamoDB tables
shorts_table = dynamodb.Table('nano_banana_shorts')
products_table = dynamodb.Table('products')

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
    system_prompt = """Tu es un CRÉATEUR TikTok expert. Tu crées des scripts vidéo UNIQUES à chaque fois.

🎯 TON OBJECTIF:
Créer un script vidéo TikTok ORIGINAL et CRÉATIF pour cet ambassadeur.
Chaque script doit être DIFFÉRENT - ne répète JAMAIS le même concept.

🧠 TU DÉCIDES TOUT TOI-MÊME:
1. Le CONCEPT unique de la vidéo (sois créatif! pas toujours "morning routine")
2. Le NOMBRE de scènes (adapté au concept)
3. La DURÉE de chaque scène (selon ce qui se passe dedans)
4. La DURÉE TOTALE (selon le concept - peut être 15s ou 45s)
5. Le RYTHME (rapide? lent? mix?)
6. Les TRANSITIONS
7. L'AMBIANCE

💡 RÉFLÉCHIS À LA DURÉE DE CHAQUE SCÈNE:
Pour chaque scène, demande-toi: "Combien de temps FAUT-IL pour que cette action soit claire et impactante?"
- Une action RAPIDE (clap, wink, jump cut) = courte durée
- Une action LENTE (exercice complet, préparation) = plus longue durée  
- Un moment ÉMOTIONNEL (flex, regard caméra) = laisser respirer
- C'est TOI qui décides, pas une règle fixe!

🎨 STYLE:
- Authentique TikTok/créateur - PAS pub/commercial
- AESTHETIC (jamais "messy", "dirty")
- Comme filmé par l'ambassadrice elle-même

📝 RÈGLES prompt_image:
1. EN ANGLAIS
2. Commence TOUJOURS par "Put this person"
3. Format: "Put this person [action] in [lieu]. [mood]"
4. Max 20 mots
5. JAMAIS décrire la personne physiquement

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

🛍️ PRODUIT À INTÉGRER (SUBTIL & NATUREL):
- Produit: {product_name}
- Marque: {product_brand}
- Catégorie: {product_category}
- Description: {product_description}

⚡ IMPORTANT PRODUIT:
- Intègre ce produit de façon NATURELLE et AUTHENTIQUE
- PAS de placement produit forcé ou commercial
- L'ambassadrice doit utiliser le produit comme si c'était son choix personnel
- Exemples d'intégration naturelle:
  • Prendre une gorgée de sa boisson/shake entre exercices
  • Vérifier ses stats sur sa montre connectée
  • Appliquer une crème/spray naturellement
  • Porter/utiliser l'équipement comme partie de sa routine
- Le produit doit apparaître dans 1-2 scènes MAX, pas partout
- Mentionne le produit dans prompt_image quand il apparaît (ex: "Put this person drinking from a protein shaker...")
- Ajoute "product_placement": true pour les scènes où le produit apparaît"""

    user_prompt = f"""Crée un script TikTok UNIQUE pour:

👤 AMBASSADEUR:
- Nom: {ambassador_name}
- Genre: {ambassador_gender}  
- Description: {ambassador_description}

👕 TENUES DISPONIBLES:
{outfits_text}
{concept_text}{product_text}

📅 Date: {datetime.now().strftime('%d/%m/%Y')}

🎬 SOIS CRÉATIF! Décide:
- Un concept ORIGINAL (pas toujours morning routine!)
- Le nombre de scènes qui convient
- La durée de chaque scène selon son contenu
- Le rythme global (rapide? posé? crescendo?)

Génère ce JSON:
{{
  "title": "Titre accrocheur",
  "concept": "Ton concept créatif expliqué",
  "total_duration": <durée totale que TU choisis>,
  "hashtags": ["#...", ...],
  "target_platform": "tiktok/instagram/both",
  "mood": "energetic/chill/motivational/aesthetic/funny/intense",
  "music_suggestion": "Type de musique qui irait bien",
  "product_id": "{product_id if product else 'null'}",
  "scenes": [
    {{
      "order": 1,
      "scene_type": "intro/workout/transition/lifestyle/pose/outro",
      "description": "Ce qui se passe",
      "duration": <durée en secondes - TU décides selon le contenu>,
      "prompt_image": "Put this person [action] in [lieu]. [mood]",
      "prompt_video": "La personne [action]. Caméra fixe.",
      "outfit_id": "<ID tenue>",
      "camera_angle": "close-up/medium/wide/pov",
      "transition_to_next": "cut/fade/swipe/none",
      "product_placement": false  // true si le produit apparaît dans cette scène
    }}
  ]
}}

⚠️ RÈGLES:
1. prompt_image: TOUJOURS "Put this person...", AESTHETIC, max 20 mots
2. Chaque vidéo doit être DIFFÉRENTE et CRÉATIVE
3. Les durées doivent avoir du SENS par rapport au contenu"""

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


def generate_scene_photos(event):
    """
    Generate 2 photos for a scene using Nano Banana Pro (Gemini 3 Pro Image)
    
    POST body:
    {
        "script_id": "uuid",
        "scene_index": 0,
        "outfit_image_url": "https://s3...jpg"  # The ambassador outfit image to use as reference
    }
    
    Returns:
    {
        "success": True,
        "scene_photos": [
            {"url": "https://s3...", "index": 0},
            {"url": "https://s3...", "index": 1}
        ]
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
        # Get the script to get the scene's prompt_image
        result = shorts_table.get_item(Key={'id': script_id})
        script = result.get('Item')
        
        if not script:
            return response(404, {'error': 'Script not found'})
        
        scenes = script.get('scenes', [])
        if scene_index < 0 or scene_index >= len(scenes):
            return response(400, {'error': 'Invalid scene_index'})
        
        scene = scenes[scene_index]
        scene_prompt = scene.get('prompt_image', 'Put this person in an aesthetic room, casual pose, relaxed vibe.')
        
        # Download the outfit image as base64
        print(f"Downloading outfit image: {outfit_image_url}")
        outfit_base64 = download_image_as_base64(outfit_image_url)
        
        if not outfit_base64:
            return response(500, {'error': 'Failed to download outfit image'})
        
        # Build the full prompt for Nano Banana Pro
        # The scene_prompt should already start with "Put this person..."
        # Just add the technical requirements
        if scene_prompt.lower().startswith('put this person'):
            full_prompt = f"{scene_prompt} Keep exact same face, body and clothes. TikTok aesthetic, natural lighting, 9:16 vertical."
        else:
            # Fallback if old format without "Put this person"
            full_prompt = f"Put this person {scene_prompt}. Keep exact same face, body and clothes. TikTok aesthetic, natural lighting, 9:16 vertical."
        
        print(f"Generating 2 photos for scene {scene_index} with prompt: {full_prompt[:100]}...")
        
        # Generate 2 photos
        scene_photos = []
        ambassador_id = script.get('ambassador_id', 'unknown')
        
        for photo_index in range(2):
            try:
                print(f"Generating photo {photo_index + 1}/2...")
                
                # Call Gemini to generate image with reference
                image_base64 = generate_image(
                    prompt=full_prompt,
                    reference_images=[outfit_base64],
                    image_size="2K"  # High quality for TikTok
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
                    else:
                        print(f"Failed to upload photo {photo_index + 1} to S3")
                else:
                    print(f"Failed to generate photo {photo_index + 1}")
                    
            except Exception as e:
                print(f"Error generating photo {photo_index + 1}: {e}")
                continue
        
        if not scene_photos:
            return response(500, {'error': 'Failed to generate any photos'})
        
        # Update the scene with the generated photos
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
        
        return response(200, {
            'success': True,
            'scene_photos': scene_photos,
            'message': f'Generated {len(scene_photos)} photos for scene {scene_index}'
        })
        
    except Exception as e:
        print(f"Error generating scene photos: {e}")
        import traceback
        traceback.print_exc()
        return response(500, {'error': f'Failed to generate scene photos: {str(e)}'})

