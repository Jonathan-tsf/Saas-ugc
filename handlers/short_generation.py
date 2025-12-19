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

# DynamoDB table for shorts
shorts_table = dynamodb.Table('nano_banana_shorts')

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
    
    Returns ambassadors with their outfits count and description.
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
                'has_showcase_videos': len(amb.get('showcase_videos', [])) > 0
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


def generate_short_script(event):
    """
    Generate a TikTok short script for a specific ambassador.
    AI decides everything: number of scenes, duration, hashtags, etc.
    
    POST /api/admin/shorts/generate-script
    
    Body: {
        "ambassador_id": "uuid",
        "concept": "Optional theme or concept hint"  # Optional - AI can decide
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
    system_prompt = """Tu es un expert SENIOR en création de contenus TikTok pour le fitness et le lifestyle.
Tu génères des scripts de vidéos avec des durées PRÉCISES et RÉFLÉCHIES pour chaque scène.

TON RÔLE:
- Analyser le profil de l'ambassadeur (description, genre)
- Choisir le MEILLEUR concept de vidéo pour cet ambassadeur
- Décider du nombre de scènes optimal (4-8 scènes)
- CALCULER la durée de chaque scène selon son CONTENU (pas au hasard!)
- Choisir les hashtags tendances pertinents
- Assigner les bonnes tenues aux bonnes scènes

STYLE OBLIGATOIRE:
- Contenu AUTHENTIQUE style TikTok/créateur - PAS commercial/publicitaire
- Vibe genuine, relatable, "real life" mais AESTHETIC (jamais "messy", "dirty", etc.)
- Comme si filmé par l'ambassadrice elle-même
- Évite: "professional photo", "commercial", "brand ambassador", "high quality", "perfect lighting"

⚠️ DURÉES RÉFLÉCHIES - RÈGLES STRICTES (PAS AU HASARD!):

CHAQUE DURÉE DOIT CORRESPONDRE AU CONTENU DE LA SCÈNE:

