"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from pydantic import (BaseModel,StrictInt,Field,field_validator,model_validator,)
from typing import Optional, Literal
from decimal import Decimal, ROUND_HALF_UP
from bson.json_util import dumps, loads

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

@app.on_event("startup")
def create_indexes():
    db.employees.create_index([("emp_code", 1)],unique=True,name="unique_employee_code")
    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)],unique=True,name="unique_employee_attendance_date")
    db.attendance_logs.create_index([("date", 1), ("late_minutes", 1)],name="attendance_date_late")
    db.attendance_logs.create_index([("date", -1), ("emp_code", 1)],name="attendance_date_employee")
    db.employees.create_index([("department", 1), ("joined_on", 1)],name="employee_department_joined")
    db.employees.create_index([("joined_on", 1)],name="employee_joined_on")



# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def to_epoch_ms(dt: Optional[datetime]) -> Optional[int]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


 
def compute_late_minutes(punch_in: datetime,shift_start: str,shift_end: str,) -> int:
    h, m = map(int, shift_start.split(":"))
    start = punch_in.replace(hour=h, minute=m, second=0, microsecond=0)
    # For an overnight shift, early-morning punches
    # belong to the shift that started the previous day.
    if shift_end <= shift_start and punch_in.strftime("%H:%M") < shift_end:
        start -= timedelta(days=1)
    minutes = int((punch_in - start).total_seconds() / 60)
    return minutes if minutes > 10 else 0

def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = (punch_out - punch_in).total_seconds()
    hours = Decimal(str(seconds)) / Decimal("3600")
    return float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def compute_overtime(punch_out: datetime,shift_start: str,shift_end: str,date_str: str,) -> int:
    if punch_out.tzinfo is None:
        punch_out = punch_out.replace(tzinfo=timezone.utc)
    punch_out = punch_out.astimezone(IST)
    start_h, start_m = map(int, shift_start.split(":"))
    end_h, end_m = map(int, shift_end.split(":"))
    shift_start_dt = datetime.fromisoformat(date_str).replace(hour=start_h, minute=start_m, second=0, microsecond=0,tzinfo=IST,)
    shift_end_dt = datetime.fromisoformat(date_str).replace(hour=end_h, minute=end_m, second=0, microsecond=0,tzinfo=IST,)
    if shift_end <= shift_start:
        shift_end_dt += timedelta(days=1)
    minutes = int((punch_out - shift_end_dt).total_seconds() / 60)
    return minutes if minutes >= 30 else 0
# def compute_overtime(punch_out: datetime, shift_end: str, date_str: str) -> int:
#     h, m = map(int, shift_end.split(":"))
#     end = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
#     minutes = int((punch_out - end).total_seconds() / 60)
#     return minutes if minutes >= 30 else 0
    # return max(0, int((punch_out - end).total_seconds() / 60))


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(
        max_length=120,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(
        default="09:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
    )
    shift_end: str = Field(
        default="18:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
    )
    joined_on: str

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value: str) -> str:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ValueError("joined_on must be a valid YYYY-MM-DD date")

        if parsed.strftime("%Y-%m-%d") != value:
            raise ValueError("joined_on must use YYYY-MM-DD format")
        return value

    @model_validator(mode="after")
    def validate_shift(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self

class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None

class RegularizeRequest(BaseModel):
    status: Optional[Literal[
        "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
    ]] = None
    punch_in: Optional[StrictInt] = None
    punch_out: Optional[StrictInt] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #

@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable"
        )

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump()
    doc["created_at"] = datetime.now()
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409,detail="emp_code already exists",)
    doc.pop("_id", None)
    doc["created_at"] = to_epoch_ms(doc["created_at"])
    return doc

