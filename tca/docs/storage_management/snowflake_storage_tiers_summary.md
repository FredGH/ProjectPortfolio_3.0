# Snowflake Storage Tiers: HOT, COOL, and COLD – Summary

## Overview
Snowflake provides multiple storage tiers to balance cost and performance based on data access patterns:
- **HOT (Standard)**: Active, frequently accessed data.
- **COOL**: Archived data with instant retrieval and moderate cost savings.
- **COLD**: Deep archive for long‑term retention with lowest storage cost but higher retrieval latency.

## HOT (Standard) Storage
- **Use case**: Active tables, recent data, workloads requiring low‑latency queries.
- **Performance**: Instant retrieval (micro‑second to millisecond latency).
- **Retention**: No minimum retention period; data can be deleted or moved at any time.
- **Cost**: Highest storage price per TB/month (varies by cloud provider, region, and edition; e.g., ~$23‑$25/TB/month with Capacity pricing, ~$40‑$45/TB/month with On‑Demand).

## COOL Storage
- **Use case**: Infrequently accessed historical data that still needs quick access (e.g., monthly reports, compliance queries).
- **Performance**: Instant retrieval (similar to HOT) once data is archived.
- **Retention**: **Minimum 90‑day archival period**. If data is deleted before 90 days, you may still be charged for the full period.
- **Cost**: Lower than HOT—typically **up to 4× less expensive** than HOT storage (exact savings depend on region and pricing model).
- **Additional charges**:
  - One‑time serverless compute fee to move data from HOT to COOL during policy execution.
  - Daily serverless compute for running the lifecycle policy.
  - Retrieval cost (usually negligible for COOL, as data is instantly available).
  - If you drop a table with COOL data before the 90‑day minimum, you pay for the remaining days.

## COLD Storage
- **Use case**: Rarely accessed data kept for long‑term compliance or historical analysis (e.g., multi‑year archives).
- **Performance**: Retrieval can take **up to 48 hours**; data is temporarily restored to standard storage when queried.
- **Retention**: **Minimum 180‑day archival period**. Early deletion incurs charges for the remaining period.
- **Cost**: **Lowest storage tier**—about **4× less expensive than COOL** (and up to 16× less expensive than HOT) according to Snowflake documentation.
- **Additional charges**:
  - One‑time serverless compute fee to archive data (same as COOL).
  - Daily lifecycle policy compute.
  - **Retrieval cost**: One‑time charge to fetch data from COLD.
  - **Temporary storage**: When querying COLD data, Snowflake temporarily stores the retrieved data in standard storage, incurring standard storage charges for the duration of the query result.
  - Minimum duration charges if data is dropped before 180 days.

## Setting Up Tiered Storage with Lifecycle Policies
Snowflake uses **storage lifecycle policies** (schema‑level objects) to automatically move rows between tiers based on conditions (e.g., data age).

### 1. Create a Policy
```sql
CREATE STORAGE LIFECYCLE POLICY my_cool_policy
  AS (event_ts TIMESTAMP)
  RETURNS BOOLEAN ->
    event_ts < DATEADD(DAY, -90, CURRENT_TIMESTAMP())   -- older than 90 days
  ARCHIVE_TIER = COOL
  ARCHIVE_FOR_DAYS = 90;   -- optional: keep in COOL for 90 days before expiring
```

For COLD:
```sql
CREATE STORAGE LIFECYCLE POLICY my_cold_policy
  AS (event_ts TIMESTAMP)
  RETURNS BOOLEAN ->
    event_ts < DATEADD(DAY, -180, CURRENT_TIMESTAMP())   -- older than 180 days
  ARCHIVE_TIER = COLD
  ARCHIVE_FOR_DAYS = 180;
```

### 2. Attach to a Table
When creating a table:
```sql
CREATE TABLE fact_orders (
  order_id NUMBER,
  event_ts TIMESTAMP,
  ...
)
WITH STORAGE LIFECYCLE POLICY my_cool_policy ON (event_ts);
```

Or alter an existing table:
```sql
ALTER TABLE fact_orders
  ADD STORAGE LIFECYCLE POLICY my_cool_policy ON (event_ts);
```

### 3. Policy Execution
- Snowflake runs the policy **daily** (after an initial ~24‑hour delay) using shared compute.
- Rows matching the condition are moved to the specified archive tier (COOL or COLD).
- Data remains in the table for the **Time Travel + Fail‑safe period** (default 7 + 7 = 14 days) before being fully available in archive only.
- You can retrieve archived data before expiration with `CREATE TABLE … FROM ARCHIVE OF`.

