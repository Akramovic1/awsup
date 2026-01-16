"""
ACM Manager for SSL certificate operations
"""
import time
from typing import Dict, List, Optional, Any
from botocore.exceptions import ClientError
from .base import BaseAWSManager
from ..config import DeploymentConfig


class ACMManager(BaseAWSManager):
    """Manages ACM SSL certificates"""

    def __init__(self, config: DeploymentConfig):
        super().__init__(config)
        # ACM must be in us-east-1 for CloudFront
        self.client = self.get_client('acm', region_name='us-east-1')
        self.domain = config.domain
        self.www_domain = f"www.{config.domain}"
        self.is_subdomain = config.is_subdomain
    
    def request_or_get_certificate(self, route53_manager) -> str:
        """
        Request new certificate or get existing valid one
        Returns: Certificate ARN
        """
        try:
            # Check for existing certificate
            existing_cert = self.get_existing_certificate()
            
            if existing_cert:
                self.logger.info(f"Using existing certificate: {existing_cert}")
                return existing_cert
            
            # Request new certificate
            # For subdomains, only request certificate for the subdomain itself (no www)
            if self.is_subdomain:
                self.logger.info(f"Requesting new ACM certificate for subdomain {self.domain}")
                san_list = [self.domain]
            else:
                self.logger.info(f"Requesting new ACM certificate for {self.domain} and {self.www_domain}")
                san_list = [self.domain, self.www_domain]

            def request_cert():
                return self.client.request_certificate(
                    DomainName=self.domain,
                    SubjectAlternativeNames=san_list,
                    ValidationMethod=self.config.certificate_validation_method,
                    Tags=[
                        {'Key': 'Name', 'Value': self.domain},
                        {'Key': 'ManagedBy', 'Value': 'AWSWebsiteDeployer'},
                        {'Key': 'Environment', 'Value': self.config.environment}
                    ]
                )
            
            response = self.retry_with_backoff(request_cert)
            cert_arn = response['CertificateArn']
            
            self.logger.info(f"Certificate requested: {cert_arn}")
            
            # Wait for validation records to be available
            time.sleep(5)
            
            # Create DNS validation records  
            self.create_dns_validation_records(cert_arn, route53_manager)
            
            # Wait for certificate validation
            self.wait_for_certificate_validation(cert_arn)
            
            return cert_arn
            
        except Exception as e:
            self.logger.error(f"ACM certificate setup failed: {e}")
            raise
    
    def get_existing_certificate(self) -> Optional[str]:
        """Check for existing certificate covering our domains (any status)"""
        try:
            # Get all certificates (no status filter)
            def list_all_certs():
                return self.client.list_certificates()
            
            response = self.retry_with_backoff(list_all_certs)
            
            # Look for certificates that cover our domains
            matching_certs = []
            
            for cert in response.get('CertificateSummaryList', []):
                def describe_cert():
                    return self.client.describe_certificate(CertificateArn=cert['CertificateArn'])
                
                cert_details = self.retry_with_backoff(describe_cert)
                cert_info = cert_details['Certificate']
                
                # Check if certificate covers our domains
                domains = [cert_info['DomainName']] + cert_info.get('SubjectAlternativeNames', [])

                # For subdomains, only need subdomain itself; for root domains, need both domain and www
                if self.is_subdomain:
                    if self.domain in domains:
                        matching_certs.append({
                            'arn': cert['CertificateArn'],
                            'status': cert_info['Status'],
                            'domains': domains
                        })
                else:
                    if (self.domain in domains and self.www_domain in domains):
                        matching_certs.append({
                            'arn': cert['CertificateArn'],
                            'status': cert_info['Status'],
                            'domains': domains
                        })
            
            if not matching_certs:
                return None
            
            # Prioritize certificates by status: ISSUED > PENDING_VALIDATION > others
            status_priority = {
                'ISSUED': 1,
                'PENDING_VALIDATION': 2,
                'VALIDATION_TIMED_OUT': 3,
                'FAILED': 4,
                'EXPIRED': 5,
                'REVOKED': 6
            }
            
            # Sort by priority (lower number = higher priority)
            matching_certs.sort(key=lambda cert: status_priority.get(cert['status'], 99))
            
            best_cert = matching_certs[0]
            self.logger.info(f"Found existing certificate for {self.domain} with status: {best_cert['status']}")
            
            return best_cert['arn']
            
        except Exception as e:
            self.logger.warning(f"Error checking existing certificates: {e}")
            return None
    
    def create_dns_validation_records(self, cert_arn: str, route53_manager):
        """Create DNS validation records in Route53"""
        try:
            def describe_cert():
                return self.client.describe_certificate(CertificateArn=cert_arn)
            
            cert_details = self.retry_with_backoff(describe_cert)
            
            # Extract validation records
            validation_records = []
            for option in cert_details['Certificate'].get('DomainValidationOptions', []):
                if 'ResourceRecord' in option:
                    record = option['ResourceRecord']
                    validation_records.append({
                        'Action': 'UPSERT',
                        'ResourceRecordSet': {
                            'Name': record['Name'],
                            'Type': record['Type'],
                            'TTL': 300,
                            'ResourceRecords': [{'Value': record['Value']}]
                        }
                    })
            
            if validation_records:
                # Determine the correct hosted zone to use
                # For subdomains, use parent domain's hosted zone
                if self.is_subdomain and self.config.parent_domain:
                    self.logger.info(f"Looking up parent domain hosted zone: {self.config.parent_domain}")
                    parent_zone = route53_manager.get_hosted_zone_for_domain(self.config.parent_domain)
                    if parent_zone:
                        hosted_zone_id = parent_zone['Id']
                        self.logger.info(f"Using parent domain hosted zone: {hosted_zone_id}")
                    else:
                        raise ValueError(f"Parent domain hosted zone not found: {self.config.parent_domain}")
                else:
                    # For root domains, use current domain's hosted zone
                    existing_zone = route53_manager.get_hosted_zone()
                    if existing_zone:
                        hosted_zone_id = existing_zone['Id']
                        self.logger.info(f"Using domain hosted zone: {hosted_zone_id}")
                    else:
                        raise ValueError("No hosted zone found for DNS validation")
                
                def create_validation_records():
                    return route53_manager.client.change_resource_record_sets(
                        HostedZoneId=hosted_zone_id,
                        ChangeBatch={'Changes': validation_records}
                    )
                
                route53_manager.retry_with_backoff(create_validation_records)
                self.logger.info(f"Created {len(validation_records)} DNS validation records")
            
        except Exception as e:
            self.logger.error(f"Failed to create DNS validation records: {e}")
            raise
    
    def wait_for_certificate_validation(self, cert_arn: str):
        """Wait for certificate to be validated and issued"""
        try:
            self.logger.info("Waiting for certificate validation (this may take a few minutes)...")
            
            waiter = self.client.get_waiter('certificate_validated')
            
            waiter.wait(
                CertificateArn=cert_arn,
                WaiterConfig={
                    'Delay': 10,
                    'MaxAttempts': self.config.certificate_validation_timeout // 10
                }
            )
            
            self.logger.info("Certificate issued successfully!")
            return True
            
        except Exception as e:
            self.logger.warning(f"Certificate validation timeout or error: {e}")
            # Check if certificate is actually valid despite timeout
            try:
                response = self.client.describe_certificate(CertificateArn=cert_arn)
                status = response['Certificate']['Status']
                
                if status == 'ISSUED':
                    self.logger.info("Certificate is actually valid despite timeout!")
                    return True
                elif status == 'PENDING_VALIDATION':
                    self.logger.error("Certificate still pending validation - cannot proceed")
                    return False
                else:
                    self.logger.error(f"Certificate in unexpected status: {status}")
                    return False
                    
            except Exception as check_error:
                self.logger.error(f"Failed to check certificate status: {check_error}")
                return False
    
    def delete_certificate(self, cert_arn: str):
        """Delete ACM certificate"""
        try:
            self.logger.info(f"Deleting ACM certificate: {cert_arn}")
            
            def delete_cert():
                return self.client.delete_certificate(CertificateArn=cert_arn)
            
            self.retry_with_backoff(delete_cert)
            self.logger.info("ACM certificate deleted successfully")
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceInUseException':
                self.logger.warning("Certificate is still in use. It will be deleted after CloudFront distribution is removed.")
            else:
                self.logger.error(f"Failed to delete certificate: {e}")
                raise
        except Exception as e:
            self.logger.error(f"Failed to delete certificate: {e}")
            raise
    
    def get_certificate_status(self, cert_arn: str) -> str:
        """Get certificate status"""
        try:
            def describe_cert():
                return self.client.describe_certificate(CertificateArn=cert_arn)
            
            response = self.retry_with_backoff(describe_cert)
            return response['Certificate']['Status']
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceNotFoundException':
                return 'NOT_FOUND'
            raise
        except Exception:
            return 'ERROR'
    
    def validate_resource_exists(self, cert_arn: str) -> bool:
        """Check if certificate exists"""
        try:
            status = self.get_certificate_status(cert_arn)
            return status not in ['NOT_FOUND', 'ERROR']
        except Exception:
            return False
    
    def get_resource_status(self, cert_arn: str) -> str:
        """Get certificate status"""
        return self.get_certificate_status(cert_arn)
    
    def add_tags(self, cert_arn: str, additional_tags: Optional[Dict[str, str]] = None):
        """Add tags to certificate"""
        try:
            tags = self.config.default_tags.copy()
            if additional_tags:
                tags.update(additional_tags)
            
            tag_list = [{'Key': k, 'Value': v} for k, v in tags.items()]
            
            def add_cert_tags():
                return self.client.add_tags_to_certificate(
                    CertificateArn=cert_arn,
                    Tags=tag_list
                )
            
            self.retry_with_backoff(add_cert_tags)
            self.logger.info(f"Added tags to certificate: {list(tags.keys())}")
            
        except Exception as e:
            self.logger.warning(f"Failed to add tags to certificate: {e}")
    
    def list_domain_certificates(self) -> List[Dict[str, Any]]:
        """List all certificates for the domain"""
        try:
            def list_certs():
                return self.client.list_certificates()
            
            response = self.retry_with_backoff(list_certs)
            
            domain_certs = []
            for cert in response.get('CertificateSummaryList', []):
                def describe_cert():
                    return self.client.describe_certificate(CertificateArn=cert['CertificateArn'])
                
                cert_details = self.retry_with_backoff(describe_cert)
                cert_info = cert_details['Certificate']
                
                # Check if certificate is for our domain
                domains = [cert_info['DomainName']] + cert_info.get('SubjectAlternativeNames', [])
                if self.domain in domains or self.www_domain in domains:
                    domain_certs.append({
                        'arn': cert['CertificateArn'],
                        'status': cert_info['Status'],
                        'domains': domains,
                        'issued_at': cert_info.get('IssuedAt'),
                        'expires_at': cert_info.get('NotAfter')
                    })
            
            return domain_certs
            
        except Exception as e:
            self.logger.error(f"Failed to list certificates: {e}")
            return []