-- Migration: Add flat grid fields to grid_bots table
-- Description: Adds support for flat grid bot type with new parameters
-- Author: Claude
-- Date: 2025-01-16

BEGIN;

-- Make old range grid fields nullable (to support both types)
ALTER TABLE grid_bots ALTER COLUMN lower_price DROP NOT NULL;
ALTER TABLE grid_bots ALTER COLUMN upper_price DROP NOT NULL;
ALTER TABLE grid_bots ALTER COLUMN grid_levels DROP NOT NULL;
ALTER TABLE grid_bots ALTER COLUMN investment_amount DROP NOT NULL;

-- Add new flat grid fields
ALTER TABLE grid_bots ADD COLUMN IF NOT EXISTS flat_spread DECIMAL(20, 8);
ALTER TABLE grid_bots ADD COLUMN IF NOT EXISTS flat_increment DECIMAL(20, 8);
ALTER TABLE grid_bots ADD COLUMN IF NOT EXISTS buy_orders_count INTEGER;
ALTER TABLE grid_bots ADD COLUMN IF NOT EXISTS sell_orders_count INTEGER;
ALTER TABLE grid_bots ADD COLUMN IF NOT EXISTS starting_price DECIMAL(20, 8);
ALTER TABLE grid_bots ADD COLUMN IF NOT EXISTS order_size DECIMAL(20, 8);

-- Update grid_type default to 'flat' for new bots
-- Existing bots will keep 'arithmetic' or their current value
ALTER TABLE grid_bots ALTER COLUMN grid_type SET DEFAULT 'flat';

-- Add comments for documentation
COMMENT ON COLUMN grid_bots.flat_spread IS 'Spread between buy and sell orders (flat grid)';
COMMENT ON COLUMN grid_bots.flat_increment IS 'Step between grid levels (flat grid)';
COMMENT ON COLUMN grid_bots.buy_orders_count IS 'Number of buy orders (flat grid)';
COMMENT ON COLUMN grid_bots.sell_orders_count IS 'Number of sell orders (flat grid)';
COMMENT ON COLUMN grid_bots.starting_price IS 'Starting price - center of grid (0 = current market)';
COMMENT ON COLUMN grid_bots.order_size IS 'Size of each order in USDT';

COMMIT;

-- Rollback script (in case needed)
-- BEGIN;
-- ALTER TABLE grid_bots DROP COLUMN IF EXISTS flat_spread;
-- ALTER TABLE grid_bots DROP COLUMN IF EXISTS flat_increment;
-- ALTER TABLE grid_bots DROP COLUMN IF EXISTS buy_orders_count;
-- ALTER TABLE grid_bots DROP COLUMN IF EXISTS sell_orders_count;
-- ALTER TABLE grid_bots DROP COLUMN IF EXISTS starting_price;
-- ALTER TABLE grid_bots DROP COLUMN IF EXISTS order_size;
-- ALTER TABLE grid_bots ALTER COLUMN grid_type SET DEFAULT 'arithmetic';
-- COMMIT;
