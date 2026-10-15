# DECISIONS.md

## 1. Indexes

I created a unique index on `employees.emp_code` to prevent duplicate employees. A unique compound index on `attendance_logs(emp_code, date)` prevents duplicate attendance records and supports employee-month queries. Indexes on attendance date and late minutes support date-filtered lists and the late leaderboard. The employee indexes on `(department, joined_on)` and `joined_on` support department filtering, trend queries, and headcount calculations. I considered indexing every field but rejected it because unnecessary indexes increase storage and write costs.

## 2. Punch-in race

If two identical punch-in requests arrive together, both may initially find no record. Both attempt to insert, but the unique compound index allows only one insert to succeed. The successful request returns HTTP 201; the other receives a duplicate-key error, which the API converts to HTTP 409.

## 3. Ties

The leaderboard uses ranking that preserves equal ranks for equal late-minute totals. The limit is applied after ranking, so employees tied at the cutoff are included rather than arbitrarily assigning different ranks. Tied rows are ordered by employee code for consistent output.

## 4. Headcount

The department summary starts from employees who joined on or before the month's end, then looks up their attendance records. This includes eligible employees even when they have no logs.

## 5. Scaling

For 100 times the data, I would review query plans and index selectivity, benchmark aggregation pipelines, and consider a suitable sharding strategy if a single MongoDB deployment became a bottleneck.