@app.get("/employees")
def list_employees(department: Optional[str] = None,page: int = Query(1, ge=1),page_size: int = Query(20, ge=1, le=100),):
    q = {}
    if department:
        q["department"] = department
    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q, {"_id": 0}).sort("emp_code", 1).skip(skip).limit(page_size))
    for item in items:
        if item.get("created_at") is not None:
            item["created_at"] = to_epoch_ms(item["created_at"])
    return {"items": items, "total": total, "page": page, "page_size": page_size}

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404,detail="Employee not found")
    # ts = datetime.fromtimestamp(body.punched_at / 1000) if body.punched_at else datetime.now()
    # d = ts.date().isoformat()
    # ts = datetime.fromtimestamp(body.punched_at / 1000,tz=timezone.utc).astimezone(IST)
    if body.punched_at is not None:
        if not 100000000000 <= body.punched_at <= 4102444800000:
            raise HTTPException(status_code=422,detail="Timestamp is outside the permitted epoch-millisecond range")
        ts = datetime.fromtimestamp(body.punched_at / 1000,tz=timezone.utc).astimezone(IST).replace(microsecond=0)
    else:
        ts = datetime.now(IST).replace(microsecond=0)

    # d = ts.date().isoformat()
    shift_start = emp["shift_start"]
    shift_end = emp["shift_end"]
    attendance_date = ts.date()
    if shift_end <= shift_start and ts.strftime("%H:%M") < shift_end:
        attendance_date -= timedelta(days=1)
    d = attendance_date.isoformat()

    if db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d}):
        raise HTTPException(409, "already punched in for this date")
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"], emp["shift_end"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    # res = db.attendance_logs.insert_one(doc)
    # doc["id"] = str(res.inserted_id)
    # doc.pop("_id", None)
    # return doc
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409,detail="Already punched in for this date")
    response = {k: v for k, v in doc.items() if k != "_id"}
    response["punch_in"] = to_epoch_ms(doc["punch_in"])
    response["punch_out"] = to_epoch_ms(doc["punch_out"])
    return response


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    attendance_query = {
        "emp_code": body.emp_code,
        "punch_out": None,
        "punch_in": {"$ne": None},
    }

    if body.punched_at is not None:
        if not 100000000000 <= body.punched_at <= 4102444800000:
            raise HTTPException(
                status_code=422,
                detail="Timestamp is outside the permitted epoch-millisecond range",
            )

        requested_time = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc,
        )

        attendance_query["punch_in"] = {
            "$ne": None,
            "$lte": requested_time,
        }

    attendance = db.attendance_logs.find_one(
        attendance_query,
        sort=[("punch_in", -1)],
    )

    if attendance is None:
        raise HTTPException(
            status_code=404,
            detail="No open attendance record found",
        )

    if body.punched_at is not None:
        ts = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc,
        ).astimezone(IST).replace(microsecond=0)
    else:
        ts = datetime.now(IST).replace(microsecond=0)

    
    if attendance["punch_in"] is None:
        raise HTTPException(status_code=409,detail="Punch-in timestamp is missing",)
     
    punch_in_ts = attendance["punch_in"]
    if punch_in_ts.tzinfo is None:
        punch_in_ts = punch_in_ts.replace(tzinfo=timezone.utc)

    
    if (ts - punch_in_ts).total_seconds() <= 0 or (ts - punch_in_ts).total_seconds() > 24 * 60 * 60:
        raise HTTPException(status_code=422,detail="Punch-out must be after punch-in and within 24 hours",)
    work_hours = compute_work_hours(punch_in_ts, ts)
    half_day = work_hours < 4.50
    overtime_minutes = compute_overtime(ts,emp["shift_start"],emp["shift_end"],attendance["date"],)

    result = db.attendance_logs.update_one(
        {
            "_id": attendance["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "half_day": half_day,
                "overtime_minutes": overtime_minutes,
            }
        },
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=409,detail="Attendance record was already punched out",)
    updated = db.attendance_logs.find_one({"_id": attendance["_id"]},{"_id": 0},)
    updated["punch_in"] = to_epoch_ms(updated["punch_in"])
    updated["punch_out"] = to_epoch_ms(updated["punch_out"])

    return updated

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizeRequest,
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    attendance = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date,
    })

    if attendance is None:
        raise HTTPException(
            status_code=404,
            detail="Attendance record not found",
        )
    if body.punch_in is not None:
        if not 100000000000 <= body.punch_in <= 4102444800000:
            raise HTTPException(
                status_code=422,
                detail="Invalid punch_in timestamp",
            )

    if body.punch_out is not None:
        if not 100000000000 <= body.punch_out <= 4102444800000:
            raise HTTPException(
                status_code=422,
                detail="Invalid punch_out timestamp",
            )

    punch_in = (datetime.fromtimestamp(body.punch_in / 1000,tz=timezone.utc,).astimezone(IST).replace(microsecond=0)if body.punch_in is not None else attendance.get("punch_in"))

    punch_out = (datetime.fromtimestamp(body.punch_out / 1000,tz=timezone.utc,).astimezone(IST).replace(microsecond=0)if body.punch_out is not None else attendance.get("punch_out"))

    if punch_in is not None and punch_in.tzinfo is None:
        punch_in = punch_in.replace(tzinfo=timezone.utc).astimezone(IST)

    if punch_out is not None and punch_out.tzinfo is None:
        punch_out = punch_out.replace(tzinfo=timezone.utc).astimezone(IST)
    
    status = body.status or attendance["status"]

    if status in ("ABSENT", "LEAVE"):
        if ("punch_in" in body.model_fields_set or "punch_out" in body.model_fields_set):
            raise HTTPException(status_code=422,detail="ABSENT/LEAVE cannot include punch times",)

        punch_in = None
        punch_out = None

    elif punch_in is None:
        raise HTTPException(status_code=422,detail="Punch-in is required for this status",)

    if punch_in is not None and punch_out is not None:
        elapsed = (punch_out - punch_in).total_seconds()

        if elapsed <= 0 or elapsed > 24 * 60 * 60:
            raise HTTPException(status_code=422,detail="Punch-out must be after punch-in and within 24 hours",)
    
    # Validate the date format
    try:
        attendance_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422,detail="Date must be in YYYY-MM-DD format",)

    if attendance_date.isoformat() != date:
        raise HTTPException(status_code=422,detail="Date must be in YYYY-MM-DD format",)

    # Validate that punch-in belongs to this attendance date
    if punch_in is not None:
        local_punch_in = punch_in.astimezone(IST)
        punch_date = local_punch_in.date()

        shift_start = datetime.strptime(emp["shift_start"], "%H:%M").time()
        shift_end = datetime.strptime(emp["shift_end"], "%H:%M").time()

        # For an overnight shift, early morning punch-in
        # belongs to the previous shift date.
        if shift_end <= shift_start and local_punch_in.time() < shift_end:
            from datetime import timedelta
            punch_date -= timedelta(days=1)

        if punch_date != attendance_date:
            raise HTTPException(status_code=422,detail="Punch-in does not belong to this attendance date",)

    # Recompute derived attendance fields
    if status in ("ABSENT", "LEAVE"):
        work_hours = None
        late_minutes = 0
        overtime_minutes = 0
        half_day = False
    else:
        late_minutes = compute_late_minutes(punch_in,emp["shift_start"],emp["shift_end"],)

        if punch_out is not None:
            work_hours = compute_work_hours(punch_in, punch_out)
            half_day = work_hours < 4.50
            overtime_minutes = compute_overtime(punch_out,emp["shift_start"],emp["shift_end"],date,)
        else:
            work_hours = None
            half_day = False
            overtime_minutes = 0

    # Store the original values before updating the database
    old_values = {
        "status": attendance["status"],
        "punch_in": attendance.get("punch_in"),
        "punch_out": attendance.get("punch_out"),
        "work_hours": attendance.get("work_hours"),
        "late_minutes": attendance.get("late_minutes", 0),
        "overtime_minutes": attendance.get("overtime_minutes", 0),
        "half_day": attendance.get("half_day", False),
    }

    new_values = {
        "status": status,
        "punch_in": punch_in,
        "punch_out": punch_out,
        "work_hours": work_hours,
        "late_minutes": late_minutes,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
    }

    changes = {}

    for field, new_value in new_values.items():
        old_value = old_values[field]

        if field in ("punch_in", "punch_out"):
            old_compare = (to_epoch_ms(old_value)if old_value is not None else None)
            new_compare = (to_epoch_ms(new_value)if new_value is not None else None)
        else:
            old_compare = old_value
            new_compare = new_value

        if old_compare != new_compare:
            changes[field] = {
                "from": old_compare,
                "to": new_compare,
            }

    if not changes:
        raise HTTPException(status_code=422,detail="No changes to regularize",)

    history_entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    result = db.attendance_logs.update_one(
        {
            "_id": attendance["_id"],
            "status": attendance["status"],
            "punch_in": attendance.get("punch_in"),
            "punch_out": attendance.get("punch_out"),
            "history": attendance.get("history", []),
        },
        {
            "$set": new_values,
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count == 0:
        raise HTTPException(status_code=409,detail="Attendance changed concurrently; please retry",)

    updated = db.attendance_logs.find_one({"_id": attendance["_id"]})

    updated.pop("_id", None)
    updated["punch_in"] = to_epoch_ms(updated.get("punch_in"))
    updated["punch_out"] = to_epoch_ms(updated.get("punch_out"))

    for entry in updated.get("history", []):
        entry["at"] = to_epoch_ms(entry["at"])
    return updated

@app.get("/attendance")
def list_attendance(emp_code: Optional[str] = None,date_from: Optional[str] = None,date_to: Optional[str] = None,status: Optional[str] = None,page: int = 1,page_size: int = 20,):
    if page < 1 or not 1 <= page_size <= 100:
        raise HTTPException(status_code=422,detail="page must be >= 1 and page_size must be between 1 and 100",)
    for value, field in [(date_from, "date_from"),(date_to, "date_to"),]:
        if value is not None:
            try:
                parsed = datetime.strptime(value, "%Y-%m-%d").date()
                if parsed.isoformat() != value:
                    raise ValueError
            except ValueError:
                raise HTTPException(status_code=422,detail=f"{field} must be in YYYY-MM-DD format",)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422,detail="date_from must be on or before date_to",)
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    # docs = list(db.attendance_logs.find(q))
    # docs.sort(key=lambda d: d["date"], reverse=True)
    # total = len(docs)
    # page_docs = docs[(page - 1) * page_size : page * page_size]
    total = db.attendance_logs.count_documents(q)
    page_docs = list(db.attendance_logs.find(q).sort("date", -1).skip((page - 1) * page_size).limit(page_size))


    for d in page_docs:
        d.pop("_id", None)
        d["punch_in"] = to_epoch_ms(d.get("punch_in"))
        d["punch_out"] = to_epoch_ms(d.get("punch_out"))
        for entry in d.get("history", []):
            entry["at"] = to_epoch_ms(entry["at"])
            for change in entry.get("changes", {}).values():
                for key in ("from", "to"):
                    if isinstance(change.get(key), datetime):
                        change[key] = to_epoch_ms(change[key])
    return {
        "items": page_docs,
        "total": total,
        "page": page,
        "page_size": page_size,
    }

def build_employee_monthly_pipeline(emp_code: str, month: str, joined_on: str):
    month_start = datetime.strptime(month, "%Y-%m").date()

    if month_start.month == 12:
        next_month = datetime(month_start.year + 1, 1, 1).date()
    else:
        next_month = datetime(month_start.year, month_start.month + 1, 1).date()

    month_end = next_month - timedelta(days=1)
    effective_start = max(month_start,datetime.strptime(joined_on, "%Y-%m-%d").date())

    start_str = effective_start.isoformat()
    end_str = month_end.isoformat()

    return [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {"$gte": start_str, "$lte": end_str}
            }
        },
        {
            "$set": {
                "_record_date": {
                    "$dateFromString": {"dateString": "$date"}
                },
                "_late": {"$ifNull": ["$late_minutes", 0]},
                "_overtime": {"$ifNull": ["$overtime_minutes", 0]}
            }
        },
        {
            "$group": {
                "_id": None,
                "leave_days": {
                    "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                },
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$gte": ["$_record_date", datetime.combine(effective_start, datetime.min.time())]},
                                    {"$lte": ["$_record_date", datetime.combine(month_end, datetime.min.time())]},
                                    {"$lt": [{"$dayOfWeek": "$_record_date"}, 7]},
                                    {"$gt": [{"$dayOfWeek": "$_record_date"}, 1]}
                                ]
                            },
                            {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                            0
                        ]
                    }
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$_late", 0]}, 1, 0]}
                },
                "total_late_minutes": {"$sum": "$_late"},
                "total_overtime_minutes": {"$sum": "$_overtime"}
            }
        },
        {
            "$project": {
                "_id": 0,
                "leave_days": 1,
                "present_days": 1,
                "late_count": 1,
                "total_late_minutes": 1,
                "total_overtime_minutes": 1
            }
        }
    ]

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    # Validate month format: YYYY-MM
    try:
        if len(month) != 7 or month[4] != "-":
            raise ValueError
        month_start = datetime.strptime(month, "%Y-%m").date()
        if month_start.strftime("%Y-%m") != month:
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=422,detail="month must be in YYYY-MM format")

    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    joined_on = employee["joined_on"]

    if month_start.month == 12:
        next_month = datetime(month_start.year + 1, 1, 1).date()
    else:
        next_month = datetime(month_start.year, month_start.month + 1, 1).date()

    month_end = next_month - timedelta(days=1)
    effective_start = max(month_start,datetime.strptime(joined_on, "%Y-%m-%d").date())

    # Count weekdays from the effective start date to month end.
    working_days = 0
    current_date = effective_start
    while current_date <= month_end:
        if current_date.weekday() < 5:
            working_days += 1
        current_date += timedelta(days=1)

    pipeline = build_employee_monthly_pipeline(emp_code, month, joined_on)
    result = list(db.attendance_logs.aggregate(pipeline))

    if result:
        stats = result[0]
    else:
        stats = {
            "present_days": 0,
            "leave_days": 0,
            "late_count": 0,
            "total_late_minutes": 0,
            "total_overtime_minutes": 0
        }

    present_days = Decimal(str(stats["present_days"]))

    if working_days > 0:
        attendance_pct = float((present_days / Decimal(working_days) * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    else:
        attendance_pct = None

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": float(present_days),
        "leave_days": stats["leave_days"],
        "late_count": stats["late_count"],
        "total_late_minutes": stats["total_late_minutes"],
        "total_overtime_minutes": stats["total_overtime_minutes"],
        "attendance_pct": attendance_pct
    }


def build_department_summary_pipeline(month: str,department: Optional[str] = None):
    month_start = datetime.strptime(month, "%Y-%m").date()
    if month_start.month == 12:
        next_month = datetime(month_start.year + 1, 1, 1).date()
    else:
        next_month = datetime(
            month_start.year, month_start.month + 1, 1
        ).date()
    month_end = next_month - timedelta(days=1)
    employee_match = {
        "joined_on": {"$lte": month_end.isoformat()}
    }
    if department is not None:
        employee_match["department"] = department

    present_statuses = ["PRESENT", "WFH", "ON_DUTY"]

    def half_up_2(expression):
        return {
            "$divide": [
                {
                    "$floor": {
                        "$add": [
                            {"$multiply": [expression, 100]},
                            0.5
                        ]
                    }
                },
                100
            ]
        }

    return [
        {"$match": employee_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$code"]},
                                    {"$gte": ["$date", month_start.isoformat()]},
                                    {"$lte": ["$date", month_end.isoformat()]}
                                ]
                            }
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {
            "$unwind": {
                "path": "$logs",
                "preserveNullAndEmptyArrays": True
            }
        },
        {
            "$addFields": {
                "_is_weekday_present": {
                    "$and": [
                        {"$in": ["$logs.status", present_statuses]},
                        {"$ne": [{"$ifNull": ["$logs.date", None]}, None]},
                        {
                            "$in": [
                                {
                                    "$dayOfWeek": {
                                        "$dateFromString": {
                                            "dateString": "$logs.date",
                                            "format": "%Y-%m-%d"
                                        }
                                    }
                                },
                                [2, 3, 4, 5, 6]
                            ]
                        }
                    ]
                },
                "_valid_work_hours": {
                    "$and": [
                        {"$in": ["$logs.status", present_statuses]},
                        {"$ne": [{"$ifNull": ["$logs.work_hours", None]}, None]}
                    ]
                }
            }
        },
        {
            "$group": {
                "_id": "$department",
                "employee_codes": {"$addToSet": "$emp_code"},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            "$_is_weekday_present",
                            {
                                "$cond": [
                                    {"$eq": ["$logs.half_day", True]},
                                    0.5,
                                    1
                                ]
                            },
                            0
                        ]
                    }
                },
                "work_hours_values": {
                    "$push": {
                        "$cond": [
                            "$_valid_work_hours",
                            "$logs.work_hours",
                            None
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]},
                            1,
                            0
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$logs.late_minutes", 0]}
                },
                "leave_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "LEAVE"]},
                            1,
                            0
                        ]
                    }
                },
                "on_duty_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "ON_DUTY"]},
                            1,
                            0
                        ]
                    }
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": {"$size": "$employee_codes"},
                "present_days": half_up_2("$present_days"),
                "avg_work_hours": {
                    "$let": {
                        "vars": {
                            "valid_hours": {
                                "$filter": {
                                    "input": "$work_hours_values",
                                    "as": "hours",
                                    "cond": {"$ne": ["$$hours", None]}
                                }
                            }
                        },
                        "in": {
                            "$cond": [
                                {"$gt": [{"$size": "$$valid_hours"}, 0]},
                                half_up_2({"$avg": "$$valid_hours"}),
                                None
                            ]
                        }
                    }
                },
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1
            }
        },
        {"$sort": {"department": 1}}
    ]



