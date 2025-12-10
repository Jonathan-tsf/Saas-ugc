"""
Booking and availability management handlers
"""
import json
import uuid
from datetime import datetime
from calendar import monthrange
from boto3.dynamodb.conditions import Attr

from config import (
    response, decimal_to_python, verify_admin,
    table, ses, OWNER_EMAIL
)


def get_availability(event):
    """Get availability for a month - GET /api/availability?month=2025-12"""
    params = event.get('queryStringParameters', {}) or {}
    month = params.get('month')  # Format: YYYY-MM
    
    if not month:
        return response(400, {'error': 'month parameter required (format: YYYY-MM)'})
    
    try:
        year, month_num = map(int, month.split('-'))
    except:
        return response(400, {'error': 'Invalid month format. Use YYYY-MM'})
    
    # Get custom availability settings for this month
    custom_settings = {}
    try:
        settings_response = table.get_item(Key={'id': f'SETTINGS#{month}'})
        custom_settings = settings_response.get('Item', {})
    except Exception as e:
        print(f"Error getting settings: {e}")
    
    # Get all bookings for this month (scan with filter)
    bookings = {}
    try:
        scan_response = table.scan(
            FilterExpression=Attr('type').eq('booking') & Attr('month').eq(month)
        )
        for item in scan_response.get('Items', []):
            slot_key = f"{item['date']}#{item['time']}"
            bookings[slot_key] = item
    except Exception as e:
        print(f"Error getting bookings: {e}")
    
    # Default working hours
    working_hours = custom_settings.get('working_hours', {
        'start': 10,
        'end': 18,
        'break_start': 12,
        'break_end': 14,
        'slot_duration': 30
    })
    
    # Blocked days (holidays, vacations)
    blocked_days = set(custom_settings.get('blocked_days', []))
    
    # Working days (default: Mon-Fri = 1-5)
    working_days = custom_settings.get('working_days', [1, 2, 3, 4, 5])
    
    # Generate days for the month
    days = []
    _, num_days = monthrange(year, month_num)
    today = datetime.now().date()
    
    for day in range(1, num_days + 1):
        date = datetime(year, month_num, day).date()
        date_str = date.strftime('%Y-%m-%d')
        day_of_week = date.weekday() + 1  # 1=Mon, 7=Sun
        if day_of_week == 7:
            day_of_week = 0  # Sunday = 0
        
        slots = []
        
        # Skip past days, weekends, blocked days
        if date >= today and day_of_week in working_days and date_str not in blocked_days:
            # Generate time slots
            start = working_hours['start']
            end = working_hours['end']
            break_start = working_hours['break_start']
            break_end = working_hours['break_end']
            duration = working_hours['slot_duration']
            
            current_hour = start
            current_minute = 0
            
            while current_hour < end:
                # Skip lunch break
                if current_hour >= break_start and current_hour < break_end:
                    current_hour = break_end
                    current_minute = 0
                    continue
                
                time_str = f"{current_hour:02d}:{current_minute:02d}"
                slot_key = f"{date_str}#{time_str}"
                
                # Check if slot is booked
                is_booked = slot_key in bookings
                
                # Check custom slot availability
                custom_slots = custom_settings.get('custom_slots', {})
                if date_str in custom_slots:
                    day_custom = custom_slots[date_str]
                    if time_str in day_custom.get('blocked', []):
                        is_booked = True
                    if time_str in day_custom.get('added', []):
                        is_booked = False
                
                slots.append({
                    'time': time_str,
                    'available': not is_booked,
                    'datetime': f"{date_str}T{time_str}:00"
                })
                
                # Next slot
                current_minute += duration
                if current_minute >= 60:
                    current_hour += 1
                    current_minute = 0
        
        days.append({
            'date': date_str,
            'dayOfWeek': day_of_week,
            'slots': slots
        })
    
    return response(200, {'days': decimal_to_python(days)})


