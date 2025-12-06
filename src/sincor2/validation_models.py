"""
SINCOR Input Validation Models
Pydantic models for validating all user inputs and preventing injection attacks
"""

from pydantic import BaseModel, EmailStr, Field, field_validator, ConfigDict
from typing import Optional, List
from datetime import datetime


class WaitlistSignup(BaseModel):
    """Validation model for waitlist signup"""
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    name: Optional[str] = Field(None, max_length=100)
    company: Optional[str] = Field(None, max_length=200)
    phone: Optional[str] = Field(None, max_length=20)
    product_interest: Optional[str] = Field(None, max_length=100)
    message: Optional[str] = Field(None, max_length=1000)

    @field_validator('name', 'company', 'product_interest', 'message')
    @classmethod
    def validate_name(cls, v):
        if v:
            # Check length
            if len(v.strip()) < 2 and v == v.strip():
                raise ValueError('Field must be at least 2 characters')
            # Block HTML/script tags
            dangerous_patterns = ['<script', '</script', '<iframe', 'javascript:', 'onerror=', 'onclick=']
            v_lower = v.lower()
            for pattern in dangerous_patterns:
                if pattern in v_lower:
                    raise ValueError('Invalid characters detected')
            # Sanitize
            return sanitize_string(v)
        return v

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v):
        if v:
            # Remove common separators
            cleaned = ''.join(c for c in v if c.isdigit() or c in '+()-. ')
            if len(cleaned) < 10:
                raise ValueError('Phone number must be at least 10 digits')
        return v


class PaymentCreateRequest(BaseModel):
    """Validation model for payment creation"""
    model_config = ConfigDict(str_strip_whitespace=True)

    amount: float = Field(gt=0, le=1000000, description="Payment amount in USD")
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    description: str = Field(min_length=1, max_length=500)
    customer_email: Optional[EmailStr] = None
    order_id: Optional[str] = Field(None, max_length=100)
    return_url: Optional[str] = Field(None, max_length=500)
    cancel_url: Optional[str] = Field(None, max_length=500)

    @field_validator('amount')
    @classmethod
    def validate_amount(cls, v):
        if v < 1:
            raise ValueError('Amount must be at least $1.00')
        if v > 1000000:
            raise ValueError('Amount cannot exceed $1,000,000')
        # Round to 2 decimal places
        return round(v, 2)

    @field_validator('description')
    @classmethod
    def validate_description(cls, v):
        if not v or len(v.strip()) < 3:
            raise ValueError('Description must be at least 3 characters')
        return v


class PaymentExecuteRequest(BaseModel):
    """Validation model for payment execution"""
    model_config = ConfigDict(str_strip_whitespace=True)

    payment_id: str = Field(min_length=1, max_length=200)
    payer_id: str = Field(min_length=1, max_length=200)

    @field_validator('payment_id', 'payer_id')
    @classmethod
    def validate_ids(cls, v):
        if not v or not v.strip():
            raise ValueError('ID cannot be empty')
        # Basic sanitization - only allow alphanumeric and hyphens
        if not all(c.isalnum() or c in '-_' for c in v):
            raise ValueError('ID contains invalid characters')
        return v


class LoginRequest(BaseModel):
    """Validation model for login"""
    model_config = ConfigDict(str_strip_whitespace=True)

    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=128)

    @field_validator('username')
    @classmethod
    def validate_username(cls, v):
        # Only allow alphanumeric, underscore, dash
        if not all(c.isalnum() or c in '_-@.' for c in v):
            raise ValueError('Username contains invalid characters')
        return v.lower()  # Normalize to lowercase


