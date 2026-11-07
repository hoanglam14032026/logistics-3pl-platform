# 3PL Logistics Business Guide Application

This is a FastAPI-based application that provides business guidelines and procedures for 3PL (Third-Party Logistics) employees.

## Features

- RESTful API for managing business guides
- SQLite database for data persistence
- CRUD operations for business guide content
- Category-based filtering of guides
- Active/inactive status management
- Automatic API documentation with Swagger UI and ReDoc

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd fastapi-todo
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Run the application:
```bash
uvicorn app.main:app --reload
```

## API Endpoints

- `GET /` - Welcome page with application information
- `GET /health` - Health check endpoint
- `GET /api/v1/guide/{guide_id}` - Get specific business guide
- `GET /api/v1/guide` - Get all business guides (with optional category filter)
- `POST /api/v1/guide` - Create new business guide
- `PUT /api/v1/guide/{guide_id}` - Update business guide
- `DELETE /api/v1/guide/{guide_id}` - Delete business guide

## Database Schema

The application uses SQLite with a single table `business_guides`:

- `id`: Primary key (Integer)
- `title`: Guide title (String, 200 characters max)
- `category`: Category of the guide (String, 100 characters max)
- `content`: Full content of the guide (Text)
- `is_active`: Status of the guide (Boolean, default: True)
- `created_at`: Timestamp when created (DateTime)
- `updated_at`: Timestamp when updated (DateTime)

## Usage Examples

### Get all guides
```bash
curl http://localhost:8000/api/v1/guide
```

### Get guides by category
```bash
curl http://localhost:8000/api/v1/guide?category=warehouse
```

### Create a new guide
```bash
curl -X POST http://localhost:8000/api/v1/guide \
     -H "Content-Type: application/json" \
     -d '{
       "title": "Warehouse Safety Procedures",
       "category": "warehouse",
       "content": "Detailed safety procedures for warehouse operations...",
       "is_active": true
     }'
```

## Documentation

The API documentation is automatically generated and available at:
- `/docs` - Swagger UI
- `/redoc` - ReDoc

## Development

To run tests or extend functionality:
1. Activate virtual environment
2. Install development dependencies as needed
3. Follow FastAPI best practices for code organization

## License

MIT License