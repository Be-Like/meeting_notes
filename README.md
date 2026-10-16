# backend-inteview

A FastAPI application for managing meetings, notes, and tasks.

## Your task

Implement all endpoints through a service layer and write it to the database, then assert that you can read it again.

To do this:

- Add authentication. Do this however you would like (external libraries are allowed). Keep it as simple as possible though.
- Create a service per resource that all implement a base service class.
- Put all of the services into a service mesh. Call's between each service should route through the mesh.
- The service mesh is user specific, and should should support caching.
- The mesh should support local development, and cloud development, and should be composable in order to accomplish this. Ie, local development is in memory, but in production the service mesh might allow gRPC connections between services seamlessly in production. Note you do not need to support gRPC for this task - but design this in such a way that the communication mode between services is easily interchangeable.
- Create a decorator that can be placed on each service. This decorator allows us to call service_mesh.get_tools(), which can be passed onwards to an LLM.
- The service mesh (and the services within in) should only be able to access resources that only the user making the request.

## Features

- **Users**: Create and manage users who attend meetings
- **Meetings**: Schedule meetings with attendees
- **Notes**: Add notes to meetings
- **Tasks**: Create tasks due at specific meetings

## Setup

Python 3.12 is required. The pinned pydantic 2.5.3 does not build on Python 3.13 or later.

1. Install dependencies:
```bash
pip install -r requirements.txt
```

For a development environment, install the runtime and development dependencies instead:
```bash
pip install -r requirements-dev.txt
```

Run the format check, linter, and tests with:
```bash
scripts/check.sh
```

2. Run the application:
```bash
uvicorn main:app --reload
```

The API will be available at `http://localhost:8000`

## API Documentation

Once the server is running, you can access:
- Interactive API docs (Swagger UI): `http://localhost:8000/docs`
- Alternative API docs (ReDoc): `http://localhost:8000/redoc`
- OpenAPI schema: `http://localhost:8000/openapi.json`

## API Endpoints

### Users
- `POST /users/` - Create a new user
- `GET /users/` - List all users
- `GET /users/{user_id}` - Get a specific user

### Meetings
- `POST /meetings/` - Create a new meeting with attendees
- `GET /meetings/` - List all meetings
- `GET /meetings/{meeting_id}` - Get a specific meeting with details
- `PUT /meetings/{meeting_id}` - Update a meeting
- `DELETE /meetings/{meeting_id}` - Delete a meeting

### Notes
- `POST /notes/` - Create a new note for a meeting
- `GET /notes/` - List all notes (can filter by meeting_id)
- `GET /notes/{note_id}` - Get a specific note

### Tasks
- `POST /tasks/` - Create a new task due at a meeting
- `GET /tasks/` - List all tasks (can filter by meeting_id or status)
- `GET /tasks/{task_id}` - Get a specific task
- `PUT /tasks/{task_id}` - Update a task (change status, reassign to different meeting)
- `DELETE /tasks/{task_id}` - Delete a task

## Example Usage

### Create a user
```bash
curl -X POST http://localhost:8000/users/ \
  -H "Content-Type: application/json" \
  -d '{"name": "John Doe", "email": "john@example.com"}'
```

### Create a meeting
```bash
curl -X POST http://localhost:8000/meetings/ \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Sprint Planning",
    "description": "Q1 Sprint Planning",
    "scheduled_time": "2024-01-15T10:00:00",
    "attendee_ids": [1, 2]
  }'
```

### Create a task for a meeting
```bash
curl -X POST http://localhost:8000/tasks/ \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Implement feature X",
    "description": "Complete implementation",
    "due_meeting_id": 1
  }'
```

### Add a note to a meeting
```bash
curl -X POST http://localhost:8000/notes/ \
  -H "Content-Type: application/json" \
  -d '{
    "content": "This is a meeting note",
    "meeting_id": 1
  }'
```

## Project Structure

- `main.py` - FastAPI application with stubbed endpoints
- `models.py` - SQLAlchemy database models
- `schemas.py` - Pydantic schemas for request/response validation
- `database.py` - Database configuration and session management
- `requirements.txt` - Python dependencies

## Note

The current implementation has stubbed endpoints that return mock data. Database integration is prepared but not yet connected to the endpoints.
