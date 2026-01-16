"""
Base manager class with common functionality
"""
import time
import logging
from typing import Dict, Any, Optional, Callable
from botocore.exceptions import ClientError
from ..config import DeploymentConfig

logger = logging.getLogger(__name__)


class BaseAWSManager:
    """Base class for AWS service managers"""

    def __init__(self, config: DeploymentConfig):
        self.config = config
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self.session = config.get_boto3_session()

    def get_client(self, service_name: str, region_name: str = None):
        """Create boto3 client using session with profile"""
        kwargs = {}
        if region_name:
            kwargs['region_name'] = region_name
        return self.session.client(service_name, **kwargs)
    
    def retry_with_backoff(
        self, 
        operation: Callable,
        max_retries: Optional[int] = None,
        backoff_factor: Optional[float] = None,
        retryable_errors: Optional[list] = None
    ) -> Any:
        """
        Execute operation with exponential backoff retry
        
        Args:
            operation: Function to execute
            max_retries: Maximum retry attempts
            backoff_factor: Exponential backoff multiplier
            retryable_errors: List of error codes to retry on
        """
        max_retries = max_retries or self.config.max_retries
        backoff_factor = backoff_factor or self.config.retry_backoff_factor
        retryable_errors = retryable_errors or [
            'Throttling', 'TooManyRequestsException', 'RequestLimitExceeded',
            'ServiceUnavailable', 'InternalServerError'
        ]
        
        last_exception = None
        
        for attempt in range(max_retries + 1):
            try:
                return operation()
            
            except ClientError as e:
                error_code = e.response['Error']['Code']
                last_exception = e
                
                if error_code not in retryable_errors:
                    # Non-retryable error
                    raise
                
                if attempt == max_retries:
                    # Max retries reached
                    self.logger.error(f"Max retries ({max_retries}) reached for operation")
                    raise
                
                # Calculate backoff delay
                delay = (backoff_factor ** attempt) + (attempt * 0.1)
                self.logger.warning(
                    f"Retryable error {error_code}, attempt {attempt + 1}/{max_retries + 1}. "
                    f"Retrying in {delay:.1f}s..."
                )
                time.sleep(delay)
            
            except Exception as e:
                # Non-AWS error, don't retry
                self.logger.error(f"Non-retryable error: {e}")
                raise
        
        # Should never reach here, but just in case
        raise last_exception
    
    def add_tags(self, resource_arn: str, additional_tags: Optional[Dict[str, str]] = None):
        """Add tags to AWS resource (override in subclasses)"""
        pass
    
    def wait_for_resource(
        self, 
        waiter_name: str, 
        waiter_kwargs: Dict[str, Any],
        timeout: Optional[int] = None,
        check_interval: int = 30
    ) -> bool:
        """
        Wait for AWS resource to reach desired state
        
        Args:
            waiter_name: AWS waiter name
            waiter_kwargs: Arguments for waiter
            timeout: Maximum wait time in seconds
            check_interval: Check interval in seconds
        """
        timeout = timeout or 600  # Default 10 minutes
        
        try:
            # This would be implemented by each service manager
            # with their specific waiter logic
            return True
        
        except Exception as e:
            self.logger.error(f"Wait operation failed: {e}")
            return False
    
    def validate_resource_exists(self, resource_id: str) -> bool:
        """Check if resource exists (override in subclasses)"""
        return False
    
    def get_resource_status(self, resource_id: str) -> str:
        """Get resource status (override in subclasses)"""
        return "unknown"