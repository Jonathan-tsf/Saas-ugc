"""
Contact form handler
"""
import json
from datetime import datetime

from config import response, ses, OWNER_EMAIL


def send_contact_email(event):
    """Send contact form email - POST /api/contact"""
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    name = body.get('name', '').strip()
    email = body.get('email', '').strip()
    message = body.get('message', '').strip()
    
    if not name or not email or not message:
        return response(400, {'error': 'name, email and message are required'})
    
    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background: #1a1a1a; color: #fff; padding: 20px;">
        <div style="max-width: 600px; margin: 0 auto; background: #2a2a2a; border-radius: 12px; padding: 30px;">
            <h1 style="color: #22c55e;">📬 Nouveau message de contact</h1>
            
            <div style="background: #333; padding: 20px; border-radius: 8px; margin: 20px 0;">
                <h2 style="margin-top: 0;">Informations</h2>
                <p><strong>Nom:</strong> {name}</p>
                <p><strong>Email:</strong> <a href="mailto:{email}" style="color: #22c55e;">{email}</a></p>
            </div>
            
            <div style="background: #333; padding: 20px; border-radius: 8px; margin: 20px 0;">
                <h2 style="margin-top: 0;">💬 Message</h2>
                <p style="white-space: pre-wrap; line-height: 1.6;">{message}</p>
            </div>
            
            <p style="color: #888; font-size: 12px; margin-top: 20px;">
                Reçu le {datetime.now().strftime('%d/%m/%Y à %H:%M')}
            </p>
        </div>
    </body>
    </html>
    """
    
    try:
        ses.send_email(
            Source=OWNER_EMAIL,
            Destination={'ToAddresses': [OWNER_EMAIL]},
            Message={
                'Subject': {'Data': f'📬 Nouveau message de {name}'},
                'Body': {'Html': {'Data': html_content}}
            },
            ReplyToAddresses=[email]
        )
        return response(200, {'success': True, 'message': 'Email sent successfully'})
    except Exception as e:
        print(f"Error sending contact email: {e}")
        return response(500, {'error': 'Failed to send email'})
