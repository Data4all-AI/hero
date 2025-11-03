EXEC sys.sp_cdc_enable_db
GO;

EXEC sys.sp_cdc_enable_table
    @source_schema = N'hero',
    @source_name   = N'dispatches',
    @role_name     = NULL
GO

