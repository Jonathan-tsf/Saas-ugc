"""
Product Management Handlers
- CRUD operations for brand products (cars, nutrition, tech, fashion, etc.)
- Products can be attributed to ambassadors for showcase/videos
"""
import json
import uuid
import base64
from datetime import datetime
from config import (
    dynamodb, 
    s3, 
    S3_BUCKET, 
    response, 
    verify_admin,
    get_bedrock_client,
    decimal_to_python
)

# Products table
products_table = dynamodb.Table('products')

# Valid categories for products
VALID_CATEGORIES = [
    # Véhicules
    'automobile',      # Voitures
    'moto',            # Motos, scooters
    'velo',            # Vélos, VTT, vélos électriques
    
    # Alimentation & Boissons
    'nutrition',       # Protéines, suppléments, compléments
    'food',            # Nourriture, snacks, barres énergétiques
    'beverage',        # Boissons, energy drinks, eau
    'alcohol',         # Alcool, vin, bière, spiritueux
    
    # Tech & Électronique
    'smartphone',      # Téléphones, mobiles
    'computer',        # PC, laptops, tablettes
    'audio',           # Casques, écouteurs, enceintes
    'gaming',          # Consoles, manettes, accessoires gaming
    'camera',          # Appareils photo, caméras, drones
    'wearable',        # Montres connectées, bracelets fitness
    
    # Mode & Accessoires
    'clothing',        # Vêtements, t-shirts, pantalons
    'shoes',           # Chaussures, sneakers, baskets
    'watch',           # Montres classiques, luxe
    'jewelry',         # Bijoux, colliers, bracelets
    'bags',            # Sacs, sacs à dos, valises
    'eyewear',         # Lunettes de soleil, lunettes de vue
    'accessories',     # Ceintures, portefeuilles, etc.
    
    # Sport & Fitness
    'fitness',         # Équipement de musculation, haltères
    'sports_equipment',# Équipement sportif général
    'outdoor',         # Camping, randonnée, escalade
    'water_sports',    # Surf, natation, plongée
    'winter_sports',   # Ski, snowboard
    'team_sports',     # Football, basketball, tennis
    
    # Beauté & Soins
    'skincare',        # Soins de la peau, crèmes
    'haircare',        # Soins capillaires, shampoings
    'makeup',          # Maquillage
    'fragrance',       # Parfums, eaux de toilette
    'grooming',        # Rasage, barbe, soins homme
    
    # Maison & Lifestyle
    'home',            # Décoration, meubles
    'kitchen',         # Cuisine, électroménager
    'garden',          # Jardin, extérieur
    'pet',             # Animaux, accessoires pour animaux
    
    # Santé & Bien-être
    'health',          # Santé, médical
    'wellness',        # Bien-être, relaxation, yoga
    'baby',            # Bébé, puériculture
    
    # Autres
    'luxury',          # Produits de luxe
    'collectible',     # Objets de collection
    'tools',           # Outils, bricolage
    'office',          # Bureau, fournitures
    'other'            # Autre
]


def upload_to_s3(key, data, content_type, cache_days=365):
    """Upload data to S3 with cache headers"""
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=data,
        ContentType=content_type,
        CacheControl=f'public, max-age={cache_days * 24 * 3600}'
    )
    return f"https://{S3_BUCKET}.s3.amazonaws.com/{key}"


def analyze_product_image(image_base64: str) -> dict:
    """
    Use Bedrock Claude to analyze a product image and generate:
    - Name suggestion
    - Description
    - Suggested category
    - Brand (if visible)
    """
    try:
        bedrock = get_bedrock_client()
        
        # Build message with image
        message = {
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
                    "text": """Analyze this product image and provide:
1. A suggested product name (short, marketing-friendly, in the original language if text is visible)
2. A brief description in French (2-3 sentences describing the product, its features, and target audience)
3. The most appropriate category from this list:
   - Vehicles: automobile, moto, velo
   - Food & Drinks: nutrition, food, beverage, alcohol
   - Tech: smartphone, computer, audio, gaming, camera, wearable
   - Fashion: clothing, shoes, watch, jewelry, bags, eyewear, accessories
   - Sports: fitness, sports_equipment, outdoor, water_sports, winter_sports, team_sports
   - Beauty: skincare, haircare, makeup, fragrance, grooming
   - Home: home, kitchen, garden, pet
   - Health: health, wellness, baby
   - Other: luxury, collectible, tools, office, other
4. The brand name if visible (or "Unknown" if not visible)

Respond in JSON format only:
{
    "name": "Product Name",
    "description": "Description en français détaillée...",
    "category": "category_from_list",
    "brand": "Brand Name or Unknown"
}"""
                }
            ]
        }
        
        response_body = bedrock.invoke_model(
            modelId="anthropic.claude-3-haiku-20240307-v1:0",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 500,
                "messages": [message]
            })
        )
        
        result = json.loads(response_body['body'].read())
        text = result['content'][0]['text']
        
        # Parse JSON from response
        # Handle potential markdown code blocks
        if '```json' in text:
            text = text.split('```json')[1].split('```')[0]
        elif '```' in text:
            text = text.split('```')[1].split('```')[0]
        
        analysis = json.loads(text.strip())
        
        # Validate category
        if analysis.get('category') not in VALID_CATEGORIES:
            analysis['category'] = 'other'
        
        return analysis
        
    except Exception as e:
        print(f"Error analyzing product image: {e}")
        return {
            "name": "Nouveau Produit",
            "description": "Description à compléter",
            "category": "other",
            "brand": "Unknown"
        }


