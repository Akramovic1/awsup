"""
S3 Manager for bucket operations
"""
import os
import json
from typing import Dict, List, Optional, Any
from pathlib import Path
from botocore.exceptions import ClientError
from .base import BaseAWSManager
from ..config import DeploymentConfig
from ..validators import FileValidator


class S3Manager(BaseAWSManager):
    """Manages S3 bucket operations"""

    def __init__(self, config: DeploymentConfig):
        super().__init__(config)
        self.client = self.get_client('s3', region_name=config.region)
        self.bucket_name = config.domain
    
    def create_or_get_bucket(self) -> str:
        """Create or get existing S3 bucket, always updating configuration"""
        try:
            bucket_exists = self._bucket_exists()
            
            if bucket_exists:
                self.logger.info(f"Found existing bucket: {self.bucket_name}")
            else:
                # Create new bucket
                self.logger.info(f"Creating S3 bucket: {self.bucket_name}")
                
                def create_bucket():
                    if self.config.region == 'us-east-1':
                        return self.client.create_bucket(Bucket=self.bucket_name)
                    else:
                        return self.client.create_bucket(
                            Bucket=self.bucket_name,
                            CreateBucketConfiguration={
                                'LocationConstraint': self.config.region
                            }
                        )
                
                self.retry_with_backoff(create_bucket)
                self.logger.info(f"Created bucket: {self.bucket_name}")
            
            # Always configure bucket settings (for both new and existing)
            self.logger.info("Updating bucket configuration...")
            self._configure_bucket()
            
            # Always update tags
            self.add_tags()
            
            action = "created" if not bucket_exists else "updated"
            self.logger.info(f"✅ Bucket {action} and configured successfully")
            
            return self.bucket_name
            
        except Exception as e:
            self.logger.error(f"S3 bucket setup failed: {e}")
            raise
    
    def _bucket_exists(self) -> bool:
        """Check if bucket exists"""
        try:
            def check_bucket():
                return self.client.head_bucket(Bucket=self.bucket_name)
            
            self.retry_with_backoff(check_bucket)
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            raise
    
    def _configure_bucket(self):
        """Configure bucket settings"""
        try:
            # Enable versioning
            if self.config.enable_versioning:
                def enable_versioning():
                    return self.client.put_bucket_versioning(
                        Bucket=self.bucket_name,
                        VersioningConfiguration={'Status': 'Enabled'}
                    )
                
                self.retry_with_backoff(enable_versioning)
                self.logger.info("Enabled versioning")
            
            # Set encryption
            if self.config.enable_encryption:
                def enable_encryption():
                    return self.client.put_bucket_encryption(
                        Bucket=self.bucket_name,
                        ServerSideEncryptionConfiguration={
                            'Rules': [{
                                'ApplyServerSideEncryptionByDefault': {
                                    'SSEAlgorithm': 'AES256'
                                }
                            }]
                        }
                    )
                
                self.retry_with_backoff(enable_encryption)
                self.logger.info("Enabled encryption")
            
            # Block public access
            def block_public_access():
                return self.client.put_public_access_block(
                    Bucket=self.bucket_name,
                    PublicAccessBlockConfiguration={
                        'BlockPublicAcls': True,
                        'IgnorePublicAcls': True,
                        'BlockPublicPolicy': True,
                        'RestrictPublicBuckets': True
                    }
                )
            
            self.retry_with_backoff(block_public_access)
            self.logger.info("Blocked public access")
            
        except Exception as e:
            self.logger.warning(f"Error configuring bucket: {e}")
    
    def upload_website_files(self, website_path: Optional[str] = None) -> int:
        """
        Upload website files to S3 bucket
        Returns: Number of files uploaded
        """
        try:
            uploaded_count = 0
            
            if not website_path:
                # Use default landing page
                self.logger.info("Using default 'Coming Soon' page...")
                default_path = Path(__file__).parent.parent / 'templates' / 'default-index.html'
                website_path = str(default_path)
            
            # Validate website path
            is_valid, error = FileValidator.validate_website_path(website_path)
            if not is_valid:
                raise ValueError(f"Invalid website path: {error}")
            
            path_obj = Path(website_path)
            
            if path_obj.is_file():
                # Single file upload - rename default template to index.html
                s3_key = "index.html" if path_obj.name == "default-index.html" else path_obj.name
                self._upload_single_file(path_obj, s3_key)
                uploaded_count = 1
            else:
                # Directory upload
                uploaded_count = self._upload_directory(path_obj)
            
            self.logger.info(f"Uploaded {uploaded_count} files successfully")
            return uploaded_count
            
        except Exception as e:
            self.logger.error(f"File upload failed: {e}")
            raise
    
    def _upload_single_file(self, file_path: Path, s3_key: str):
        """Upload single file to S3"""
        content_type = self._get_content_type(file_path.suffix)
        
        def upload_file():
            return self.client.upload_file(
                str(file_path),
                self.bucket_name,
                s3_key,
                ExtraArgs={
                    'ContentType': content_type,
                    'CacheControl': self._get_cache_control(file_path.suffix)
                }
            )
        
        self.retry_with_backoff(upload_file)
        self.logger.info(f"Uploaded: {s3_key}")
    
    def _upload_directory(self, dir_path: Path) -> int:
        """Upload directory contents to S3"""
        uploaded_count = 0
        
        for file_path in dir_path.rglob('*'):
            if file_path.is_file() and not file_path.name.startswith('.'):
                relative_path = file_path.relative_to(dir_path)
                s3_key = str(relative_path).replace('\\', '/')  # Windows compatibility
                
                self._upload_single_file(file_path, s3_key)
                uploaded_count += 1
        
        return uploaded_count
    
    def _get_content_type(self, extension: str) -> str:
        """Get content type based on file extension"""
        ext = extension.lower()
        
        content_types = {
            '.html': 'text/html',
            '.css': 'text/css',
            '.js': 'application/javascript',
            '.json': 'application/json',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.svg': 'image/svg+xml',
            '.ico': 'image/x-icon',
            '.txt': 'text/plain',
            '.xml': 'application/xml',
            '.pdf': 'application/pdf',
            '.woff': 'font/woff',
            '.woff2': 'font/woff2',
            '.ttf': 'font/ttf',
            '.eot': 'application/vnd.ms-fontobject'
        }
        
        return content_types.get(ext, 'application/octet-stream')
    
    def _get_cache_control(self, extension: str) -> str:
        """Get cache control headers based on file type"""
        ext = extension.lower()
        
        # Cache static assets longer
        if ext in ['.css', '.js', '.jpg', '.jpeg', '.png', '.gif', '.svg', 
                   '.woff', '.woff2', '.ttf', '.eot']:
            return 'public, max-age=31536000'  # 1 year
        
        # Cache HTML files shorter
        if ext in ['.html', '.htm']:
            return 'public, max-age=3600'  # 1 hour
        
        # Default cache
        return 'public, max-age=86400'  # 1 day
    
    def update_bucket_policy(self, distribution_id: str, account_id: str):
        """Update S3 bucket policy for CloudFront access"""
        try:
            policy = {
                "Version": "2012-10-17",
                "Statement": [{
                    "Sid": "AllowCloudFrontAccessOnly",
                    "Effect": "Allow",
                    "Principal": {
                        "Service": "cloudfront.amazonaws.com"
                    },
                    "Action": "s3:GetObject",
                    "Resource": f"arn:aws:s3:::{self.bucket_name}/*",
                    "Condition": {
                        "StringEquals": {
                            "AWS:SourceArn": f"arn:aws:cloudfront::{account_id}:distribution/{distribution_id}"
                        }
                    }
                }]
            }
            
            def update_policy():
                return self.client.put_bucket_policy(
                    Bucket=self.bucket_name,
                    Policy=json.dumps(policy)
                )
            
            self.retry_with_backoff(update_policy)
            self.logger.info("Updated S3 bucket policy for CloudFront access")
            
        except Exception as e:
            self.logger.error(f"Failed to update bucket policy: {e}")
            raise
    
    def delete_bucket_and_contents(self):
        """Delete S3 bucket and all contents"""
        try:
            self.logger.info(f"Deleting S3 bucket: {self.bucket_name}")
            
            # Delete all object versions and delete markers
            def list_versions():
                return self.client.list_object_versions(Bucket=self.bucket_name)
            
            paginator = self.client.get_paginator('list_object_versions')
            
            delete_list = []
            
            for page in paginator.paginate(Bucket=self.bucket_name):
                # Collect all versions and delete markers
                for version in page.get('Versions', []):
                    delete_list.append({
                        'Key': version['Key'],
                        'VersionId': version['VersionId']
                    })
                
                for marker in page.get('DeleteMarkers', []):
                    delete_list.append({
                        'Key': marker['Key'],
                        'VersionId': marker['VersionId']
                    })
                
                # Delete in batches of 1000 (AWS limit)
                if len(delete_list) >= 1000:
                    self._delete_objects_batch(delete_list[:1000])
                    delete_list = delete_list[1000:]
            
            # Delete remaining objects
            if delete_list:
                self._delete_objects_batch(delete_list)
            
            # Delete the bucket
            def delete_bucket():
                return self.client.delete_bucket(Bucket=self.bucket_name)
            
            self.retry_with_backoff(delete_bucket)
            self.logger.info("S3 bucket deleted successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to delete S3 bucket: {e}")
            raise
    
    def _delete_objects_batch(self, delete_list: List[Dict]):
        """Delete batch of objects"""
        def delete_objects():
            return self.client.delete_objects(
                Bucket=self.bucket_name,
                Delete={'Objects': delete_list}
            )
        
        self.retry_with_backoff(delete_objects)
    
    def add_tags(self, additional_tags: Optional[Dict[str, str]] = None):
        """Add tags to S3 bucket"""
        try:
            tags = self.config.default_tags.copy()
            tags['Purpose'] = 'StaticWebsite'
            
            if additional_tags:
                tags.update(additional_tags)
            
            tag_set = [{'Key': k, 'Value': v} for k, v in tags.items()]
            
            def add_bucket_tags():
                return self.client.put_bucket_tagging(
                    Bucket=self.bucket_name,
                    Tagging={'TagSet': tag_set}
                )
            
            self.retry_with_backoff(add_bucket_tags)
            self.logger.info(f"Added tags to bucket: {list(tags.keys())}")
            
        except Exception as e:
            self.logger.warning(f"Failed to add tags: {e}")
    
    def validate_resource_exists(self, bucket_name: str = None) -> bool:
        """Check if bucket exists"""
        bucket_name = bucket_name or self.bucket_name
        return self._bucket_exists()
    
    def get_resource_status(self, bucket_name: str = None) -> str:
        """Get bucket status"""
        bucket_name = bucket_name or self.bucket_name
        
        try:
            if self._bucket_exists():
                return "available"
            return "not_found"
        except Exception:
            return "error"