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
import os
import urllib.request
import urllib.error
import boto3
from datetime import datetime

from config import (
    response, decimal_to_python, verify_admin,
    dynamodb, s3, S3_BUCKET, REPLICATE_API_KEY, upload_to_s3
)
from handlers.gemini_client import generate_image as gemini_generate_image

# DynamoDB tables
ambassadors_table = dynamodb.Table('ambassadors')
jobs_table = dynamodb.Table('nano_banana_jobs')

# AWS Bedrock client for Claude
bedrock_runtime = boto3.client('bedrock-runtime', region_name='us-east-1')

# Lambda client for async invocation
lambda_client = boto3.client('lambda')
LAMBDA_FUNCTION_NAME = 'saas-ugc'

# Claude Sonnet 4.5 model ID via inference profile
CLAUDE_MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

# Replicate API URL for fallback
REPLICATE_API_URL = "https://api.replicate.com/v1/models/google/nano-banana-pro/predictions"

# Number of showcase photos to generate
NUM_SHOWCASE_PHOTOS = 15

# Few-shot learning examples for scene descriptions
FEW_SHOT_EXAMPLES = """
A. Face cam "TikTok talk" (SEULEMENT pour ces scènes, regard caméra approprié):
- Scène TikTok face caméra: Assis sur une chaise moderne dans un salon épuré aux tons neutres, face à la caméra, mains posées naturellement sur les cuisses, buste légèrement penché vers l'avant en position d'écoute active, léger sourire confiant, fond mur blanc minimaliste avec plante verte floue, éclairage doux et naturel, ambiance lifestyle décontractée.
- Scène TikTok face caméra: Assis au bord d'un canapé gris confortable dans un intérieur cosy moderne, une main qui gesticule légèrement comme pour expliquer quelque chose avec passion, expression calme et sincère, regard direct et engageant, ambiance conversation authentique entre amis.
- Scène TikTok face caméra: Debout dans un espace lumineux et aéré, face caméra, pieds largeur d'épaules en position stable, mains liées devant le bassin en posture ouverte, expression neutre professionnelle, fond mur simple ou porte blanche, éclairage de studio naturel.

B. Scènes avec ordinateur / bureau (REGARD SUR L'ÉCRAN, PAS la caméra):
- Scène bureau travail productif: Assis à un bureau minimaliste en bois clair, laptop Apple ouvert, regard intensément focalisé sur l'écran avec concentration profonde, mains sur le clavier en position de frappe, profil trois-quarts, lumière naturelle de fenêtre, plante décorative en arrière-plan, ambiance entrepreneur digital productif.
- Scène bureau concentration: Assis au bureau moderne, une main sur la souris ergonomique, regard hypnotiquement focalisé sur l'écran, expression de concentration intense et sérieuse, dos droit en bonne posture, ambiance travail créatif professionnel.
- Scène bureau réflexion: Assis au bureau épuré, penché vers l'écran avec curiosité, une main sur le menton en position pensive, complètement absorbé par la lecture, ambiance étude ou recherche, éclairage chaud de lampe de bureau.

C. Scènes cuisine / manger / boire (regard naturel sur l'activité):
- Scène cuisine healthy préparation: Debout dans une cuisine moderne aux lignes épurées, comptoir en marbre blanc, regarde attentivement les ingrédients frais qu'elle prépare sur le plan de travail, expression concentrée de chef amateur, légumes colorés et ustensiles, lumière naturelle abondante, ambiance lifestyle healthy nutrition.
- Scène repas healthy: Assis à une table en bois naturel, regarde son assiette colorée healthy, fourchette à la main prêt à manger, moment naturel authentique du repas, smoothie vert à côté, ambiance alimentation équilibrée bien-être.
- Scène smoothie preparation: Debout dans cuisine lumineuse, verse soigneusement un smoothie protéiné vert dans un verre élégant, regarde précisément ce qu'il fait, fruits frais autour, lumière naturelle matinale, ambiance routine fitness nutrition.

D. Scènes fitness / gym (REGARD SUR L'EXERCICE ou droit devant, PAS la caméra):
- Scène gym repos fitness: Debout face au miroir d'une salle de sport moderne équipée, regarde son reflet pour vérifier sa posture, position de repos entre les séries d'exercices, épaules détendues, ambiance training musculation, équipements fitness en arrière-plan flou.
- Scène gym concentration: Assis sur un banc de musculation professionnel, regarde droit devant avec détermination, expression concentrée et focalisée, repos entre exercices de force, serviette sur l'épaule, ambiance workout intense.
- Scène stretching récupération: Debout dans espace fitness lumineux, étirements post-workout, regarde vers le sol en suivant son mouvement, expression calme et focalisée, muscles en extension, ambiance wellness récupération.
- Scène exercice effort: En position de planche parfaite ou exercice au sol, regard déterminé vers le sol, concentration totale sur l'effort physique, tapis de yoga, ambiance home workout training.

E. Debout / positions simples (mélange regard caméra et regard naturel):
- Scène pensive casual: Debout dans salon moderne, bras croisés sur la poitrine en position réflexive, regarde légèrement sur le côté avec expression pensive, ambiance réflexion créative, éclairage doux lifestyle.
- Scène contemplation fenêtre: Debout près d'une grande fenêtre lumineuse, une main dans la poche de façon décontractée, regarde le paysage par la fenêtre avec sérénité, profil naturel artistique, lumière dorée sur le visage, ambiance moment de calme mindfulness.
- Scène téléphone relax: Debout appuyé contre un mur texturé, regarde son téléphone dans sa main avec intérêt, scroll décontracté, posture relaxée, ambiance digital lifestyle quotidien.

F. Téléphone / scroll (regard sur le téléphone):
- Scène scroll canapé: Assis confortablement sur un canapé moelleux, téléphone dans les deux mains, regarde l'écran avec attention, expression concentrée et absorbée, jambes repliées, ambiance chill digital, coussins et plaid autour.
- Scène message debout: Debout en position naturelle, téléphone dans une main, tape activement un message, regard fixé sur l'écran avec concentration, ambiance communication connectée moderne.

G. Scènes lifestyle naturelles:
- Scène lecture détente: Assis dans un fauteuil confortable du salon cosy, lit un livre captivant ou magazine lifestyle, regard complètement absorbé par les pages, expression sereine, plante verte et lumière naturelle, ambiance self-care intellectual wellness.
- Scène fenêtre contemplation: Debout près d'une grande baie vitrée lumineuse, regarde dehors vers l'horizon avec expression pensive, profil pensif artistique, lumière naturelle douce sur le visage, ambiance moment introspection mindfulness.
- Scène écriture créative: Assis à un bureau épuré avec un carnet élégant ouvert, écrit quelque chose avec concentration, regard focalisé sur le carnet, stylo élégant dans la main, ambiance productivité créative journaling.
"""


