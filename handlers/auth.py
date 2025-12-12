"""
User authentication and profile handlers
Handles Cognito authentication and user management in DynamoDB
"""
import json
import boto3
import hmac
import hashlib
import base64
from datetime import datetime
from config import response

# Initialize AWS clients
cognito = boto3.client('cognito-idp', region_name='us-east-1')
dynamodb = boto3.resource('dynamodb', region_name='us-east-1')

# Configuration
USER_POOL_ID = 'us-east-1_OvfFg7tYb'
CLIENT_ID = '4hl2tc7qb388kogd113sffmt8k'
CLIENT_SECRET = None  # Set this if your app client has a secret
USERS_TABLE = 'ugc_users'

# Get DynamoDB table
users_table = dynamodb.Table(USERS_TABLE)


def get_secret_hash(username: str) -> str:
    """Calculate secret hash if client secret is configured"""
    if not CLIENT_SECRET:
        return None
    
    message = username + CLIENT_ID
    dig = hmac.new(
        CLIENT_SECRET.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).digest()
    return base64.b64encode(dig).decode()


def sign_up(event):
    """
    Register a new user with email/password
    POST /api/auth/signup
    Body: { email, password, name }
    """
    try:
        body = json.loads(event.get('body', '{}'))
        email = body.get('email', '').lower().strip()
        password = body.get('password', '')
        name = body.get('name', '')
        
        if not email or not password:
            return response(400, {'message': 'Email and password are required'})
        
        # Build attributes
        user_attributes = [
            {'Name': 'email', 'Value': email},
        ]
        if name:
            user_attributes.append({'Name': 'name', 'Value': name})
        
        # Build sign up params
        params = {
            'ClientId': CLIENT_ID,
            'Username': email,
            'Password': password,
            'UserAttributes': user_attributes,
        }
        
        secret_hash = get_secret_hash(email)
        if secret_hash:
            params['SecretHash'] = secret_hash
        
        # Register user in Cognito
        result = cognito.sign_up(**params)
        
        return response(200, {
            'message': 'User registered successfully. Please check your email for verification.',
            'user_id': result['UserSub'],
            'email': email,
            'confirmed': result.get('UserConfirmed', False),
        })
        
    except cognito.exceptions.UsernameExistsException:
        return response(400, {'message': 'Un compte existe déjà avec cet email'})
    except cognito.exceptions.InvalidPasswordException as e:
        return response(400, {'message': f'Mot de passe invalide: {str(e)}'})
    except cognito.exceptions.InvalidParameterException as e:
        return response(400, {'message': f'Paramètre invalide: {str(e)}'})
    except Exception as e:
        print(f"Sign up error: {e}")
        return response(500, {'message': 'Une erreur est survenue lors de l\'inscription'})


def confirm_sign_up(event):
    """
    Confirm user registration with verification code
    POST /api/auth/confirm
    Body: { email, code }
    """
    try:
        body = json.loads(event.get('body', '{}'))
        email = body.get('email', '').lower().strip()
        code = body.get('code', '')
        
        if not email or not code:
            return response(400, {'message': 'Email and confirmation code are required'})
        
        params = {
            'ClientId': CLIENT_ID,
            'Username': email,
            'ConfirmationCode': code,
        }
        
        secret_hash = get_secret_hash(email)
        if secret_hash:
            params['SecretHash'] = secret_hash
        
        cognito.confirm_sign_up(**params)
        
        return response(200, {
            'message': 'Email confirmé avec succès. Vous pouvez maintenant vous connecter.',
        })
        
    except cognito.exceptions.CodeMismatchException:
        return response(400, {'message': 'Code de vérification incorrect'})
    except cognito.exceptions.ExpiredCodeException:
        return response(400, {'message': 'Code de vérification expiré. Veuillez demander un nouveau code.'})
    except cognito.exceptions.UserNotFoundException:
        return response(404, {'message': 'Utilisateur non trouvé'})
    except Exception as e:
        print(f"Confirm sign up error: {e}")
        return response(500, {'message': 'Une erreur est survenue lors de la confirmation'})