def get_products(event):
    """Get all products with optional filters - GET /api/admin/products"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        # Get optional filters from query params
        query_params = event.get('queryStringParameters') or {}
        category_filter = query_params.get('category')
        brand_filter = query_params.get('brand')
        
        # Scan all products (for admin, no limit)
        scan_params = {}
        
        if category_filter:
            scan_params['FilterExpression'] = 'category = :cat'
            scan_params['ExpressionAttributeValues'] = {':cat': category_filter}
        
        result = products_table.scan(**scan_params)
        products = result.get('Items', [])
        
        # Additional filtering by brand if needed
        if brand_filter:
            products = [p for p in products if p.get('brand', '').lower() == brand_filter.lower()]
        
        # Sort by created_at descending
        products.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        return response(200, {
            'success': True,
            'products': decimal_to_python(products),
            'total': len(products)
        })
        
    except Exception as e:
        print(f"Error fetching products: {e}")
        return response(500, {'error': f'Failed to fetch products: {str(e)}'})


def get_product(event):
    """Get a single product by ID - GET /api/admin/products/{id}"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        path_params = event.get('pathParameters', {}) or {}
        product_id = path_params.get('id')
        
        if not product_id:
            # Try to extract from path
            path = event.get('path', '')
            parts = path.split('/')
            if len(parts) > 0:
                product_id = parts[-1]
        
        if not product_id:
            return response(400, {'error': 'Product ID is required'})
        
        result = products_table.get_item(Key={'id': product_id})
        product = result.get('Item')
        
        if not product:
            return response(404, {'error': 'Product not found'})
        
        return response(200, {
            'success': True,
            'product': decimal_to_python(product)
        })
        
    except Exception as e:
        print(f"Error fetching product: {e}")
        return response(500, {'error': f'Failed to fetch product: {str(e)}'})


def create_product(event):
    """Create a new product - POST /api/admin/products"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    # Validate required fields
    if 'image_base64' not in body:
        return response(400, {'error': 'image_base64 is required'})
    
    try:
        product_id = str(uuid.uuid4())
        image_base64 = body['image_base64']
        
        # Analyze image with AI to get product details
        analysis = analyze_product_image(image_base64)
        
        # Use provided values or AI-generated values
        name = body.get('name') or analysis.get('name', 'Nouveau Produit')
        description = body.get('description') or analysis.get('description', '')
        category = body.get('category') or analysis.get('category', 'other')
        brand = body.get('brand') or analysis.get('brand', 'Unknown')
        
        # Validate category
        if category not in VALID_CATEGORIES:
            return response(400, {
                'error': f'Invalid category. Must be one of: {", ".join(VALID_CATEGORIES)}'
            })
        
        # Upload image to S3 (JPEG from frontend compression)
        image_key = f"products/{product_id}.jpg"
        image_data = base64.b64decode(image_base64)
        image_url = upload_to_s3(image_key, image_data, 'image/jpeg', cache_days=365)
        
        # Create product record
        now = datetime.now().isoformat()
        product = {
            'id': product_id,
            'name': name,
            'description': description,
            'category': category,
            'brand': brand,
            'image_url': image_url,
            'ambassador_count': 0,
            'created_at': now,
            'updated_at': now
        }
        
        products_table.put_item(Item=product)
        
        return response(201, {
            'success': True,
            'product': product
        })
        
    except Exception as e:
        print(f"Error creating product: {e}")
        return response(500, {'error': f'Failed to create product: {str(e)}'})


def update_product(event):
    """Update a product - PUT /api/admin/products/{id}"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        path_params = event.get('pathParameters', {}) or {}
        product_id = path_params.get('id')
        
        if not product_id:
            # Try to extract from path
            path = event.get('path', '')
            parts = path.split('/')
            if len(parts) > 0:
                product_id = parts[-1]
        
        if not product_id:
            return response(400, {'error': 'Product ID is required'})
        
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    try:
        # Check if product exists
        result = products_table.get_item(Key={'id': product_id})
        existing = result.get('Item')
        
        if not existing:
            return response(404, {'error': 'Product not found'})
        
        # Build update expression
        update_expr = "SET updated_at = :updated_at"
        expr_values = {':updated_at': datetime.now().isoformat()}
        expr_names = {}
        
        if 'name' in body:
            update_expr += ", #name = :name"
            expr_values[':name'] = body['name']
            expr_names['#name'] = 'name'
        
        if 'description' in body:
            update_expr += ", description = :description"
            expr_values[':description'] = body['description']
        
        if 'category' in body:
            if body['category'] not in VALID_CATEGORIES:
                return response(400, {
                    'error': f'Invalid category. Must be one of: {", ".join(VALID_CATEGORIES)}'
                })
            update_expr += ", category = :category"
            expr_values[':category'] = body['category']
        
        if 'brand' in body:
            update_expr += ", brand = :brand"
            expr_values[':brand'] = body['brand']
        
        # Handle image update
        if 'image_base64' in body:
            image_key = f"products/{product_id}.png"
            image_data = base64.b64decode(body['image_base64'])
            image_url = upload_to_s3(image_key, image_data, 'image/png', cache_days=365)
            update_expr += ", image_url = :image_url"
            expr_values[':image_url'] = image_url
        
        update_params = {
            'Key': {'id': product_id},
            'UpdateExpression': update_expr,
            'ExpressionAttributeValues': expr_values,
            'ReturnValues': 'ALL_NEW'
        }
        
        if expr_names:
            update_params['ExpressionAttributeNames'] = expr_names
        
        result = products_table.update_item(**update_params)
        
        return response(200, {
            'success': True,
            'product': decimal_to_python(result['Attributes'])
        })
        
    except Exception as e:
        print(f"Error updating product: {e}")
        return response(500, {'error': f'Failed to update product: {str(e)}'})