# Products table for fetching ambassador's products
products_table = dynamodb.Table('products')


def get_ambassador_products(ambassador):
    """
    Get all products assigned to an ambassador with their details.
    Returns list of product dicts with id, name, description, image_url, category.
    """
    product_ids = ambassador.get('product_ids', [])
    if not product_ids:
        return []
    
    products = []
    for product_id in product_ids:
        try:
            result = products_table.get_item(Key={'id': product_id})
            product = result.get('Item')
            if product:
                products.append({
                    'id': product.get('id'),
                    'name': product.get('name', ''),
                    'description': product.get('description', ''),
                    'image_url': product.get('image_url', ''),
                    'category': product.get('category', 'other'),
                    'brand': product.get('brand', '')
                })
        except Exception as e:
            print(f"Error fetching product {product_id}: {e}")
    
    return products


def plan_product_placement(num_photos, products):
    """
    Plan which photos will feature products and which products.
    
    Rules:
    - Products should appear in 30-50% of photos (diversified, not too much)
    - Multiple products can be combined in some scenes
    - Some scenes should have NO product (natural lifestyle shots)
    - Product placement should feel natural based on scene type
    
    Returns: list of dicts with photo_index and product(s) to feature
    """
    if not products:
        return [{'photo_index': i, 'products': [], 'has_product': False} for i in range(num_photos)]
    
    placements = []
    num_products = len(products)
    
    # Calculate how many photos should have products (30-50%)
    min_product_photos = int(num_photos * 0.3)
    max_product_photos = int(num_photos * 0.5)
    num_product_photos = random.randint(min_product_photos, max_product_photos)
    
    # Select which photo indices will have products
    all_indices = list(range(num_photos))
    random.shuffle(all_indices)
    product_photo_indices = set(all_indices[:num_product_photos])
    
    # Distribute products across those photos
    product_cycle = products * (num_product_photos // num_products + 1)  # Ensure enough products
    random.shuffle(product_cycle)
    
    product_idx = 0
    for i in range(num_photos):
        if i in product_photo_indices:
            # This photo has a product
            selected_product = product_cycle[product_idx]
            product_idx += 1
            
            # Sometimes combine 2 products if we have multiple (10% chance)
            combined_products = [selected_product]
            if num_products >= 2 and random.random() < 0.1 and product_idx < len(product_cycle):
                additional_product = product_cycle[product_idx]
                if additional_product['id'] != selected_product['id']:
                    combined_products.append(additional_product)
                    product_idx += 1
            
            placements.append({
                'photo_index': i,
                'products': combined_products,
                'has_product': True
            })
        else:
            placements.append({
                'photo_index': i,
                'products': [],
                'has_product': False
            })
    
    return placements


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


def generate_scene_descriptions_with_claude(available_categories, ambassador_gender, ambassador_description="", products=None, product_placements=None):
    """
    Use AWS Bedrock Claude to generate scene descriptions.
    
    Now enhanced with:
    - Ambassador description for personality/style context
    - Products list with descriptions for coherent product integration
    - Product placement plan (which photos should feature which products)
    """
    
    categories_str = ", ".join(available_categories)
    gender_pronoun = "il" if ambassador_gender == "male" else "elle"
    gender_article = "un homme" if ambassador_gender == "male" else "une femme"
    
    # Build product context if products exist
    product_context = ""
    product_instructions = ""
    if products and product_placements:
        product_descriptions = []
        for p in products:
            desc = f"- {p['name']}"
            if p.get('brand'):
                desc += f" ({p['brand']})"
            if p.get('description'):
                desc += f": {p['description'][:150]}"
            if p.get('category'):
                desc += f" [catégorie: {p['category']}]"
            product_descriptions.append(desc)
        
        product_context = f"""

PRODUITS DE L'AMBASSADEUR (à intégrer naturellement dans certaines scènes):
{chr(10).join(product_descriptions)}

"""
        # Build which photos should have products
        photos_with_products = [p for p in product_placements if p['has_product']]
        if photos_with_products:
            product_photo_instructions = []
            for placement in photos_with_products:
                photo_num = placement['photo_index'] + 1
                product_names = [prod['name'] for prod in placement['products']]
                product_photo_instructions.append(f"  - Photo {photo_num}: intégrer {', '.join(product_names)}")
            
            product_instructions = f"""

INTÉGRATION DES PRODUITS (TRÈS IMPORTANT):
Les produits doivent apparaître de façon NATURELLE et COHÉRENTE dans ces photos:
{chr(10).join(product_photo_instructions)}

Pour les photos AVEC produit:
- Le produit doit être VISIBLE et RECONNAISSABLE dans la scène
- L'intégration doit être naturelle (pas forcée, pas publicitaire)
- Exemples: tenir une bouteille de boisson, porter des écouteurs, avoir un shaker sur la table, etc.
- Le produit doit correspondre au contexte de la scène

Pour les photos SANS produit (les autres):
- Scènes lifestyle naturelles SANS aucun produit visible
- Focus sur l'ambassadeur et l'ambiance uniquement
"""
    
    # Build ambassador context
    ambassador_context = ""
    if ambassador_description:
        ambassador_context = f"""

PROFIL DE L'AMBASSADEUR:
{ambassador_description}

Utilise ce profil pour adapter le style et l'ambiance des scènes à la personnalité de l'ambassadeur.
"""
    
    system_prompt = f"""Tu es un expert en création de contenu pour TikTok et réseaux sociaux. 
Tu dois générer exactement 15 descriptions de scènes TRÈS DÉTAILLÉES pour des photos d'ambassadeurs UGC.
{ambassador_context}{product_context}
RÈGLES CRITIQUES:
1. Le regard caméra est UNIQUEMENT pour les scènes "face cam TikTok talk" (max 4-5 scènes sur 15)
2. Pour les autres scènes: regard NATUREL sur l'activité (écran d'ordi, téléphone, livre, exercice, nourriture, etc.)
3. PAS de selfie (la caméra filme, pas de téléphone tenu pour se prendre en photo)
4. PAS d'expressions exagérées (pas de surprise, colère, etc.)
5. Expressions autorisées: neutre, léger sourire, concentré, calme, sérieux, pensif
6. La tenue doit être cohérente avec le décor (fitness pour la gym, casual pour la maison, etc.)
7. Tu ne peux utiliser QUE ces catégories de tenues: {categories_str}
8. Répartis équitablement les catégories sur les 15 photos
{product_instructions}
RÈGLE ABSOLUE - ZÉRO TEXTE:
- AUCUN texte visible dans la scène (pas de marques, pas de logos, pas d'écritures)
- Pas de valeurs sur les poids de gym (haltères sans chiffres)
- Pas de marques sur les vêtements, équipements, appareils
- Pas de texte sur les écrans d'ordinateur ou téléphone
- Environnement complètement neutre sans aucune inscription

La personne est {gender_article}.

FORMAT DESCRIPTION REQUIS (TRÈS IMPORTANT):
Chaque description doit contenir OBLIGATOIREMENT:
1. Un PRÉFIXE de catégorie (ex: "Scène fitness gym:", "Scène bureau travail:", "Scène lifestyle relaxation:")
2. Le DÉCOR détaillé (type de pièce, couleurs, meubles, lumière, ambiance)
3. La POSE précise (position du corps, des mains, orientation)
4. La DIRECTION DU REGARD (vers quoi la personne regarde exactement)
5. L'EXPRESSION faciale
6. L'AMBIANCE générale avec des mots-clés lifestyle (fitness, wellness, productivity, healthy, etc.)
7. Si un produit est à intégrer: COMMENT et OÙ il apparaît dans la scène

MOTS-CLÉS À INTÉGRER selon la scène:
- Fitness/Gym: workout, training, musculation, exercise, fitness, gym, sport, athletic, wellness
- Bureau/Travail: productivity, work, business, professional, entrepreneur, digital, creative
- Cuisine/Food: healthy, nutrition, cooking, food, meal, smoothie, preparation, lifestyle
- Lifestyle: relaxation, mindfulness, self-care, wellness, lifestyle, modern, cozy, authentic

IMPORTANT: Tu dois UNIQUEMENT répondre avec un JSON valide, sans aucun texte avant ou après."""

    user_prompt = f"""Génère 15 descriptions de scènes TRÈS DÉTAILLÉES pour un ambassadeur UGC.

Catégories de tenues disponibles: {categories_str}

Exemples de scènes inspirantes (SUIT CE FORMAT PRÉCIS):
{FEW_SHOT_EXAMPLES}

DISTRIBUTION DU REGARD (sur 15 photos):
- 4-5 photos: regard caméra (scènes "TikTok talk" face cam uniquement)
- 10-11 photos: regard naturel sur l'activité (écran, exercice, livre, téléphone, fenêtre, etc.)

Réponds UNIQUEMENT avec un JSON valide au format suivant (sans markdown, sans ```json, juste le JSON pur):
{{
    "picture_1": {{
        "position": "Scène [catégorie]: Description TRÈS détaillée de la scène avec décor, pose, expression, direction du regard, ambiance, mots-clés lifestyle, et si applicable: description du produit visible et comment il est intégré...",
        "outfit_category": "casual",
        "has_product": true,
        "product_name": "Nom du produit si applicable ou null"
    }},
    "picture_2": {{
        "position": "Scène [catégorie]: ...",
        "outfit_category": "fitness",
        "has_product": false,
        "product_name": null
    }},
    ...jusqu'à picture_15
}}

CHECKLIST POUR CHAQUE DESCRIPTION:
✅ Préfixe "Scène [type]:"
✅ Minimum 50 mots par description
✅ Décor détaillé (couleurs, meubles, lumière)
✅ Pose précise du corps
✅ Direction du regard claire
✅ Expression faciale
✅ Mots-clés lifestyle/fitness/wellness intégrés
✅ ZÉRO texte, marque, logo, chiffre visible
✅ Si produit: description claire de son intégration"""

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
        print(f"✅ Claude generated {len(scenes)} scenes successfully")
        return scenes
        
    except Exception as e:
        print(f"❌ ERROR calling Claude for scene generation: {e}")
        import traceback
        traceback.print_exc()
        # Re-raise the exception instead of using fallback - we want to see the error
        raise Exception(f"Claude scene generation failed: {e}")


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
- Follow the gaze direction specified in the scene description (NOT always at camera)
- Use natural, professional lighting
- High quality, photo-realistic result

ABSOLUTE RULE - ZERO TEXT:
- NO text anywhere in the image (no brands, no logos, no writing)
- NO numbers on gym weights or equipment (blank weights only)
- NO brand names on clothes, devices, or any objects
- NO text on screens (blank or abstract colors only)
- Completely clean, text-free environment"""

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


def generate_showcase_image(outfit_image_base64, scene_description, product_images_base64=None):
    """
    Generate a showcase image using Gemini 3 Pro Image (Nano Banana Pro) with Vertex AI fallback.
    
    Args:
        outfit_image_base64: Base64 encoded image of person wearing outfit
        scene_description: Description of the scene to generate
        product_images_base64: Optional list of dicts with 'image_base64' and 'description' for products to include
    
    Nano Banana Pro can use up to 14 reference images.
    """
    
    # Build product instructions if products are provided
    product_instructions = ""
    if product_images_base64:
        product_names = [p.get('name', 'product') for p in product_images_base64]
        product_instructions = f"""

IMPORTANT - PRODUCT INTEGRATION:
The following product(s) must appear NATURALLY in the scene: {', '.join(product_names)}
- The product(s) should be VISIBLE and RECOGNIZABLE in the image
- Integrate them naturally into the scene context (held, on a table, being used, etc.)
- Preserve the exact appearance of the product(s) from the reference images
- The product placement should feel authentic, not forced or overly promotional
"""
    
    prompt = f"""Using the provided image of a person wearing an outfit, create a new photo of this EXACT same person in the following scene:

{scene_description}
{product_instructions}
CRITICAL REQUIREMENTS:
- The person's face, body, skin tone, and ALL physical features must remain COMPLETELY IDENTICAL
- The outfit they are wearing must remain EXACTLY the same as in the reference image
- DO NOT change anything about the person or their clothing
- Only change the BACKGROUND, POSE, and SETTING as described
- Follow the gaze direction specified in the scene description (NOT always at camera)
- Use natural, professional lighting
- High quality, photo-realistic result

ABSOLUTE RULE - ZERO TEXT:
- NO text anywhere in the image (no brands, no logos, no writing)
- NO numbers on gym weights or equipment (blank weights only)
- NO brand names on clothes, devices, or any objects
- NO text on computer screens or phones (screens should be blank or show abstract colors)
- Completely clean, text-free environment"""

    # Build reference images list: outfit first, then products
    reference_images = [outfit_image_base64]
    
    if product_images_base64:
        for product in product_images_base64[:6]:  # Limit to 6 product images
            if product.get('image_base64'):
                reference_images.append(product['image_base64'])
                print(f"Added product image to request: {product.get('name', 'unknown')}")
    
    try:
        print(f"Calling Gemini (with Vertex AI fallback) for scene: {scene_description[:50]}...")
        
        image_base64 = gemini_generate_image(
            prompt=prompt,
            reference_images=reference_images,
            aspect_ratio="9:16",
            image_size="2K"
        )
        
        if image_base64:
            print("Image generated successfully")
            return image_base64
        else:
            print("No image returned from Gemini")
            return None
            
    except Exception as e:
        error_msg = str(e)
        print(f"Error generating showcase image: {error_msg}")
        
        # Check if it's a quota error to trigger Replicate fallback
        if "quota" in error_msg.lower() or "429" in error_msg:
            raise QuotaExceededException("Gemini API quota exceeded")
        
        import traceback
        traceback.print_exc()
        return None


class QuotaExceededException(Exception):
    """Raised when API quota is exceeded - triggers Replicate async fallback"""
    pass


def save_showcase_image_to_s3(image_base64, ambassador_id, index):
    """Save generated showcase image to S3 and return URL with cache headers"""
    try:
        image_data = base64.b64decode(image_base64)
        key = f"showcase_photos/{ambassador_id}/showcase_{index}_{uuid.uuid4().hex[:8]}.png"
        
        # Use helper with cache headers for fast loading
        return upload_to_s3(key, image_data, 'image/png', cache_days=365)
    except Exception as e:
        print(f"Error saving showcase image to S3: {e}")
        return None


def start_showcase_generation(event):
    """
    Start showcase generation - returns job_id immediately, generates scenes async
    POST /api/admin/ambassadors/showcase/generate
    
    This is now fully async:
    1. Creates job in 'generating_scenes' status
    2. Invokes Lambda async to generate scenes with Claude
    3. Returns immediately with job_id
    
    Frontend polls /showcase/status to get scenes when ready
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
    
    # Create job immediately in 'generating_scenes' status
    job_id = str(uuid.uuid4())
    job = {
        'id': job_id,
        'job_id': job_id,
        'type': 'showcase_generation',
        'ambassador_id': ambassador_id,
        'ambassador_gender': ambassador_gender,
        'available_categories': available_categories,
        'status': 'generating_scenes',  # Claude is generating scene descriptions
        'total_scenes': NUM_SHOWCASE_PHOTOS,
        'completed_scenes': 0,
        'current_scene_number': 0,
        'scenes': [],
        'results': [],
        'error': None,
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat()
    }
    
    jobs_table.put_item(Item=job)
    print(f"Created showcase job {job_id} for ambassador {ambassador_id}")
    
    # Invoke Lambda async to generate scenes with Claude
    payload = {
        'action': 'generate_showcase_scenes_async',
        'job_id': job_id
    }
    
    lambda_client.invoke(
        FunctionName=os.environ.get('AWS_LAMBDA_FUNCTION_NAME', 'ugc-booking'),
        InvocationType='Event',  # Asynchronous invocation
        Payload=json.dumps(payload)
    )
    
    # Return immediately with job_id - frontend polls /status
    return response(200, {
        'success': True,
        'job_id': job_id,
        'status': 'generating_scenes',
        'total_scenes': NUM_SHOWCASE_PHOTOS,
        'message': 'Generating scene descriptions with AI. Poll /showcase/status to get scenes when ready.'
    })


def generate_showcase_scenes_async(job_id):
    """
    Generate scene descriptions with Claude asynchronously.
    Called by Lambda invoke (InvocationType='Event').
    Updates job in DynamoDB when complete.
    
    Now enhanced with:
    - Ambassador description for personality context
    - Products with descriptions for coherent integration
    - Product placement planning (30-50% of photos)
    """
    print(f"[{job_id}] Starting async scene generation with Claude...")
    
    try:
        # Get job from DynamoDB
        result = jobs_table.get_item(Key={'id': job_id})
        job = result.get('Item')
        
        if not job:
            print(f"[{job_id}] Job not found")
            return
        
        ambassador_id = job.get('ambassador_id')
        ambassador_gender = job.get('ambassador_gender', 'male')
        available_categories = job.get('available_categories', ['casual'])
        
        # Get full ambassador data for description and products
        ambassador_result = ambassadors_table.get_item(Key={'id': ambassador_id})
        ambassador = ambassador_result.get('Item', {})
        
        ambassador_description = ambassador.get('description', '')
        print(f"[{job_id}] Ambassador description: {ambassador_description[:100] if ambassador_description else 'None'}...")
        
        # Get ambassador's products
        products = get_ambassador_products(ambassador)
        print(f"[{job_id}] Ambassador has {len(products)} products assigned")
        
        # Plan product placement (which photos will have products)
        product_placements = plan_product_placement(NUM_SHOWCASE_PHOTOS, products)
        photos_with_products = sum(1 for p in product_placements if p['has_product'])
        print(f"[{job_id}] Product placement: {photos_with_products}/{NUM_SHOWCASE_PHOTOS} photos will have products")
        
        # Generate scenes with Claude (with products and ambassador context)
        print(f"[{job_id}] Calling Claude to generate {NUM_SHOWCASE_PHOTOS} scenes...")
        try:
            scenes = generate_scene_descriptions_with_claude(
                available_categories, 
                ambassador_gender,
                ambassador_description=ambassador_description,
                products=products,
                product_placements=product_placements
            )
            print(f"[{job_id}] Claude generated {len(scenes)} scenes successfully")
        except Exception as e:
            print(f"[{job_id}] ERROR calling Claude: {e}")
            import traceback
            traceback.print_exc()
            scenes = generate_fallback_scenes(available_categories, ambassador_gender)
            print(f"[{job_id}] Using fallback scenes: {len(scenes)} scenes")
        
        # Convert scenes to list format with product info
        scenes_list = []
        for i, (key, scene) in enumerate(scenes.items(), 1):
            scene_id = str(uuid.uuid4())
            
            # Get product placement info for this scene
            placement = product_placements[i-1] if i-1 < len(product_placements) else {'has_product': False, 'products': []}
            
            # Try to get product info from Claude's response or from our placement plan
            has_product = scene.get('has_product', placement['has_product'])
            product_name = scene.get('product_name')
            product_ids = [p['id'] for p in placement.get('products', [])] if placement['has_product'] else []
            
            scenes_list.append({
                'scene_id': scene_id,
                'scene_number': i,
                'scene_description': scene['position'],
                'outfit_category': scene['outfit_category'],
                'has_product': has_product,
                'product_name': product_name,
                'product_ids': product_ids,  # IDs of products to include in image generation
                'generated_images': [],
                'selected_image': None,
                'status': 'pending'
            })
        
        # Update job with scenes and product placements
        jobs_table.update_item(
            Key={'id': job_id},
            UpdateExpression='SET #status = :status, scenes = :scenes, results = :results, product_placements = :placements, updated_at = :updated',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'scenes_ready',
                ':scenes': scenes_list,
                ':results': scenes_list,
                ':placements': product_placements,
                ':updated': datetime.now().isoformat()
            }
        )
        
        # Save scenes to ambassador record
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
            print(f"[{job_id}] Error saving showcase photos to ambassador: {e}")
        
        print(f"[{job_id}] Scene generation complete. {len(scenes_list)} scenes ready.")
        
    except Exception as e:
        print(f"[{job_id}] Fatal error in scene generation: {e}")
        import traceback
        traceback.print_exc()
        
        # Mark job as error
        try:
            jobs_table.update_item(
                Key={'id': job_id},
                UpdateExpression='SET #status = :status, #error = :error, updated_at = :updated',
                ExpressionAttributeNames={'#status': 'status', '#error': 'error'},
                ExpressionAttributeValues={
                    ':status': 'error',
                    ':error': str(e),
                    ':updated': datetime.now().isoformat()
                }
            )
        except:
            pass