def sign_in(event):
    """
    Authenticate user with email/password
    POST /api/auth/signin
    Body: { email, password }
    """
    try:
        body = json.loads(event.get('body', '{}'))
        email = body.get('email', '').lower().strip()
        password = body.get('password', '')
        
        if not email or not password:
            return response(400, {'message': 'Email and password are required'})
        
        auth_params = {
            'USERNAME': email,
            'PASSWORD': password,
        }
        
        secret_hash = get_secret_hash(email)
        if secret_hash:
            auth_params['SECRET_HASH'] = secret_hash
        
        result = cognito.initiate_auth(
            ClientId=CLIENT_ID,
            AuthFlow='USER_PASSWORD_AUTH',
            AuthParameters=auth_params,
        )
        
        auth_result = result.get('AuthenticationResult', {})
        
        # Sync user to DynamoDB
        try:
            user_info = cognito.get_user(
                AccessToken=auth_result.get('AccessToken')
            )
            user_attrs = {attr['Name']: attr['Value'] for attr in user_info.get('UserAttributes', [])}
            
            sync_user_to_db({
                'user_id': user_attrs.get('sub'),
                'email': email,
                'name': user_attrs.get('name', ''),
                'provider': 'email',
            })
        except Exception as sync_error:
            print(f"User sync error: {sync_error}")
        
        return response(200, {
            'id_token': auth_result.get('IdToken'),
            'access_token': auth_result.get('AccessToken'),
            'refresh_token': auth_result.get('RefreshToken'),
            'expires_in': auth_result.get('ExpiresIn'),
            'token_type': auth_result.get('TokenType'),
        })
        
    except cognito.exceptions.NotAuthorizedException:
        return response(401, {'message': 'Email ou mot de passe incorrect'})
    except cognito.exceptions.UserNotConfirmedException:
        return response(403, {'message': 'Veuillez confirmer votre email avant de vous connecter'})
    except cognito.exceptions.UserNotFoundException:
        return response(404, {'message': 'Utilisateur non trouvé'})
    except Exception as e:
        print(f"Sign in error: {e}")
        return response(500, {'message': 'Une erreur est survenue lors de la connexion'})


def resend_confirmation_code(event):
    """
    Resend verification code to user's email
    POST /api/auth/resend-code
    Body: { email }
    """
    try:
        body = json.loads(event.get('body', '{}'))
        email = body.get('email', '').lower().strip()
        
        if not email:
            return response(400, {'message': 'Email is required'})
        
        params = {
            'ClientId': CLIENT_ID,
            'Username': email,
        }
        
        secret_hash = get_secret_hash(email)
        if secret_hash:
            params['SecretHash'] = secret_hash
        
        cognito.resend_confirmation_code(**params)
        
        return response(200, {
            'message': 'Un nouveau code de vérification a été envoyé à votre email',
        })
        
    except cognito.exceptions.UserNotFoundException:
        return response(404, {'message': 'Utilisateur non trouvé'})
    except cognito.exceptions.LimitExceededException:
        return response(429, {'message': 'Trop de tentatives. Veuillez réessayer plus tard.'})
    except Exception as e:
        print(f"Resend code error: {e}")
        return response(500, {'message': 'Une erreur est survenue'})


def forgot_password(event):
    """
    Initiate password reset flow
    POST /api/auth/forgot-password
    Body: { email }
    """
    try:
        body = json.loads(event.get('body', '{}'))
        email = body.get('email', '').lower().strip()
        
        if not email:
            return response(400, {'message': 'Email is required'})
        
        params = {
            'ClientId': CLIENT_ID,
            'Username': email,
        }
        
        secret_hash = get_secret_hash(email)
        if secret_hash:
            params['SecretHash'] = secret_hash
        
        cognito.forgot_password(**params)
        
        return response(200, {
            'message': 'Un code de réinitialisation a été envoyé à votre email',
        })
        
    except cognito.exceptions.UserNotFoundException:
        # Don't reveal if user exists or not
        return response(200, {
            'message': 'Si cet email existe, un code de réinitialisation a été envoyé',
        })
    except cognito.exceptions.LimitExceededException:
        return response(429, {'message': 'Trop de tentatives. Veuillez réessayer plus tard.'})
    except Exception as e:
        print(f"Forgot password error: {e}")
        return response(500, {'message': 'Une erreur est survenue'})


