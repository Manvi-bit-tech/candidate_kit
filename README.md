# Candidate kit
# Employee Attendance & Analytics API

A REST API built with **Python, FastAPI, and MongoDB** for employee attendance management, punch-in/punch-out operations, attendance regularization with audit history, and attendance analytics.

## Features

- Employee management and paginated API responses.
- Attendance punch-in and punch-out with business-rule validation.
- Manual attendance corrections with an audit trail.
- Monthly attendance summaries, department analytics, late-arrival leaderboard, and department trends.
- MongoDB aggregation pipelines and query execution-plan inspection.
- Health-check endpoint and database indexes.

## Tech Stack

- Python 3.11+
- FastAPI
- MongoDB 6.0+
- PyMongo

## Setup and Run

1. Create and activate a virtual environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Start MongoDB locally or configure a MongoDB Atlas connection. Set the required `MONGO_URI` and `MONGO_DB` environment variables according to the application configuration.

4. Start the API:

   ```bash
   uvicorn app.main:app --port 8000
   ```

5. Open `http://127.0.0.1:8000/docs` to explore the API using Swagger UI.

   Check `http://127.0.0.1:8000/health` to verify application and database connectivity.

## Code Review and Design Decisions

See [REVIEW.md](REVIEW.md) for the code review and [DECISIONS.md](DECISIONS.md) for implementation trade-offs and design decisions.

## Notes

MongoDB must be running and correctly configured before starting the application. No secrets or local environment files should be committed to the repository.





<!-- 


Read in this order:

1. `PROBLEM_STATEMENT.docx`: what you are asked to do, how it is evaluated.
2. `openapi.yaml`: the API contract and the business rules (R1 - R10).
3. `DATA_MODEL.md`: what is stored in MongoDB, with nine sample documents.
4. `app/main.py`: the starter. Put **all** your code in this one file. It must start with `uvicorn app.main:app --port 8000`.
5. `REVIEW.md`, `DECISIONS.md`: templates to fill in.

`sample_data/` holds the nine sample documents as MongoDB Extended JSON; `sample_seed.py` loads them. The grader's dataset is
much larger and contains cases they do not show.

## Local setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # set MONGO_URI / MONGO_DB (local docker: docker run -p 27017:27017 mongo:7)
python sample_seed.py
uvicorn app.main:app --port 8000 --reload
```
MongoDB **6.0 or newer** is required (the grader uses 7.0). MongoDB Atlas free tier (M0) works.

## Submit
A public Git repository with `app/main.py`, `requirements.txt`, `REVIEW.md`, `DECISIONS.md` and a short `README.md`.
No `.env`, no secrets, no Dockerfile. -->
