# API Conventions

Sets rules for API design patterns in this project.

## REST Endpoints

### URL Structure

- Use lowercase, hyphen-separated resource names: `/user-profiles`, not `/userProfiles`
- Use plural nouns for collections: `/users`, `/orders`
- Nest resources to show relationships: `/users/{id}/orders`
- No trailing slashes

### HTTP Methods

| Action | Method | Example |
|---|---|---|
| List | `GET` | `GET /users` |
| Get one | `GET` | `GET /users/{id}` |
| Create | `POST` | `POST /users` |
| Full update | `PUT` | `PUT /users/{id}` |
| Partial update | `PATCH` | `PATCH /users/{id}` |
| Delete | `DELETE` | `DELETE /users/{id}` |

### Status Codes

- `200 OK` — successful GET, PATCH, PUT
- `201 Created` — successful POST (include `Location` header)
- `204 No Content` — successful DELETE
- `400 Bad Request` — invalid input (include error details)
- `401 Unauthorized` — missing or invalid auth
- `403 Forbidden` — authenticated but not authorized
- `404 Not Found` — resource does not exist
- `422 Unprocessable Entity` — validation error
- `500 Internal Server Error` — unexpected server failure

## Request / Response Format

### Request Body (JSON)

```json
{
  "field_name": "value"
}
```

- Use `snake_case` for all field names
- Include `Content-Type: application/json` header

### Response Envelope

**Success (collection):**
```json
{
  "data": [...],
  "meta": {
    "total": 100,
    "page": 1,
    "per_page": 20
  }
}
```

**Success (single resource):**
```json
{
  "data": { ... }
}
```

**Error:**
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Human-readable description",
    "details": [
      { "field": "email", "issue": "Invalid format" }
    ]
  }
}
```

## Versioning

- Version in the URL path: `/v1/users`
- Bump major version only for breaking changes
- Keep old versions alive for at least one deprecation cycle

## Authentication

- Use Bearer token in `Authorization` header
- Never pass secrets in query parameters
