class AutomationError(Exception):
    """Base class for automation errors"""
    pass

class AuthError(AutomationError):
    """Authentication failed"""
    pass

class NetworkError(AutomationError):
    """Network connection failed"""
    pass

class RateLimitError(AutomationError):
    """Rate limit exceeded"""
    pass

class ServerError(AutomationError):
    """Server returned 5xx error"""
    pass

class DSLValidationError(AutomationError):
    """DSL validation failed"""
    pass
