CREATE TABLE IF NOT EXISTS public.custom_links (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    name VARCHAR NOT NULL,
    original_url VARCHAR NOT NULL,
    slug VARCHAR UNIQUE NOT NULL,
    tag VARCHAR,
    expires_at TIMESTAMP WITH TIME ZONE,
    click_count INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_custom_links_slug ON public.custom_links (slug);
CREATE INDEX IF NOT EXISTS ix_custom_links_user_id ON public.custom_links (user_id);
