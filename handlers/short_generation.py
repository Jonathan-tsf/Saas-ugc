"""
TikTok Short Generation Handlers
Generates scripted scenes for TikTok shorts using AWS Bedrock Claude
"""
import json
import uuid
from datetime import datetime
from decimal import Decimal

from config import (
    response, decimal_to_python, verify_admin,
    dynamodb, bedrock_runtime
)

# DynamoDB tables
shorts_table = dynamodb.Table('nano_banana_shorts')
outfits_table = dynamodb.Table('outfits')
ambassadors_table = dynamodb.Table('ambassadors')

# AWS Bedrock Claude model for scripting
BEDROCK_MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

# Scene types for TikTok shorts
SCENE_TYPES = [
    {
        "id": "intro",
        "name": "Intro / Hook",
        "description": "Accroche initiale pour capter l'attention",
        "typical_duration": 3,
        "examples": ["Regard caméra intense", "Transformation reveal", "Question choc"]
    },
    {
        "id": "workout",
        "name": "Workout / Sport",
        "description": "Exercices et mouvements sportifs",
        "typical_duration": 5,
        "examples": ["Squat", "Biceps curl", "Running", "Jumping", "Stretching"]
    },
    {
        "id": "transition",
        "name": "Transition",
        "description": "Changement de scène fluide",
        "typical_duration": 2,
        "examples": ["Marche vers caméra", "Rotation", "Jump cut pose"]
    },
    {
        "id": "lifestyle",
        "name": "Lifestyle",
        "description": "Moments de vie quotidienne",
        "typical_duration": 4,
        "examples": ["Phone check", "Mirror selfie", "Coffee moment", "Getting ready"]
    },
    {
        "id": "pose",
        "name": "Pose / Flex",
        "description": "Pose statique ou semi-dynamique",
        "typical_duration": 3,
        "examples": ["Mirror flex", "Confident stance", "Product showcase"]
    },
    {
        "id": "outro",
        "name": "Outro / CTA",
        "description": "Conclusion avec call-to-action",
        "typical_duration": 3,
        "examples": ["Wave goodbye", "Point to bio", "Logo reveal"]
    }
]

# Current trends context (updated manually or via API)
CURRENT_TRENDS = {
    "date": "2024-12",
    "trends": [
        {"name": "Get Ready With Me (GRWM)", "hashtag": "#grwm", "style": "Casual lifestyle, morning routine"},
        {"name": "Outfit Check", "hashtag": "#outfitcheck", "style": "Quick outfit reveal, confidence"},
        {"name": "Gym Progress", "hashtag": "#gymtok", "style": "Before/after, workout clips"},
        {"name": "Day in My Life", "hashtag": "#dayinmylife", "style": "Lifestyle montage"},
        {"name": "Slow Mo Reveal", "hashtag": "#slowmo", "style": "Dramatic slow motion entrance"},
        {"name": "POV Videos", "hashtag": "#pov", "style": "First person perspective scenarios"},
    ]
}


def get_scene_types(event):
    """
    Get available scene types for short generation.
    GET /api/admin/shorts/scene-types
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    return response(200, {
        'success': True,
        'scene_types': SCENE_TYPES,
        'trends': CURRENT_TRENDS
    })


def get_outfits_for_short(event):
    """
    Get available outfits for short generation.
    GET /api/admin/shorts/outfits?gender=female
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    params = event.get('queryStringParameters', {}) or {}
    gender = params.get('gender', 'female')
    
    try:
        # Scan outfits filtered by gender
        result = outfits_table.scan()
        outfits = result.get('Items', [])
        
        # Filter by gender (include unisex)
        filtered = [o for o in outfits if o.get('gender') in [gender, 'unisex']]
        
        # Sort by type for organization
        filtered.sort(key=lambda x: (x.get('type', ''), x.get('created_at', '')))
        
        return response(200, {
            'success': True,
            'outfits': decimal_to_python(filtered),
            'count': len(filtered)
        })
        
    except Exception as e:
        print(f"Error getting outfits: {e}")
        return response(500, {'error': f'Failed to get outfits: {str(e)}'})