def confirm_forgot_password(event):
    """
    Complete password reset with code
    POST /api/auth/reset-password
    Body: { email, code, new_password }
    """
    try:
        body = json.loads(event.get('body', '{}'))
        email = body.get('email', '').lower().strip()
        code = body.get('code', '')
        new_password = body.get('new_password', '')
        
        if not email or not code or not new_password:
            return response(400, {'message': 'Email, code and new password are required'})
        
        params = {
            'ClientId': CLIENT_ID,
            'Username': email,
            'ConfirmationCode': code,
            'Password': new_password,
        }
        
        secret_hash = get_secret_hash(email)
        if secret_hash:
            params['SecretHash'] = secret_hash
        
        cognito.confirm_forgot_password(**params)
        
        return response(200, {
            'message': 'Mot de passe réinitialisé avec succès. Vous pouvez maintenant vous connecter.',
        })
        
    except cognito.exceptions.CodeMismatchException:
        return response(400, {'message': 'Code de vérification incorrect'})
    except cognito.exceptions.ExpiredCodeException:
        return response(400, {'message': 'Code de vérification expiré'})
    except cognito.exceptions.InvalidPasswordException as e:
        return response(400, {'message': f'Mot de passe invalide: {str(e)}'})
    except Exception as e:
        print(f"Reset password error: {e}")
        return response(500, {'message': 'Une erreur est survenue'})


def refresh_token(event):
    """
    Refresh access token using refresh token
    POST /api/auth/refresh
    Body: { refresh_token }
    """
    try:
        body = json.loads(event.get('body', '{}'))
        refresh_token_value = body.get('refresh_token', '')
        
        if not refresh_token_value:
            return response(400, {'message': 'Refresh token is required'})
        
        auth_params = {
            'REFRESH_TOKEN': refresh_token_value,
        }
        
        # Note: SECRET_HASH not needed for refresh token flow
        
        result = cognito.initiate_auth(
            ClientId=CLIENT_ID,
            AuthFlow='REFRESH_TOKEN_AUTH',
            AuthParameters=auth_params,
        )
        
        auth_result = result.get('AuthenticationResult', {})
        
        return response(200, {
            'id_token': auth_result.get('IdToken'),
            'access_token': auth_result.get('AccessToken'),
            'expires_in': auth_result.get('ExpiresIn'),
            'token_type': auth_result.get('TokenType'),
        })
        
    except cognito.exceptions.NotAuthorizedException:
        return response(401, {'message': 'Token de rafraîchissement invalide ou expiré'})
    except Exception as e:
        print(f"Refresh token error: {e}")
        return response(500, {'message': 'Une erreur est survenue'})


def sync_user_to_db(user_data: dict):
    """
    Create or update user in DynamoDB
    """
    try:
        now = datetime.utcnow().isoformat()
        
        users_table.update_item(
            Key={'user_id': user_data['user_id']},
            UpdateExpression='SET email = :email, #name = :name, provider = :provider, updated_at = :updated_at, picture = if_not_exists(picture, :picture)',
            ExpressionAttributeNames={'#name': 'name'},
            ExpressionAttributeValues={
                ':email': user_data.get('email', ''),
                ':name': user_data.get('name', ''),
                ':provider': user_data.get('provider', 'email'),
                ':updated_at': now,
                ':picture': user_data.get('picture', ''),
            },
            ReturnValues='ALL_NEW',
        )
        
        # Set created_at if this is a new user
        users_table.update_item(
            Key={'user_id': user_data['user_id']},
            UpdateExpression='SET created_at = if_not_exists(created_at, :created_at)',
            ExpressionAttributeValues={':created_at': now},
        )
        
        print(f"User synced to DB: {user_data['user_id']}")
        return True
        
    except Exception as e:
        print(f"Error syncing user to DB: {e}")
        return False


