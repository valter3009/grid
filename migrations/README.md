# Database Migrations

This directory contains SQL migration scripts for the grid trading bot database.

## How to Apply Migrations

### Option 1: Using psql (recommended)

```bash
# Connect to your database and run the migration
psql -h localhost -U your_user -d your_database -f migrations/001_add_flat_grid_fields.sql
```

### Option 2: Using Docker

```bash
# If using docker-compose
docker-compose exec postgres psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -f /path/to/migration.sql
```

### Option 3: Manually via PgAdmin or similar tool

1. Connect to your database
2. Open the migration file
3. Execute the SQL script

## Migration List

| File | Description | Status |
|------|-------------|--------|
| 001_add_flat_grid_fields.sql | Adds flat grid support to grid_bots table | Ready |

## Notes

- **IMPORTANT**: Always backup your database before running migrations!
- Migrations are designed to be safe and non-destructive
- Old range grid bots will continue to work after migration
- The migration makes old fields nullable to support both grid types

## Verifying Migration

After running the migration, verify the new columns exist:

```sql
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'grid_bots'
ORDER BY ordinal_position;
```

You should see the new columns:
- flat_spread
- flat_increment
- buy_orders_count
- sell_orders_count
- starting_price
- order_size
