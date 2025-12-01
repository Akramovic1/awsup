"""
Input validation and security checks for AWS Website Deployer
"""
import re
import os
import ipaddress
from typing import List, Dict, Optional, Tuple
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class DomainValidator:
    """Domain name validation"""
    
    # RFC 1123 compliant domain regex
    DOMAIN_REGEX = re.compile(
        r'^(?=.{1,253}$)'                           # Total length <= 253
        r'(?!.*\.\.)' +                             # No consecutive dots
        r'(?![.-])'                                 # Not start with . or -
        r'[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?'  # Valid characters
        r'(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$'  # Additional labels
    )
    
    @classmethod
    def validate_domain(cls, domain: str) -> Tuple[bool, Optional[str]]:
        """
        Validate domain name format
        Returns: (is_valid, error_message)
        """
        if not domain:
            return False, "Domain cannot be empty"
        
        domain = domain.lower().strip()
        
        if not cls.DOMAIN_REGEX.match(domain):
            return False, "Invalid domain format"
        
        # Check for TLD (must have at least one dot)
        if '.' not in domain:
            return False, "Domain must have a TLD (e.g., .com, .org)"
        
        # Additional checks
        if len(domain) < 4:  # minimum like "a.co"
            return False, "Domain too short (minimum 4 characters)"
        
        if domain.startswith('www.'):
            return False, "Please provide root domain without 'www' prefix"
        
        # Check for reserved/blocked domains (only localhost and AWS domains)
        blocked_domains = [
            'localhost', 'invalid',
            'amazonaws.com', 'amazon.com'
        ]
        
        if domain in blocked_domains:
            return False, f"Domain '{domain}' is reserved or blocked"
        
        return True, None
    
    @classmethod
    def normalize_domain(cls, domain: str) -> str:
        """Normalize domain name"""
        return domain.lower().strip().rstrip('.')

    @classmethod
    def is_subdomain(cls, domain: str) -> bool:
        """
        Check if domain is a subdomain (has more than 2 parts, e.g., api.example.com)
        Returns: True if subdomain, False if root domain
        """
        domain = cls.normalize_domain(domain)
        parts = domain.split('.')

        # Special handling for common 2-part TLDs like .co.uk, .com.au
        two_part_tlds = ['co.uk', 'com.au', 'co.in', 'co.za', 'com.br']

        for tld in two_part_tlds:
            if domain.endswith(tld):
                # For .co.uk domains: example.co.uk is root, api.example.co.uk is subdomain
                return len(parts) > 3

        # Standard TLDs: example.com is root, api.example.com is subdomain
        return len(parts) > 2

    @classmethod
    def get_parent_domain(cls, subdomain: str) -> Optional[str]:
        """
        Extract parent domain from subdomain
        E.g., 'api.example.com' -> 'example.com'
        Returns: Parent domain or None if already root domain
        """
        subdomain = cls.normalize_domain(subdomain)

        if not cls.is_subdomain(subdomain):
            return None

        parts = subdomain.split('.')

        # Special handling for common 2-part TLDs
        two_part_tlds = ['co.uk', 'com.au', 'co.in', 'co.za', 'com.br']

        for tld in two_part_tlds:
            if subdomain.endswith(tld):
                # Return the last 3 parts (domain.tld1.tld2)
                return '.'.join(parts[-3:])

        # Standard TLDs: return last 2 parts (domain.tld)
        return '.'.join(parts[-2:])


class FileValidator:
    """Website file validation"""
    
    ALLOWED_EXTENSIONS = {
        '.html', '.htm', '.css', '.js', '.json', '.xml', '.txt',
        '.jpg', '.jpeg', '.png', '.gif', '.svg', '.ico', '.webp',
        '.woff', '.woff2', '.ttf', '.eot', '.otf',
        '.pdf', '.zip', '.tar.gz'
    }
    
    SECURITY_PATTERNS = [
        r'<script[^>]*>.*?</script>',              # Script tags (basic check)
        r'javascript:',                           # JavaScript URLs
        r'on\w+\s*=',                            # Event handlers
        r'eval\s*\(',                            # eval() calls
        r'document\.write',                       # document.write
        r'window\.location',                      # Location manipulation
    ]
    
    @classmethod
    def validate_website_path(cls, path: str) -> Tuple[bool, Optional[str]]:
        """
        Validate website file/directory path
        Returns: (is_valid, error_message)
        """
        if not path:
            return False, "Path cannot be empty"
        
        path_obj = Path(path)
        
        if not path_obj.exists():
            return False, f"Path does not exist: {path}"
        
        # If it's a file, validate it
        if path_obj.is_file():
            return cls.validate_file(path)
        
        # If it's a directory, validate contents
        if path_obj.is_dir():
            return cls.validate_directory(path)
        
        return False, "Path is neither file nor directory"
    
    @classmethod
    def validate_file(cls, file_path: str) -> Tuple[bool, Optional[str]]:
        """Validate individual file"""
        path_obj = Path(file_path)
        
        # Check file extension
        if path_obj.suffix.lower() not in cls.ALLOWED_EXTENSIONS:
            return False, f"File type not allowed: {path_obj.suffix}"
        
        # Check file size (100MB limit)
        if path_obj.stat().st_size > 100 * 1024 * 1024:
            return False, "File too large (>100MB)"
        
        # Basic security scan for HTML/JS files
        if path_obj.suffix.lower() in ['.html', '.htm', '.js']:
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read(10000)  # Read first 10KB for security scan
                    
                for pattern in cls.SECURITY_PATTERNS:
                    if re.search(pattern, content, re.IGNORECASE):
                        logger.warning(f"Potential security issue in {file_path}: {pattern}")
            except Exception:
                pass  # Skip security scan if file can't be read
        
        return True, None
    
    @classmethod
    def validate_directory(cls, dir_path: str) -> Tuple[bool, Optional[str]]:
        """Validate directory contents"""
        path_obj = Path(dir_path)
        
        # Check if index.html exists
        index_file = path_obj / 'index.html'
        if not index_file.exists():
            return False, "Directory must contain index.html"
        
        # Validate all files in directory
        total_size = 0
        file_count = 0
        
        for file_path in path_obj.rglob('*'):
            if file_path.is_file():
                # Skip hidden files
                if file_path.name.startswith('.'):
                    continue
                
                file_count += 1
                total_size += file_path.stat().st_size
                
                # Validate individual file
                is_valid, error = cls.validate_file(str(file_path))
                if not is_valid:
                    return False, f"Invalid file {file_path.name}: {error}"
        
        # Check total size (1GB limit)
        if total_size > 1024 * 1024 * 1024:
            return False, "Directory too large (>1GB)"
        
        # Check file count (10,000 limit)
        if file_count > 10000:
            return False, "Too many files (>10,000)"
        
        return True, None


