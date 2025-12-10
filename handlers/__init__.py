"""
Handlers package - export all handlers
"""
from handlers.bookings import (
    get_availability,
    create_booking,
    get_bookings,
    delete_booking
)

from handlers.admin import (
    admin_login,
    update_availability_settings,
    get_availability_settings
)

from handlers.contact import send_contact_email

from handlers.ambassadors import (
    get_ambassadors,
    get_ambassador,
    create_ambassador,
    update_ambassador,
    delete_ambassador,
    get_upload_url,
    get_public_ambassadors
)

from handlers.transform_async import (
    start_transformation,
    continue_transformation,
    get_transformation_session,
    finalize_ambassador,
)

from handlers.outfits import (
    get_outfits,
    get_outfit,
    create_outfit,
    update_outfit,
    delete_outfit,
    get_upload_url as get_outfit_upload_url,
    increment_outfit_count,
)

from handlers.outfit_generation import (
    start_outfit_generation,
    get_outfit_generation_status,
    select_outfit_image,
    generate_outfit_photos_async,
)

__all__ = [
    # Bookings
    'get_availability',
    'create_booking',
    'get_bookings',
    'delete_booking',
    # Admin
    'admin_login',
    'update_availability_settings',
    'get_availability_settings',
    # Contact
    'send_contact_email',
    # Ambassadors
    'get_ambassadors',
    'get_ambassador',
    'create_ambassador',
    'update_ambassador',
    'delete_ambassador',
    'get_upload_url',
    'get_public_ambassadors',
    # Transform
    'start_transformation',
    'continue_transformation',
    'get_transformation_session',
    'finalize_ambassador',
    # Outfits
    'get_outfits',
    'get_outfit',
    'create_outfit',
    'update_outfit',
    'delete_outfit',
    'get_outfit_upload_url',
    'increment_outfit_count',
    # Outfit Generation
    'start_outfit_generation',
    'get_outfit_generation_status',
    'select_outfit_image',
    'generate_outfit_photos_async',
]
