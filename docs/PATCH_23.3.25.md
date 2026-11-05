# Patch 23.3.25: Performance Test Data

Cumulative patch for 23.2.2 and 23.3.0 to 23.3.24. This is milestone 4 of 8 of the JMeter performance-testing
module. Scripts can now read test data (user names, passwords, ids, search terms) so that each virtual user works
with its own values, and request values are bound to the data's columns without editing XML.

## What is new

**Performance Testing > Performance Test Data** (new page)

* A data set is a table: named columns with a type, and rows. Create one with the column names, or import a CSV
  file whose first line names the columns.
* CSV import reads UTF-8 (with or without BOM), UTF-16 and, for files saved by older Excel versions, Windows-1252
  (the import says so, so accented letters can be checked). Comma, semicolon, tab and | separators are detected.
  Lines with a different number of values than the header are refused with their line numbers.
* Every value is kept as text, exactly as entered: `00123` stays `00123`, and accents, other scripts and spaces are
  kept. Column names become JMeter variable names; names that cannot be one (spaces, a leading digit) are changed and
  the import says how.
* Column types: text, whole number, number, email, date (YYYY-MM-DD), true/false and secret. The type is guessed on
  import (columns with leading zeros stay text; columns named like password, token or PIN become secret) and can be
  changed. Values that do not match their type, line breaks and empty cells are listed with their row numbers.
* Edit the table: values, column names and types, add or remove rows and columns. A save based on an older version
  is refused, so two people cannot overwrite each other. Data sets with more than 1,000 rows are changed by
  importing a CSV file (add the rows, or replace columns and rows).
* Export CSV (UTF-8 with BOM, so Excel opens it correctly).

**Secret columns**

* Values of secret columns are kept in the Windows Credential Manager, not in the database. They are never shown
  again (the table says "saved" or "not set"), exports leave them empty, and they are not part of config.json, logs
  or results.
* When a test runs, the data file is written next to the script with the secret values and deleted as soon as
  JMeter ends. A data file without secret columns is kept with the run, with its SHA-256 in config.json.

**Generate rows**

* Rules per column: running number with a prefix (`user0001`), email with a running number at `example.test`,
  whole number or decimal in a range, one of a list, a date between two dates, a random id, a fixed value, or empty.
  Add the rows or replace all rows.
* Ask the AI: the AI model set up on this PC writes invented rows from the column names, types and a description
  (at most 50 at a time). Secret columns stay empty. Every row is checked against the column types; rows that do not
  match are dropped and counted.

**Script editor > Test data tab**

* Add a data set to a thread group or the whole test plan, as a JMeter CSV Data Set Config (UTF-8, quoted data, the
  column names as variables). Rows are shared by all users (each takes the next row) or read by each user from the
  start; at the end of the rows the data starts again, or not.
* Bind request values to columns: JSON body values, form values (also recorded ones), query parameters and request
  headers are listed with their current values. Pick a column and press Bind:
  * form and query values become `${__urlencode(${column})}`;
  * JSON text values are written by JMeter's built-in Groovy JSON encoder, so quotes and backslashes in the data
    stay valid JSON; JSON numbers and true/false use the value as it is;
  * header values become `${column}`.
* The changes go into the working copy of the script; Save keeps them as a new version.
* The JMX references the data as `datasets/<id>.csv`, relative to the script, so it carries no path of this PC.

**Pre-run checks**

* For each data set the script reads: it exists, it has the columns the script reads (a missing column fails), its
  values match their types, it has rows, and there are enough rows for the users when the rows are shared and do
  not start again.
* The data set's file is no longer reported as a missing CSV file.
* The Performance Test Data page shows which scripts use each data set; a data set in use cannot be deleted.

## API

* `GET/POST /api/performance/datasets`, `POST /api/performance/datasets/import` {csv, filename, name}
* `GET/PUT/DELETE /api/performance/datasets/{id}`, `POST .../{id}/import` {csv, mode: append|replace}
* `GET .../{id}/csv`, `POST .../{id}/generate` {rules, count, replace, seed}, `POST .../{id}/generate-ai`
* `POST /api/performance/script/fields` {script}: bindable values and the data sets a working copy reads
* `POST /api/performance/script/apply` accepts `{"op": "add", "type": "csv_dataset"}` and
  `{"op": "bind", node, kind, key, var, raw}`
* Migration 14 adds the table `perf_datasets`.

## Tests

`tests/test_perf_data_23325.py`: CSV import (leading zeros, Unicode, quoted values, Windows-1252, semicolons),
secret values never in views or exports, type checks, edits that keep secrets, stale saves, the row generator, AI
rows checked against the types, binding of JSON, form, query and header values, pre-run checks (missing column),
and two real JMeter 5.6.3 runs: three users each log in with their own row (secret password with a comma and a
quote, Unicode names, ids with leading zeros all arrive intact, 0% errors, the data file with the passwords is
gone afterwards), and a data file without secrets is kept with the run.
