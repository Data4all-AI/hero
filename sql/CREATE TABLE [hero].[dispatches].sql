SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
CREATE TABLE [hero].[dispatches](
	[dispatch_id] [int] IDENTITY(1000,1) NOT NULL,
	[emergency_reporting_number] [int] NULL,
	[dispatch_number] [int] NULL,
	[status] [varchar](20) NULL,
	[dispatch_triage_code] [varchar](10) NULL,
	[incident_location_latitude] [decimal](8, 6) NULL,
	[incident_location_longitude] [decimal](9, 6) NULL,
	[vehicle_number] [varchar](20) NULL,
	[vehicle_origin_latitude] [decimal](8, 6) NULL,
	[dispatch_datetime] [datetime2](7) NOT NULL,
	[vehicle_origin_longitude] [decimal](9, 6) NULL,
	[incident_coordinate] [nvarchar](50) NULL,
	[origin_coordinate] [nvarchar](50) NULL
) ON [PRIMARY]
GO
ALTER TABLE [hero].[dispatches] ADD PRIMARY KEY CLUSTERED 
(
	[dispatch_id] ASC
)WITH (STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ONLINE = OFF, OPTIMIZE_FOR_SEQUENTIAL_KEY = OFF) ON [PRIMARY]
GO
ALTER TABLE [hero].[dispatches] ADD  DEFAULT (getdate()) FOR [dispatch_datetime]
GO
