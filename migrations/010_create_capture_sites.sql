CREATE TABLE IF NOT EXISTS public.capture_sites (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    title VARCHAR NOT NULL,
    subtitle VARCHAR,
    button_text VARCHAR,
    button_link VARCHAR,
    benefits JSON,
    slug VARCHAR UNIQUE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_capture_sites_id ON public.capture_sites (id);
CREATE INDEX IF NOT EXISTS ix_capture_sites_slug ON public.capture_sites (slug);
