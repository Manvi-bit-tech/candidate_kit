# REVIEW.md

## Defects Found in the Starter Code

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `compute_late_minutes()` | Late calculation must apply a strict 10-minute grace period and count whole elapsed minutes from shift start. | For a 09:30 shift, test punch-ins at 09:40:00, 09:40:01 and 10:15:59. | Applied the strict grace-period condition and calculated whole minutes from shift start. |
| 2 | `compute_work_hours()` | Work-hour calculations require two-decimal rounding using half-up rounding rather than relying on ordinary floating-point rounding. | Test durations that fall on rounding boundaries. | Used `Decimal` with `ROUND_HALF_UP` and rounded to two decimal places. |
| 3 | `compute_overtime()` | Overnight shifts require the shift-end datetime to fall on the following calendar day. | Test a 22:00–06:00 shift with punch-out after 06:00. | Adjusted the shift-end datetime for overnight shifts and applied the 30-minute overtime threshold. |
| 4 | `POST /employees` | Concurrent requests with the same employee code could create a race condition without database-level uniqueness enforcement. | Send simultaneous employee-creation requests using the same `emp_code`. | Added a unique index on `emp_code` and handled `DuplicateKeyError` with HTTP 409. |
| 5 | `POST /employees` | The `created_at` response field must be an integer in epoch milliseconds rather than a datetime object. | Create an employee and inspect the response field's type and value. | Converted `created_at` to epoch milliseconds before returning the response. |
| 6 | `GET /employees` | Pagination must enforce the required bounds, and results must be sorted by employee code. | Test invalid page values, page sizes above 100 and ordering across pages. | Added pagination validation and sorted employee results by `emp_code`. |
| 7 | `POST /attendance/punch-in` | Concurrent punch-ins for the same employee and attendance date must not create duplicate records. | Send simultaneous punch-in requests for the same employee and date. | Added a unique compound index on `(emp_code, date)` and handled duplicate-key conflicts with HTTP 409. |
| 8 | `POST /attendance/punch-in` | Timestamp handling must follow the epoch-millisecond API contract, use IST for attendance dates and handle overnight shifts correctly. | Test timestamps near midnight and early-morning punch-ins for overnight shifts. | Added timestamp validation, IST conversion, whole-second truncation and overnight attendance-date assignment. |
| 9 | `GET /attendance` | Fetching all matching documents and paginating in Python does not scale to a large attendance collection. | Test list performance against a large dataset. | Moved filtering, counting, sorting, skipping and limiting into MongoDB operations. |
| 10 | `POST /attendance/punch-out` | The selected attendance record must be open and its punch-in must be at or before the requested punch-out time. | Submit a punch-out timestamp earlier than the latest open record's punch-in. | Added a `punch_in <= requested_time` query condition and selected the latest eligible record. |
| 11 | `POST /attendance/punch-out` | Concurrent punch-out requests must not both close the same attendance record successfully. | Send simultaneous punch-out requests for the same open record. | Used a conditional atomic update and returned HTTP 409 when the record no longer matched the open-record condition. |
| 12 | `PATCH /attendance/{emp_code}/{date}` | Regularization must validate input, recompute derived attendance fields, reject no-op corrections and append an audit entry containing only actual changes. | Submit invalid timestamps, an unchanged correction and a valid correction; inspect the response and history. | Added validation, derived-field recalculation, no-op rejection and a history entry with before-and-after values. |
| 13 | `PATCH /attendance/{emp_code}/{date}` | Concurrent regularizations must not silently overwrite each other's changes or history entries. | Submit concurrent corrections against the same attendance record. | Used a conditional update and returned HTTP 409 when the record changed concurrently. |
| 14 | Analytics endpoints | Analytics must follow the business rules for working days, half-days, presence, join dates, employees without logs, ties and missing dates. | Test mid-month joiners, employees without logs, weekend attendance, half-days, tied leaderboard scores and date gaps. | Implemented MongoDB aggregation pipelines with the applicable business rules. |
| 15 | `GET /admin/explain/{endpoint}` and index creation | Query plans must demonstrate indexed execution for supported endpoint queries and aggregations. | Inspect `executionStats` and check for `IXSCAN` without `COLLSCAN` on the relevant main query. | Added the explain endpoint and the required indexes for employee, attendance and analytics queries. |

## Reviewed and Considered Not Defective

- **Employee identity:** `emp_code` is used as the business identifier, and MongoDB `_id` is excluded from API responses.
- **Timestamp representation:** Instants are stored as BSON datetimes in MongoDB and converted to epoch milliseconds at the API boundary.
- **Health endpoint:** `/health` returns HTTP 200 when MongoDB responds and HTTP 503 when the database is unavailable.
- **Attendance history:** History is reserved for manual regularizations; punch-in and punch-out operations do not append regularization entries.
- **Derived attendance fields:** Work hours, late minutes, overtime minutes and half-day status are calculated by the API and stored for subsequent analytics.