def create_booking(event):
    """Create a new booking - POST /api/book-demo"""
    try:
        body = json.loads(event.get('body', '{}'))
    except:
        return response(400, {'error': 'Invalid JSON body'})
    
    name = body.get('name', '').strip()
    email = body.get('email', '').strip()
    start_time = body.get('start_time', '').strip()  # Format: 2025-12-10T14:00:00
    profile_type = body.get('profile_type')
    offer = body.get('offer')
    answers = body.get('answers', {})
    
    if not name or not email or not start_time:
        return response(400, {'error': 'name, email and start_time are required'})
    
    # Parse datetime
    try:
        dt = datetime.fromisoformat(start_time.replace('Z', ''))
        date_str = dt.strftime('%Y-%m-%d')
        time_str = dt.strftime('%H:%M')
        month_str = dt.strftime('%Y-%m')
    except Exception as e:
        return response(400, {'error': f'Invalid start_time format: {e}'})
    
    # Check if slot is already booked
    try:
        scan_response = table.scan(
            FilterExpression=Attr('type').eq('booking') & Attr('date').eq(date_str) & Attr('time').eq(time_str)
        )
        if scan_response.get('Items'):
            return response(409, {'error': 'This slot is already booked'})
    except Exception as e:
        print(f"Error checking slot: {e}")
    
    # Create booking with unique ID
    booking_id = str(uuid.uuid4())
    created_at = datetime.now().isoformat()
    
    booking = {
        'id': booking_id,
        'type': 'booking',
        'name': name,
        'email': email,
        'date': date_str,
        'time': time_str,
        'month': month_str,
        'start_time': start_time,
        'profile_type': profile_type,
        'offer': offer,
        'answers': answers,
        'status': 'confirmed',
        'created_at': created_at
    }
    
    try:
        table.put_item(Item=booking)
    except Exception as e:
        print(f"Error creating booking: {e}")
        return response(500, {'error': 'Failed to create booking'})
    
    # Send confirmation emails
    try:
        send_confirmation_emails(booking)
    except Exception as e:
        print(f"Error sending emails: {e}")
    
    return response(201, {
        'success': True,
        'booking_id': booking_id,
        'message': 'Booking confirmed'
    })