@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    # Validate YYYY-MM
    try:
        if len(month) != 7 or month[4] != "-":
            raise ValueError
        month_start = datetime.strptime(month, "%Y-%m").date()
        if month_start.strftime("%Y-%m") != month:
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=422,detail="month must be in YYYY-MM format")
    pipeline = build_department_summary_pipeline(month, department)
    items = list(db.employees.aggregate(pipeline))
    return {
        "month": month,
        "items": items
    }


def build_late_leaderboard_pipeline(month: str,limit: int, department: Optional[str] = None):
    month_start = month + "-01"

    year, month_num = map(int, month.split("-"))
    if month_num == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month_num + 1, 1)

    month_end = (next_month - timedelta(days=1)).strftime("%Y-%m-%d")

    pipeline = [
        {
            "$match": {
                "date": {"$gte": month_start, "$lte": month_end},
                "late_minutes": {"$gt": 0}
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1}
            }
        },
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee"
            }
        },
        {"$unwind": "$employee"},
    ]

    if department is not None:
        pipeline.append({
            "$match": {"employee.department": department}
        })

    pipeline.extend([
        {
            "$sort": {
                "total_late_minutes": -1,
                "_id": 1
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}}
                }
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {
            "$project": {
                "_id": 0,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "late_count": 1,
                "rank": 1
            }
        },
        {"$sort": {"rank": 1, "emp_code": 1}}
    ])

    return pipeline