class AgentTaskRequest(BaseModel):
    """Validation model for agent task creation"""
    model_config = ConfigDict(str_strip_whitespace=True)

    task_id: Optional[str] = Field(None, max_length=100)
    description: str = Field(min_length=10, max_length=2000)
    priority: str = Field(default="medium", pattern="^(low|medium|high|urgent)$")
    assigned_agents: Optional[List[str]] = Field(None, max_length=10)
    deadline: Optional[str] = None
    budget: Optional[float] = Field(None, ge=0, le=1000000)

    @field_validator('description')
    @classmethod
    def validate_description(cls, v):
        if len(v.strip()) < 10:
            raise ValueError('Task description must be at least 10 characters')
        return v


class BusinessIntelligenceRequest(BaseModel):
    """Validation model for BI service requests"""
    model_config = ConfigDict(str_strip_whitespace=True)

    client_email: EmailStr
    service_type: str = Field(pattern="^(instant_bi|predictive_analytics|market_research|competitor_analysis)$")
    budget: float = Field(ge=1000, le=500000)
    timeline: str = Field(pattern="^(rush|standard|extended)$")
    requirements: List[str] = Field(min_length=1, max_length=20)
    industry: Optional[str] = Field(None, max_length=100)

    @field_validator('requirements')
    @classmethod
    def validate_requirements(cls, v):
        if not v or len(v) == 0:
            raise ValueError('At least one requirement must be specified')
        # Validate each requirement
        for req in v:
            if len(req.strip()) < 3:
                raise ValueError('Each requirement must be at least 3 characters')
        return v


class AgentCoordinationRequest(BaseModel):
    """Validation model for agent coordination"""
    model_config = ConfigDict(str_strip_whitespace=True)

    task: dict = Field(...)
    available_agents: List[dict] = Field(min_length=1, max_length=50)
    constraints: Optional[dict] = None

    @field_validator('task')
    @classmethod
    def validate_task(cls, v):
        required_fields = ['description']
        for field in required_fields:
            if field not in v:
                raise ValueError(f'Task must include "{field}" field')
        return v


class ContentGenerationRequest(BaseModel):
    """Validation model for content generation"""
    model_config = ConfigDict(str_strip_whitespace=True)

    content_type: str = Field(pattern="^(blog|whitepaper|case_study|email|social_post|script)$")
    topic: str = Field(min_length=5, max_length=200)
    target_length: int = Field(ge=100, le=10000, description="Target word count")
    tone: str = Field(default="professional", pattern="^(professional|casual|technical|persuasive|educational)$")
    audience: Optional[str] = Field(None, max_length=200)
    keywords: Optional[List[str]] = Field(None, max_length=20)

    @field_validator('keywords')
    @classmethod
    def validate_keywords(cls, v):
        if v:
            for keyword in v:
                if len(keyword.strip()) < 2:
                    raise ValueError('Keywords must be at least 2 characters')
        return v


# Validation helper functions

def validate_request(model_class, data: dict) -> tuple:
    """
    Validate request data against Pydantic model

    Returns:
        (validated_data, error_message)
        If validation succeeds: (dict, None)
        If validation fails: (None, str)
    """
    try:
        validated = model_class.model_validate(data)
        return validated.model_dump(), None
    except Exception as e:
        # Extract user-friendly error message
        error_msg = str(e)
        if hasattr(e, 'errors'):
            errors = e.errors()
            if errors:
                # Get first error for user-friendly message
                first_error = errors[0]
                field = '.'.join(str(loc) for loc in first_error['loc'])
                msg = first_error['msg']
                error_msg = f"Validation error in '{field}': {msg}"
        return None, error_msg


def sanitize_string(value: str, max_length: int = 1000) -> str:
    """
    Sanitize string input to prevent injection attacks

    - Strips whitespace
    - Removes null bytes
    - Limits length
    - Escapes HTML special characters (basic)
    """
    if not value:
        return ""

    # Remove null bytes
    sanitized = value.replace('\x00', '')

    # Strip whitespace
    sanitized = sanitized.strip()

    # Limit length
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length]

    # Basic HTML escaping (Flask/Jinja2 also does this)
    replacements = {
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#x27;',
        '&': '&amp;'
    }

    for char, escape in replacements.items():
        sanitized = sanitized.replace(char, escape)

    return sanitized


