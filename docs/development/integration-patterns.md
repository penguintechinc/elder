## Common Integration Patterns

### License-Gated Features
```python
# Python feature gating
from shared.licensing import license_client, requires_feature

@requires_feature("advanced_analytics")
def generate_advanced_report():
    """This feature requires professional+ license"""
    return advanced_analytics.generate_report()

# Startup validation
def initialize_application():
    client = license_client.get_client()
    validation = client.validate()
    if not validation.get("valid"):
        logger.error(f"License validation failed: {validation.get('message')}")
        sys.exit(1)

    logger.info(f"License valid for {validation['customer']} ({validation['tier']})")
    return validation
```

```go
// Go feature gating
package main

import (
    "log"
    "os"
    "your-project/internal/license"
)

func main() {
    client := license.NewClient(os.Getenv("LICENSE_KEY"), "your-product")

    validation, err := client.Validate()
    if err != nil || !validation.Valid {
        log.Fatal("License validation failed")
    }

    log.Printf("License valid for %s (%s)", validation.Customer, validation.Tier)

    // Check features
    if hasAdvanced, _ := client.CheckFeature("advanced_feature"); hasAdvanced {
        log.Println("Advanced features enabled")
    }
}
```

### Database Integration
```python
# Python with SQLAlchemy
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)
```

```go
// Go with GORM
import "gorm.io/gorm"

type User struct {
    ID    uint   `gorm:"primaryKey"`
    Name  string `gorm:"not null"`
    Email string `gorm:"uniqueIndex;not null"`
}
```

### API Development
```python
# Python with Flask
from flask import Blueprint, request, jsonify
from flask_cors import cross_origin
from apps.api.models import User
from shared.database import db

bp = Blueprint("users", __name__)

@bp.route('/users', methods=['GET', 'POST'])
@cross_origin()
def api_users():
    if request.method == 'GET':
        users = User.query.all()
        return jsonify({'users': [u.to_dict() for u in users]})
    # Handle POST...
```

```go
// Go with Gin
func setupRoutes() *gin.Engine {
    r := gin.Default()
    r.Use(cors.Default())

    v1 := r.Group("/api/v1")
    {
        v1.GET("/users", getUsers)
        v1.POST("/users", createUser)
    }
    return r
}
```

### Monitoring Integration
```python
# Python metrics with Flask
from prometheus_flask_exporter import PrometheusMetrics
from flask import Flask

app = Flask(__name__)
metrics = PrometheusMetrics(app)

# Metrics are automatically collected and exposed at /metrics
# Custom metrics can be added:
metrics.info('app_info', 'Application info', version='1.0.0')
```

```go
// Go metrics
import "github.com/prometheus/client_golang/prometheus"

var (
    requestCount = prometheus.NewCounterVec(
        prometheus.CounterOpts{Name: "http_requests_total"},
        []string{"method", "endpoint"})
    requestDuration = prometheus.NewHistogramVec(
        prometheus.HistogramOpts{Name: "http_request_duration_seconds"},
        []string{"method", "endpoint"})
)
```

### Cross-Language Schema Validation

#### Python: Pydantic 2 with @validated_request Decorator
```python
# Python validation with Pydantic 2
from pydantic import BaseModel, Field, validator
from functools import wraps
from flask import request, jsonify
from typing import Optional

class CreateUserRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email: str = Field(..., regex=r'^[\w\.-]+@[\w\.-]+\.\w+$')
    age: Optional[int] = Field(None, ge=0, le=150)

    @validator('name')
    def name_must_be_alphanumeric(cls, v):
        if not v.replace(' ', '').isalnum():
            raise ValueError('Name must contain only alphanumeric characters')
        return v

def validated_request(schema_cls):
    """Decorator for validating Flask request JSON against Pydantic schema"""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            try:
                data = request.get_json()
                validated = schema_cls(**data)
                return f(validated, *args, **kwargs)
            except ValueError as e:
                return jsonify({'error': str(e)}), 400
        return wrapper
    return decorator

@app.route('/users', methods=['POST'])
@validated_request(CreateUserRequest)
def create_user(payload: CreateUserRequest):
    # payload is validated and type-safe
    user = User(name=payload.name, email=payload.email, age=payload.age)
    db.session.add(user)
    db.session.commit()
    return jsonify(user.to_dict()), 201
```

#### Go: Struct Validation with go-playground/validator
```go
// Go validation with go-playground/validator
package main

import (
    "github.com/go-playground/validator/v10"
    "github.com/gin-gonic/gin"
    "net/http"
)

type CreateUserRequest struct {
    Name  string `json:"name" validate:"required,min=1,max=255,alpha"`
    Email string `json:"email" validate:"required,email"`
    Age   *int   `json:"age" validate:"omitempty,min=0,max=150"`
}

var validate = validator.New()

func createUser(c *gin.Context) {
    var req CreateUserRequest

    if err := c.ShouldBindJSON(&req); err != nil {
        c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
        return
    }

    // Validate struct against tags
    if err := validate.Struct(req); err != nil {
        c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
        return
    }

    // req is validated and type-safe
    user := &User{
        Name:  req.Name,
        Email: req.Email,
        Age:   req.Age,
    }

    if err := db.Create(user).Error; err != nil {
        c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
        return
    }

    c.JSON(http.StatusCreated, user)
}

func main() {
    r := gin.Default()
    r.POST("/users", createUser)
    r.Run()
}
```

#### Node.js: Zod Schemas with safeParse
```typescript
// Node.js validation with Zod
import { z } from 'zod';
import express, { Request, Response } from 'express';

const CreateUserSchema = z.object({
    name: z.string().min(1).max(255),
    email: z.string().email(),
    age: z.number().min(0).max(150).optional(),
});

type CreateUserRequest = z.infer<typeof CreateUserSchema>;

// Middleware for automatic validation
const validateRequest = (schema: z.ZodSchema) => {
    return (req: Request, res: Response, next: Function) => {
        const result = schema.safeParse(req.body);

        if (!result.success) {
            return res.status(400).json({
                error: 'Validation failed',
                details: result.error.errors,
            });
        }

        req.body = result.data;
        next();
    };
};

const app = express();
app.use(express.json());

app.post('/users', validateRequest(CreateUserSchema), async (req: Request, res: Response) => {
    const payload: CreateUserRequest = req.body;

    // payload is validated and type-safe
    const user = await User.create({
        name: payload.name,
        email: payload.email,
        age: payload.age,
    });

    res.status(201).json(user);
});

app.listen(3000, () => console.log('Server running on port 3000'));
```