def send_confirmation_emails(booking):
    """Send confirmation emails to owner and client"""
    name = booking['name']
    email = booking['email']
    date = booking['date']
    time = booking['time']
    profile_type = booking.get('profile_type', 'Non spécifié')
    offer = booking.get('offer', 'Non spécifié')
    answers = booking.get('answers', {})
    
    # Format date nicely
    dt = datetime.strptime(date, '%Y-%m-%d')
    formatted_date = dt.strftime('%A %d %B %Y')
    
    # Email to owner
    owner_html = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background: #1a1a1a; color: #fff; padding: 20px;">
        <div style="max-width: 600px; margin: 0 auto; background: #2a2a2a; border-radius: 12px; padding: 30px;">
            <h1 style="color: #22c55e;">🎉 Nouvelle réservation de démo !</h1>
            
            <div style="background: #333; padding: 20px; border-radius: 8px; margin: 20px 0;">
                <h2 style="margin-top: 0;">Informations client</h2>
                <p><strong>Nom:</strong> {name}</p>
                <p><strong>Email:</strong> {email}</p>
                <p><strong>Type:</strong> {profile_type}</p>
                <p><strong>Offre:</strong> {offer}</p>
            </div>
            
            <div style="background: #333; padding: 20px; border-radius: 8px; margin: 20px 0;">
                <h2 style="margin-top: 0;">📅 Rendez-vous</h2>
                <p style="font-size: 18px;"><strong>{formatted_date}</strong></p>
                <p style="font-size: 24px; color: #22c55e;"><strong>{time}</strong></p>
            </div>
            
            {f'''<div style="background: #333; padding: 20px; border-radius: 8px; margin: 20px 0;">
                <h2 style="margin-top: 0;">📝 Réponses onboarding</h2>
                <pre style="white-space: pre-wrap; font-size: 12px;">{json.dumps(answers, indent=2, ensure_ascii=False)}</pre>
            </div>''' if answers else ''}
        </div>
    </body>
    </html>
    """
    
    # Email to client
    client_html = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background: #1a1a1a; color: #fff; padding: 20px;">
        <div style="max-width: 600px; margin: 0 auto; background: #2a2a2a; border-radius: 12px; padding: 30px;">
            <h1 style="color: #22c55e;">✅ Ta démo est confirmée !</h1>
            
            <p>Salut {name} 👋</p>
            <p>Merci d'avoir réservé une démo avec UGC Studio. On a hâte de te montrer comment on peut booster ton contenu !</p>
            
            <div style="background: #333; padding: 20px; border-radius: 8px; margin: 20px 0; text-align: center;">
                <h2 style="margin-top: 0;">📅 Ton rendez-vous</h2>
                <p style="font-size: 18px;"><strong>{formatted_date}</strong></p>
                <p style="font-size: 32px; color: #22c55e; margin: 10px 0;"><strong>{time}</strong></p>
                <p style="color: #888;">Durée: 30 minutes</p>
            </div>
            
            <div style="background: #333; padding: 20px; border-radius: 8px; margin: 20px 0;">
                <h2 style="margin-top: 0;">📞 Comment ça va se passer ?</h2>
                <ul style="color: #ccc;">
                    <li>On t'appellera sur Google Meet</li>
                    <li>Tu recevras le lien 1h avant</li>
                    <li>Prépare tes questions !</li>
                </ul>
            </div>
            
            <p style="color: #888; font-size: 12px; margin-top: 30px;">
                Si tu dois annuler ou reporter, réponds simplement à cet email.
            </p>
        </div>
    </body>
    </html>
    """
    
    # Send to owner
    try:
        ses.send_email(
            Source=OWNER_EMAIL,
            Destination={'ToAddresses': [OWNER_EMAIL]},
            Message={
                'Subject': {'Data': f'🎉 Nouvelle démo: {name} - {date} à {time}'},
                'Body': {'Html': {'Data': owner_html}}
            }
        )
    except Exception as e:
        print(f"Failed to send owner email: {e}")
    
    # Send to client
    try:
        ses.send_email(
            Source=OWNER_EMAIL,
            Destination={'ToAddresses': [email]},
            Message={
                'Subject': {'Data': '✅ Ta démo UGC Studio est confirmée !'},
                'Body': {'Html': {'Data': client_html}}
            }
        )
    except Exception as e:
        print(f"Failed to send client email: {e}")


def get_bookings(event):
    """Get all bookings (admin only) - GET /api/admin/bookings"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    params = event.get('queryStringParameters', {}) or {}
    month = params.get('month')
    
    try:
        if month:
            scan_response = table.scan(
                FilterExpression=Attr('type').eq('booking') & Attr('month').eq(month)
            )
        else:
            scan_response = table.scan(
                FilterExpression=Attr('type').eq('booking')
            )
        
        bookings = [decimal_to_python(item) for item in scan_response.get('Items', [])]
        bookings.sort(key=lambda x: (x.get('date', ''), x.get('time', '')))
        return response(200, {'bookings': bookings})
    except Exception as e:
        print(f"Error getting bookings: {e}")
        return response(500, {'error': 'Failed to get bookings'})


def delete_booking(event):
    """Delete a booking (admin only) - DELETE /api/admin/bookings/{id}"""
    if not verify_admin(event):
        return response(401, {'error': 'Unauthorized'})
    
    params = event.get('pathParameters', {}) or {}
    query_params = event.get('queryStringParameters', {}) or {}
    booking_id = params.get('id') or query_params.get('id')
    
    if not booking_id:
        return response(400, {'error': 'booking id required'})
    
    try:
        table.delete_item(Key={'id': booking_id})
        return response(200, {'success': True})
    except Exception as e:
        print(f"Error deleting booking: {e}")
        return response(500, {'error': 'Failed to delete booking'})