### 4. Monitoring
- Check policy history: `SELECT * FROM INFORMATION_SCHEMA.STORAGE_LIFECYCLE_POLICY_HISTORY();`
- View storage usage per tier via `ACCOUNT_USAGE.STORAGE_USAGE`.

## Cost Considerations
| Tier          | Storage Cost (relative) | Retrieval Latency | Minimum Retention | Key Cost Factors |
|---------------|-------------------------|-------------------|-------------------|------------------|
| HOT (Standard)| 1× (base)               | Instant           | None              | Storage TB/month |
| COOL          | ~0.25× HOT (up to 4× savings) | Instant   | 90 days           | Archiving compute, policy runs, early‑deletion charges |
| COLD          | ~0.0625× HOT (up to 16× savings) | Up to 48 h | 180 days          | Archiving compute, retrieval fees, temporary storage during query, early‑deletion charges |

*Note: Exact multipliers depend on region, cloud provider (AWS/Azure/GCP), and pricing model (On‑Demand vs Capacity).*

## Back‑Fill Strategies (Fast & Cost‑Effective)
While not the focus of this summary, the conversation covered efficient back‑filling in Snowflake:

### 1. COPY INTO with Parallelism
- **File sizing**: Split data into multiple files (100-250 MB each) to enable optimal parallel processing
- **Warehouse sizing**: Use appropriately sized warehouses - scale out (more smaller warehouses) rather than up (fewer larger warehouses) for better parallelism
- **Parallel loading**: Snowflake can process multiple files simultaneously when using COPY INTO
- **Compression**: Compress files (gzip, bz2, etc.) to reduce data transfer size and improve throughput
- **Pattern matching**: Use PATTERN parameter to load specific file sets when dealing with partitioned data
- **Validation**: Use VALIDATION_MODE to check data integrity before committing to load
- **Error handling**: Set ON_ERROR = 'CONTINUE' or 'SKIP_FILE' to handle problematic files gracefully

### 2. Snowpipe for Continuous/Staged Backfills
- **Serverless architecture**: Automatically scales compute resources based on load
- **Cost efficiency**: Pay only for ingested data (per-byte) rather than warehouse runtime
- **Auto-ingest**: Configure event notifications from cloud storage (S3 Event Notifications, Azure Event Grid, GCS Pub/Sub) for automatic triggering
- **REST API**: Use Snowpipe REST API for manual triggering when needed
- **Exactly-once processing**: Built-in deduplication prevents duplicate data ingestion
- **Lower operational overhead**: No warehouse management, scaling, or suspend/resume concerns
- **File size optimization**: Works best with files in the 100-250 MB range after compression
- **Error notifications**: Configure notifications for pipe errors via ACCOUNT_USAGE or ALERT mechanisms

### 3. MERGE/UPSERT Patterns
- **INSERT ... WHEN NOT MATCHED**: Ideal for insert-only backfills where source data contains only new records
- **MERGE statement**: Use for true upserts (INSERT new records, UPDATE existing matches)
- **Join optimization**: Cluster target tables on join/date keys to enable efficient partition pruning
- **Change data capture**: Use CDC tools or Snowflake Streams to identify only changed data since last backfill
- **Batch processing**: Process data in reasonable batches (e.g., 10K-1M rows) to balance transaction overhead with resource usage
- **Duplicate handling**: Use QUALIFY ROW_NUMBER() OVER (PARTITION BY key ORDER BY ts DESC) = 1 to deduplicate source data
- **Performance monitoring**: Use QUERY_HISTORY to monitor MERGE performance and adjust clustering or batch sizes

### 4. Staging & Transformation Approach
- **Staging tables**: Load raw data into temporary or transient tables first
- **SQL transformations**: Perform all data cleansing, enrichment, and transformations using SQL in staging
- **Table swapping**: Use ALTER TABLE ... SWAP WITH or CREATE OR REPLACE TABLE to atomically replace production data
- **Lock minimization**: Reduces contention on production tables since transforms happen offline
- **Validation gates**: Add data quality checks in staging before making data available to consumers
- **Transient tables**: Use transient tables for staging to avoid Fail-safe costs (no 7-day Fail-safe for transient tables)
- **Schema evolution**: Easily handle schema changes in staging before promoting to production

### 5. Auto-suspend Warehouses
- **Aggressive suspend**: Set AUTO_SUSPEND=60 (or lower like 30/10 seconds) to minimize idle compute costs
- **Right-sizing**: Size warehouse for expected workload but let auto-suspend handle idle periods
- **Multi-cluster warehouses**: Consider for variable workloads to enable automatic scaling
- **Queue monitoring**: Monitor QUEUE_TIME in WAREHOUSE_LOAD_HISTORY to ensure adequate sizing
- **Startup factor**: Consider warehouse startup time when setting AUTO_SUSPEND (very low values may cause thrashing)
- **Credit usage**: Credits consumed only when warehouse is running; suspended warehouses consume zero compute credits