def generate_short_script(event):
    """
    Generate a TikTok short script using AWS Bedrock Claude.
    POST /api/admin/shorts/generate-script
    
    Body: {
        "theme": "Gym motivation",
        "total_duration": 30,
        "gender": "female",
        "style": "energetic",
        "num_scenes": 5,
        "outfit_ids": ["outfit1", "outfit2"],  # Optional: specific outfits to use
        "existing_scenes": [],  # Previous scenes to avoid duplicates
        "trends_to_use": ["#gymtok", "#grwm"]  # Optional: specific trends
    }
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    theme = body.get('theme', 'Sport lifestyle')
    total_duration = body.get('total_duration', 30)
    gender = body.get('gender', 'female')
    style = body.get('style', 'dynamic')
    num_scenes = body.get('num_scenes', 5)
    outfit_ids = body.get('outfit_ids', [])
    existing_scenes = body.get('existing_scenes', [])
    trends_to_use = body.get('trends_to_use', [])
    
    # Get available outfits
    try:
        result = outfits_table.scan()
        all_outfits = result.get('Items', [])
        
        # Filter by gender
        available_outfits = [o for o in all_outfits if o.get('gender') in [gender, 'unisex']]
        
        # If specific outfits requested, filter to those
        if outfit_ids:
            available_outfits = [o for o in available_outfits if o.get('id') in outfit_ids]
        
        # Format outfits for prompt
        outfits_desc = []
        for o in available_outfits:
            outfits_desc.append(f"- ID: {o['id']} | Type: {o.get('type', 'Unknown')} | Description: {o.get('description', 'No description')}")
        
        outfits_text = "\n".join(outfits_desc) if outfits_desc else "Aucune tenue spécifique disponible"
        
    except Exception as e:
        print(f"Error loading outfits: {e}")
        outfits_text = "Erreur lors du chargement des tenues"
    
    # Format existing scenes to avoid duplicates
    existing_scenes_text = ""
    if existing_scenes:
        existing_scenes_text = "\n\nSCÈNES DÉJÀ GÉNÉRÉES (NE PAS RÉPÉTER):\n"
        for scene in existing_scenes:
            existing_scenes_text += f"- {scene.get('description', 'Scene sans description')}\n"
    
    # Format trends
    trends_text = ""
    if trends_to_use:
        trends_text = f"\n\nTENDANCES À INTÉGRER: {', '.join(trends_to_use)}"
    else:
        # Include all current trends as context
        trends_list = [f"{t['name']} ({t['hashtag']})" for t in CURRENT_TRENDS['trends']]
        trends_text = f"\n\nTENDANCES ACTUELLES (pour inspiration): {', '.join(trends_list)}"
    
    # Scene types for reference
    scene_types_ref = "\n".join([f"- {s['id']}: {s['name']} ({s['typical_duration']}s) - {s['description']}" for s in SCENE_TYPES])
    
    # Build the prompt for Claude
    system_prompt = """Tu es un expert en création de contenus TikTok viraux pour le fitness et le lifestyle.
Tu génères des scripts de vidéos courts (shorts) avec des scènes précises.