def generate_scene(event):
    """
    Generate 2 images for a single scene - ASYNC VERSION
    POST /api/admin/ambassadors/showcase/scene
    
    This function now works asynchronously:
    1. If called from API Gateway: marks scene as 'processing' and invokes Lambda async, returns immediately
    2. If called with is_async=True (from Lambda invoke): does the actual generation work
    
    Body: { ambassador_id, scene_id, job_id, is_async? }
    Returns: { status: 'processing', scene_id } immediately, or full result if async
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
    is_async = body.get('is_async', False)  # True when called from Lambda invoke
    
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
    
    # If already generated, return the result
    if scene.get('generated_images') and len(scene.get('generated_images', [])) > 0:
        return response(200, {
            'success': True,
            'scene': decimal_to_python(scene),
            'message': 'Scene already has generated images'
        })
    
    # If currently processing, return processing status
    if scene.get('status') == 'processing' and not is_async:
        return response(202, {
            'success': True,
            'status': 'processing',
            'scene_id': scene_id,
            'message': 'Scene generation in progress. Poll /showcase/scene/poll for results.'
        })
    
    # If this is a synchronous call from API Gateway, start async processing
    if not is_async:
        # Mark scene as processing
        scene['status'] = 'processing'
        scene['processing_started_at'] = datetime.now().isoformat()
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
            print(f"Error marking scene as processing: {e}")
        
        # Invoke Lambda asynchronously
        try:
            lambda_client.invoke(
                FunctionName=LAMBDA_FUNCTION_NAME,
                InvocationType='Event',  # Async invocation
                Payload=json.dumps({
                    'action': 'generate_scene_async',
                    'ambassador_id': ambassador_id,
                    'scene_id': scene_id,
                    'job_id': job_id
                })
            )
            print(f"Async Lambda invoked for scene {scene_id}")
        except Exception as e:
            print(f"Error invoking async Lambda: {e}")
            # Mark scene as failed
            scene['status'] = 'failed'
            scene['error'] = str(e)
            showcase_photos[scene_index] = scene
            ambassadors_table.update_item(
                Key={'id': ambassador_id},
                UpdateExpression='SET showcase_photos = :photos',
                ExpressionAttributeValues={':photos': showcase_photos}
            )
            return response(500, {'error': f'Failed to start async generation: {str(e)}'})
        
        # Return immediately with processing status
        return response(202, {
            'success': True,
            'status': 'processing',
            'scene_id': scene_id,
            'message': 'Scene generation started. Poll /showcase/scene/poll for results.'
        })
    
    # ===== ASYNC EXECUTION: Actually generate the images =====
    scene_number = scene.get('scene_number', scene_index + 1)
    scene_description = scene.get('scene_description', '')
    outfit_category = scene.get('outfit_category', 'casual')
    has_product = scene.get('has_product', False)
    product_ids = scene.get('product_ids', [])
    
    print(f"Generating images for scene {scene_number}: {scene_description[:50]}...")
    print(f"Scene has product: {has_product}, product_ids: {product_ids}")
    
    # Get outfit image for this category
    outfit_image_url = get_outfit_image_for_category(ambassador, outfit_category)
    if not outfit_image_url:
        # Try any available category
        available_categories = get_available_outfit_categories(ambassador)
        if available_categories:
            outfit_image_url = get_outfit_image_for_category(ambassador, available_categories[0])
    
    if not outfit_image_url:
        # Mark scene as failed
        scene['status'] = 'failed'
        scene['error'] = f'No validated outfit image available for category {outfit_category}'
        showcase_photos[scene_index] = scene
        ambassadors_table.update_item(
            Key={'id': ambassador_id},
            UpdateExpression='SET showcase_photos = :photos',
            ExpressionAttributeValues={':photos': showcase_photos}
        )
        return response(400, {'error': f'No validated outfit image available for category {outfit_category}'})
    
    # Get base64 of outfit image
    outfit_image_base64 = get_image_from_s3(outfit_image_url)
    if not outfit_image_base64:
        scene['status'] = 'failed'
        scene['error'] = 'Failed to get outfit image from S3'
        showcase_photos[scene_index] = scene
        ambassadors_table.update_item(
            Key={'id': ambassador_id},
            UpdateExpression='SET showcase_photos = :photos',
            ExpressionAttributeValues={':photos': showcase_photos}
        )
        return response(500, {'error': 'Failed to get outfit image from S3'})
    
    print(f"Using outfit image: {outfit_image_url[:80]}...")
    
    # Get product images if this scene should have products
    product_images_base64 = None
    if has_product and product_ids:
        product_images_base64 = []
        for product_id in product_ids:
            try:
                product_result = products_table.get_item(Key={'id': product_id})
                product = product_result.get('Item')
                if product and product.get('image_url'):
                    product_image_base64 = get_image_from_s3(product['image_url'])
                    if product_image_base64:
                        product_images_base64.append({
                            'id': product_id,
                            'name': product.get('name', 'Product'),
                            'description': product.get('description', ''),
                            'image_base64': product_image_base64
                        })
                        print(f"Loaded product image: {product.get('name', product_id)}")
                    else:
                        print(f"Failed to load image for product {product_id}")
                else:
                    print(f"Product {product_id} not found or has no image")
            except Exception as e:
                print(f"Error loading product {product_id}: {e}")
        
        if not product_images_base64:
            print("Warning: No product images loaded, generating scene without products")
            product_images_base64 = None
    
    # Generate 2 variations
    generated_urls = []
    replicate_predictions = []  # Store prediction IDs for async processing
    quota_exceeded = False
    
    for variation in range(2):
        print(f"Generating variation {variation + 1}/2...")
        try:
            image_base64 = generate_showcase_image(
                outfit_image_base64, 
                scene_description,
                product_images_base64=product_images_base64
            )
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
    Poll scene generation status (works for both Gemini async and Replicate fallback)
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
    
    current_status = scene.get('status', 'unknown')
    
    # Check if already completed (generated via Gemini or Replicate)
    if current_status == 'generated' and scene.get('generated_images'):
        return response(200, {
            'success': True,
            'status': 'completed',
            'all_completed': True,
            'generated_images': scene.get('generated_images', []),
            'scene': decimal_to_python(scene)
        })
    
    # Check if failed
    if current_status == 'failed':
        return response(200, {
            'success': False,
            'status': 'failed',
            'all_completed': True,
            'error': scene.get('error', 'Generation failed'),
            'scene': decimal_to_python(scene)
        })
    
    # If still processing (Gemini async), return processing status
    if current_status == 'processing':
        return response(200, {
            'success': True,
            'status': 'processing',
            'all_completed': False,
            'message': 'Scene generation in progress (Gemini)',
            'scene': decimal_to_python(scene)
        })
    
    # Check if we're processing Replicate predictions
    if current_status != 'processing_replicate':
        return response(200, {
            'success': True,
            'status': current_status,
            'all_completed': current_status in ['generated', 'failed', 'selected'],
            'scene': decimal_to_python(scene),
            'message': f'Scene status: {current_status}'
        })
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