### 6. Clustering & Partitioning Strategy
- **Clustering keys**: Cluster large fact tables on backfill dimensions (event_date, load_timestamp, tenant_id)
- **Micro-partition pruning**: Clustering enables Snowflake to skip irrelevant micro-partitions during scans
- **Automatic clustering**: Consider enabling for tables with evolving data patterns where manual clustering keys are hard to define
- **Clustering depth**: Monitor via SYSTEM$CLUSTERING_INFORMATION and re-cluster if depth grows too high
- **Partition granularity**: Use DATE_TRUNC('DAY', event_timestamp) for daily partitioning or similar for other grains
- **Multi-dimensional clustering**: Cluster on multiple columns when queries filter on several dimensions
- **Re-clustering schedule**: Schedule during off-peak hours using TASKS if manual re-clustering needed

### 7. Validation with Time Travel
- **Historical queries**: Use AT(OFFSET => -60) to view table state 60 seconds ago or TIMESTAMP => '2026-04-23 10:00:00' for specific point-in-time
- **Comparison techniques**: 
  - Row count validation: SELECT COUNT(*) FROM table AT(OFFSET => -60)
  - Checksum validation: Using HASH_AGG(*) or similar techniques
  - Sample data comparison: LIMIT 1000 ORDER BY rand() comparisons
- **Clone for detailed analysis**: CREATE TABLE validation_clone CLONE table AT(TIMESTAMP => '...')
- **No re-scanning needed**: Validation uses Snowflake's metadata, avoiding expensive re-scans of source data
- **Time Travel retention**: Depends on edition (1 day Standard, 7-30 days Enterprise, up to 90 days for Enterprise with add-on)
- **Fail-safe consideration**: Remember Time Travel + Fail-safe (7 days) provides total recovery window

These approaches separate compute (pay‑per‑second) from storage (flat‑rate), allowing you to burst compute for speed then scale down to minimize cost.

## Back‑Fill Strategy Selection Guide

| Scenario | Data Volume | Frequency | Transformation Complexity | Recommended Subset of Strategies |
|----------|-------------|-----------|---------------------------|-----------------------------------|
| **A – Small, Frequent, Light** | < 1 GB per run | Daily or more | None / Minimal | • Snowpipe (serverless) for continuous ingest  <br>• Auto‑suspend warehouse (AUTO_SUSPEND=10‑30 s)  <br>• Validate with Time Travel  <br>• Optional clustering on target table |
| **B – Small, Frequent, Complex** | < 1 GB per run | Daily or more | Complex (multiple joins, UDFs) | • Staging → Transform → Swap (transient table)  <br>• Auto‑suspend warehouse  <br>• Validate with Time Travel  <br>• Clustering on target table |
| **C – Medium, Batch, Light** | 1‑100 GB per run | Weekly / Monthly | None / Minimal | • **COPY INTO** with parallel files (100-250 MB) and appropriately sized warehouse  <br>• Auto‑suspend warehouse (AUTO_SUSPEND=60 s)  <br>• Clustering on target table (date/key)  <br>• Validate with Time Travel  <br>• Optional Snowpipe if you want trigger‑less loading |
| **D – Medium, Batch, Complex** | 1‑100 GB per run | Weekly / Monthly | Light / Moderate SQL transforms | • Staging → Transform → Swap (using transient table)  <br>• COPY INTO into staging (parallel files)  <br>• Auto‑suspend warehouse  <br>• Clustering on target table  <br>• Validate with Time Travel |
| **E – Large, Batch, Light** | > 100 GB per run | Weekly / Monthly | None / Minimal | • COPY INTO with extensive parallelism (many 100‑250 MB files)  <br>• Larger warehouse (size‑out) + auto‑suspend  <br>• Clustering (critical for pruning)  <br>• Validate with Time Travel  <br>• Consider Snowpipe for continuous micro‑batches if latency matters |
| **F – Large, Batch, Complex** | > 100 GB per run | Weekly / Monthly | Complex transforms | • Staging → Transform → Swap (transient)  <br>• COPY INTO into staging (massive parallelism)  <br>• Auto‑suspend warehouse  <br>• Clustering on target table  <br>• Validate with Time Travel  <br>• Use result‑scan or temporary tables for intermediate steps |

### Where 25 million records/month lands

*Assumptions*  
- Typical record size ~1 KB → ~25 GB/month (medium volume).  
- Frequency: monthly batch (not daily/continuous).  
- Transformation: most backfills need at least light SQL cleaning/casting → **light/mod**.