def get_user_profile(event):
    """
    Get current user's profile
    GET /api/user/profile
    Requires: Authorization header with access token
    Supports both Cognito native tokens and OAuth tokens
    """
    try:
        # Verify token and get user info
        auth_header = event.get('headers', {}).get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return response(401, {'message': 'Token manquant'})
        
        access_token = auth_header.replace('Bearer ', '')
        
        user_id = None
        user_attrs = {}
        is_oauth_user = False
        
        # Try to get user info from Cognito first
        try:
            user_info = cognito.get_user(AccessToken=access_token)
            user_attrs = {attr['Name']: attr['Value'] for attr in user_info.get('UserAttributes', [])}
            user_id = user_attrs.get('sub')
        except cognito.exceptions.NotAuthorizedException:
            # Token might be an OAuth token - try to decode it
            try:
                import base64
                import json
                
                # Decode JWT payload (middle part)
                parts = access_token.split('.')
                if len(parts) >= 2:
                    # Add padding if needed
                    payload = parts[1]
                    padding = 4 - len(payload) % 4
                    if padding != 4:
                        payload += '=' * padding
                    
                    decoded = base64.urlsafe_b64decode(payload)
                    token_data = json.loads(decoded)
                    
                    # Extract user_id from token (sub claim)
                    user_id = token_data.get('sub')
                    user_attrs = {
                        'email': token_data.get('email', ''),
                        'name': token_data.get('name', token_data.get('cognito:username', '')),
                    }
                    is_oauth_user = True
                    print(f"OAuth user detected from token: {user_id}")
            except Exception as decode_error:
                print(f"Failed to decode token: {decode_error}")
                return response(401, {'message': 'Token invalide ou expiré'})
        
        if not user_id:
            return response(401, {'message': 'Impossible de récupérer l\'identifiant utilisateur'})
        
        # Get extended profile from DynamoDB
        db_result = users_table.get_item(Key={'user_id': user_id})
        db_user = db_result.get('Item', {})
        
        # For OAuth users, prefer DB data; for native users, prefer Cognito data
        return response(200, {
            'user_id': user_id,
            'email': user_attrs.get('email', db_user.get('email', '')),
            'name': db_user.get('name') or user_attrs.get('name', ''),
            'picture': db_user.get('picture') or user_attrs.get('picture', ''),
            'provider': db_user.get('provider', 'google' if is_oauth_user else 'email'),
            'created_at': db_user.get('created_at'),
            'updated_at': db_user.get('updated_at'),
            # Pipeline preferences
            'pipeline_preferences': db_user.get('pipeline_preferences'),
            'user_profile_type': db_user.get('user_profile_type'),
            'user_profile_other': db_user.get('user_profile_other'),
            'main_sectors': db_user.get('main_sectors'),
            'sub_sectors': db_user.get('sub_sectors'),
            'content_style': db_user.get('content_style'),
            'company_name': db_user.get('company_name'),
            'website': db_user.get('website'),
            'instagram_handle': db_user.get('instagram_handle'),
        })
        
    except Exception as e:
        print(f"Get profile error: {e}")
        return response(500, {'message': 'Une erreur est survenue'})