class AWSValidator:
    """AWS-specific validation"""
    
    @staticmethod
    def validate_region(region: str) -> bool:
        """Validate AWS region"""
        # Standard AWS regions (as of 2024)
        valid_regions = {
            'us-east-1', 'us-east-2', 'us-west-1', 'us-west-2',
            'eu-west-1', 'eu-west-2', 'eu-west-3', 'eu-central-1', 'eu-north-1',
            'ap-southeast-1', 'ap-southeast-2', 'ap-northeast-1', 'ap-northeast-2', 'ap-south-1',
            'ca-central-1', 'sa-east-1'
        }
        return region in valid_regions
    
    @staticmethod
    def validate_bucket_name(bucket_name: str) -> Tuple[bool, Optional[str]]:
        """Validate S3 bucket name according to AWS rules"""
        if not bucket_name:
            return False, "Bucket name cannot be empty"
        
        if len(bucket_name) < 3 or len(bucket_name) > 63:
            return False, "Bucket name must be 3-63 characters"
        
        # AWS bucket naming rules
        if not re.match(r'^[a-z0-9.-]+$', bucket_name):
            return False, "Bucket name can only contain lowercase letters, numbers, dots, and hyphens"
        
        if bucket_name.startswith('.') or bucket_name.endswith('.'):
            return False, "Bucket name cannot start or end with a dot"
        
        if bucket_name.startswith('-') or bucket_name.endswith('-'):
            return False, "Bucket name cannot start or end with a hyphen"
        
        if '..' in bucket_name:
            return False, "Bucket name cannot contain consecutive dots"
        
        # Check for IP address format
        try:
            ipaddress.ip_address(bucket_name)
            return False, "Bucket name cannot be an IP address"
        except ValueError:
            pass  # Not an IP address, which is good
        
        return True, None
    
    @staticmethod
    def check_service_availability() -> Dict[str, bool]:
        """Check AWS service availability"""
        services = {}
        
        try:
            boto3.client('sts').get_caller_identity()
            services['credentials'] = True
        except Exception:
            services['credentials'] = False
        
        for service in ['route53', 's3', 'cloudfront', 'acm']:
            try:
                client = boto3.client(service, region_name='us-east-1')
                # Make a simple API call to test permissions
                if service == 'route53':
                    client.list_hosted_zones(MaxItems='1')
                elif service == 's3':
                    client.list_buckets()
                elif service == 'cloudfront':
                    client.list_distributions(MaxItems='1')
                elif service == 'acm':
                    client.list_certificates(MaxItems=1)
                
                services[service] = True
            except Exception:
                services[service] = False
        
        return services


class SecurityValidator:
    """Security validation and checks"""
    
    SENSITIVE_PATTERNS = [
        r'aws_access_key_id',
        r'aws_secret_access_key',
        r'password\s*[=:]',
        r'secret\s*[=:]',
        r'token\s*[=:]',
        r'api_key\s*[=:]',
        r'private_key',
    ]
    
    @classmethod
    def scan_file_for_secrets(cls, file_path: str) -> List[str]:
        """Scan file for potential secrets"""
        issues = []
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                
            for pattern in cls.SENSITIVE_PATTERNS:
                matches = re.finditer(pattern, content, re.IGNORECASE)
                for match in matches:
                    line_num = content[:match.start()].count('\n') + 1
                    issues.append(f"Potential secret at line {line_num}: {match.group()}")
        
        except Exception as e:
            logger.warning(f"Could not scan file {file_path}: {e}")
        
        return issues
    
    @classmethod
    def validate_environment_variables(cls) -> List[str]:
        """Check for exposed secrets in environment"""
        issues = []
        
        for key, value in os.environ.items():
            if any(pattern.replace(r'\s*[=:]', '').replace('_', '').lower() in key.lower() 
                   for pattern in cls.SENSITIVE_PATTERNS):
                if value and len(value) > 10:  # Likely actual secret
                    issues.append(f"Potential secret in environment variable: {key}")
        
        return issues