"""
AWS Profile Manager for managing ~/.aws/credentials
"""
import os
import configparser
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import boto3


class AWSProfileManager:
    """Manages AWS profiles in ~/.aws/credentials"""

    CREDENTIALS_PATH = Path.home() / '.aws' / 'credentials'
    CONFIG_PATH = Path.home() / '.aws' / 'config'

    def __init__(self):
        self._ensure_aws_directory()

    def _ensure_aws_directory(self):
        """Ensure ~/.aws directory exists"""
        aws_dir = Path.home() / '.aws'
        if not aws_dir.exists():
            aws_dir.mkdir(mode=0o700)

    def list_profiles(self) -> List[Dict[str, str]]:
        """
        List all AWS profiles from credentials file.
        Returns list of dicts with profile info.
        """
        profiles = []

        if not self.CREDENTIALS_PATH.exists():
            return profiles

        parser = configparser.ConfigParser()
        parser.read(self.CREDENTIALS_PATH)

        for section in parser.sections():
            profile_info = {
                'name': section,
                'has_access_key': 'aws_access_key_id' in parser[section],
                'has_secret_key': 'aws_secret_access_key' in parser[section],
            }

            # Try to get account info for this profile
            try:
                session = boto3.Session(profile_name=section)
                sts = session.client('sts')
                identity = sts.get_caller_identity()
                profile_info['account_id'] = identity.get('Account')
                profile_info['arn'] = identity.get('Arn')
                profile_info['valid'] = True
            except Exception:
                profile_info['account_id'] = None
                profile_info['arn'] = None
                profile_info['valid'] = False

            profiles.append(profile_info)

        return profiles

    def add_profile(
        self,
        profile_name: str,
        access_key: str,
        secret_key: str,
        region: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        Add a new profile to credentials file.
        Returns (success, message).
        """
        parser = configparser.ConfigParser()

        if self.CREDENTIALS_PATH.exists():
            parser.read(self.CREDENTIALS_PATH)

        if profile_name in parser.sections():
            return False, f"Profile '{profile_name}' already exists"

        parser[profile_name] = {
            'aws_access_key_id': access_key,
            'aws_secret_access_key': secret_key
        }

        # Write credentials file with secure permissions
        with open(self.CREDENTIALS_PATH, 'w') as f:
            parser.write(f)
        os.chmod(self.CREDENTIALS_PATH, 0o600)

        # If region specified, add to config file
        if region:
            self._set_profile_region(profile_name, region)

        return True, f"Profile '{profile_name}' added successfully"

    def remove_profile(self, profile_name: str) -> Tuple[bool, str]:
        """
        Remove a profile from credentials file.
        Returns (success, message).
        """
        if not self.CREDENTIALS_PATH.exists():
            return False, "No credentials file found"

        parser = configparser.ConfigParser()
        parser.read(self.CREDENTIALS_PATH)

        if profile_name not in parser.sections():
            return False, f"Profile '{profile_name}' not found"

        if profile_name == 'default':
            return False, "Cannot remove the default profile"

        parser.remove_section(profile_name)

        with open(self.CREDENTIALS_PATH, 'w') as f:
            parser.write(f)

        # Also remove from config if present
        self._remove_profile_config(profile_name)

        return True, f"Profile '{profile_name}' removed successfully"

    def validate_profile(self, profile_name: str) -> Tuple[bool, Optional[Dict]]:
        """
        Validate that a profile exists and credentials work.
        Returns (valid, account_info or None).
        """
        try:
            session = boto3.Session(profile_name=profile_name)
            sts = session.client('sts')
            identity = sts.get_caller_identity()
            return True, {
                'account_id': identity.get('Account'),
                'arn': identity.get('Arn'),
                'user_id': identity.get('UserId')
            }
        except Exception:
            return False, None

    def profile_exists(self, profile_name: str) -> bool:
        """Check if a profile exists in credentials file"""
        if not self.CREDENTIALS_PATH.exists():
            return False

        parser = configparser.ConfigParser()
        parser.read(self.CREDENTIALS_PATH)
        return profile_name in parser.sections()

    def _set_profile_region(self, profile_name: str, region: str):
        """Set default region for profile in config file"""
        parser = configparser.ConfigParser()

        if self.CONFIG_PATH.exists():
            parser.read(self.CONFIG_PATH)

        # AWS config uses 'profile xxx' for non-default profiles
        section_name = profile_name if profile_name == 'default' else f'profile {profile_name}'

        if section_name not in parser.sections():
            parser.add_section(section_name)

        parser[section_name]['region'] = region

        with open(self.CONFIG_PATH, 'w') as f:
            parser.write(f)
        os.chmod(self.CONFIG_PATH, 0o600)

    def _remove_profile_config(self, profile_name: str):
        """Remove profile section from config file"""
        if not self.CONFIG_PATH.exists():
            return

        parser = configparser.ConfigParser()
        parser.read(self.CONFIG_PATH)

        section_name = profile_name if profile_name == 'default' else f'profile {profile_name}'

        if section_name in parser.sections():
            parser.remove_section(section_name)
            with open(self.CONFIG_PATH, 'w') as f:
                parser.write(f)