📍 HOOKS/INTRO (capter l'attention):
- Réveil/ouvre les yeux → 1.5s (geste instantané)
- Regarde la caméra → 1s
- Texte qui apparaît → 2s (temps de lecture)
- Question posée → 2-2.5s

📍 PRÉPARATION/LIFESTYLE:
- Attrape son téléphone → 1.5s
- Check le téléphone/scroll → 2-3s (selon si on voit l'écran)
- Boit un café/shaker → 2s (une gorgée)
- S'habille (enfile un haut) → 2-3s
- Prépare son sac → 2.5s
- Se regarde dans le miroir → 2s

📍 MOUVEMENT/DÉPLACEMENT:
- Se lève du lit → 2s
- Marche vers la porte → 2s
- Entre dans la salle → 2s
- S'approche d'une machine → 2s

📍 WORKOUT/EXERCICES:
- 1-2 répétitions d'un exercice → 3s
- 2-3 répétitions → 4s
- Flexing/pose → 2s
- Préparation avant exercice → 2s

📍 TRANSITIONS:
- Cut simple → 0.5s
- Swipe/effet → 1s

📍 OUTRO:
- Thumbs up/smile → 1.5s
- Logo/CTA → 2s
- Dernier regard caméra → 1.5s

🚨 INTERDIT:
- 2s pour "réveil" (trop long! c'est 1-1.5s)
- 5s pour "marche" (trop long! c'est 2s)
- Durées identiques pour toutes les scènes (chaque scène a sa durée LOGIQUE)

RÈGLES POUR prompt_image (TRÈS IMPORTANT):
1. EN ANGLAIS
2. Format OBLIGATOIRE: "Put this person [action] in [lieu]. [mood/style]"
3. TOUJOURS commencer par "Put this person"
4. Max 20 mots total
5. Style TikTok aesthetic - JAMAIS "messy", "dirty", "cluttered"
6. INTERDIT: décrire la personne, son corps, ses cheveux, ses vêtements

EXEMPLES CORRECTS de prompt_image:
✅ "Put this person stretching in an aesthetic bedroom. Soft morning light, genuine vibe."
✅ "Put this person mixing a shaker in a clean kitchen. Focused energy."
✅ "Put this person walking into a gym entrance. Determined look."
✅ "Put this person doing squats at a squat rack. Intense focus."
✅ "Put this person checking outfit in a mirror. Confident smile."
✅ "Put this person flexing in a gym mirror. Proud post-workout glow."
✅ "Put this person giving thumbs up. Happy energetic vibe."

EXEMPLES INTERDITS:
❌ "aesthetic bedroom, stretching in bed" (manque "Put this person")
❌ "Professional photo of a fit female athlete..."
❌ "messy bedroom" (TikTok = aesthetic)

FORMAT: JSON uniquement, pas de texte avant/après."""

    concept_text = f"\n\nCONCEPT SUGGÉRÉ PAR L'UTILISATEUR: {concept}" if concept else ""

    user_prompt = f"""Génère un script TikTok/Reel pour cet ambassadeur:

AMBASSADEUR:
- Nom: {ambassador_name}
- Genre: {ambassador_gender}
- Description: {ambassador_description}

TENUES DISPONIBLES (tu DOIS utiliser ces IDs):
{outfits_text}
{concept_text}

DATE: {datetime.now().strftime('%d/%m/%Y')}

DÉCIDE TOI-MÊME:
- Le concept/thème de la vidéo
- Le nombre de scènes (entre 5 et 8 scènes - court et impactant)
- La durée totale (SOMME des durées = généralement 15-30 secondes)
- Les hashtags tendances (5-10)
- Comment utiliser au mieux les tenues

⚠️ AVANT DE GÉNÉRER, RÉFLÉCHIS:
Pour chaque scène, demande-toi: "Combien de temps dure réellement cette action dans la vraie vie?"
- Un réveil = instantané (1-1.5s)
- Une gorgée de café = 2s
- 2-3 squats = 3-4s
- Un pas vers la porte = 2s

Génère le JSON suivant:
{{
  "title": "Titre accrocheur du short",
  "concept": "Explication du concept choisi",
  "total_duration": <nombre en secondes - SOMME des durées de toutes les scènes>,
  "hashtags": ["#hashtag1", "#hashtag2", ...],
  "target_platform": "tiktok" ou "instagram" ou "both",
  "mood": "energetic/chill/motivational/aesthetic/funny",
  "music_suggestion": "Type de musique recommandé",
  "scenes": [
    {{
      "order": 1,
      "scene_type": "intro/workout/transition/lifestyle/pose/outro",
      "description": "Description courte de la scène",
      "duration": <DURÉE CALCULÉE selon le contenu - voir règles ci-dessus>,
      "duration_reasoning": "<Explique pourquoi cette durée: ex: 'ouvre les yeux = geste instantané = 1.5s'>",
      "prompt_image": "Put this person [action] in [lieu]. [mood/style] - TOUJOURS commencer par 'Put this person'",
      "prompt_video": "La personne [action dynamique]. Caméra fixe.",
      "outfit_id": "<ID de la tenue à utiliser>",
      "camera_angle": "close-up/medium/wide/pov",
      "transition_to_next": "cut/fade/swipe/none"
    }}
  ]
}}

⚠️ RAPPELS CRITIQUES:
1. prompt_image: TOUJOURS commencer par "Put this person", max 20 mots, style AESTHETIC
2. DURÉES: Chaque durée DOIT être justifiée par le contenu (pas de durées aléatoires!)
3. "réveil/ouvre les yeux" = 1-1.5s MAX (c'est instantané!)
4. "marche/déplacement" = 2s MAX
5. "exercice" = 3-4s pour montrer 2-3 reps
6. JAMAIS "messy", "professional photo", description physique de la personne
7. L'image de référence de la personne sera fournie à l'IA
8. total_duration = SOMME de toutes les durées de scènes"""

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
        
        # Validate scenes
        if 'scenes' not in script or not script['scenes']:
            raise Exception("No scenes generated")
        
        # Create outfit map for quick lookup
        outfit_map = {o['id']: o for o in outfits}
        
        # Enrich scenes with outfit details
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

