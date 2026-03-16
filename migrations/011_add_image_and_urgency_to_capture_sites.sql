-- Migration to add image_url and urgency_text to capture_sites

ALTER TABLE capture_sites ADD COLUMN image_url VARCHAR(255) NULL;
ALTER TABLE capture_sites ADD COLUMN urgency_text VARCHAR(255) NULL;