def update_user_profile(event):
    """
    Update current user's profile
    PUT /api/user/profile
    Body: { name, picture, user_id (optional for OAuth users) }
    """
    try:
        # Verify token
        auth_header = event.get('headers', {}).get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return response(401, {'message': 'Token manquant'})
        
        access_token = auth_header.replace('Bearer ', '')
        body = json.loads(event.get('body', '{}'))
        
        user_id = None
        is_oauth_user = False
        
        # Try to get user info from Cognito
        try:
            user_info = cognito.get_user(AccessToken=access_token)
            user_attrs = {attr['Name']: attr['Value'] for attr in user_info.get('UserAttributes', [])}
            user_id = user_attrs.get('sub')
            
            # Check if this is an OAuth user (identities attribute present)
            identities = user_attrs.get('identities', '')
            is_oauth_user = 'Google' in identities or 'google' in identities
            
            # Update Cognito attributes only for non-OAuth users
            if not is_oauth_user and 'name' in body:
                cognito_updates = [{'Name': 'name', 'Value': body['name']}]
                cognito.update_user_attributes(
                    AccessToken=access_token,
                    UserAttributes=cognito_updates,
                )
        except cognito.exceptions.NotAuthorizedException:
            # Token might be an ID token or from OAuth - try to decode it
            try:
                # Decode JWT to get user_id
                import base64
                parts = access_token.split('.')
                if len(parts) == 3:
                    # Add padding if needed
                    payload = parts[1]
                    padding = 4 - len(payload) % 4
                    if padding != 4:
                        payload += '=' * padding
                    decoded = json.loads(base64.b64decode(payload))
                    user_id = decoded.get('sub')
                    is_oauth_user = True
            except Exception as decode_error:
                print(f"Token decode error: {decode_error}")
                return response(401, {'message': 'Token invalide ou expiré'})
        
        if not user_id:
            return response(401, {'message': 'Impossible d\'identifier l\'utilisateur'})
        
        # Ensure user exists in DynamoDB first (create if needed)
        now = datetime.utcnow().isoformat()
        
        # Check if user exists
        existing_user = users_table.get_item(Key={'user_id': user_id}).get('Item')
        
        if not existing_user:
            # Create new user record
            print(f"Creating new user in DynamoDB: {user_id}")
            users_table.put_item(Item={
                'user_id': user_id,
                'created_at': now,
                'updated_at': now,
                'provider': 'google' if is_oauth_user else 'email',
            })
        
        # Build update expression
        update_expr = 'SET updated_at = :updated_at'
        expr_values = {':updated_at': now}
        expr_names = {}
        
        if 'name' in body:
            update_expr += ', #name = :name'
            expr_values[':name'] = body['name']
            expr_names['#name'] = 'name'
        
        if 'picture' in body:
            update_expr += ', picture = :picture'
            expr_values[':picture'] = body['picture']
        
        # Pipeline preferences - stored as JSON object
        if 'pipeline_preferences' in body:
            update_expr += ', pipeline_preferences = :pipeline_prefs'
            expr_values[':pipeline_prefs'] = body['pipeline_preferences']
        
        # Individual pipeline fields for backward compatibility
        if 'user_profile_type' in body:
            update_expr += ', user_profile_type = :user_profile_type'
            expr_values[':user_profile_type'] = body['user_profile_type']
        
        if 'user_profile_other' in body:
            update_expr += ', user_profile_other = :user_profile_other'
            expr_values[':user_profile_other'] = body['user_profile_other']
        
        if 'main_sectors' in body:
            update_expr += ', main_sectors = :main_sectors'
            expr_values[':main_sectors'] = body['main_sectors']
        
        if 'sub_sectors' in body:
            update_expr += ', sub_sectors = :sub_sectors'
            expr_values[':sub_sectors'] = body['sub_sectors']
        
        if 'content_style' in body:
            update_expr += ', content_style = :content_style'
            expr_values[':content_style'] = body['content_style']
        
        if 'company_name' in body:
            update_expr += ', company_name = :company_name'
            expr_values[':company_name'] = body['company_name']
        
        if 'website' in body:
            update_expr += ', website = :website'
            expr_values[':website'] = body['website']
        
        if 'instagram_handle' in body:
            update_expr += ', instagram_handle = :instagram_handle'
            expr_values[':instagram_handle'] = body['instagram_handle']
        
        # Build update params
        update_params = {
            'Key': {'user_id': user_id},
            'UpdateExpression': update_expr,
            'ExpressionAttributeValues': expr_values,
        }
        
        # Only add ExpressionAttributeNames if we have any
        if expr_names:
            update_params['ExpressionAttributeNames'] = expr_names
        
        users_table.update_item(**update_params)
        
        return response(200, {'message': 'Profil mis à jour avec succès'})
        
    except cognito.exceptions.NotAuthorizedException:
        return response(401, {'message': 'Token invalide ou expiré'})
    except Exception as e:
        print(f"Update profile error: {e}")
        return response(500, {'message': 'Une erreur est survenue'})


def create_user_from_oauth(event):
    """
    Create/update user profile after OAuth authentication
    POST /api/user/profile
    Body: { user_id, email, name, picture, provider }
    """
    try:
        # Verify token
        auth_header = event.get('headers', {}).get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return response(401, {'message': 'Token manquant'})
        
        body = json.loads(event.get('body', '{}'))
        
        if not body.get('user_id') or not body.get('email'):
            return response(400, {'message': 'user_id and email are required'})
        
        success = sync_user_to_db(body)
        
        if success:
            return response(200, {'message': 'Profil créé/mis à jour avec succès'})
        else:
            return response(500, {'message': 'Erreur lors de la création du profil'})
        
    except Exception as e:
        print(f"Create OAuth user error: {e}")
        return response(500, {'message': 'Une erreur est survenue'})
