SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO

CREATE PROCEDURE [hero].[RunFakeDispatchStream]
    @MinDelaySec INT = 60,   -- minimum wait (default 1 minute)
    @MaxDelaySec INT = 300   -- maximum wait (default 5 minutes)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE
        @emergency_reporting_number INT = 10000,
        @dispatch_number INT = 1,
        @status NVARCHAR(20),
        @triage_code NVARCHAR(10),
        @incident_lat DECIMAL(8,6),
        @incident_lon DECIMAL(9,6),
        @vehicle NVARCHAR(20),
        @hours INT,
        @minutes INT,
        @seconds INT,
        @wait_str CHAR(8),
        @wait_seconds INT;

    PRINT 'Starting fake dispatch stream... (press Stop/Cancel to end)';

    WHILE 1 = 1  -- infinite loop
    BEGIN
        -- random status: dispatched / cancelled
        SET @status = CASE ABS(CHECKSUM(NEWID())) % 2
                        WHEN 0 THEN 'dispatched'
                        ELSE 'dispatched' --cancelled
                      END;

        -- random triage: rosso / giallo / verde
        SET @triage_code = CASE ABS(CHECKSUM(NEWID())) % 3
                             WHEN 0 THEN 'red'
                             WHEN 1 THEN 'yellow'
                             ELSE 'green'
                           END;

        -- random coordinates around Milan (roughly 45.45–45.52 N, 9.15–9.25 E)
        SET @incident_lat = 45.45 + (RAND(CHECKSUM(NEWID())) * 0.07);
        SET @incident_lon = 9.15 + (RAND(CHECKSUM(NEWID())) * 0.10);

        -- random vehicle between AMB001–AMB005
        SET @vehicle = CONCAT('AMB00', (ABS(CHECKSUM(NEWID())) % 5) + 1);

        -- incrementing IDs (loosely)
        SET @emergency_reporting_number += (ABS(CHECKSUM(NEWID())) % 5) + 1;
        SET @dispatch_number += 1;

        -- insert the fake row
        INSERT INTO [hero].[dispatches]
            ([emergency_reporting_number],
             [dispatch_number],
             [status],
             [dispatch_triage_code],
             [incident_location_latitude],
             [incident_location_longitude],
             [vehicle_number],
             [vehicle_origin_latitude],
             [vehicle_origin_longitude],
             [origin_coordinate],
             [incident_coordinate])
        VALUES
            (@emergency_reporting_number,
             @dispatch_number,
             @status,
             @triage_code,
             @incident_lat,
             @incident_lon,
             @vehicle,
             45.51281686755878,  -- fixed origin osp niguarda, 
             9.184800834657725, -- fixed origin osp niguarda
             '45.51281686755878,9.184800834657725',
             CONCAT(@incident_lat,',',@incident_lon)); 

        PRINT CONCAT(
            FORMAT(GETDATE(), 'yyyy-MM-dd HH:mm:ss'),
            'Inserted dispatch ', @dispatch_number, 
            ' (', @status, ', ', @triage_code, ', ', @vehicle, ')'
        );

        -- random wait between min and max delay
        SET @wait_seconds = @MinDelaySec + (ABS(CHECKSUM(NEWID())) % (@MaxDelaySec - @MinDelaySec + 1));

        -- build hh:mm:ss string for WAITFOR
        SET @hours = @wait_seconds / 3600;
        SET @minutes = (@wait_seconds % 3600) / 60;
        SET @seconds = @wait_seconds % 60;

        SET @wait_str = RIGHT('0' + CAST(@hours AS VARCHAR(2)), 2) + ':' +
                        RIGHT('0' + CAST(@minutes AS VARCHAR(2)), 2) + ':' +
                        RIGHT('0' + CAST(@seconds AS VARCHAR(2)), 2);

        PRINT CONCAT('Waiting ', @wait_seconds, ' seconds (', @wait_str, ')...');
        
        WAITFOR DELAY @wait_str;
    END
END;
GO
