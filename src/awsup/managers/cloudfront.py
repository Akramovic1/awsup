"""
CloudFront Manager for CDN operations
"""
import time
from typing import Dict, List, Optional, Any
from botocore.exceptions import ClientError
from .base import BaseAWSManager
from ..config import DeploymentConfig


class CloudFrontManager(BaseAWSManager):
    """Manages CloudFront distributions"""

    # AWS Managed Cache Policy IDs
    CACHE_POLICY_OPTIMIZED = '658327ea-f89d-4fab-a63d-7e88639e58f6'
    CACHE_POLICY_DISABLED = '4135ea2d-6df8-44a3-9df3-4b5a84be39ad'

    def __init__(self, config: DeploymentConfig):
        super().__init__(config)
        self.client = self.get_client('cloudfront')
        self.domain = config.domain
        self.www_domain = f"www.{config.domain}"
        self.is_subdomain = config.is_subdomain
    
    def create_or_update_distribution(self, bucket_name: str, cert_arn: str, account_id: str) -> Dict[str, Any]:
        """
        Create new distribution or update existing one
        Returns: Dict with distribution info
        """
        try:
            # Check for existing distribution
            existing_dist = self.get_existing_distribution()
            
            if existing_dist:
                self.logger.info(f"Found existing distribution: {existing_dist['Id']}")
                
                # Update distribution if needed
                self.update_distribution(existing_dist['Id'], bucket_name, cert_arn)
                
                return {
                    'id': existing_dist['Id'],
                    'domain': existing_dist['DomainName'],
                    'action': 'updated'
                }
            
            # Create new distribution
            self.logger.info("Creating CloudFront distribution...")
            
            # Create Origin Access Control first
            oac_id = self.create_origin_access_control()
            
            distribution_config = self._build_distribution_config(bucket_name, cert_arn, oac_id)
            
            def create_dist():
                return self.client.create_distribution(DistributionConfig=distribution_config)
            
            response = self.retry_with_backoff(create_dist)
            
            distribution_id = response['Distribution']['Id']
            distribution_domain = response['Distribution']['DomainName']
            
            self.logger.info(f"Created distribution: {distribution_id}")
            self.logger.info(f"Distribution domain: {distribution_domain}")
            
            # Add tags
            self.add_tags(distribution_id)
            
            return {
                'id': distribution_id,
                'domain': distribution_domain,
                'action': 'created'
            }
            
        except Exception as e:
            self.logger.error(f"CloudFront distribution setup failed: {e}")
            raise
    
    def create_origin_access_control(self) -> str:
        """Create Origin Access Control for S3 access"""
        try:
            oac_name = f'{self.domain}-oac'
            
            # Check for existing OAC
            def list_oacs():
                return self.client.list_origin_access_controls()
            
            response = self.retry_with_backoff(list_oacs)
            
            for oac in response.get('OriginAccessControlList', {}).get('Items', []):
                if oac['Name'] == oac_name:
                    self.logger.info(f"Using existing OAC: {oac['Id']}")
                    return oac['Id']
            
            # Create new OAC
            def create_oac():
                return self.client.create_origin_access_control(
                    OriginAccessControlConfig={
                        'Name': oac_name,
                        'Description': f'OAC for {self.domain}',
                        'SigningProtocol': 'sigv4',
                        'SigningBehavior': 'always',
                        'OriginAccessControlOriginType': 's3'
                    }
                )
            
            response = self.retry_with_backoff(create_oac)
            oac_id = response['OriginAccessControl']['Id']
            
            self.logger.info(f"Created OAC: {oac_id}")
            return oac_id
            
        except Exception as e:
            self.logger.error(f"OAC creation failed: {e}")
            raise
    
    def _build_distribution_config(self, bucket_name: str, cert_arn: str, oac_id: str) -> Dict:
        """Build CloudFront distribution configuration"""
        return {
            'CallerReference': f"{self.domain}-{int(time.time())}",
            'Comment': f'Distribution for {self.domain} (Environment: {self.config.environment})',
            'Enabled': True,
            'Origins': {
                'Quantity': 1,
                'Items': [{
                    'Id': f's3-{bucket_name}',
                    'DomainName': f'{bucket_name}.s3.amazonaws.com',
                    'S3OriginConfig': {
                        'OriginAccessIdentity': ''
                    },
                    'OriginAccessControlId': oac_id
                }]
            },
            'DefaultRootObject': 'index.html',
            'DefaultCacheBehavior': {
                'TargetOriginId': f's3-{bucket_name}',
                'ViewerProtocolPolicy': 'redirect-to-https',
                'AllowedMethods': {
                    'Quantity': 2,
                    'Items': ['GET', 'HEAD'],
                    'CachedMethods': {
                        'Quantity': 2,
                        'Items': ['GET', 'HEAD']
                    }
                },
                'Compress': True,
                'CachePolicyId': self.CACHE_POLICY_OPTIMIZED if self.config.enable_cache else self.CACHE_POLICY_DISABLED,
                'TrustedSigners': {
                    'Enabled': False,
                    'Quantity': 0
                }
            },
            'Aliases': {
                'Quantity': 1 if self.is_subdomain else 2,
                'Items': [self.domain] if self.is_subdomain else [self.domain, self.www_domain]
            },
            'ViewerCertificate': {
                'ACMCertificateArn': cert_arn,
                'SSLSupportMethod': 'sni-only',
                'MinimumProtocolVersion': 'TLSv1.2_2021'
            },
            'CustomErrorResponses': {
                'Quantity': 2,
                'Items': [
                    {
                        'ErrorCode': 404,
                        'ResponsePagePath': '/404.html',
                        'ResponseCode': '404',
                        'ErrorCachingMinTTL': 300
                    },
                    {
                        'ErrorCode': 403,
                        'ResponsePagePath': '/index.html',
                        'ResponseCode': '200',
                        'ErrorCachingMinTTL': 300
                    }
                ]
            },
            'HttpVersion': self.config.http_version,
            'IsIPV6Enabled': self.config.enable_ipv6,
            'PriceClass': self.config.price_class
        }
    
    def get_existing_distribution(self) -> Optional[Dict]:
        """Check for existing CloudFront distribution (any status)"""
        try:
            def list_distributions():
                return self.client.list_distributions()
            
            response = self.retry_with_backoff(list_distributions)
            
            # Look for distributions that serve our domains
            matching_distributions = []
            
            for dist in response.get('DistributionList', {}).get('Items', []):
                aliases = dist.get('Aliases', {}).get('Items', [])
                
                if self.domain in aliases or self.www_domain in aliases:
                    matching_distributions.append({
                        'distribution': dist,
                        'status': dist['Status'],
                        'enabled': dist['Enabled']
                    })
            
            if not matching_distributions:
                return None
            
            # Prioritize distributions: Deployed+Enabled > Deployed+Disabled > InProgress > others
            def get_priority(dist_info):
                status = dist_info['status']
                enabled = dist_info['enabled']
                
                if status == 'Deployed' and enabled:
                    return 1  # Best option
                elif status == 'Deployed' and not enabled:
                    return 2  # Good option, just needs enabling
                elif status == 'InProgress':
                    return 3  # Acceptable, wait for completion
                else:
                    return 4  # Other statuses
            
            # Sort by priority and return the best match
            matching_distributions.sort(key=get_priority)
            best_match = matching_distributions[0]
            
            self.logger.info(f"Found existing distribution for {self.domain} with status: {best_match['status']}")
            return best_match['distribution']
            
        except Exception as e:
            self.logger.warning(f"Error checking distributions: {e}")
            return None
    
    def update_distribution(self, distribution_id: str, bucket_name: str, cert_arn: str):
        """Update existing CloudFront distribution with latest configuration"""
        try:
            # Get current configuration
            def get_config():
                return self.client.get_distribution_config(Id=distribution_id)
            
            response = self.retry_with_backoff(get_config)
            config = response['DistributionConfig']
            etag = response['ETag']
            
            # Track if any updates needed
            updated = False
            changes = []
            
            # Ensure distribution is enabled
            if not config.get('Enabled', True):
                config['Enabled'] = True
                updated = True
                changes.append("enabled distribution")
            
            # Update certificate if different
            current_cert = config['ViewerCertificate'].get('ACMCertificateArn')
            if current_cert != cert_arn:
                config['ViewerCertificate'] = {
                    'ACMCertificateArn': cert_arn,
                    'SSLSupportMethod': 'sni-only',
                    'MinimumProtocolVersion': 'TLSv1.2_2021'
                }
                updated = True
                changes.append("updated SSL certificate")
            
            # Update aliases if missing or incomplete
            current_aliases = config.get('Aliases', {}).get('Items', [])
            if self.domain not in current_aliases or self.www_domain not in current_aliases:
                config['Aliases'] = {
                    'Quantity': 2,
                    'Items': [self.domain, self.www_domain]
                }
                updated = True
                changes.append("updated domain aliases")
            
            # Update origin configuration
            current_origin = config['Origins']['Items'][0] if config['Origins']['Items'] else {}
            expected_origin_domain = f'{bucket_name}.s3.amazonaws.com'
            
            if current_origin.get('DomainName') != expected_origin_domain:
                config['Origins']['Items'][0]['DomainName'] = expected_origin_domain
                config['Origins']['Items'][0]['Id'] = f's3-{bucket_name}'
                config['DefaultCacheBehavior']['TargetOriginId'] = f's3-{bucket_name}'
                updated = True
                changes.append("updated S3 origin")
            
            # Ensure modern settings
            if config.get('HttpVersion') != self.config.http_version:
                config['HttpVersion'] = self.config.http_version
                updated = True
                changes.append("updated HTTP version")
            
            if config.get('IsIPV6Enabled') != self.config.enable_ipv6:
                config['IsIPV6Enabled'] = self.config.enable_ipv6
                updated = True
                changes.append("updated IPv6 setting")

            # Update cache policy if different
            current_cache_policy = config['DefaultCacheBehavior'].get('CachePolicyId')
            expected_cache_policy = self.CACHE_POLICY_OPTIMIZED if self.config.enable_cache else self.CACHE_POLICY_DISABLED

            if current_cache_policy != expected_cache_policy:
                config['DefaultCacheBehavior']['CachePolicyId'] = expected_cache_policy
                updated = True
                cache_status = "enabled" if self.config.enable_cache else "disabled"
                changes.append(f"updated cache policy (caching {cache_status})")

            # Update if changes detected
            if updated:
                def update_dist():
                    return self.client.update_distribution(
                        Id=distribution_id,
                        DistributionConfig=config,
                        IfMatch=etag
                    )
                
                self.retry_with_backoff(update_dist)
                self.logger.info(f"✅ Updated distribution: {', '.join(changes)}")
            else:
                self.logger.info("Distribution configuration is already up to date")
                
        except Exception as e:
            self.logger.warning(f"Error updating distribution: {e}")
            raise
    
    def create_invalidation(self, distribution_id: str, paths: List[str] = None) -> str:
        """Create CloudFront cache invalidation"""
        try:
            if not paths:
                paths = ['/*']  # Invalidate everything by default
            
            self.logger.info(f"Creating invalidation for paths: {paths}")
            
            def create_invalidation():
                return self.client.create_invalidation(
                    DistributionId=distribution_id,
                    InvalidationBatch={
                        'Paths': {
                            'Quantity': len(paths),
                            'Items': paths
                        },
                        'CallerReference': f"invalidation-{int(time.time())}"
                    }
                )
            
            response = self.retry_with_backoff(create_invalidation)
            invalidation_id = response['Invalidation']['Id']
            
            self.logger.info(f"Created invalidation: {invalidation_id}")
            return invalidation_id
            
        except Exception as e:
            self.logger.error(f"Failed to create invalidation: {e}")
            raise
    
    def delete_distribution(self, distribution_id: str):
        """Delete CloudFront distribution"""
        try:
            self.logger.info(f"Deleting CloudFront distribution: {distribution_id}")
            
            # Get current configuration
            def get_config():
                return self.client.get_distribution_config(Id=distribution_id)
            
            response = self.retry_with_backoff(get_config)
            config = response['DistributionConfig']
            etag = response['ETag']
            
            # Disable distribution first if enabled
            if config['Enabled']:
                self.logger.info("Disabling distribution...")
                config['Enabled'] = False
                
                def update_dist():
                    return self.client.update_distribution(
                        Id=distribution_id,
                        DistributionConfig=config,
                        IfMatch=etag
                    )
                
                self.retry_with_backoff(update_dist)
                
                # Wait for distribution to be deployed with disabled state
                self.logger.info("Waiting for distribution to be disabled...")
                self.wait_for_distribution_deployed(distribution_id)
                
                # Get updated ETag after disable
                response = self.retry_with_backoff(get_config)
                etag = response['ETag']
            
            # Delete distribution
            def delete_dist():
                return self.client.delete_distribution(Id=distribution_id, IfMatch=etag)
            
            self.retry_with_backoff(delete_dist)
            self.logger.info("CloudFront distribution deleted successfully")
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'DistributionNotDisabled':
                self.logger.warning("Distribution not fully disabled yet. Please wait and try cleanup again.")
                raise
            else:
                self.logger.error(f"Failed to delete distribution: {e}")
                raise
        except Exception as e:
            self.logger.error(f"Failed to delete distribution: {e}")
            raise
    
    def wait_for_distribution_deployed(self, distribution_id: str, timeout: int = None):
        """Wait for distribution to be deployed"""
        try:
            timeout = timeout or self.config.distribution_deployment_timeout
            
            self.logger.info("Waiting for distribution deployment (this may take 15-20 minutes)...")
            
            waiter = self.client.get_waiter('distribution_deployed')
            waiter.wait(
                Id=distribution_id,
                WaiterConfig={
                    'Delay': 30,
                    'MaxAttempts': timeout // 30
                }
            )
            
            self.logger.info("Distribution deployed successfully!")
            
        except Exception as e:
            self.logger.warning(f"Distribution deployment timeout or error: {e}")
            self.logger.info("Distribution deployment may still be in progress...")
    
    def get_distribution_status(self, distribution_id: str) -> str:
        """Get distribution status"""
        try:
            def get_dist():
                return self.client.get_distribution(Id=distribution_id)
            
            response = self.retry_with_backoff(get_dist)
            return response['Distribution']['Status']
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchDistribution':
                return 'NOT_FOUND'
            raise
        except Exception:
            return 'ERROR'
    
    def validate_resource_exists(self, distribution_id: str) -> bool:
        """Check if distribution exists"""
        try:
            status = self.get_distribution_status(distribution_id)
            return status not in ['NOT_FOUND', 'ERROR']
        except Exception:
            return False
    
    def get_resource_status(self, distribution_id: str) -> str:
        """Get distribution status"""
        return self.get_distribution_status(distribution_id)
    
    def add_tags(self, distribution_id: str, additional_tags: Optional[Dict[str, str]] = None):
        """Add tags to CloudFront distribution"""
        try:
            tags = self.config.default_tags.copy()
            tags['Service'] = 'CloudFront'
            
            if additional_tags:
                tags.update(additional_tags)
            
            tag_list = [{'Key': k, 'Value': v} for k, v in tags.items()]
            
            def add_dist_tags():
                return self.client.tag_resource(
                    Resource=f"arn:aws:cloudfront::{self._get_account_id()}:distribution/{distribution_id}",
                    Tags={'Items': tag_list}
                )
            
            self.retry_with_backoff(add_dist_tags)
            self.logger.info(f"Added tags to distribution: {list(tags.keys())}")
            
        except Exception as e:
            self.logger.warning(f"Failed to add tags to distribution: {e}")
    
    def _get_account_id(self) -> str:
        """Get AWS account ID"""
        try:
            sts = self.session.client('sts')
            return sts.get_caller_identity()['Account']
        except Exception as e:
            self.logger.error(f"Failed to get account ID: {e}")
            raise
    
    def list_domain_distributions(self) -> List[Dict[str, Any]]:
        """List all distributions for the domain"""
        try:
            def list_distributions():
                return self.client.list_distributions()
            
            response = self.retry_with_backoff(list_distributions)
            
            domain_distributions = []
            for dist in response.get('DistributionList', {}).get('Items', []):
                aliases = dist.get('Aliases', {}).get('Items', [])
                
                if any(alias in [self.domain, self.www_domain] for alias in aliases):
                    domain_distributions.append({
                        'id': dist['Id'],
                        'domain_name': dist['DomainName'],
                        'status': dist['Status'],
                        'enabled': dist['Enabled'],
                        'aliases': aliases,
                        'last_modified': dist['LastModifiedTime']
                    })
            
            return domain_distributions
            
        except Exception as e:
            self.logger.error(f"Failed to list distributions: {e}")
            return []
    
    def get_distribution_metrics(self, distribution_id: str, start_time, end_time) -> Dict[str, Any]:
        """Get CloudWatch metrics for distribution"""
        try:
            cloudwatch = self.session.client('cloudwatch', region_name='us-east-1')
            
            metrics = {}
            
            # Get requests metric
            response = cloudwatch.get_metric_statistics(
                Namespace='AWS/CloudFront',
                MetricName='Requests',
                Dimensions=[{'Name': 'DistributionId', 'Value': distribution_id}],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,  # 1 hour
                Statistics=['Sum']
            )
            metrics['requests'] = response.get('Datapoints', [])
            
            # Get bytes downloaded
            response = cloudwatch.get_metric_statistics(
                Namespace='AWS/CloudFront',
                MetricName='BytesDownloaded',
                Dimensions=[{'Name': 'DistributionId', 'Value': distribution_id}],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,
                Statistics=['Sum']
            )
            metrics['bytes_downloaded'] = response.get('Datapoints', [])
            
            # Get error rates
            for error_type in ['4xxErrorRate', '5xxErrorRate']:
                response = cloudwatch.get_metric_statistics(
                    Namespace='AWS/CloudFront',
                    MetricName=error_type,
                    Dimensions=[{'Name': 'DistributionId', 'Value': distribution_id}],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=3600,
                    Statistics=['Average']
                )
                metrics[error_type.lower()] = response.get('Datapoints', [])
            
            return metrics
            
        except Exception as e:
            self.logger.error(f"Failed to get distribution metrics: {e}")
            return {}
    
    def get_cache_statistics(self, distribution_id: str) -> Dict[str, Any]:
        """Get cache hit/miss statistics"""
        try:
            def get_realtime_log_config():
                return self.client.get_realtime_log_config(Name=f"{self.domain}-realtime-logs")
            
            # This would require real-time logs to be configured
            # For now, return basic info
            
            def get_dist():
                return self.client.get_distribution(Id=distribution_id)
            
            response = self.retry_with_backoff(get_dist)
            dist_info = response['Distribution']
            
            return {
                'distribution_id': distribution_id,
                'status': dist_info['Status'],
                'last_modified': dist_info['LastModifiedTime'],
                'enabled': dist_info['DistributionConfig']['Enabled'],
                'price_class': dist_info['DistributionConfig']['PriceClass']
            }
            
        except Exception as e:
            self.logger.warning(f"Could not get cache statistics: {e}")
            return {}