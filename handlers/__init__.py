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
    get_public_ambassadors,
    get_hero_videos
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

from handlers.showcase_generation import (
    start_showcase_generation,
    get_showcase_generation_status,
    generate_showcase_photos_async,
    select_showcase_photo,
)

from handlers.products import (
    get_products,
    get_product,
    create_product,
    update_product,
    delete_product,
    get_product_upload_url,
    increment_product_count,
)

from handlers.outfit_variations import (
    generate_outfit_variations,
    apply_outfit_variation,
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
    # Showcase Generation
    'start_showcase_generation',
    'get_showcase_generation_status',
    'generate_showcase_photos_async',
    'select_showcase_photo',
    # Products
    'get_products',
    'get_product',
    'create_product',
    'update_product',
    'delete_product',
    'get_product_upload_url',
    'increment_product_count',
    # Outfit Variations
    'generate_outfit_variations',
    'apply_outfit_variation',
]
