-- Layered warehouse schemas (see docs/SCOPE.md, "Architecture").
CREATE SCHEMA IF NOT EXISTS raw;      -- immutable source snapshots live on disk; this holds metadata only
CREATE SCHEMA IF NOT EXISTS staging;  -- typed, source-shaped tables with row-level lineage
CREATE SCHEMA IF NOT EXISTS core;     -- standardized dimensions and fact_observation
CREATE SCHEMA IF NOT EXISTS marts;    -- analytical tables and views
CREATE SCHEMA IF NOT EXISTS ml;       -- model-ready feature tables
CREATE SCHEMA IF NOT EXISTS dq;       -- data-quality run results and metrics