RÈGLES IMPORTANTES:
1. Chaque scène DOIT avoir: description, durée, type, prompt_image, prompt_video, outfit_id
2. Le prompt_image doit être en ANGLAIS, optimisé pour Gemini 3 Pro (génération d'image)
3. Le prompt_video doit être en FRANÇAIS, court et dynamique pour Kling AI
4. Les durées doivent s'additionner pour atteindre la durée totale demandée
5. Varier les types de scènes pour un montage dynamique
6. Associer une tenue appropriée à chaque scène

FORMAT DE SORTIE: JSON valide uniquement, pas de texte avant ou après."""

    user_prompt = f"""Génère un script TikTok avec les paramètres suivants:

THÈME: {theme}
DURÉE TOTALE: {total_duration} secondes
GENRE AMBASSADEUR: {gender}
STYLE: {style}
NOMBRE DE SCÈNES: {num_scenes}

TYPES DE SCÈNES DISPONIBLES:
{scene_types_ref}

TENUES DISPONIBLES:
{outfits_text}
{existing_scenes_text}
{trends_text}

DATE ACTUELLE: {datetime.now().strftime('%d/%m/%Y')}

Génère exactement {num_scenes} scènes au format JSON suivant:
{{
  "title": "Titre accrocheur du short",
  "total_duration": {total_duration},
  "hashtags": ["#hashtag1", "#hashtag2"],
  "scenes": [
    {{
      "order": 1,
      "scene_type": "intro",
      "description": "Description courte de la scène",
      "duration": 3,
      "prompt_image": "Professional photo of a fit {gender} athlete in [outfit], [action], [lighting], [style], high quality, 9:16 portrait",
      "prompt_video": "La personne [action dynamique]. Caméra fixe.",
      "outfit_id": "ID de la tenue à utiliser",
      "outfit_type": "Sport"
    }}
  ]
}}

IMPORTANT:
- prompt_image en ANGLAIS, très détaillé pour génération d'image
- prompt_video en FRANÇAIS, format Kling (action simple + Caméra fixe)
- Utiliser les outfit_id des tenues disponibles
- Varier les scènes pour un contenu engageant"""

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
        
        print(f"Calling Bedrock for short script generation...")
        
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
        # Find JSON in response (in case there's extra text)
        json_start = content.find('{')
        json_end = content.rfind('}') + 1
        
        if json_start != -1 and json_end > json_start:
            json_str = content[json_start:json_end]
            script = json.loads(json_str)
        else:
            raise Exception("No valid JSON found in response")
        
        # Validate and enrich script
        script['id'] = str(uuid.uuid4())
        script['created_at'] = datetime.now().isoformat()
        script['status'] = 'draft'
        script['gender'] = gender
        script['theme'] = theme
        script['style'] = style
        
        # Validate scenes
        if 'scenes' not in script or not script['scenes']:
            raise Exception("No scenes generated")
        
        # Enrich scenes with outfit details
        outfit_map = {o['id']: o for o in available_outfits}
        for scene in script['scenes']:
            outfit_id = scene.get('outfit_id')
            if outfit_id and outfit_id in outfit_map:
                outfit = outfit_map[outfit_id]
                scene['outfit_description'] = outfit.get('description', '')
                scene['outfit_image_url'] = outfit.get('image_url', '')
            scene['id'] = str(uuid.uuid4())
            scene['status'] = 'pending'  # pending, generating, completed, error
        
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
        "script": { ... full script ... },
        "scene_index": 2,
        "feedback": "Make it more energetic"
    }
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    script = body.get('script', {})
    scene_index = body.get('scene_index')
    feedback = body.get('feedback', '')
    
    if not script or scene_index is None:
        return response(400, {'error': 'script and scene_index are required'})
    
    scenes = script.get('scenes', [])
    if scene_index < 0 or scene_index >= len(scenes):
        return response(400, {'error': 'Invalid scene_index'})
    
    current_scene = scenes[scene_index]
    
    # Get outfits for context
    try:
        result = outfits_table.scan()
        all_outfits = result.get('Items', [])
        gender = script.get('gender', 'female')
        available_outfits = [o for o in all_outfits if o.get('gender') in [gender, 'unisex']]
        outfits_desc = [f"- ID: {o['id']} | {o.get('type', '')} | {o.get('description', '')}" for o in available_outfits]
        outfits_text = "\n".join(outfits_desc)
    except:
        outfits_text = "Tenues non disponibles"
    
    # Build context from other scenes
    other_scenes = [s for i, s in enumerate(scenes) if i != scene_index]
    other_scenes_text = "\n".join([f"- Scène {s['order']}: {s['description']}" for s in other_scenes])
    
    system_prompt = """Tu régénères UNE seule scène d'un script TikTok.
Garde la cohérence avec les autres scènes.
Retourne UNIQUEMENT le JSON de la nouvelle scène."""

    user_prompt = f"""SCRIPT ACTUEL:
Titre: {script.get('title', '')}
Thème: {script.get('theme', '')}
Style: {script.get('style', '')}
Genre: {script.get('gender', '')}

AUTRES SCÈNES (pour cohérence):
{other_scenes_text}

SCÈNE À RÉGÉNÉRER (index {scene_index}):
{json.dumps(current_scene, indent=2)}

FEEDBACK UTILISATEUR: {feedback if feedback else "Pas de feedback spécifique"}

TENUES DISPONIBLES:
{outfits_text}

Génère une nouvelle version de cette scène au format:
{{
  "order": {current_scene.get('order', scene_index + 1)},
  "scene_type": "type",
  "description": "Description",
  "duration": X,
  "prompt_image": "English prompt for Gemini 3 Pro...",
  "prompt_video": "Prompt français pour Kling...",
  "outfit_id": "ID tenue",
  "outfit_type": "Type"
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
            raise Exception("No valid JSON in response")
        
        # Preserve scene ID and add metadata
        new_scene['id'] = current_scene.get('id', str(uuid.uuid4()))
        new_scene['status'] = 'pending'
        new_scene['regenerated_at'] = datetime.now().isoformat()
        
        # Enrich with outfit details
        outfit_id = new_scene.get('outfit_id')
        if outfit_id:
            for o in available_outfits:
                if o['id'] == outfit_id:
                    new_scene['outfit_description'] = o.get('description', '')
                    new_scene['outfit_image_url'] = o.get('image_url', '')
                    break
        
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
    
    Body: { "script": { ... full script ... } }
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
    
    # Ensure ID exists
    if 'id' not in script:
        script['id'] = str(uuid.uuid4())
    
    script['updated_at'] = datetime.now().isoformat()
    if 'created_at' not in script:
        script['created_at'] = script['updated_at']
    
    try:
        # Convert floats to Decimal for DynamoDB
        def float_to_decimal(obj):
            if isinstance(obj, float):
                return Decimal(str(obj))
            elif isinstance(obj, dict):
                return {k: float_to_decimal(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [float_to_decimal(i) for i in obj]
            return obj
        
        script_db = float_to_decimal(script)
        shorts_table.put_item(Item=script_db)
        
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
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        result = shorts_table.scan()
        scripts = result.get('Items', [])
        
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
    Get a single short script by ID.
    GET /api/admin/shorts/{id}
    """
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    path_params = event.get('pathParameters', {}) or {}
    script_id = path_params.get('id')
    
    if not script_id:
        return response(400, {'error': 'Script ID is required'})
    
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
    
    path_params = event.get('pathParameters', {}) or {}
    script_id = path_params.get('id')
    
    if not script_id:
        return response(400, {'error': 'Script ID is required'})
    
    try:
        shorts_table.delete_item(Key={'id': script_id})
        
        return response(200, {
            'success': True,
            'message': 'Script deleted successfully'
        })
        
    except Exception as e:
        print(f"Error deleting script: {e}")
        return response(500, {'error': f'Failed to delete script: {str(e)}'})


def update_scene_manually(event):
    """
    Update a scene manually (edit prompt, duration, etc.).
    PUT /api/admin/shorts/scene
    
    Body: {
        "script_id": "xxx",
        "scene_index": 2,
        "updates": {
            "duration": 5,
            "prompt_video": "New prompt",
            ...
        }
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
    updates = body.get('updates', {})
    
    if not script_id or scene_index is None:
        return response(400, {'error': 'script_id and scene_index are required'})
    
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
        for key, value in updates.items():
            if key not in ['id']:  # Protect ID
                scenes[scene_index][key] = value
        
        scenes[scene_index]['updated_at'] = datetime.now().isoformat()
        script['scenes'] = scenes
        script['updated_at'] = datetime.now().isoformat()
        
        # Save back to DB
        def float_to_decimal(obj):
            if isinstance(obj, float):
                return Decimal(str(obj))
            elif isinstance(obj, dict):
                return {k: float_to_decimal(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [float_to_decimal(i) for i in obj]
            return obj
        
        shorts_table.put_item(Item=float_to_decimal(script))
        
        return response(200, {
            'success': True,
            'scene': decimal_to_python(scenes[scene_index])
        })
        
    except Exception as e:
        print(f"Error updating scene: {e}")
        return response(500, {'error': f'Failed to update scene: {str(e)}'})
