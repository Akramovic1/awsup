"""
Tests for configuration management
"""
import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from awsup.config import DeploymentConfig, StateManager


class TestDeploymentConfig:
    """Test deployment configuration"""
    
    def test_default_config(self):
        """Test default configuration values"""
        config = DeploymentConfig(domain="example.com")
        
        assert config.domain == "example.com"
        assert config.region == "us-east-1"
        assert config.environment == "prod"
        assert config.enable_versioning is True
        assert config.enable_encryption is True
        assert config.max_retries == 3
        assert config.default_tags["Domain"] == "example.com"
    
    def test_custom_config(self):
        """Test custom configuration values"""
        config = DeploymentConfig(
            domain="test.org",
            region="us-west-2", 
            environment="dev",
            enable_versioning=False,
            max_retries=5
        )
        
        assert config.domain == "test.org"
        assert config.region == "us-west-2"
        assert config.environment == "dev"
        assert config.enable_versioning is False
        assert config.max_retries == 5
    
    def test_config_validation_valid(self):
        """Test valid configuration passes validation"""
        config = DeploymentConfig(domain="example.com", region="us-east-1")
        config.validate()  # Should not raise
    
    def test_config_validation_invalid_domain(self):
        """Test invalid domain fails validation"""
        config = DeploymentConfig(domain="invalid", region="us-east-1")
        
        with pytest.raises(ValueError, match="Configuration validation failed"):
            config.validate()
    
    def test_config_validation_invalid_region(self):
        """Test invalid region fails validation"""
        config = DeploymentConfig(domain="example.com", region="invalid-region")
        
        with pytest.raises(ValueError, match="Invalid region"):
            config.validate()
    
    def test_config_to_file(self):
        """Test saving configuration to file"""
        config = DeploymentConfig(domain="example.com")
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            config_path = f.name
        
        try:
            config.to_file(config_path)
            
            # Verify file was created and contains correct data
            assert Path(config_path).exists()
            
            with open(config_path, 'r') as f:
                saved_data = json.load(f)
            
            assert saved_data['domain'] == "example.com"
            assert saved_data['region'] == "us-east-1"
            
        finally:
            Path(config_path).unlink()
    
    def test_config_from_file(self):
        """Test loading configuration from file"""
        config_data = {
            "domain": "test.com",
            "region": "us-west-2",
            "environment": "staging"
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(config_data, f)
            config_path = f.name
        
        try:
            config = DeploymentConfig.from_file(config_path)
            
            assert config.domain == "test.com"
            assert config.region == "us-west-2" 
            assert config.environment == "staging"
            
        finally:
            Path(config_path).unlink()
    
    def test_config_from_nonexistent_file(self):
        """Test loading from non-existent file raises error"""
        with pytest.raises(FileNotFoundError):
            DeploymentConfig.from_file("/nonexistent/config.json")


class TestAWSCredentialValidator:
    """Test AWS credential validation"""

    def _clear_modules_and_cache(self):
        """Remove awsup modules from sys.modules and clear boto3 cache"""
        import sys
        import boto3

        # Clear boto3's default session cache
        boto3.DEFAULT_SESSION = None

        # Remove cached modules to ensure fresh import
        modules_to_remove = [k for k in list(sys.modules.keys())
                           if k.startswith('awsup')]
        for mod in modules_to_remove:
            del sys.modules[mod]

    def test_validate_credentials_success(self):
        """Test successful credential validation"""
        self._clear_modules_and_cache()

        mock_session = MagicMock()
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {'Account': '123456789012'}
        mock_session.client.return_value = mock_sts

        with patch('boto3.Session', return_value=mock_session):
            from awsup.config import AWSCredentialValidator
            result = AWSCredentialValidator.validate_credentials()
            assert result is True

    def test_validate_credentials_failure(self):
        """Test credential validation failure"""
        self._clear_modules_and_cache()

        mock_session = MagicMock()
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.side_effect = Exception("No credentials")
        mock_session.client.return_value = mock_sts

        with patch('boto3.Session', return_value=mock_session):
            from awsup.config import AWSCredentialValidator
            result = AWSCredentialValidator.validate_credentials()
            assert result is False

    def test_get_account_id_success(self):
        """Test successful account ID retrieval"""
        self._clear_modules_and_cache()

        mock_session = MagicMock()
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {'Account': '123456789012'}
        mock_session.client.return_value = mock_sts

        with patch('boto3.Session', return_value=mock_session):
            from awsup.config import AWSCredentialValidator
            account_id = AWSCredentialValidator.get_account_id()
            assert account_id == '123456789012'

    def test_get_account_id_failure(self):
        """Test account ID retrieval failure"""
        self._clear_modules_and_cache()

        mock_session = MagicMock()
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.side_effect = Exception("No credentials")
        mock_session.client.return_value = mock_sts

        with patch('boto3.Session', return_value=mock_session):
            from awsup.config import AWSCredentialValidator
            account_id = AWSCredentialValidator.get_account_id()
            assert account_id is None

    def test_validate_permissions(self):
        """Test permission validation"""
        self._clear_modules_and_cache()

        mock_clients = {
            'route53': MagicMock(),
            's3': MagicMock(),
            'cloudfront': MagicMock(),
            'acm': MagicMock()
        }
        mock_session = MagicMock()
        mock_session.client.side_effect = lambda service, **kw: mock_clients[service]

        with patch('boto3.Session', return_value=mock_session):
            from awsup.config import AWSCredentialValidator
            config = DeploymentConfig(domain="example.com")
            permissions = AWSCredentialValidator.validate_permissions(config)

            assert permissions['route53'] is True
            assert permissions['s3'] is True
            assert permissions['cloudfront'] is True
            assert permissions['acm'] is True


class TestStateManager:
    """Test state management"""
    
    def test_state_manager_init(self):
        """Test state manager initialization"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Change to temp directory for test
            original_cwd = Path.cwd()
            try:
                import os
                os.chdir(temp_dir)
                
                state_manager = StateManager("example.com", "test")
                
                assert state_manager.domain == "example.com"
                assert state_manager.environment == "test"
                assert state_manager.state_dir.exists()
                
            finally:
                os.chdir(original_cwd)
    
    def test_save_and_load_state(self):
        """Test state save and load operations"""
        with tempfile.TemporaryDirectory() as temp_dir:
            original_cwd = Path.cwd()
            try:
                import os
                os.chdir(temp_dir)
                
                state_manager = StateManager("example.com", "test")
                
                # Save state
                test_state = {
                    'hosted_zone_id': '/hostedzone/Z123456789',
                    'bucket_name': 'example.com',
                    'phase1_complete': True
                }
                
                state_manager.save_state(test_state)
                
                # Load state
                loaded_state = state_manager.load_state()
                
                assert loaded_state['hosted_zone_id'] == '/hostedzone/Z123456789'
                assert loaded_state['bucket_name'] == 'example.com'
                assert loaded_state['phase1_complete'] is True
                assert 'last_updated' in loaded_state
                
            finally:
                os.chdir(original_cwd)
    
    def test_get_state_summary(self):
        """Test state summary generation"""
        with tempfile.TemporaryDirectory() as temp_dir:
            original_cwd = Path.cwd()
            try:
                import os
                os.chdir(temp_dir)
                
                state_manager = StateManager("example.com", "prod")
                
                # Save test state
                test_state = {
                    'hosted_zone_id': '/hostedzone/Z123456789',
                    'distribution_id': 'E123456789',
                    'phase1_complete': True,
                    'phase2_complete': True
                }
                
                state_manager.save_state(test_state)
                
                # Get summary
                summary = state_manager.get_state_summary()
                
                assert summary['domain'] == "example.com"
                assert summary['environment'] == "prod"
                assert summary['phase1_complete'] is True
                assert summary['phase2_complete'] is True
                assert summary['resources']['hosted_zone_id'] == '/hostedzone/Z123456789'
                assert summary['resources']['distribution_id'] == 'E123456789'
                
            finally:
                os.chdir(original_cwd)
    
    def test_clear_state(self):
        """Test state clearing"""
        with tempfile.TemporaryDirectory() as temp_dir:
            original_cwd = Path.cwd()
            try:
                import os
                os.chdir(temp_dir)
                
                state_manager = StateManager("example.com", "test")
                
                # Save state
                state_manager.save_state({'test': 'data'})
                assert state_manager.state_file.exists()
                
                # Clear state
                state_manager.clear_state()
                assert not state_manager.state_file.exists()
                
            finally:
                os.chdir(original_cwd)