@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str, limit: int = 10, department: Optional[str] = None):
    # Validate month
    try:
        if len(month) != 7 or month[4] != "-":
            raise ValueError
        datetime.strptime(month, "%Y-%m")
    except ValueError:
        raise HTTPException(status_code=422, detail="month must be in YYYY-MM format")

    if limit < 1 or limit > 50:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 50")

    pipeline = build_late_leaderboard_pipeline(month, limit, department)
    items = list(db.attendance_logs.aggregate(pipeline))
    return {
        "month": month,
        "items": items
    }


def build_department_trend_pipeline(department: str,from_: str,to: str):
    start_date = datetime.strptime(from_, "%Y-%m-%d").date()
    end_date = datetime.strptime(to, "%Y-%m-%d").date()

    start_str = start_date.isoformat()
    end_str = end_date.isoformat()

    pipeline = [
        {"$match": {"department": department}},
        {
            "$group": {
                "_id": None,
                "employees": {
                    "$push": {
                        "emp_code": "$emp_code",
                        "joined_on": "$joined_on"
                    }
                }
            }
        },
        {
            "$set": {
                "start_date": {
                    "$dateFromString": {"dateString": start_str}
                },
                "end_date": {
                    "$dateFromString": {"dateString": end_str}
                }
            }
        },
        {
            "$set": {
                "dates": {
                    "$range": [
                        0,
                        {
                            "$add": [
                                {
                                    "$dateDiff": {
                                        "startDate": "$start_date",
                                        "endDate": "$end_date",
                                        "unit": "day"
                                    }
                                },
                                1
                            ]
                        }
                    ]
                }
            }
        },
        {"$unwind": "$dates"},
        {
            "$set": {
                "date": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": {
                            "$dateAdd": {
                                "startDate": "$start_date",
                                "unit": "day",
                                "amount": "$dates"
                            }
                        }
                    }
                }
            }
        },
        {
            "$set": {
                "eligible_employees": {
                    "$filter": {
                        "input": "$employees",
                        "as": "employee",
                        "cond": {
                            "$lte": ["$$employee.joined_on", "$date"]
                        }
                    }
                }
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "current_date": "$date",
                    "employee_codes": {
                        "$map": {
                            "input": "$eligible_employees",
                            "as": "employee",
                            "in": "$$employee.emp_code"
                        }
                    }
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$date", "$$current_date"]},
                                    {
                                        "$in": [
                                            "$emp_code",
                                            "$$employee_codes"
                                        ]
                                    }
                                ]
                            }
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {
            "$set": {
                "is_working_day": {
                    "$in": [
                        {
                            "$dayOfWeek": {
                                "$dateFromString": {
                                    "dateString": "$date"
                                }
                            }
                        },
                        [2, 3, 4, 5, 6]
                    ]
                },
                "headcount": {"$size": "$eligible_employees"},
                "present_count": {
                    "$sum": {
                        "$map": {
                            "input": {
                                "$filter": {
                                    "input": "$logs",
                                    "as": "log",
                                    "cond": {
                                        "$in": [
                                            "$$log.status",
                                            ["PRESENT", "WFH", "ON_DUTY"]
                                        ]
                                    }
                                }
                            },
                            "as": "log",
                            "in": {
                                "$cond": [
                                    {"$eq": ["$$log.half_day", True]},
                                    0.5,
                                    1
                                ]
                            }
                        }
                    }
                },
                "late_count": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "log",
                            "cond": {
                                "$gt": [
                                    {"$ifNull": ["$$log.late_minutes", 0]},
                                    0
                                ]
                            }
                        }
                    }
                }
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]}
                            ]
                        },
                        {"$divide": ["$present_count", "$headcount"]},
                        None
                    ]
                }
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {"$ne": ["$attendance_rate", None]},
                        {
                            "$divide": [
                                {
                                    "$floor": {
                                        "$add": [
                                            {
                                                "$multiply": [
                                                    "$attendance_rate",
                                                    10000
                                                ]
                                            },
                                            0.5
                                        ]
                                    }
                                },
                                10000
                            ]
                        },
                        None
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]}
                    }
                }
            }
        },
        {
            "$set": {
                "moving_avg_7d": {
                    "$cond": [
                        {"$ne": ["$moving_avg_7d", None]},
                        {
                            "$divide": [
                                {
                                    "$floor": {
                                        "$add": [
                                            {
                                                "$multiply": [
                                                    "$moving_avg_7d",
                                                    10000
                                                ]
                                            },
                                            0.5
                                        ]
                                    }
                                },
                                10000
                            ]
                        },
                        None
                    ]
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1
            }
        },
        {"$sort": {"date": 1}}
    ]

    return pipeline