def validate_email(email: str) -> tuple:
    """
    Validate email address

    Returns:
        (is_valid, error_message)
    """
    try:
        # Use Pydantic's EmailStr for validation
        from pydantic import TypeAdapter
        adapter = TypeAdapter(EmailStr)
        validated = adapter.validate_python(email)
        return True, None
    except Exception as e:
        return False, "Invalid email address format"


def validate_url(url: str, allow_relative: bool = False) -> tuple:
    """
    Validate URL

    Returns:
        (is_valid, error_message)
    """
    if not url:
        return False, "URL cannot be empty"

    # Basic URL validation
    if allow_relative:
        if url.startswith('/'):
            return True, None

    if not (url.startswith('http://') or url.startswith('https://')):
        return False, "URL must start with http:// or https://"

    # Check for obvious injection attempts
    dangerous_patterns = ['javascript:', 'data:', 'vbscript:', '<script']
    for pattern in dangerous_patterns:
        if pattern.lower() in url.lower():
            return False, "URL contains invalid pattern"

    return True, None


# Test function
def test_validation():
    """Test validation models"""
    print("Testing SINCOR Validation Models...")

    # Test 1: Valid waitlist signup
    valid_signup = {
        'email': 'test@example.com',
        'name': 'John Doe',
        'company': 'Acme Corp',
        'phone': '+1-555-123-4567'
    }
    data, error = validate_request(WaitlistSignup, valid_signup)
    assert error is None, f"Valid signup failed: {error}"
    print("✅ WaitlistSignup validation works")

    # Test 2: Invalid email
    invalid_signup = {
        'email': 'not-an-email',
        'name': 'John Doe'
    }
    data, error = validate_request(WaitlistSignup, invalid_signup)
    assert error is not None, "Invalid email should fail"
    print("✅ Email validation catches invalid emails")

    # Test 3: Valid payment request
    valid_payment = {
        'amount': 100.50,
        'description': 'Test payment',
        'currency': 'USD'
    }
    data, error = validate_request(PaymentCreateRequest, valid_payment)
    assert error is None, f"Valid payment failed: {error}"
    assert data['amount'] == 100.50
    print("✅ PaymentCreateRequest validation works")

    # Test 4: Invalid amount
    invalid_payment = {
        'amount': -50,
        'description': 'Test'
    }
    data, error = validate_request(PaymentCreateRequest, invalid_payment)
    assert error is not None, "Negative amount should fail"
    print("✅ Payment amount validation catches negative values")

    # Test 5: Valid login
    valid_login = {
        'username': 'admin',
        'password': 'securepass123'
    }
    data, error = validate_request(LoginRequest, valid_login)
    assert error is None, f"Valid login failed: {error}"
    print("✅ LoginRequest validation works")

    # Test 6: Short password
    invalid_login = {
        'username': 'admin',
        'password': 'short'
    }
    data, error = validate_request(LoginRequest, invalid_login)
    assert error is not None, "Short password should fail"
    print("✅ Password length validation works")

    # Test 7: String sanitization
    dangerous_input = "<script>alert('xss')</script>"
    sanitized = sanitize_string(dangerous_input)
    assert '<script>' not in sanitized, "Script tags should be escaped"
    print("✅ String sanitization works")

    # Test 8: Email validation
    is_valid, _ = validate_email('test@example.com')
    assert is_valid, "Valid email should pass"
    is_valid, _ = validate_email('not-an-email')
    assert not is_valid, "Invalid email should fail"
    print("✅ Email validation helper works")

    # Test 9: URL validation
    is_valid, _ = validate_url('https://example.com')
    assert is_valid, "Valid URL should pass"
    is_valid, _ = validate_url('javascript:alert(1)')
    assert not is_valid, "JavaScript URL should fail"
    print("✅ URL validation helper works")

    print("\n✅ ALL VALIDATION TESTS PASSED!")


if __name__ == "__main__":
    test_validation()
