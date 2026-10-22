# Email attachments ETL to SQL Server

*[Versión en español](README.md)*

A Python ETL pipeline that reads the reports arriving as attachments in a mailbox, normalises them with pandas and loads them into SQL Server. It is **incremental**: every processed email is recorded, and the next run asks only for what arrived afterwards.

> **About this public version.** It is based on an ETL I wrote for work. The original reads Outlook through COM (`win32com`), which ties it to one Windows desktop with Outlook installed and signed in; here the source is **IMAP**, the same job over a standard protocol, so the project runs anywhere. The data is **synthetic** and `docker-compose` includes a test mail server, so the whole pipeline can be run without access to any real mailbox.

---

## The problem

Several operational reports arrived by email every morning, each in its own format: one as pipe-separated latin-1 text inside a ZIP, another as an Excel workbook. Someone had to open the email, download the attachment, unzip it, clean it and load it into the database. That meant:

- **Repetitive manual work** before anything could be queried.
- **A risk of double-loading or skipping a day**, because the record of what had already been loaded lived in someone's memory.
- **A bottleneck per new report**: adding one meant writing another script.

## The solution

```mermaid
flowchart LR
    subgraph Source
        A["IMAP mailbox<br/><i>one subject per report</i>"]
    end

    subgraph Pipeline["Python pipeline"]
        direction TB
        B["<b>Read</b><br/>search by subject<br/>since the last load"]
        C["<b>Attachments</b><br/>save · unzip"]
        D["<b>Transform</b><br/>map columns · types"]
        E["<b>Load</b><br/>batched append"]
        B --> C --> D --> E
    end

    subgraph Destination["SQL Server"]
        F[("branch_sales")]
        G[("inventory_movements")]
        H[("etl_load_history")]
    end

    Y["config/reports.yaml"]

    A --> B
    Y -. "defines each report" .-> B
    Y -. "" .-> D
    E --> F
    E --> G
    E == "records the load" ==> H
    H == "watermark" ==> B
```

## Design decisions

**The watermark lives in the database, not on disk or in memory.** Every loaded attachment is recorded in `etl_load_history` with the subject it arrived under and the email's received time. On startup the pipeline reads back the latest email loaded **per subject** and asks the mailbox only for what came after it:

```sql
WITH ranked AS (
    SELECT subject, received_at,
           ROW_NUMBER() OVER (PARTITION BY subject ORDER BY received_at DESC) AS rn
    FROM dbo.etl_load_history
)
SELECT subject, received_at FROM ranked WHERE rn = 1;
```

Because the watermark advances as each attachment is recorded, a run that dies halfway resumes from the last attachment it actually loaded rather than starting over. A subject nobody has loaded yet starts from a bounded window (`INITIAL_LOOKBACK_DAYS`) instead of reading the whole mailbox.

**Adding a report is editing a YAML file, not writing code.** [`config/reports.yaml`](config/reports.yaml) declares, per report: the subject to search for, which attachments to recognise and how to read them (format, encoding, separator), the source-to-destination column mapping, the types and the destination table. The pipeline does not change.

**Filtering happens on the server and again on the client.** IMAP only filters dates with day resolution, so the server is asked for one extra day and the exact cut is applied against each message's own timestamp. Filtering on the server first keeps the download small.

**Attachments are stored with the email's timestamp as a prefix**, so two deliveries of the same name never overwrite each other. But the YAML patterns are matched against the **original** filename: the configuration describes the file as it arrives, not how this pipeline chooses to store it.

**Every ZIP entry is reduced to its base name before being written.** A crafted archive can carry names like `../../something`, and honouring them would allow writes outside the work directory.

**An unreadable attachment neither stops the run nor moves the watermark.** It is logged, skipped, and the rest continue. But an attachment missing an expected column **is** an error: silently loading a truncated report is worse than failing.

**Timestamps are normalised to naive UTC.** `DATETIME2` carries no offset, and a single timezone-aware value reaching the watermark comparison is enough to break it.

## Running it

**Requirements:** Python 3.10+, [Docker](https://www.docker.com/products/docker-desktop/) and the [ODBC Driver 18 for SQL Server](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server).

```bash
docker compose up -d                    # SQL Server + test mail server
cp .env.example .env                    # Windows: copy .env.example .env

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python scripts/seed_mailbox.py --days 3 # deliver 3 days of reports
python -m src.pipeline
```

The pipeline creates the database and the control table if they are missing.

### The part worth seeing

Run it a second time without sending anything new:

```
Read 2 watermark(s) from the control table
Reporte diario de ventas: 0 new email(s)
Movimientos de inventario: 0 new email(s)
  0 email(s), 0 attachment(s), 0 row(s)
```

Now pretend the next day's reports arrive, and run it again:

```bash
python scripts/seed_mailbox.py --days 1
python -m src.pipeline
```

```
Reporte diario de ventas: 1 new email(s)
  branch_sales: 60 row(s) loaded
Movimientos de inventario: 1 new email(s)
  inventory_movements: 40 row(s) loaded
  2 email(s), 2 attachment(s), 100 row(s)
```

Only what is new. From the database:

```sql
SELECT subject, received_at, attachment, rows_loaded
FROM etl_load_history ORDER BY received_at;
```

### Tests

```bash
pytest
```

## Layout

```
config/reports.yaml   declarative definition of each report
src/
  config.py       configuration from environment variables
  reports.py      loads and validates the YAML
  mailbox.py      reading the mailbox over IMAP
  attachments.py  saving attachments and unpacking archives
  transform.py    column mapping and type coercion
  control.py      control table and incremental watermark
  db.py           SQLAlchemy engine, retries, database creation
  load.py         loading into the destination table
  pipeline.py     orchestration
scripts/
  generate_data.py   builds the synthetic attachments
  seed_mailbox.py    delivers them to the test mailbox over SMTP
sql/schema.sql       control table DDL, for reference
tests/               configuration, transformation and mailbox tests
```

## Limitations and what I would improve

- **The report tables are created by `to_sql`**, which infers their types: text columns end up as `varchar(max)` and amounts as `float`. It works, but `varchar(max)` cannot be indexed well and money should be `DECIMAL`. I would write the DDL by hand, as the control table already has.
- **The load appends and never reprocesses.** The watermark guarantees an email is processed once, but a corrected report sent again needs the previous rows deleted by hand. A delete-by-date before inserting would fix it.
- **There is no orchestrator.** This runs from Task Scheduler. Airflow or Prefect would give retries, alerting and lineage without writing them by hand.
- **There are no integration tests.** The tests cover configuration, transformation and mailbox/ZIP handling, but not the real load against SQL Server or the IMAP read, which today are only verified by hand with `docker-compose`.
- **The mailbox is read, never marked.** Messages are neither moved nor flagged, so the mailbox grows without bound. In production they should be archived once processed.

---

Built with AI assistance (Claude); the problem, the design decisions and the validation are mine.

**José Francisco Yudico Martínez** — Interdisciplinary professional in data science and analysis.

MIT licensed.