@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str,from_: str = Query(..., alias="from"),to: str = Query(...)):
    # 1. Validate date format
    try:
        start_date = datetime.strptime(from_, "%Y-%m-%d").date()
        end_date = datetime.strptime(to, "%Y-%m-%d").date()

        if start_date.strftime("%Y-%m-%d") != from_:
            raise ValueError
        if end_date.strftime("%Y-%m-%d") != to:
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=422,detail="Dates must be in YYYY-MM-DD format")

    # 2. Validate date range
    if end_date < start_date:
        raise HTTPException(status_code=422,detail="'to' must be on or after 'from'")

    if (end_date - start_date).days + 1 > 92:
        raise HTTPException(status_code=422,detail="Date range cannot exceed 92 days")

    # 3. Check whether the department exists
    if not db.employees.find_one({"department": department}):
        raise HTTPException(status_code=404,detail="Department not found")

    start_str = start_date.isoformat()
    end_str = end_date.isoformat()
    pipeline = build_department_trend_pipeline(department, from_, to)
    items = list(db.employees.aggregate(pipeline))

    return {
        "department": department,
        "from": start_str,
        "to": end_str,
        "items": items
    }



# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #

EXPLAIN_ENDPOINTS = {
    "attendance_list",
    "employee_monthly",
    "department_summary",
    "late_leaderboard",
    "department_trend",
}


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = 10,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if endpoint not in EXPLAIN_ENDPOINTS:
        raise HTTPException(status_code=422,detail=f"Unsupported endpoint. Choose one of: {sorted(EXPLAIN_ENDPOINTS)}",)

    if page < 1 or not 1 <= page_size <= 100:
        raise HTTPException(status_code=422,detail="page must be >= 1 and page_size must be between 1 and 100",)

    if endpoint == "attendance_list":
        for value, field in [(date_from, "date_from"),(date_to, "date_to"),]:
            if value is not None:
                try:
                    parsed = datetime.strptime(value, "%Y-%m-%d").date()
                    if parsed.isoformat() != value:
                        raise ValueError
                except ValueError:
                    raise HTTPException(status_code=422,detail=f"{field} must be in YYYY-MM-DD format",)
        if date_from and date_to and date_from > date_to:
            raise HTTPException(status_code=422,detail="date_from must be on or before date_to",)

        query = {}
        if emp_code:
            query["emp_code"] = emp_code
        if date_from or date_to:
            query["date"] = {}
            if date_from:
                query["date"]["$gte"] = date_from
            if date_to:
                query["date"]["$lte"] = date_to
        if status:
            query["status"] = status

        explain_doc = db.command({
            "explain": {
                "find": "attendance_logs",
                "filter": query,
                "sort": {"date": -1},
                "skip": (page - 1) * page_size,
                "limit": page_size,
            },
            "verbosity": "executionStats",
        })
        collection = "attendance_logs"

    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(status_code=422,detail="emp_code and month are required for employee_monthly",)

        try:
            parsed_month = datetime.strptime(month, "%Y-%m").date()
            if parsed_month.strftime("%Y-%m") != month:
                raise ValueError
        except ValueError:
            raise HTTPException(422, "month must be in YYYY-MM format")

        employee = db.employees.find_one({"emp_code": emp_code})
        if employee is None:
            raise HTTPException(404, "Employee not found")

        pipeline = build_employee_monthly_pipeline(
            emp_code, month, employee["joined_on"]
        )
        explain_doc = db.command({
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        })
        collection = "attendance_logs"

    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(status_code=422,detail="month is required for department_summary")

        try:
            parsed_month = datetime.strptime(month, "%Y-%m").date()
            if parsed_month.strftime("%Y-%m") != month:
                raise ValueError
        except ValueError:
            raise HTTPException(status_code=422,detail="month must be in YYYY-MM format")


        # Reuse the same aggregation pipeline as the actual endpoint.
        pipeline = build_department_summary_pipeline(month, department)
        explain_doc = db.command({
            "explain": {
                "aggregate": "employees",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        })
        collection = "employees"

    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(status_code=422,detail="month is required for late_leaderboard",)
        if not 1 <= limit <= 50:
            raise HTTPException(status_code=422,detail="limit must be between 1 and 50",)
        try:
            parsed_month = datetime.strptime(month, "%Y-%m").date()
            if parsed_month.strftime("%Y-%m") != month:
                raise ValueError
        except ValueError:
            raise HTTPException(status_code=422,detail="month must be in YYYY-MM format",)

        pipeline = build_late_leaderboard_pipeline(month, limit, department)
        explain_doc = db.command({
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        })
        collection = "attendance_logs"

    else:  # department_trend
        if not department or not from_ or not to:
            raise HTTPException(
                status_code=422,
                detail="department, from and to are required for department_trend",
            )
        for value, field in [(from_, "from"),(to, "to"),]:
            try:
                parsed = datetime.strptime(value, "%Y-%m-%d").date()
                if parsed.isoformat() != value:
                    raise ValueError
            except ValueError:
                raise HTTPException(status_code=422,detail=f"{field} must be in YYYY-MM-DD format",)
        if from_ > to:
            raise HTTPException(status_code=422,detail="from must be on or before to",)
        if (datetime.strptime(to, "%Y-%m-%d").date()- datetime.strptime(from_, "%Y-%m-%d").date()).days > 92:
            raise HTTPException(status_code=422,detail="Date range cannot exceed 93 days",)
        pipeline = build_department_trend_pipeline(department, from_, to)
        explain_doc = db.command({
            "explain": {
                "aggregate": "employees",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        })
        collection = "employees"

    return {
        "endpoint": endpoint,
        "collection": collection,
        "explain": loads(dumps(explain_doc)),
    }