**Best‑fit scenario:** **C** (Medium, Batch, Light) or **D** if you have light SQL transforms.

**Recommended subset for 25 M records/month**

1. **COPY INTO** – Source data can reside in external stages such as AWS S3, Azure Blob Storage, or Google Cloud Storage (or Snowflake internal stages). Split source files into 100‑250 MB chunks (compressed) using tools like Unix `split -b 200M` or Python scripting; load with a warehouse sized for expected throughput (e.g., X‑Small to Small), enable auto‑suspend (`AUTO_SUSPEND=60`).
2. **Dedicated Warehouse** – Use a separate, appropriately sized warehouse for backfills to isolate workloads and enable independent scaling/suspension.
3. **Clustering** – Cluster the target table on the backfill dimension (e.g., `event_date` or `load_timestamp`) to allow micro‑partition pruning during subsequent queries/MERGES.
4. **Auto‑suspend warehouse** – `AUTO_SUSPEND=60` automatically pauses the warehouse after 60 seconds of idle time, saving compute credits while allowing rapid resumption for subsequent batches.
5. **Staging → Transform → Swap** – If any SQL cleaning or derived columns are needed, follow a medallion‑style approach using transient tables:
   a. **Bronze (raw) load**: Use COPY INTO to load the raw data exactly as‑is from the external stage into a transient bronze table.
   b. **Silver (cleaned) transform**: Create or truncate a transient silver table, then run SQL transformations (cleaning, filtering, derivations, calculations, joins with reference data, applying business rules) against the bronze table to populate the silver table. This keeps transformations isolated and repeatable.
   c. **Atomic swap**: Once the silver table is validated, swap it with the production table using `ALTER TABLE … SWAP WITH`. This metadata‑only operation is nearly instantaneous, incurs no data copying, and minimizes lock contention/downtime for concurrent readers/writers. After the swap, the former production table becomes a transient table you can drop or reuse for the next cycle.
   Alternatively, if transformations are lightweight and you prefer a single step, you can load directly into a transient table, transform it in place, then swap—achieving the same isolation and atomic cut‑over.
6. **Validate with Time Travel** – After load, run a quick `SELECT COUNT(*) FROM table AT(OFFSET => -60)` or compare a hash aggregate to ensure no data loss/duplication.
7. (Optional) **Snowpipe** – If you prefer a fully managed, trigger‑less ingest, configure auto‑ingest from your storage bucket; otherwise stick with manual/ scheduled COPY INTO.

This combination gives you parallel, cost‑controlled loading, efficient query performance via clustering, minimal idle compute cost, and a safety net for validation—all appropriate for a medium‑sized monthly backfill.

**File count for 25 M records/month**

25 GB ≈ 25 × 1024 MB = 25 600 MB
Chunk size = 250 MB → 25 600 / 250 ≈ 102.4 → 103 chunks (round up)
Parallelism per warehouse (Snowflake’s compute model)

Warehouse	Approx. servers	Approx. cores/threads*
X‑Small	1	~8 threads
Small	2	~16 threads
*Each server provides ~8 vCPUs that can be used for parallel file ingest.		
Estimated load time (parallel COPY INTO)
Assume a realistic ingest rate of ~50 MB /s per thread (conservative for compressed CSV/Parquet).

Time to ingest one 250 MB chunk on a single thread ≈ 250 MB / 50 MB s⁻¹ ≈ 5 s.
With full parallelism, each "batch" of files finishes in roughly the time of one chunk.
Warehouse	Threads	Batches = ⌈103 / threads⌉	Approx. wall‑clock time
X‑Small	8	⌈103/8⌉ = 13	13 × 5 s ≈ 65 s (≈1 min)
Small	16	⌈103/16⌉ = 7	7 × 5 s ≈ 35 s (≈0.6 min)
These are ideal‑case estimates; actual time will vary with network latency, file compression, and any transformation steps you add.

Disk‑swap / memory usage
During a straight COPY INTO the data is streamed directly into Snowflake’s columnar storage; the operation does not require the whole file to reside in memory or on local disk. Snowflake may spill to remote disk only if downstream transformations (e.g., complex joins, sorts) need more memory than the warehouse provides. For a pure load‑only step you should see negligible local swap/spill.

Take‑away
103 files of ~250 MB each.
An X‑Small warehouse can finish the load in about a minute; a Small warehouse cuts that to roughly half a minute, assuming the stated ingest rate.
The load is stream‑based, so disk swapping is not a concern unless you add heavyweight transformations that exceed the warehouse’s memory. If you need more headroom for such steps, simply size up the warehouse (e.g., Medium) or increase the auto‑suspend/resume window to keep it warm during the batch.

---
*Summary compiled from the conversation on 2026-04-23.*