def delete_product(event):
    """Delete a product - DELETE /api/admin/products/{id}"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        path_params = event.get('pathParameters', {}) or {}
        product_id = path_params.get('id')
        
        if not product_id:
            # Try to extract from path
            path = event.get('path', '')
            parts = path.split('/')
            if len(parts) > 0:
                product_id = parts[-1]
        
        if not product_id:
            return response(400, {'error': 'Product ID is required'})
        
        # Check if product exists and get ambassador_count
        result = products_table.get_item(Key={'id': product_id})
        existing = result.get('Item')
        
        if not existing:
            return response(404, {'error': 'Product not found'})
        
        # Prevent deletion if ambassadors are using this product
        if existing.get('ambassador_count', 0) > 0:
            return response(400, {
                'error': f"Cannot delete product. {existing['ambassador_count']} ambassador(s) are using it."
            })
        
        # Delete from S3
        try:
            s3.delete_object(Bucket=S3_BUCKET, Key=f"products/{product_id}.png")
        except Exception as e:
            print(f"Warning: Could not delete S3 object: {e}")
        
        # Delete from DynamoDB
        products_table.delete_item(Key={'id': product_id})
        
        return response(200, {
            'success': True,
            'message': 'Product deleted successfully'
        })
        
    except Exception as e:
        print(f"Error deleting product: {e}")
        return response(500, {'error': f'Failed to delete product: {str(e)}'})


def get_product_upload_url(event):
    """Get presigned URL for product image upload - GET /api/admin/products/upload-url"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    try:
        product_id = str(uuid.uuid4())
        key = f"products/{product_id}.png"
        
        presigned_url = s3.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': S3_BUCKET,
                'Key': key,
                'ContentType': 'image/png'
            },
            ExpiresIn=300  # 5 minutes
        )
        
        image_url = f"https://{S3_BUCKET}.s3.amazonaws.com/{key}"
        
        return response(200, {
            'success': True,
            'upload_url': presigned_url,
            'image_url': image_url,
            'product_id': product_id
        })
        
    except Exception as e:
        print(f"Error generating upload URL: {e}")
        return response(500, {'error': f'Failed to generate upload URL: {str(e)}'})


def increment_product_count(product_id, increment=1):
    """Helper function to increment/decrement ambassador count for a product"""
    try:
        products_table.update_item(
            Key={'id': product_id},
            UpdateExpression='SET ambassador_count = if_not_exists(ambassador_count, :zero) + :inc, updated_at = :updated',
            ExpressionAttributeValues={
                ':inc': increment,
                ':zero': 0,
                ':updated': datetime.now().isoformat()
            }
        )
        return True
    except Exception as e:
        print(f"Error updating product count: {e}")
        return False
